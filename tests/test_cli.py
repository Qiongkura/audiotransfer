import unittest
from pathlib import Path

from audiotransfer.cli import build_parser


class CliParserTests(unittest.TestCase):
    def test_stm32_and_upload_options(self) -> None:
        args = build_parser().parse_args(
            [
                "input.mp3",
                "-o",
                "out",
                "--target",
                "platformio-stm32",
                "--board",
                "bluepill_f103c8",
                "--stm32-pin",
                "PB6",
                "--upload-protocol",
                "stlink",
                "--upload",
                "--pio",
                "C:/tools/pio.exe",
            ]
        )
        self.assertEqual(args.input, Path("input.mp3"))
        self.assertEqual(args.output_dir, Path("out"))
        self.assertEqual(args.target, "platformio-stm32")
        self.assertEqual(args.board, "bluepill_f103c8")
        self.assertEqual(args.stm32_pin, "PB6")
        self.assertEqual(args.upload_protocol, "stlink")
        self.assertTrue(args.upload)
        self.assertEqual(args.pio, "C:/tools/pio.exe")

    def test_defaults_keep_full_song_for_stm32(self) -> None:
        args = build_parser().parse_args(["input.mp3", "-o", "out"])
        self.assertEqual(args.target, "platformio-stm32")
        self.assertEqual(args.board, "bluepill_f103c8")
        self.assertEqual(args.stm32_pin, "PA8")
        self.assertEqual(args.upload_protocol, "stlink")
        self.assertFalse(args.upload)
        self.assertEqual(args.pio, "pio")
        self.assertEqual(args.pin, 8)
        self.assertEqual(args.speed, 1.0)
        self.assertEqual(args.min_note_ms, 80)
        self.assertEqual(args.max_notes, 0)
        self.assertEqual(args.max_frequency_hz, 1000)
        self.assertEqual(args.pitch_mode, "song")
        self.assertEqual(args.candidate_count, 5)
        self.assertEqual(args.tracking_min_frequency_hz, 140)

    def test_speed_and_analysis_overrides(self) -> None:
        args = build_parser().parse_args(
            [
                "input.flac",
                "-o",
                "out",
                "--speed",
                "1.5",
                "--min-note-ms",
                "40",
                "--max-notes",
                "5000",
                "--max-frequency-hz",
                "1800",
                "--pitch-mode",
                "legacy",
                "--candidate-count",
                "7",
                "--tracking-min-frequency-hz",
                "180",
            ]
        )
        self.assertEqual(args.speed, 1.5)
        self.assertEqual(args.min_note_ms, 40)
        self.assertEqual(args.max_notes, 5000)
        self.assertEqual(args.max_frequency_hz, 1800)
        self.assertEqual(args.pitch_mode, "legacy")
        self.assertEqual(args.candidate_count, 7)
        self.assertEqual(args.tracking_min_frequency_hz, 180)


if __name__ == "__main__":
    unittest.main()
