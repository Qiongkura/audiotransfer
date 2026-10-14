import unittest

from audiotransfer.analysis import AnalysisConfig, PitchFrame
from audiotransfer.notes import midi_to_frequency
from audiotransfer.pipeline import events_from_song
from audiotransfer.song import SongAnalysis


def singing(parts, attacks=(), hop_ms=20, sources=None):
    frames = [
        PitchFrame(midi_to_frequency(note) if note is not None else 0, confidence, 0.1)
        for note, duration, confidence in parts
        for _ in range(round(duration / hop_ms))
    ]
    regions = sources or ["vocals" if f.frequency_hz > 0 else "rest" for f in frames]
    analysis = SongAnalysis(
        frames,
        {round(t / hop_ms) for t in attacks},
        regions,
        round(len(frames) * hop_ms * 16),
        16000,
    )
    config = AnalysisConfig(pitch_mode="song", hop_ms=hop_ms)
    return events_from_song(analysis, config, 80), analysis


class VocalContinuityTests(unittest.TestCase):
    def test_weak_short_wide_jump_returns_to_actual_held_note(self):
        events, analysis = singing([(69, 400, 0.95), (76, 120, 0.3), (69, 400, 0.95)])
        self.assertEqual([(e.note, e.duration_ms) for e in events], [("A4", 920)])
        self.assertEqual(analysis.details["regularized_vocal_ms"], 120)

    def test_two_adjacent_weak_intrusions_do_not_form_two_new_notes(self):
        events, _ = singing(
            [(69, 400, 0.95), (74, 80, 0.28), (81, 80, 0.25), (69, 400, 0.95)]
        )
        self.assertEqual([(e.note, e.duration_ms) for e in events], [("A4", 960)])

    def test_credible_short_leap_is_preserved(self):
        events, _ = singing(
            [(69, 400, 0.95), (76, 100, 0.95), (69, 400, 0.95)], (400, 500)
        )
        self.assertEqual(
            [(e.note, e.duration_ms) for e in events],
            [("A4", 400), ("E5", 100), ("A4", 400)],
        )

    def test_fast_reliable_legato_melody_keeps_all_notes(self):
        events, _ = singing([(n, 100, 0.97) for n in (69, 71, 74, 72, 76, 74)])
        self.assertEqual([e.note for e in events], ["A4", "B4", "D5", "C5", "E5", "D5"])
        self.assertTrue(all(e.duration_ms == 100 for e in events))

    def test_attacked_short_semitone_is_preserved(self):
        events, _ = singing(
            [(69, 400, 0.9), (70, 100, 0.9), (69, 400, 0.9)], (400, 500)
        )
        self.assertEqual([e.note for e in events], ["A4", "A#4", "A4"])
        self.assertEqual(sum(e.duration_ms for e in events), 900)

    def test_real_breath_is_not_filled_or_used_to_borrow_a_note(self):
        events, _ = singing([(69, 400, 0.95), (None, 200, 0), (76, 400, 0.3)])
        self.assertEqual(
            [(e.note, e.duration_ms) for e in events],
            [("A4", 400), ("rest", 200), ("E5", 400)],
        )

    def test_instrument_note_is_not_used_as_vocal_reference(self):
        sources = ["instruments"] * 20 + ["vocals"] * 6 + ["instruments"] * 20
        events, analysis = singing(
            [(69, 400, 0.95), (76, 120, 0.3), (69, 400, 0.95)], sources=sources
        )
        self.assertIn("E5", [e.note for e in events])
        self.assertEqual(analysis.details["regularized_vocal_ms"], 0)

    def test_low_confidence_sustained_real_change_cannot_be_held_forever(self):
        events, _ = singing([(69, 400, 0.95), (74, 2000, 0.3), (69, 400, 0.95)])
        self.assertIn("D5", [e.note for e in events])
        self.assertGreaterEqual(
            sum(e.duration_ms for e in events if e.note == "D5"), 1600
        )
        self.assertEqual(sum(e.duration_ms for e in events), 2800)

    def test_time_resolution_does_not_change_weak_spike_policy(self):
        for hop in (10, 20, 40):
            with self.subTest(hop_ms=hop):
                events, _ = singing(
                    [(69, 400, 0.95), (76, 120, 0.3), (69, 400, 0.95)], hop_ms=hop
                )
                self.assertEqual(
                    [(e.note, e.duration_ms) for e in events], [("A4", 920)]
                )


if __name__ == "__main__":
    unittest.main()
