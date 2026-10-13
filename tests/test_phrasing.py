import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from audiotransfer.analysis import AnalysisConfig, PitchFrame
from audiotransfer.arrange import arrange_events, phrase_expression
from audiotransfer.events import NoteEvent, scale_note_events
from audiotransfer.notes import midi_to_frequency
from audiotransfer.output import write_outputs
from audiotransfer.pipeline import events_from_song
from audiotransfer.preview import render_events, load_events
from audiotransfer.song import SongAnalysis, confirmed_vocals, foreground_instruments, repair_phrase_gaps, _selected_instrument_track


class PhrasingTests(unittest.TestCase):
    config = AnalysisConfig(pitch_mode='song')

    def test_sustained_boundary_pitch_does_not_latch_wrong_semitone(self):
        frames = [PitchFrame(midi_to_frequency(60) * 2 ** (.45 / 12), .9, .1)]
        frames += [PitchFrame(midi_to_frequency(60) * 2 ** (.7 / 12), .9, .1)] * 24
        a = SongAnalysis(frames, set(), ['vocals'] * 25, 8000, 16000)
        result = events_from_song(a, self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in result], [('C#4', 500)])

    def test_real_semitone_change_survives_local_pitch_centre(self):
        frames = [PitchFrame(440, .9, .1)] * 20 + [PitchFrame(midi_to_frequency(70), .9, .1)] * 20
        a = SongAnalysis(frames, set(), ['vocals'] * 40, 12800, 16000)
        self.assertEqual([(e.note, e.duration_ms) for e in events_from_song(a, self.config, 80)], [('A4', 400), ('A#4', 400)])

    def test_brief_unattacked_semitone_return_is_simplified_but_real_attack_stays(self):
        frames = [PitchFrame(440, .9, .1)] * 20 + [PitchFrame(midi_to_frequency(68), .9, .1)] * 6 + [PitchFrame(440, .9, .1)] * 20
        held = SongAnalysis(frames, set(), ['vocals'] * 46, 14720, 16000)
        result = events_from_song(held, self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in result], [('A4', 920)])
        attacked = SongAnalysis(frames, {20}, ['vocals'] * 46, 14720, 16000)
        result = events_from_song(attacked, self.config, 80)
        self.assertIn('G#4', [e.note for e in result])
        self.assertEqual(sum(e.duration_ms for e in result), 920)

    def gap_case(self, quiet=False, distance=0):
        sr = 16000
        t = np.arange(6400) / sr
        voice = (.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        voice[1920:3520] = 0 if quiet else np.random.default_rng(5).normal(0, .05, 1600)
        frames = [PitchFrame(440, .9, .14)] * 6 + [PitchFrame(0, .05, .05)] * 5
        frames += [PitchFrame(440 * 2 ** (distance / 12), .9, .14)] * 9
        sources = ['vocals'] * 6 + ['rest'] * 5 + ['vocals'] * 9
        a = SongAnalysis(frames, {11}, sources, len(voice), sr)
        return repair_phrase_gaps(a, voice, np.zeros_like(voice), self.config)

    def test_energized_consonant_gap_is_bridged_and_attack_kept(self):
        result = self.gap_case()
        self.assertEqual(result.details['repaired_gaps'], 1)
        self.assertTrue(all(f.frequency_hz > 0 for f in result.frames))
        self.assertIn(11, result.onsets)
        events = events_from_song(result, self.config, 80)
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(e.duration_ms for e in events), 400)

    def test_true_quiet_breath_and_large_jump_are_not_bridged(self):
        for result in (self.gap_case(quiet=True), self.gap_case(distance=7)):
            self.assertEqual(result.details['repaired_gaps'], 0)
            self.assertTrue(all(f.frequency_hz == 0 for f in result.frames[6:11]))

    def test_quiet_vocal_leakage_does_not_protect_instrumental_intro(self):
        voices = [PitchFrame(392, .9, .006)] * 50
        mask = confirmed_vocals(voices, np.full(50, .006), np.full(50, .1), self.config)
        self.assertFalse(np.any(mask))
        energy = np.full(50, .06)
        energy[20:25] = .009
        mask = confirmed_vocals(voices, energy, np.full(50, .1), self.config)
        self.assertTrue(np.all(mask))

    def test_foreground_tracks_quieter_melody_over_sustained_backing(self):
        sr = 16000
        t = np.arange(sr * 4) / sr
        targets = [392, 440, 493.883, 523.251]
        phase = 2 * np.pi * np.cumsum(np.repeat(targets, sr)) / sr
        mixed = (.6 * np.sin(2 * np.pi * 220 * t) + .2 * np.sin(2 * np.pi * 440 * t) + .22 * np.sin(phase)).astype(np.float32)
        config = AnalysisConfig(pitch_mode='tracked', frame_ms=80, min_frequency_hz=140)
        lead = _selected_instrument_track(foreground_instruments(mixed, config), config, np.ones(200, bool), spectral=True)
        for i, target in enumerate(targets):
            found = np.median([f.frequency_hz for f in lead[i * 50 + 15:i * 50 + 35]])
            self.assertAlmostEqual(found, target, delta=5)

    def test_legato_changes_pitch_without_releasing_each_short_note(self):
        events = [{'frequency_hz': 500, 'duration_ms': 200, 'velocity': 100},
                  {'frequency_hz': 600, 'duration_ms': 200, 'velocity': 100, 'legato': True},
                  {'frequency_hz': 0, 'duration_ms': 100}]
        joined = render_events(events, sample_rate=16000, sound_mode='soft', gap_ms=6)
        detached = render_events([dict(x, legato=False) for x in events], sample_rate=16000, sound_mode='soft', gap_ms=6)
        self.assertEqual(len(joined), 8000)
        self.assertTrue(np.all(joined[6400:] == 0))
        def rms(x): return float(np.sqrt(np.mean(x.astype(float) ** 2)))
        self.assertGreater(rms(joined[3120:3200]), rms(detached[3120:3200]) * 3)
        self.assertGreater(rms(joined[3200:3280]), rms(detached[3200:3280]) * 2)

    def test_real_attack_and_repeated_note_do_not_become_legato(self):
        events = [NoteEvent(440, 250, 'A4'), NoteEvent(midi_to_frequency(71), 250, 'B4'), NoteEvent(midi_to_frequency(71), 250, 'B4')]
        a = arrange_events(events, np.arange(0, 3, .5), transpose=0, phrasing=True)
        self.assertEqual([e.legato for e in a.events], [False, True, False])
        b = arrange_events(events, np.arange(0, 3, .5), transpose=0, onsets_ms={250}, phrasing=True)
        self.assertFalse(any(e.legato for e in b.events))

    def test_repeat_phrases_share_expression_without_changing_notes(self):
        phrase = [NoteEvent(midi_to_frequency(n), 250, str(n)) for n in [60, 62, 64, 65, 67, 64]]
        events = phrase + [NoteEvent(0, 400, 'rest')] + phrase
        velocities, info = phrase_expression(events, np.arange(0, 6, .5), None)
        self.assertEqual(info['matched_repeat_phrases'], 1)
        self.assertEqual(velocities[:6], velocities[7:])
        self.assertGreater(len(set(velocities[:6])), 3)

    def test_repeat_motif_keeps_a_real_melodic_variant(self):
        notes = [60, 62, 64, 65, 67, 64, 62, 60]
        phrase = [NoteEvent(midi_to_frequency(n), 300, str(n)) for n in notes]
        variant = [NoteEvent(midi_to_frequency(n + (1 if i == 4 else 0)), 300, str(n + (1 if i == 4 else 0))) for i, n in enumerate(notes)]
        events = phrase + [NoteEvent(0, 3000, 'rest')] + variant
        velocities, info = phrase_expression(events, np.arange(0, 10, .5), None)
        self.assertEqual(info['matched_repeat_motifs'], 1)
        self.assertEqual(events[13].note, '68')
        self.assertEqual(velocities[:4], velocities[9:13])

    def test_legato_is_preserved_by_speed_export_and_preview_reload(self):
        events = [NoteEvent(440, 250, 'A4'), NoteEvent(494, 250, 'B4', True)]
        self.assertTrue(scale_note_events(events, 1.5)[1].legato)
        with tempfile.TemporaryDirectory() as folder:
            paths = write_outputs(events, folder, target='platformio-stm32', sound_mode='soft')
            self.assertTrue(load_events(paths.json_path)[1]['legato'])
            self.assertIn('legato', paths.csv_path.read_text())
            self.assertEqual(json.loads(paths.playback_path.read_text())['legato_transitions'], 1)
            self.assertIn('bool continued', paths.main_cpp_path.read_text())


if __name__ == '__main__':
    unittest.main()
