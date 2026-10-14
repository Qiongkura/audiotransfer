import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from audiotransfer.desktop import (
    ConversionRequest,
    DesktopResult,
    convert,
    flash,
    project_hashes,
    validate_audio,
)
from audiotransfer.events import NoteEvent
from audiotransfer.output import write_outputs


class DesktopTests(unittest.TestCase):
    def test_full_workflow_uses_current_hardware_and_keeps_long_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "带 空格 (录音).flac"
            source.write_bytes(b"fixture-audio")

            def extract(src, output, **options):
                self.assertEqual(options["config"].pitch_mode, "song")
                self.assertEqual(options["config"].content_mode, "auto")
                self.assertEqual(options["target"], "platformio-stm32")
                self.assertEqual(options["sound_mode"], "pulse")
                self.assertTrue(options["active_low"])
                return write_outputs(
                    [NoteEvent(440, 200, "A4"), NoteEvent(0, 100, "rest")],
                    output,
                    target="platformio-stm32",
                    sound_mode="pulse",
                    active_low=True,
                )

            title = "很长的完整歌曲标题 Lose My Mind (feat. Doja Cat)"
            with patch("audiotransfer.pipeline.process_file", side_effect=extract):
                result = convert(
                    ConversionRequest(source, root / "out", title, "很长的歌手名称"),
                    lambda _: None,
                )
            self.assertEqual(result.duration_ms, 300)
            self.assertTrue(result.preview.is_file())
            result.validate_for_flash()
            settings = json.loads(
                (result.project / "player.json").read_text(encoding="utf-8")
            )
            self.assertEqual(settings["title"], title)
            self.assertEqual(settings["pause"], "PA2")
            self.assertEqual(settings["volume_initial_percent"], 50)
            self.assertTrue((result.project / "src/player_config.h").is_file())
            (result.project / "src/main.cpp").write_text("modified", encoding="utf-8")
            with patch("audiotransfer.desktop.subprocess.Popen") as process:
                with self.assertRaisesRegex(ValueError, "重新转换"):
                    flash(result, lambda _: None)
                process.assert_not_called()

    def test_desktop_passes_manual_content_mode_and_records_detected_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "music.wav"
            source.write_bytes(b"fixture")

            def extract(src, output, **options):
                self.assertEqual(options["config"].content_mode, "instrumental")
                self.assertEqual(options["separation_cache_dir"], root / "out")
                paths = write_outputs(
                    [NoteEvent(440, 300, "A4")],
                    output,
                    target="platformio-stm32",
                    sound_mode="pulse",
                    active_low=True,
                )
                (Path(output) / "source-regions.json").write_text(
                    json.dumps(
                        dict(
                            requested_content_mode="instrumental",
                            detected_content_mode="instrumental",
                            regions=[],
                        )
                    ),
                    encoding="utf-8",
                )
                return paths

            with patch("audiotransfer.pipeline.process_file", side_effect=extract):
                result = convert(
                    ConversionRequest(
                        source, root / "out", "纯音乐", content_mode="instrumental"
                    ),
                    lambda _: None,
                )
            self.assertEqual(result.detected_content_mode, "instrumental")
            self.assertTrue((result.project / "source-regions.json").is_file())
            result.validate_for_flash()
            meta = json.loads(
                (result.project / "desktop-job.json").read_text(encoding="utf-8")
            )
            self.assertEqual(meta["requested_content_mode"], "instrumental")
            self.assertEqual(meta["converter_version"], "0.3.2")

    def test_missing_or_incomplete_result_cannot_be_flashed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = DesktopResult(root, root / "preview.wav", "test", 300, 1, "hash")
            with patch("audiotransfer.desktop.subprocess.Popen") as process:
                with self.assertRaises(ValueError):
                    flash(result, lambda _: None)
                process.assert_not_called()

    def test_failed_conversion_never_creates_ready_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "test.wav"
            source.write_bytes(b"fixture")
            with patch(
                "audiotransfer.pipeline.process_file",
                side_effect=RuntimeError("decode failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "decode failed"):
                    convert(
                        ConversionRequest(source, root / "out", "test"), lambda _: None
                    )
            self.assertEqual(list(root.rglob("desktop-job.json")), [])

    def test_file_validation_rejects_non_audio_and_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "not audio.txt"
            path.write_text("text")
            with self.assertRaises(ValueError):
                validate_audio(path)
            with self.assertRaises(ValueError):
                validate_audio(path.with_suffix(".wav"))

    def test_failed_flash_reports_output_and_is_retryable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "src/main.cpp").write_text("firmware")
            (root / "platformio.ini").write_text("[env:test]")
            (root / "desktop-job.json").write_text(
                json.dumps(
                    dict(
                        status="ready", source_sha256="hash", files=project_hashes(root)
                    )
                )
            )
            result = DesktopResult(root, root / "preview.wav", "test", 300, 1, "hash")
            with (
                patch("audiotransfer.desktop.platformio_command", return_value=["pio"]),
                patch("audiotransfer.desktop.subprocess.Popen") as popen,
            ):
                proc = popen.return_value.__enter__.return_value
                proc.stdout = ["Error: No ST-Link\n"]
                proc.wait.return_value = 1
                with self.assertRaisesRegex(RuntimeError, "No ST-Link"):
                    flash(result, lambda _: None)
            result.validate_for_flash()

    def test_build_precedes_upload_in_ascii_staging_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 输出"
            (root / "src").mkdir(parents=True)
            (root / "src/main.cpp").write_text("firmware")
            (root / "platformio.ini").write_text("[env:test]")
            (root / "desktop-job.json").write_text(
                json.dumps(
                    dict(
                        status="ready", source_sha256="hash", files=project_hashes(root)
                    )
                )
            )
            result = DesktopResult(root, root / "preview.wav", "test", 300, 1, "hash")
            observed = []

            def run(command, **kwargs):
                observed.append(command)
                build = Path(command[command.index("-d") + 1])
                self.assertTrue(str(build).isascii())
                self.assertEqual(project_hashes(build), project_hashes(root))
                process = MagicMock()
                process.__enter__.return_value = process
                process.stdout = (
                    ["** Verified OK **\n"] if "-t" in command else ["Build success\n"]
                )
                process.wait.return_value = 0
                return process

            with (
                patch("audiotransfer.desktop.platformio_command", return_value=["pio"]),
                patch("audiotransfer.desktop.subprocess.Popen", side_effect=run),
            ):
                output = flash(result, lambda _: None)
            self.assertNotIn("-t", observed[0])
            self.assertEqual(observed[1][-2:], ["-t", "upload"])
            self.assertIn("Verified OK", output)

    def test_success_exit_without_verification_is_not_reported_as_flashed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "src/main.cpp").write_text("firmware")
            (root / "platformio.ini").write_text("[env:test]")
            (root / "desktop-job.json").write_text(
                json.dumps(
                    dict(
                        status="ready", source_sha256="hash", files=project_hashes(root)
                    )
                )
            )
            result = DesktopResult(root, root / "preview.wav", "test", 300, 1, "hash")
            with (
                patch("audiotransfer.desktop.platformio_command", return_value=["pio"]),
                patch("audiotransfer.desktop.subprocess.Popen") as popen,
            ):
                process = popen.return_value.__enter__.return_value
                process.stdout = ["SUCCESS\n"]
                process.wait.return_value = 0
                with self.assertRaisesRegex(RuntimeError, "不能确认"):
                    flash(result, lambda _: None)


if __name__ == "__main__":
    unittest.main()
