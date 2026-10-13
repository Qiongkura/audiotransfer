"""Decode compressed audio through ffmpeg into mono float32 PCM."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

import numpy as np


class AudioDecodeError(RuntimeError):
    """Raised when ffmpeg cannot decode the requested input."""


@dataclass(frozen=True)
class DecodedAudio:
    """Decoded mono audio samples and their sample rate."""

    samples: np.ndarray
    sample_rate: int


def find_ffmpeg(ffmpeg: str = "ffmpeg") -> str:
    """Return an executable ffmpeg path or raise a clear error."""

    candidate = shutil.which(ffmpeg)
    if candidate:
        return candidate
    # shutil.which can return None for an explicit path on some Windows setups.
    explicit = Path(ffmpeg)
    if explicit.is_file():
        return str(explicit)
    if ffmpeg == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            pass
    raise AudioDecodeError(
        f"找不到 ffmpeg 可执行文件: {ffmpeg!r}。"
        "请安装 ffmpeg 并加入 PATH，或通过 --ffmpeg 指定完整路径。"
    )


def decode_with_ffmpeg(
    input_path: str | Path,
    sample_rate: int = 16_000,
    ffmpeg: str = "ffmpeg",
    channels: int = 1,
) -> DecodedAudio:
    """Decode *input_path* as signed 16-bit little-endian mono PCM.

    ffmpeg writes raw PCM to stdout.  The returned values are normalized to
    approximately [-1, 1] and use float32 to keep later analysis predictable.
    """

    input_path = Path(input_path)
    if not input_path.is_file():
        raise AudioDecodeError(f"输入音频不存在或不是文件: {input_path}")
    if sample_rate <= 0:
        raise ValueError("sample_rate 必须为正数")
    if channels not in (1, 2):
        raise ValueError("channels 必须为 1 或 2")

    executable = find_ffmpeg(ffmpeg)
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-f",
        "s16le",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise AudioDecodeError(f"无法启动 ffmpeg: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = f"ffmpeg 退出码 {completed.returncode}"
        raise AudioDecodeError(f"ffmpeg 解码失败: {detail}")

    raw = completed.stdout
    if len(raw) % 2:
        # A malformed PCM stream should not silently produce a shifted sample.
        raw = raw[:-1]
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        samples = samples[: samples.size // 2 * 2].reshape(-1, 2)
    return DecodedAudio(samples=samples, sample_rate=sample_rate)
