import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

import numpy as np

from audiotransfer.analysis import AnalysisConfig, PitchFrame
from audiotransfer.arrange import arrange_events
from audiotransfer.desktop import CHATANG_SHA256, ConversionRequest, convert
from audiotransfer.events import NoteEvent
from audiotransfer.output import write_outputs
from audiotransfer.pipeline import events_from_song
from audiotransfer.singing import SungNote, GameModel, overlay_events, stitch_chunks, transcribe, ensure_model, _download
from audiotransfer.song import SongAnalysis, analyze_stems


class SingingTests(unittest.TestCase):
    def test_exact_repeats_fast_notes_and_breaths_survive_overlay(self):
        baseline = [NoteEvent(440, 3000, 'A4')]
        notes = [SungNote(0, 600, None), SungNote(600, 800, 60), SungNote(800, 1000, 60),
                 SungNote(1000, 1200, None), SungNote(1200, 1270, 62), SungNote(1270, 3000, None)]
        result = overlay_events(baseline, notes, [(500, 1500)], 3000)
        self.assertEqual([e.duration_ms for e in result], [500, 100, 200, 200, 200, 70, 230, 1500])
        self.assertEqual([e.note for e in result], ['A4', 'rest', 'C4', 'C4', 'rest', 'D4', 'rest', 'A4'])
        self.assertEqual(sum(e.duration_ms for e in result), 3000)

    def test_artificial_seam_merges_hold_but_not_a_real_same_pitch_attack(self):
        left = (0, 1000, 0, [SungNote(0, 1100, 60)])
        held = (1000, 2000, 900, [SungNote(0, 1100, 60)])
        self.assertEqual(stitch_chunks([left, held], 2000), [SungNote(0, 2000, 60)])
        repeated = (1000, 2000, 900, [SungNote(0, 100, 60), SungNote(100, 1100, 60)])
        self.assertEqual(stitch_chunks([left, repeated], 2000), [SungNote(0, 1000, 60), SungNote(1000, 2000, 60)])

    def test_no_smoothing_of_a_real_sub_80ms_model_note(self):
        analysis = SongAnalysis([PitchFrame(440, .9, .2)] * 50, set(), ['vocals'] * 50, 16000, 16000,
                                sung_notes=[SungNote(0, 40, 60), SungNote(40, 1000, 60)], singing_regions=[(0, 1000)])
        events = events_from_song(analysis, AnalysisConfig(pitch_mode='song', song_backend='game'), 80)
        self.assertEqual([e.duration_ms for e in events], [40, 960])

    def test_note_timing_is_not_forced_onto_beat_grid(self):
        events = [NoteEvent(440, 230, 'A4'), NoteEvent(494, 270, 'B4'), NoteEvent(0, 500, 'rest')]
        arranged = arrange_events(events, np.array([0, .5, 1, 1.5]), preserve_timing=True, transpose=0)
        self.assertEqual([e.duration_ms for e in arranged.events], [230, 270, 500])
        self.assertEqual(arranged.metadata['snapped_boundaries'], 0)

    def test_silence_needs_no_model_or_download(self):
        with patch('audiotransfer.singing.ensure_model', side_effect=AssertionError('download')):
            self.assertEqual(transcribe(np.zeros(16000, np.float32), 16000), [SungNote(0, 1000, None)])

    def test_instrumental_mode_does_not_invoke_singing_model(self):
        baseline = SongAnalysis([], set(), [], 0, 16000, dict(detected_content_mode='instrumental'))
        with patch('audiotransfer.song.analyze_stems', return_value=baseline), patch('audiotransfer.singing.transcribe') as infer:
            result = analyze_stems(np.zeros(0), np.zeros(0), AnalysisConfig(song_backend='game', content_mode='instrumental'))
        infer.assert_not_called()
        self.assertIs(result, baseline)

    def test_presence_flag_wins_over_nonzero_unvoiced_pitch(self):
        model = GameModel.__new__(GameModel)
        model.config = dict(samplerate=16000, languages=dict(zh=4))
        model.sessions = {name: MagicMock() for name in ('encoder', 'dur2bd', 'segmenter', 'bd2dur', 'estimator')}
        mask = np.ones((1, 20), bool)
        model.sessions['encoder'].run.return_value = (np.zeros((1, 20, 2)), np.zeros((1, 20, 2)), mask)
        model.sessions['dur2bd'].run.return_value = [np.zeros((1, 20), bool)]
        model.sessions['segmenter'].run.return_value = [np.zeros((1, 20), bool)]
        model.sessions['bd2dur'].run.return_value = (np.array([[.1, .1]]), np.array([[True, True]]))
        model.sessions['estimator'].run.return_value = (np.array([[False, True]]), np.array([[75., 69.]]))
        result = model.predict(np.ones(3200, np.float32), 16000)
        self.assertEqual(result, [SungNote(0, 100, None), SungNote(100, 200, 69)])

    def test_local_archive_and_extracted_files_are_both_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); research = root / 'game-research'; research.mkdir()
            archive = research / 'GAME-1.0.3-small-onnx.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('GAME-1.0.3-small-onnx/config.json', b'{}')
                bundle.writestr('../must-not-extract', b'bad')
            with patch('audiotransfer.singing.MODELS', root), patch('audiotransfer.singing.FILES', {'config.json': hashlib.sha256(b'{}').hexdigest()}), patch('audiotransfer.singing.ARCHIVE_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest()), patch('audiotransfer.singing._download', side_effect=AssertionError('network')):
                directory = ensure_model()
                self.assertEqual((directory / 'config.json').read_bytes(), b'{}')
                (directory / 'config.json').write_bytes(b'corrupt')
                ensure_model()
                self.assertEqual((directory / 'config.json').read_bytes(), b'{}')
            self.assertFalse((root / 'must-not-extract').exists())

    def test_range_download_resumes_a_truncated_response_and_verifies_archive(self):
        class Response(io.BytesIO):
            def __init__(self, data, headers=None):
                super().__init__(data); self.headers = headers or {}; self.status = 206
            def geturl(self): return 'https://example.invalid/asset?signed=yes'
        seen = []
        def open_url(request, timeout):
            if request.get_method() == 'HEAD': return Response(b'')
            bounds = request.get_header('Range'); seen.append(bounds)
            if bounds == 'bytes=0-5': return Response(b'ab', {'Content-Range': 'bytes 0-5/6'})
            self.assertEqual(bounds, 'bytes=2-5')
            return Response(b'cdef', {'Content-Range': 'bytes 2-5/6'})
        with tempfile.TemporaryDirectory() as folder, patch('audiotransfer.singing.ARCHIVE_SIZE', 6), patch('audiotransfer.singing.ARCHIVE_SHA256', hashlib.sha256(b'abcdef').hexdigest()), patch('audiotransfer.singing.urllib.request.urlopen', side_effect=open_url):
            archive = Path(folder) / 'model.zip'
            _download(archive, lambda _: None)
            self.assertEqual(archive.read_bytes(), b'abcdef')
        self.assertEqual(seen, ['bytes=0-5', 'bytes=2-5'])

    def test_new_mode_never_substitutes_the_reviewed_tea_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / '茶汤.flac'; source.write_bytes(b'fixture')
            def extract(src, output, **options):
                self.assertEqual(options['config'].song_backend, 'game')
                return write_outputs([NoteEvent(440, 300, 'A4')], output, target='platformio-stm32', sound_mode='pulse', active_low=True)
            with patch('audiotransfer.desktop.digest', return_value=CHATANG_SHA256), patch('audiotransfer.pipeline.process_file', side_effect=extract), patch('audiotransfer.scored_song.build', side_effect=AssertionError('reviewed score')):
                result = convert(ConversionRequest(source, root / 'out', '茶汤'), lambda _: None)
            self.assertTrue(result.preview.is_file())
            meta = json.loads((result.project / 'desktop-job.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['song_backend'], 'game')


if __name__ == '__main__':
    unittest.main()
