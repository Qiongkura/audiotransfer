"""Musical regressions independent of a recording, title, key, or score."""
import unittest
from unittest.mock import patch

import numpy as np

from audiotransfer.analysis import AnalysisConfig, PitchFrame
from audiotransfer.arrange import arrange_events
from audiotransfer.notes import midi_to_frequency
from audiotransfer.pipeline import events_from_song
from audiotransfer.singing import SungNote, reconcile_notes
from audiotransfer.song import SongAnalysis, apply_singing_notes


def analysis(pitches, sources=None, onsets=()):
    frames = [PitchFrame(midi_to_frequency(p) if p is not None else 0, .95, .1 if p is not None else 0)
              for p in pitches]
    return SongAnalysis(frames, set(onsets), sources or ['vocals'] * len(frames), len(frames) * 320, 16000)


class GeneralMelodyTests(unittest.TestCase):
    config = AnalysisConfig(pitch_mode='song')

    def test_detuned_vibrato_does_not_become_a_repeated_semitone(self):
        # The pitch is centred 40 cents high and crosses the rounding boundary.
        # Test several registers and both lead sources, without any known tune.
        for centre in (48, 60, 69, 78):
            for source in ('vocals', 'instruments'):
                pitches = centre + .4 + .5 * np.sin(np.arange(100) * .02 * 5 * 2 * np.pi)
                result = events_from_song(analysis(pitches, [source] * 100), self.config, 80)
                self.assertEqual(len(result), 1)
                self.assertAlmostEqual(result[0].frequency_hz, midi_to_frequency(centre))
                self.assertEqual(result[0].duration_ms, 2000)

    def test_quick_real_notes_with_attacks_survive(self):
        pitches = np.repeat([60, 61, 64, 62, 67, 65], 4)
        result = events_from_song(analysis(pitches, onsets=range(0, 24, 4)), self.config, 80)
        self.assertEqual([round(69 + 12 * np.log2(e.frequency_hz / 440)) for e in result], [60, 61, 64, 62, 67, 65])
        self.assertEqual([e.duration_ms for e in result], [80] * 6)

    def test_consonant_attack_does_not_validate_a_weak_forty_ms_wrong_pitch(self):
        a = analysis([48] * 2 + [72] * 25, onsets={0})
        a.frames[:2] = [PitchFrame(midi_to_frequency(48), .65, .1)] * 2
        events = events_from_song(a, self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in events], [('C5', 540)])

    def test_clear_attacked_forty_ms_note_is_preserved(self):
        a = analysis([60] * 10 + [67] * 2 + [60] * 10, onsets={10, 12})
        result = events_from_song(a, self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in result], [('C4', 200), ('G4', 40), ('C4', 200)])

    def test_unconfirmed_island_stays_silent_instead_of_borrowing_over_a_breath(self):
        result = events_from_song(analysis([60] * 20 + [None] * 5 + [76] + [None] * 5 + [60] * 20), self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in result], [('C4', 400), ('rest', 220), ('C4', 400)])

    def test_confirmed_short_breath_is_not_swallowed_by_note_merging(self):
        result = events_from_song(analysis([60] * 20 + [None] * 2 + [60] * 20), self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in result], [('C4', 400), ('rest', 40), ('C4', 400)])

    def test_contiguous_source_change_cannot_borrow_a_neighbour_pitch(self):
        a = analysis([60] * 20 + [72] * 4, ['vocals'] * 20 + ['instruments'] * 4)
        a.frames[-4:] = [PitchFrame(midi_to_frequency(72), .3, .1)] * 4
        result = events_from_song(a, self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in result], [('C4', 400), ('C5', 80)])

    def test_anchor_repairs_model_rest_and_swallowed_pitch_change(self):
        result = reconcile_notes([SungNote(0, 500, None), SungNote(500, 1000, 60)],
                                 [SungNote(0, 500, 60), SungNote(500, 1000, 64)], set(), 1000)
        self.assertEqual(result, [SungNote(0, 500, 60), SungNote(500, 1000, 64)])

    def test_silence_anchor_and_true_repeated_attack_are_preserved(self):
        proposals = [SungNote(0, 500, 60), SungNote(500, 1000, 60)]
        self.assertEqual(reconcile_notes(proposals, [], set(), 1000), [SungNote(0, 1000, 60)])
        self.assertEqual(reconcile_notes(proposals, [], {500}, 1000), proposals)
        result = reconcile_notes(proposals, [SungNote(400, 600, None)], set(), 1000)
        self.assertEqual(result, [SungNote(0, 400, 60), SungNote(400, 600, None), SungNote(600, 1000, 60)])

    def test_model_boundary_alone_does_not_cut_legato(self):
        a = analysis([60] * 25 + [64] * 25)
        proposals = [SungNote(0, 500, 60), SungNote(500, 1000, 64)]
        with patch('audiotransfer.singing.transcribe', return_value=proposals):
            result = apply_singing_notes(a, np.full(16000, .1, np.float32), self.config)
        self.assertNotIn(25, result.onsets)
        events = events_from_song(result, self.config, 80)
        arranged = arrange_events(events, np.array([]), onsets_ms=set(), analysis=result,
                                  phrasing=True, preserve_timing=True, transpose=0)
        self.assertEqual(len(arranged.events), 2)
        self.assertTrue(arranged.events[1].legato)

    def test_full_singing_route_recovers_clear_vowel_called_rest(self):
        a = analysis([60] * 25 + [64] * 25)
        with patch('audiotransfer.singing.transcribe', return_value=[SungNote(0, 1000, None)]):
            result = apply_singing_notes(a, np.full(16000, .1, np.float32), self.config)
        events = events_from_song(result, self.config, 80)
        self.assertEqual([(e.note, e.duration_ms) for e in events], [('C4', 500), ('E4', 500)])
