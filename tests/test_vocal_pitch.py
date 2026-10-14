import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from audiotransfer.analysis import AnalysisConfig, PitchFrame
from audiotransfer.song import vocal_frames
from audiotransfer.vocal_pitch import (
    cross_check_vocals,
    decode_salience,
    estimate_vocals,
    ensure_model,
)


class VocalPitchTests(unittest.TestCase):
    def test_weak_wrong_pitch_is_replaced_by_reliable_singing_pitch(self):
        result = cross_check_vocals(
            [PitchFrame(660, 0.35, 0.1)], np.array([440.0]), np.array([0.9])
        )
        self.assertEqual(result[0].frequency_hz, 440)

    def test_both_uncertain_models_leave_rest_instead_of_an_intruding_note(self):
        result = cross_check_vocals(
            [PitchFrame(660, 0.5, 0.1)], np.array([0.0]), np.array([0.002])
        )
        self.assertEqual(result[0].frequency_hz, 0)
        self.assertEqual(result[0].rms, 0.1)

    def test_clear_crepe_note_survives_a_missed_or_uncertain_rmvpe_note(self):
        for pitch, confidence in [(0.0, 0.002), (660.0, 0.2)]:
            result = cross_check_vocals(
                [PitchFrame(440, 0.95, 0.1)], np.array([pitch]), np.array([confidence])
            )
            self.assertEqual(result[0].frequency_hz, 440)
            self.assertEqual(result[0].confidence, 0.95)

    def test_matching_models_retain_quiet_vocal_evidence(self):
        result = cross_check_vocals(
            [PitchFrame(440, 0.9, 0.06)], np.array([442.0]), np.array([0.22])
        )
        self.assertEqual(result[0].frequency_hz, 442)
        self.assertEqual(result[0].confidence, 0.9)

    def test_time_misalignment_is_rejected(self):
        with self.assertRaises(ValueError):
            cross_check_vocals([PitchFrame(440, 0.9, 0.1)], np.array([]), np.array([]))

    def test_silence_is_not_decoded_as_a_low_note(self):
        salience = np.zeros((2, 360))
        salience[1, 180] = 0.9
        pitch, confidence = decode_salience(salience)
        self.assertEqual(pitch[0], 0)
        self.assertGreater(pitch[1], 0)
        self.assertEqual(confidence[1], 0.9)

    def test_cold_download_failure_does_not_install_an_incomplete_model(self):
        from io import BytesIO

        response = BytesIO(b"truncated archive")
        response.headers = {"Content-Length": "340638958"}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("urllib.request.urlopen", return_value=response),
        ):
            target = Path(directory)
            with self.assertRaisesRegex(ValueError, "校验失败"):
                ensure_model(target)
            self.assertFalse((target / "model.pt").exists())
            self.assertEqual(list(target.iterdir()), [])

    def test_cached_native_pitch_uses_exact_requested_time_grid(self):
        samples = np.zeros(16000, dtype=np.float32)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("audiotransfer.vocal_pitch.MODELS", Path(directory)),
            patch(
                "audiotransfer.vocal_pitch.ensure_model", return_value=Path("unused")
            ),
            patch(
                "audiotransfer.vocal_pitch._predict",
                return_value=(np.arange(100.0) + 100, np.full(100, 0.9)),
            ) as predict,
        ):
            first = estimate_vocals(samples, 16000, 320, device="cpu")
            second = estimate_vocals(samples, 16000, 320, device="cpu")
            self.assertEqual(predict.call_count, 1)
            np.testing.assert_array_equal(first[0], np.arange(0, 100, 2) + 100)
            np.testing.assert_array_equal(first[0], second[0])

    def test_backend_retains_real_quiet_energy_gate_and_frequency_limits(self):
        previous = [PitchFrame(0, 0, 0.001)] * 5 + [PitchFrame(440, 0.9, 0.1)] * 5
        pitch = np.array([440.0] * 5 + [2200.0] * 5)
        with (
            patch("audiotransfer.song.neural_frames", return_value=previous),
            patch(
                "audiotransfer.vocal_pitch.estimate_vocals",
                return_value=(pitch, np.full(10, 0.9)),
            ),
        ):
            result = vocal_frames(np.zeros(3200), AnalysisConfig(pitch_mode="song"))
        self.assertTrue(all(frame.frequency_hz == 0 for frame in result[:5]))
        for frame in result[5:]:
            self.assertAlmostEqual(frame.frequency_hz, 440)

    def test_damaged_pitch_cache_is_recomputed(self):
        samples = np.zeros(16000, dtype=np.float32)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("audiotransfer.vocal_pitch.MODELS", Path(directory)),
            patch(
                "audiotransfer.vocal_pitch.ensure_model", return_value=Path("unused")
            ),
            patch(
                "audiotransfer.vocal_pitch._predict",
                return_value=(np.full(100, 440.0), np.full(100, 0.9)),
            ) as predict,
        ):
            estimate_vocals(samples, 16000, 320, device="cpu")
            cache = next((Path(directory) / "pitch-cache").glob("*.npz"))
            np.savez(cache, pitch=np.array([np.nan]), confidence=np.zeros(1))
            pitch, confidence = estimate_vocals(samples, 16000, 320, device="cpu")
            self.assertEqual(predict.call_count, 2)
            self.assertEqual(len(pitch), 50)
            self.assertTrue(np.all(np.isfinite(pitch)))

    def test_backend_keeps_observed_weak_vowel_but_does_not_fill_real_breath(self):
        for quiet in (False, True):
            previous = [PitchFrame(440, 0.95, 0.1)] * 10
            previous += [PitchFrame(440, 0.22, 0.001 if quiet else 0.1)] * 30
            previous += [PitchFrame(440, 0.95, 0.1)] * 10
            pitch = np.array([440.0] * 10 + [0.0] * 30 + [440.0] * 10)
            confidence = np.array([0.9] * 10 + [0.0] * 30 + [0.9] * 10)
            with (
                patch("audiotransfer.song.neural_frames", return_value=previous),
                patch(
                    "audiotransfer.vocal_pitch.estimate_vocals",
                    return_value=(pitch, confidence),
                ),
            ):
                result = vocal_frames(
                    np.zeros(16000), AnalysisConfig(pitch_mode="song")
                )
            if quiet:
                self.assertTrue(all(frame.frequency_hz == 0 for frame in result[10:40]))
            else:
                self.assertTrue(
                    all(abs(frame.frequency_hz - 440) < 0.01 for frame in result)
                )


if __name__ == "__main__":
    unittest.main()
