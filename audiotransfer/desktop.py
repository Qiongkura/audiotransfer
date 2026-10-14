"""Desktop jobs. No Tk or heavy audio imports at application startup."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable
import uuid

ROOT = Path(__file__).resolve().parent.parent
AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
CHATANG_SHA256 = "2b34f483a9b57082300ce296988bfc6cbdfca94711a691bf0d7b34b0cad11e4b"
STAGES = ("检查音频", "分离人声与伴奏", "提取完整旋律", "整理节奏", "生成播放器")


@dataclass(frozen=True)
class JobProgress:
    stage: int
    message: str
    percent: int | None = None


@dataclass(frozen=True)
class ConversionRequest:
    source: Path
    output_root: Path
    title: str
    artist: str = "未知歌手"
    controller: str = "ssd1306"
    mode: str = "song"


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def validate_audio(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError("音频文件不存在，请重新选择。")
    if path.suffix.lower() not in AUDIO_SUFFIXES:
        raise ValueError("请选择 MP3、FLAC、WAV、M4A、AAC、OGG、OPUS 或 WMA 音频。")
    return path


def audio_labels(path: Path) -> tuple[str, str]:
    title, artist = path.stem, "未知歌手"
    try:
        import mutagen

        audio = mutagen.File(path, easy=True)
        if audio and audio.tags:
            title = str(audio.tags.get("title", [title])[0])
            artist = str(audio.tags.get("artist", [artist])[0])
    except Exception:
        pass  # Bad/missing tags must not prevent importing a decodable file.
    return title, artist


def project_hashes(project: Path) -> dict[str, str]:
    paths = [project / "platformio.ini", *sorted((project / "src").rglob("*"))]
    return {
        str(p.relative_to(project)).replace("\\", "/"): digest(p)
        for p in paths
        if p.is_file()
    }


@dataclass(frozen=True)
class DesktopResult:
    project: Path
    preview: Path
    title: str
    duration_ms: int
    event_count: int
    source_sha256: str

    def validate_for_flash(self) -> None:
        manifest = self.project / "desktop-job.json"
        if not manifest.is_file():
            raise ValueError("转换结果不完整，请重新转换后烧录。")
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            saved.get("status") != "ready"
            or saved.get("source_sha256") != self.source_sha256
            or saved.get("files") != project_hashes(self.project)
            or not (self.project / "src/main.cpp").is_file()
            or not (self.project / "platformio.ini").is_file()
        ):
            raise ValueError("生成的工程已被修改或不完整，请重新转换后烧录。")


def convert(
    request: ConversionRequest, report: Callable[[JobProgress], None]
) -> DesktopResult:
    source = validate_audio(request.source)
    if request.mode not in ("song", "tracked"):
        raise ValueError("请选择完整歌曲或单旋律模式。")
    title, artist = request.title.strip(), request.artist.strip() or "未知歌手"
    if not title:
        raise ValueError("请填写歌名。")
    report(JobProgress(0, "检查音频并匹配已校对曲谱"))
    before = source.stat()
    signature = digest(source)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:48] or "song"
    job = request.output_root / f"{name}-{uuid.uuid4().hex[:8]}"
    raw, project = job / "melody", job / "player"
    job.mkdir(parents=True, exist_ok=False)
    from .analysis import AnalysisConfig
    from .output import write_outputs
    from .pipeline import process_file
    from .player import write_player_project

    if signature == CHATANG_SHA256 and request.mode == "song":
        # A fingerprint, never the filename, selects this recording's reviewed score.
        import numpy as np
        from .scored_song import build

        example = ROOT / "examples/chatang"
        report(JobProgress(2, "已匹配《茶汤》原录音，使用此前校对的完整曲谱"))
        profile = json.loads((example / "profile.json").read_text(encoding="utf-8"))
        baseline = json.loads(
            (example / "baseline-v5.json").read_text(encoding="utf-8")
        )
        with np.load(example / "song-analysis.npz", allow_pickle=False) as analysis:
            events, strengths, metadata = build(profile, baseline, analysis, 12)
        report(JobProgress(3, "保留校对后的演唱、前奏、间奏与尾奏"))
        write_outputs(
            events,
            raw,
            target="platformio-stm32",
            stm32_pin="PA8",
            gap_ms=12,
            sound_mode="pulse",
            active_low=True,
            velocities=strengths,
            arrangement=metadata,
        )
    else:
        stage = 0

        def forward(message: str) -> None:
            nonlocal stage
            if any(word in message for word in ("整理", "编排")):
                stage = max(stage, 3)
            elif any(word in message for word in ("跟踪", "音高", "旋律")):
                stage = max(stage, 2)
            elif any(word in message for word in ("分离", "分轨")):
                stage = max(stage, 1)
            match = re.search(r"进度[：:]\s*(\d+)%", message)
            report(
                JobProgress(stage, message, min(100, int(match[1])) if match else None)
            )

        report(
            JobProgress(
                1 if request.mode == "song" else 2,
                "准备分析音频；首次使用可能需要下载模型",
            )
        )
        process_file(
            source,
            raw,
            config=AnalysisConfig(pitch_mode=request.mode),
            min_note_ms=80,
            gap_ms=12,
            target="platformio-stm32",
            stm32_pin="PA8",
            sound_mode="pulse",
            active_low=True,
            arrangement="score",
            progress=forward,
        )
    report(JobProgress(4, "生成中文屏幕、音量键和暂停键固件"))
    write_player_project(
        raw, project, title=title, artist=artist, controller=request.controller
    )
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("转换期间原音频发生变化，请重新转换。")
    notes = json.loads((project / "notes.json").read_text(encoding="utf-8"))
    duration = sum(n["duration_ms"] for n in notes)
    if not any(n["frequency_hz"] > 0 for n in notes):
        raise ValueError("没有提取到可播放旋律，请确认音频中有清晰的主旋律。")
    preview = project / "preview.wav"
    # OutputPaths owns the filename; avoid assuming it if the exporter changes.
    previews = list(project.glob("*.wav"))
    if not preview.is_file() and len(previews) == 1:
        preview = previews[0]
    if not preview.is_file():
        raise ValueError("试听文件未生成，请重新转换。")
    manifest = dict(
        status="ready",
        source_sha256=signature,
        title=title,
        artist=artist,
        source_name=source.name,
        duration_ms=duration,
        event_count=len(notes),
        files=project_hashes(project),
    )
    (project / "desktop-job.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report(JobProgress(4, "转换完成，可以试听或烧录", 100))
    return DesktopResult(project, preview, title, duration, len(notes), signature)


def platformio_command() -> list[str]:
    candidates = [
        shutil.which("pio"),
        shutil.which("platformio"),
        Path(sys.executable).parent / "pio.exe",
        Path(sys.base_prefix) / "Scripts/pio.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return [str(candidate)]
    if importlib.util.find_spec("platformio"):
        return [sys.executable, "-m", "platformio"]
    raise RuntimeError("未找到烧录工具。请先运行 setup.bat 安装 PlatformIO。")


def flash(result: DesktopResult, report: Callable[[JobProgress], None]) -> str:
    result.validate_for_flash()
    # The ARM linker's map-file handling on Windows fails in Unicode paths.
    # Stage only the checked build inputs in an ASCII temporary directory.
    locations = [Path(tempfile.gettempdir()), ROOT]
    if os.environ.get("PUBLIC"):
        locations.append(Path(os.environ["PUBLIC"]) / "Documents")
    for base in locations:
        if str(base.resolve()).isascii() and base.is_dir():
            try:
                scratch = tempfile.TemporaryDirectory(prefix="audiotransfer-", dir=base)
            except OSError:
                continue
            break
    else:
        raise RuntimeError(
            "烧录工具需要英文临时路径，请设置 TEMP 为可写的英文路径后重试。"
        )
    with scratch as folder:
        build = Path(folder)
        shutil.copy2(result.project / "platformio.ini", build / "platformio.ini")
        shutil.copytree(result.project / "src", build / "src")
        if project_hashes(build) != project_hashes(result.project):
            raise ValueError("复制工程时文件发生变化，请重新转换。")
        return _flash_project(build, result.project, report)


def _flash_project(
    build: Path, output_dir: Path, report: Callable[[JobProgress], None]
) -> str:
    base_command = [*platformio_command(), "run", "-d", str(build)]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    report(JobProgress(0, "准备编译并连接 ST-Link"))
    lines: list[str] = []
    stage = 0
    # Build first: the upload target alone does not create firmware.bin.
    for command in (base_command, [*base_command, "-t", "upload"]):
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                lines.append(line)
                if "Uploading " in line or "Programming Started" in line:
                    stage = max(stage, 3)
                elif "Linking " in line or "Building " in line:
                    stage = max(stage, 2)
                elif "Compiling " in line:
                    stage = max(stage, 1)
                if "Verified OK" in line:
                    stage = 4
                report(JobProgress(stage, line.strip()))
            code = process.wait()
        if code:
            break
    output = "".join(lines)
    (output_dir / "flash.log").write_text(output, encoding="utf-8")
    for binary in (build / ".pio/build").glob("*/firmware.bin"):
        shutil.copy2(binary, output_dir / "firmware.bin")
    if code:
        errors = [line.strip() for line in lines if "error" in line.lower()]
        detail = errors[-1][:180] if errors else f"工具退出码 {code}"
        hint = (
            "无法连接开发板，请检查 ST-Link、开发板供电和 SWD 接线。"
            if any(
                word in output.lower()
                for word in ("init mode failed", "no st-link", "no stlink", "libusb")
            )
            else "固件编译或烧录未完成，可打开结果查看 flash.log。"
        )
        raise RuntimeError(hint + "\n" + detail)
    if "Verified OK" not in output:
        raise RuntimeError(
            "烧录工具未返回写入校验成功，不能确认烧录完成。请查看 flash.log。"
        )
    report(JobProgress(4, "烧录完成，开发板已开始播放", 100))
    return output


class WavePreview:
    """Windows MCI keeps preview playback/pause out of the Tk event loop."""

    def __init__(self):
        self.opened = False
        self.paused = False
        self.alias = "at" + uuid.uuid4().hex[:10]

    def _send(self, command: str) -> str:
        import ctypes

        buffer = ctypes.create_unicode_buffer(256)
        error = ctypes.windll.winmm.mciSendStringW(command, buffer, 256, None)
        if error:
            ctypes.windll.winmm.mciGetErrorStringW(error, buffer, 256)
            raise RuntimeError(buffer.value or "无法播放试听音频")
        return buffer.value

    def play(self, path: Path) -> None:
        self.close()
        self._send(f'open "{path.resolve()}" type waveaudio alias {self.alias}')
        self.opened = True
        self._send(f"set {self.alias} time format milliseconds")
        self._send(f"play {self.alias} from 0")
        self.paused = False

    def toggle(self) -> None:
        if not self.opened:
            return
        self._send(f'{"resume" if self.paused else "pause"} {self.alias}')
        self.paused = not self.paused

    def position(self) -> int:
        return int(self._send(f"status {self.alias} position")) if self.opened else 0

    def close(self) -> None:
        if self.opened:
            self._send(f"close {self.alias}")
        self.opened = self.paused = False
