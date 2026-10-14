import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from audiotransfer.analysis import AnalysisConfig, analyze_samples_tracked, analyze_samples
from audiotransfer.events import NoteEvent, NoteFrame, merge_note_frames
from audiotransfer.legacy_gui import AudioTransferApp
from audiotransfer.output import write_outputs
from audiotransfer.pipeline import events_from_samples
from audiotransfer.preview import playback_gap, render_events


class RegressionTests(unittest.TestCase):
    def test_gui_resolves_targets_and_starts_worker(self):
        app = object.__new__(AudioTransferApp)
        values = dict(input='input.wav', output='out', ffmpeg='ffmpeg', target='STM32 PlatformIO',
                      board='bluepill_f103c8', stm32_pin='PA8', pio='pio', upload_protocol='stlink',
                      upload=False, pitch_mode='完整歌曲（人声＋乐器）', pin='8', sample_rate='16000',
                      frame_ms='40', hop_ms='20', speed='1', candidate_count='5',
                      tracking_min_frequency='140', min_note_ms='40', max_notes='0', gap_ms='20')
        for key, value in values.items():
            setattr(app, key + '_var', SimpleNamespace(get=lambda v=value: v))
        app.start_button = SimpleNamespace(configure=lambda **_: None)
        app.status_var = SimpleNamespace(set=lambda _: None)
        app._append_log = lambda _: None
        with patch('audiotransfer.legacy_gui.threading.Thread') as thread:
            app._start()
            thread.return_value.start.assert_called_once()
            self.assertEqual(thread.call_args.kwargs['args'][9], 'platformio-stm32')
            self.assertEqual(thread.call_args.kwargs['args'][3].pitch_mode, 'song')

    def test_upper_bound_and_low_notes_keep_pitch(self):
        time = np.arange(16000) / 16000
        for mode in ('legacy', 'tracked'):
            for frequency in (110, 980, 1000):
                config = AnalysisConfig(pitch_mode=mode)
                samples = .7 * np.sin(2 * np.pi * frequency * time)
                frames = analyze_samples(samples, config) if mode == 'legacy' else analyze_samples_tracked(samples, config)
                voiced = [f.frequency_hz for f in frames if f.frequency_hz > 0]
                self.assertEqual(len(voiced), len(frames), (mode, frequency))
                self.assertAlmostEqual(float(np.median(voiced)), frequency, delta=8)

    def test_pcm_white_noise_is_unvoiced(self):
        noise = np.clip(.3 * np.random.default_rng(20261012).standard_normal(16000), -1, 1)
        frames = analyze_samples_tracked(noise)
        self.assertTrue(all(f.frequency_hz == 0 for f in frames))

    def test_timeline_and_preview_keep_exact_duration(self):
        sr = 22050
        samples = .7 * np.sin(2 * np.pi * 440 * np.arange(sr * 3 + 137) / sr)
        events = events_from_samples(samples, AnalysisConfig(sample_rate=sr), min_note_ms=0)
        expected = round(len(samples) * 1000 / sr)
        self.assertEqual(sum(e.duration_ms for e in events), expected)
        repeated = [NoteEvent(440, 41, 'A4'), NoteEvent(0, 37, 'rest')] * 200
        rendered = render_events([e.as_dict() for e in repeated], sample_rate=sr, gap_ms=20)
        self.assertEqual(len(rendered), round(sr * sum(e.duration_ms for e in repeated) / 1000))

    def test_same_pitch_attacks_remain_separate(self):
        frames = [NoteFrame('A4', 440) for _ in range(5)]
        frames[3] = NoteFrame('A4', 440, onset=True)
        events = merge_note_frames(frames, 20, 40, None)
        self.assertEqual([e.duration_ms for e in events], [60, 40])

    def test_preview_loads_firmware_gap_and_score_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = write_outputs([NoteEvent(440, 100, 'A4'), NoteEvent(0, 25, 'rest')], folder, gap_ms=13)
            self.assertEqual(playback_gap(paths.json_path), 13)
            self.assertTrue(paths.midi_path.read_bytes().startswith(b'MThd'))
            self.assertTrue(paths.preview_path.is_file())
            self.assertIn("6\tA4", paths.score_path.read_text(encoding='utf-8'))
            self.assertEqual(json.loads(paths.playback_path.read_text())['duration_ms'], 125)

    def test_nonfinite_energy_is_rejected(self):
        with self.assertRaises(ValueError):
            AnalysisConfig(energy_threshold=float('nan')).validate()


if __name__ == '__main__':
    unittest.main()
