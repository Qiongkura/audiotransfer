import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from audiotransfer.analysis import (
    AnalysisConfig,
    PitchFrame,
    analyze_samples,
    analyze_samples_tracked,
    estimate_pitch,
)
from audiotransfer.events import NoteEvent, NoteFrame, merge_note_frames, scale_note_events
from audiotransfer.notes import midi_to_frequency, quantize_frequency
from audiotransfer.output import generate_arduino, write_outputs


class CoreTests(unittest.TestCase):
    def test_frequency_quantization(self) -> None:
        a4 = quantize_frequency(440.0, confidence=0.99)
        self.assertEqual(a4.note, "A4")
        self.assertAlmostEqual(a4.frequency_hz, 440.0, places=6)

        c5 = quantize_frequency(midi_to_frequency(72), confidence=0.99)
        self.assertEqual(c5.note, "C5")
        self.assertEqual(c5.midi, 72)

        rest = quantize_frequency(440.0, confidence=0.1)
        self.assertEqual(rest.note, "rest")
        self.assertEqual(rest.frequency_hz, 0.0)

    def test_event_merge_short_event_and_crop(self) -> None:
        frames = [
            NoteFrame("C4", midi_to_frequency(60)),
            NoteFrame("G4", midi_to_frequency(67)),
            NoteFrame("G4", midi_to_frequency(67)),
            NoteFrame("G4", midi_to_frequency(67)),
        ]
        smoothed = merge_note_frames(frames, hop_ms=20, min_note_ms=40)
        self.assertEqual(len(smoothed), 1)
        self.assertEqual(smoothed[0].note, "G4")
        self.assertEqual(smoothed[0].duration_ms, 80)

        uncropped = merge_note_frames(
            [NoteFrame("C4", 261.63), NoteFrame("G4", 392.0)],
            hop_ms=20,
            min_note_ms=0,
            max_notes=None,
        )
        self.assertEqual([event.note for event in uncropped], ["C4", "G4"])
        cropped = merge_note_frames(
            [NoteFrame("C4", 261.63), NoteFrame("G4", 392.0)],
            hop_ms=20,
            min_note_ms=0,
            max_notes=1,
        )
        self.assertEqual(len(cropped), 1)

    def test_short_note_between_rests_is_removed(self) -> None:
        frames = [
            NoteFrame("rest", 0.0),
            NoteFrame("rest", 0.0),
            NoteFrame("A4", 440.0),
            NoteFrame("rest", 0.0),
            NoteFrame("rest", 0.0),
        ]
        events = merge_note_frames(frames, hop_ms=20, min_note_ms=40)
        self.assertEqual([event.note for event in events], ["rest"])
        self.assertEqual(events[0].duration_ms, 100)

        same_note = merge_note_frames(
            [NoteFrame("A4", 440.0), NoteFrame("rest", 0.0), NoteFrame("A4", 440.0)],
            hop_ms=20,
            min_note_ms=40,
        )
        self.assertEqual([(event.note, event.duration_ms) for event in same_note], [("A4", 60)])

    def test_scale_note_events_changes_only_duration(self) -> None:
        events = [NoteEvent(440.0, 100, "A4"), NoteEvent(0.0, 1, "rest")]
        scaled = scale_note_events(events, speed=2.0)
        self.assertEqual(scaled, [NoteEvent(440.0, 50, "A4"), NoteEvent(0.0, 1, "rest")])
        self.assertEqual(scale_note_events([NoteEvent(440.0, 1, "A4")], 1000.0)[0].duration_ms, 1)
        with self.assertRaises(ValueError):
            scale_note_events(events, speed=0.0)

    def test_pitch_estimation_and_empty_analysis(self) -> None:
        sample_rate = 16_000
        time = np.arange(sample_rate, dtype=np.float32) / sample_rate
        samples = 0.7 * np.sin(2 * np.pi * 440.0 * time)
        result = estimate_pitch(samples[:640], sample_rate, energy_threshold=0.01)
        self.assertAlmostEqual(result.frequency_hz, 440.0, delta=8.0)
        self.assertGreater(result.confidence, 0.35)

        empty = analyze_samples(np.array([], dtype=np.float32), AnalysisConfig())
        self.assertEqual(empty, [])

    def test_tracked_mode_prefers_melody_over_bass(self) -> None:
        sample_rate = 16_000
        time = np.arange(sample_rate, dtype=np.float32) / sample_rate
        samples = (
            0.8 * np.sin(2 * np.pi * 110.0 * time)
            + 0.35 * np.sin(2 * np.pi * 440.0 * time)
        )
        config = AnalysisConfig(
            pitch_mode="tracked",
            max_frequency_hz=1_000.0,
            tracking_min_frequency_hz=140.0,
        )
        frames = analyze_samples_tracked(samples, config)
        voiced = [frame.frequency_hz for frame in frames if frame.frequency_hz > 0]
        self.assertEqual(len(frames), 49)
        self.assertEqual(len(voiced), len(frames))
        self.assertAlmostEqual(float(np.median(voiced)), 440.0, delta=10.0)
        self.assertEqual(sum(frequency < 140.0 for frequency in voiced), 0)

    def test_tracked_silence_preserves_frame_count(self) -> None:
        config = AnalysisConfig(pitch_mode="tracked")
        frames = analyze_samples_tracked(np.zeros(16_000, dtype=np.float32), config)
        self.assertEqual(len(frames), 49)
        self.assertTrue(all(frame.frequency_hz == 0.0 for frame in frames))

        sample_rate = 16_000
        frame = np.sin(2 * np.pi * 440.0 * np.arange(640) / sample_rate)
        tail = np.zeros(640, dtype=np.float64)
        tail[0] = 1.0
        tail[1:7] = 0.98
        tail[7] = 0.99
        tail[8] = 0.98
        tail[9:292] = np.linspace(0.97, 0.2, 283)
        autocorrelation = np.zeros(1279, dtype=np.float64)
        autocorrelation[639:] = tail
        with patch("audiotransfer.analysis.np.correlate", return_value=autocorrelation):
            result = estimate_pitch(
                frame,
                sample_rate,
                min_frequency_hz=55.0,
                max_frequency_hz=2_000.0,
                energy_threshold=0.01,
            )
        self.assertEqual(result.frequency_hz, 0.0)

    def test_isolated_octave_frame_is_folded(self) -> None:
        config = AnalysisConfig()
        samples = np.ones(config.frame_size + 2 * config.hop_size, dtype=np.float32)
        with patch(
            "audiotransfer.analysis.estimate_pitch",
            side_effect=[
                PitchFrame(440.0, 0.9, 0.2),
                PitchFrame(880.0, 0.9, 0.2),
                PitchFrame(440.0, 0.9, 0.2),
            ],
        ):
            result = analyze_samples(samples, config)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[1].frequency_hz, 440.0)

    def test_arduino_generation(self) -> None:
        events = [NoteEvent(440.0, 250, "A4"), NoteEvent(0.0, 80, "rest")]
        sketch = generate_arduino(events, pin=9, gap_ms=35)
        self.assertIn("PROGMEM", sketch)
        self.assertIn("tone(BUZZER_PIN", sketch)
        self.assertIn("noTone(BUZZER_PIN", sketch)
        self.assertIn("const uint8_t BUZZER_PIN = 9;", sketch)
        self.assertIn("MELODY_GAP_MS = 35UL;", sketch)
        self.assertIn("MELODY_LENGTH = 2UL", sketch)

    def test_empty_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = write_outputs([], Path(directory), pin=8)
            self.assertEqual(json.loads(paths.json_path.read_text(encoding="utf-8")), [])
            self.assertEqual(
                paths.csv_path.read_text(encoding="utf-8").splitlines()[0],
                "frequency_hz,duration_ms,note",
            )
            sketch = paths.ino_path.read_text(encoding="utf-8")
            self.assertIn("MELODY_LENGTH = 0UL", sketch)
            self.assertIn("{0, 0UL}", sketch)


if __name__ == "__main__":
    unittest.main()
