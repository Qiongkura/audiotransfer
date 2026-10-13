import unittest
from unittest.mock import patch

import numpy as np

from audiotransfer.analysis import AnalysisConfig, PitchFrame
from audiotransfer.pipeline import events_from_song, events_from_samples
from audiotransfer.song import SongAnalysis, refine_neural_pitch, clear_note_attack


class SongQualityTests(unittest.TestCase):
    def test_weak_part_of_sustained_note_does_not_turn_into_rests(self):
        config = AnalysisConfig(pitch_mode='song')
        pitch = np.full(20, 440.0)
        probability = np.array([.8] * 5 + [.20, .22, .25, .20, .24] + [.8] * 10)
        frames = refine_neural_pitch(pitch, probability, np.full(20, .1), config)
        result = events_from_song(SongAnalysis(frames, set(), ['vocals'] * 20, 6400, 16000), config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in result], [('A4', 400)])

    def test_real_silence_and_unreliable_noise_remain_rests(self):
        config = AnalysisConfig(pitch_mode='song')
        pitch = np.full(20, 440.0)
        rms = np.full(20, .1)
        rms[8:11] = 0
        frames = refine_neural_pitch(pitch, np.full(20, .8), rms, config)
        self.assertTrue(all(f.frequency_hz == 0 for f in frames[8:11]))
        noise = refine_neural_pitch(pitch, np.full(20, .1), np.full(20, .1), config)
        self.assertTrue(all(f.frequency_hz == 0 for f in noise))

    def test_smooth_before_attacks_removes_stranded_pitch_fragments(self):
        config = AnalysisConfig(pitch_mode='song')
        frames = [PitchFrame(440, .8, .1) for _ in range(25)]
        frames[8:10] = [PitchFrame(466.164, .8, .1)] * 2
        analysis = SongAnalysis(frames, {8, 10, 13, 15}, ['vocals'] * 25, 8000, 16000)
        events = events_from_song(analysis, config, 80)
        self.assertEqual(sum(e.duration_ms for e in events), 500)
        self.assertTrue(all(e.note == 'A4' for e in events))
        self.assertTrue(all(e.duration_ms >= 80 for e in events))

    def test_short_dropout_is_bridged_but_large_pitch_jump_is_not(self):
        config = AnalysisConfig(pitch_mode='song')
        pitch = np.full(20, 440.0)
        probability = np.full(20, .8)
        probability[8:10] = .05
        frames = refine_neural_pitch(pitch, probability, np.full(20, .1), config)
        self.assertTrue(all(f.frequency_hz > 0 for f in frames))
        pitch[10:] = 660
        frames = refine_neural_pitch(pitch, probability, np.full(20, .1), config)
        self.assertTrue(all(f.frequency_hz == 0 for f in frames[8:10]))

    def test_vibrato_crossing_half_semitone_does_not_create_trill(self):
        config = AnalysisConfig(pitch_mode='song')
        time = np.arange(100) * .02
        frequencies = 440 * 2 ** ((.65 * np.sin(2 * np.pi * 5 * time)) / 12)
        frames = [PitchFrame(float(f), .9, .1) for f in frequencies]
        analysis = SongAnalysis(frames, set(), ['vocals'] * 100, 32000, 16000)
        events = events_from_song(analysis, config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in events], [('A4', 2000)])

    def test_real_semitone_change_keeps_its_timing(self):
        config = AnalysisConfig(pitch_mode='song')
        frames = [PitchFrame(440, .9, .1)] * 10 + [PitchFrame(466.164, .9, .1)] * 10
        analysis = SongAnalysis(frames, set(), ['vocals'] * 20, 6400, 16000)
        events = events_from_song(analysis, config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in events], [('A4', 200), ('A#4', 200)])

    def test_only_clear_energy_reattack_repeats_a_sustained_note(self):
        steady = np.full(100, .1)
        self.assertFalse(clear_note_attack(steady, 50, .005))
        vibrato = .1 + .02 * np.sin(np.arange(100) * .1)
        self.assertFalse(clear_note_attack(vibrato, 50, .005))
        attack = steady.copy()
        attack[47:55] = .01
        self.assertTrue(clear_note_attack(attack, 50, .005))
        fading = np.linspace(.18, .02, 100)
        self.assertFalse(clear_note_attack(fading, 50, .005))

    def test_sample_api_does_not_reject_validated_weak_voiced_frames(self):
        config = AnalysisConfig(pitch_mode='song')
        frames = [PitchFrame(440, .8, .1)] * 5 + [PitchFrame(440, .2, .1)] * 5 + [PitchFrame(440, .8, .1)] * 10
        with patch('audiotransfer.pipeline.vocal_frames', return_value=frames):
            events = events_from_samples(np.ones(6400), config, min_note_ms=80)
        self.assertEqual([(e.note, e.duration_ms) for e in events], [('A4', 400)])


if __name__ == '__main__':
    unittest.main()
