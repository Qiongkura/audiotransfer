import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audiotransfer.events import NoteEvent
from audiotransfer.output import write_outputs
from audiotransfer.pipeline import PlatformIOUploadError, upload_platformio


class PlatformIOOutputTests(unittest.TestCase):
    def test_empty_stm32_project_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = write_outputs(
                [],
                Path(directory),
                target="platformio-stm32",
                board="bluepill_f103c8",
                stm32_pin="PA8",
                upload_protocol="stlink",
            )
            self.assertIsNotNone(paths.platformio_ini_path)
            self.assertIsNotNone(paths.main_cpp_path)
            self.assertEqual(paths.target, "platformio-stm32")
            self.assertTrue(paths.ino_path.is_file())

            platformio_ini = paths.platformio_ini_path.read_text(encoding="utf-8")
            self.assertIn("platform = ststm32", platformio_ini)
            self.assertIn("board = bluepill_f103c8", platformio_ini)
            self.assertIn("framework = arduino", platformio_ini)
            self.assertIn("upload_protocol = stlink", platformio_ini)

            main_cpp = paths.main_cpp_path.read_text(encoding="utf-8")
            self.assertIn("#include <Arduino.h>", main_cpp)
            self.assertIn("const uint32_t BUZZER_PIN = PA8;", main_cpp)
            self.assertIn("tone(BUZZER_PIN", main_cpp)
            self.assertIn("noTone(BUZZER_PIN", main_cpp)
            self.assertIn("const MelodyNote melody[]", main_cpp)
            self.assertIn("MELODY_LENGTH = 0UL", main_cpp)
            self.assertIn("{0, 0UL}", main_cpp)
            self.assertNotIn("avr/pgmspace.h", main_cpp)
            self.assertNotIn("pgm_read_", main_cpp)

    def test_stm32_pin_validation_rejects_cpp_punctuation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                write_outputs(
                    [NoteEvent(440.0, 100, "A4")],
                    directory,
                    target="platformio-stm32",
                    stm32_pin="PA8); noTone(0);//",
                )

    @patch("audiotransfer.pipeline.subprocess.run")
    def test_upload_command_and_result(self, run_mock) -> None:
        run_mock.return_value = __import__("subprocess").CompletedProcess(
            ["pio"], 0, stdout="upload ok\n", stderr=""
        )
        with tempfile.TemporaryDirectory() as directory:
            result = upload_platformio(directory, pio_command="pio")

        command = run_mock.call_args.args[0]
        self.assertEqual(
            command,
            ["pio", "run", "-d", directory, "-t", "upload"],
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("upload ok", result.output)

    @patch("audiotransfer.pipeline.subprocess.run")
    def test_upload_failure_is_clear(self, run_mock) -> None:
        run_mock.return_value = __import__("subprocess").CompletedProcess(
            ["pio"], 1, stdout="", stderr="No ST-Link\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PlatformIOUploadError) as context:
                upload_platformio(directory)
        self.assertIn("PlatformIO 上传失败", str(context.exception))
        self.assertIn("No ST-Link", str(context.exception))


if __name__ == "__main__":
    unittest.main()
