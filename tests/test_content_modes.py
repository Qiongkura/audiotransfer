import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from audiotransfer.analysis import AnalysisConfig, PitchFrame
from audiotransfer.cli import build_parser
from audiotransfer.pipeline import events_from_song, process_file
from audiotransfer.preview import write_wav
from audiotransfer.song import (
    SongAnalysisError,
    analyze_stems,
    continuous_instrument_lead,
    detect_content_mode,
    suppress_brief_octave_alias,
    recover_weak_vocal_hold,
)


class ContentModeTests(unittest.TestCase):
    def test_low_instrument_solo_is_not_discarded_as_bass(self):
        sr = 16000
        time = np.arange(sr * 2) / sr
        config = AnalysisConfig(
            pitch_mode="song", content_mode="instrumental", song_backend="pyin"
        )
        result = analyze_stems(
            np.zeros(sr * 2), 0.5 * np.sin(2 * np.pi * 110 * time), config
        )
        self.assertAlmostEqual(
            np.median([f.frequency_hz for f in result.frames[15:-15]]), 110, delta=2
        )

    def test_breath_is_not_filled_by_louder_accompaniment_but_interlude_survives(self):
        sr, hop, seconds = 16000, 320, 14
        time = np.arange(sr * seconds) / sr
        instruments = 0.6 * np.sin(2 * np.pi * 523.251 * time)
        vocals = np.zeros_like(instruments)
        frames = []
        for index in range(seconds * 50):
            singing = (
                1 <= index * 0.02 < 3
                or 4.4 <= index * 0.02 < 6
                or 11 <= index * 0.02 < 13
            )
            if singing:
                start = index * hop
                vocals[start : start + hop] = 0.5 * np.sin(
                    2 * np.pi * 392 * time[start : start + hop]
                )
            frames.append(
                PitchFrame(
                    392 if singing else 0, 0.9 if singing else 0, 0.2 if singing else 0
                )
            )
        config = AnalysisConfig(
            pitch_mode="song", content_mode="vocal", song_backend="pyin"
        )
        with patch("audiotransfer.song.vocal_frames", return_value=frames):
            result = analyze_stems(vocals, instruments, config)
        self.assertTrue(all(s == "rest" for s in result.sources[160:210]))
        self.assertTrue(all(f.frequency_hz == 0 for f in result.frames[160:210]))
        self.assertTrue(all(s == "instruments" for s in result.sources[350:500]))
        self.assertAlmostEqual(
            np.median([f.frequency_hz for f in result.frames[350:500]]),
            523.251,
            delta=4,
        )
        self.assertEqual(
            sum(e.duration_ms for e in events_from_song(result, config, 80)), 14000
        )

    def test_instrumental_mode_recovers_solo_leaked_into_vocal_stem(self):
        sr = 16000
        time = np.arange(sr * 4) / sr
        melody = np.where(
            time < 1, 440, np.where(time < 2, 493.883, np.where(time < 3, 523.251, 440))
        )
        # This stem assignment deliberately puts the actual solo in 'vocals'.
        vocals = 0.5 * np.sin(2 * np.pi * melody * time)
        backing = 0.45 * np.sin(2 * np.pi * 220 * time)
        config = AnalysisConfig(
            pitch_mode="song", content_mode="instrumental", song_backend="pyin"
        )
        with patch(
            "audiotransfer.song.vocal_frames",
            side_effect=AssertionError("must not classify singing"),
        ):
            result = analyze_stems(vocals, backing, config)
        self.assertEqual(result.details["detected_content_mode"], "instrumental")
        self.assertNotIn("vocals", result.sources)
        for first, last, expected in [
            (15, 35, 440),
            (65, 85, 493.883),
            (115, 135, 523.251),
            (165, 185, 440),
        ]:
            self.assertAlmostEqual(
                np.median([f.frequency_hz for f in result.frames[first:last]]),
                expected,
                delta=4,
            )
        self.assertEqual(
            sum(e.duration_ms for e in events_from_song(result, config, 80)), 4000
        )

    def test_neural_mixed_period_is_not_admitted_without_an_observed_peak(self):
        sr = 16000
        time = np.arange(sr * 2) / sr
        signal = 0.4 * np.sin(2 * np.pi * 440 * time) + 0.4 * np.sin(
            2 * np.pi * 660 * time
        )
        model = [PitchFrame(220, 0.99, 0.2)] * 100
        config = AnalysisConfig(pitch_mode="song", content_mode="instrumental")
        with patch("audiotransfer.song.neural_frames", return_value=model):
            frames = continuous_instrument_lead(
                signal, config, np.ones(100, dtype=bool)
            )
        voiced = [f.frequency_hz for f in frames[10:-10] if f.frequency_hz > 0]
        self.assertEqual(len(voiced), 80)
        self.assertTrue(all(min(abs(f - 440), abs(f - 660)) < 4 for f in voiced))

    def test_auto_detection_uses_whole_recording_not_isolated_leakage(self):
        evidence = np.zeros(5000, dtype=bool)
        evidence[20:25] = True
        evidence[1000:1005] = True
        self.assertEqual(detect_content_mode(evidence, 0.02)[0], "instrumental")
        evidence[2000:2300] = True
        self.assertEqual(detect_content_mode(evidence, 0.02)[0], "vocal")

    def test_auto_instrumental_and_manual_vocal_without_singing(self):
        sr = 16000
        time = np.arange(sr * 2) / sr
        config = AnalysisConfig(pitch_mode="song", song_backend="pyin")
        result = analyze_stems(
            np.zeros(sr * 2), 0.5 * np.sin(2 * np.pi * 440 * time), config
        )
        self.assertEqual(result.details["detected_content_mode"], "instrumental")
        with self.assertRaisesRegex(SongAnalysisError, "未找到可靠主唱"):
            analyze_stems(
                np.zeros(sr * 2),
                np.ones(sr * 2),
                AnalysisConfig(
                    pitch_mode="song", content_mode="vocal", song_backend="pyin"
                ),
            )

    def test_low_confidence_octave_alias_is_repaired_but_real_octave_is_preserved(self):
        frames = (
            [PitchFrame(440, 0.9, 0.1)] * 10
            + [PitchFrame(880, 0.3, 0.1)] * 5
            + [PitchFrame(440, 0.9, 0.1)] * 10
        )
        signal = np.ones(8000) * 0.1
        fixed, count = suppress_brief_octave_alias(frames, signal, AnalysisConfig())
        self.assertEqual(count, 1)
        self.assertTrue(all(f.frequency_hz == 440 for f in fixed))
        frames[10:15] = [PitchFrame(880, 0.95, 0.1)] * 5
        fixed, count = suppress_brief_octave_alias(frames, signal, AnalysisConfig())
        self.assertEqual(count, 0)
        self.assertEqual(fixed[12].frequency_hz, 880)

    def test_weak_vocal_hold_survives_but_real_breath_is_not_extended(self):
        frames = (
            [PitchFrame(440, 0.9, 0.1)] * 10
            + [PitchFrame(440, 0.22, 0.1)] * 40
            + [PitchFrame(440, 0.9, 0.1)] * 10
        )
        valid = np.array([True] * 10 + [False] * 40 + [True] * 10)
        rms = np.full(60, 0.1)
        recovered, count = recover_weak_vocal_hold(
            valid, frames, rms, rms, AnalysisConfig()
        )
        self.assertEqual(count, 40)
        self.assertTrue(np.all(recovered))
        rms[20:30] = 0
        recovered, count = recover_weak_vocal_hold(
            valid, frames, rms, np.full(60, 0.1), AnalysisConfig()
        )
        self.assertEqual(count, 0)
        self.assertFalse(np.any(recovered[10:50]))

    def test_pipeline_exports_content_decision_and_keeps_exact_duration(self):
        sr, sample_count = 16000, 32037
        time = np.arange(sample_count) / sr
        signal = 0.4 * np.sin(2 * np.pi * 440 * time)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_wav((signal * 32767).astype("<i2"), source, sr)
            config = AnalysisConfig(
                pitch_mode="song", content_mode="instrumental", song_backend="pyin"
            )
            with patch(
                "audiotransfer.pipeline.separate_file",
                return_value=(signal, np.zeros(sample_count)),
            ):
                paths = process_file(
                    source, root / "out", config=config, arrangement="raw"
                )
            decision = json.loads(
                (root / "out/source-regions.json").read_text(encoding="utf-8")
            )
            notes = json.loads(paths.json_path.read_text(encoding="utf-8"))
            expected = round(sample_count * 1000 / sr)
            self.assertEqual(decision["requested_content_mode"], "instrumental")
            self.assertEqual(decision["detected_content_mode"], "instrumental")
            self.assertEqual(decision["regions"][-1]["end_ms"], expected)
            self.assertEqual(sum(n["duration_ms"] for n in notes), expected)

    def test_cli_exposes_all_modes_and_rejects_invalid_config(self):
        for mode in ("auto", "vocal", "instrumental"):
            args = build_parser().parse_args(
                ["input.wav", "-o", "out", "--content-mode", mode]
            )
            self.assertEqual(args.content_mode, mode)
        with self.assertRaises(ValueError):
            AnalysisConfig(content_mode="random").validate()


if __name__ == "__main__":
    unittest.main()
