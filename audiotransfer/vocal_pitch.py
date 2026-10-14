"""Singing-specific F0 estimation and independent vocal evidence checks."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import shutil
import tempfile
import time
import urllib.request
import zipfile

import numpy as np

from .analysis import PitchFrame

MODEL_URL = "https://github.com/yxlllc/RMVPE/releases/download/230917/rmvpe.zip"
ARCHIVE_SHA256 = "54ae40d9c066d998b94574f6ef0deea19ed1565bd655b3f0d9b1ad612fb5309c"
MODEL_SHA256 = "19dc1809cf4cdb0a18db93441816bc327e14e5644b72eeaae5220560c6736fe2"
MODELS = Path(__file__).resolve().parent.parent / ".models"


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return (
            hashlib.file_digest(stream, "sha256").hexdigest()
            if hasattr(hashlib, "file_digest")
            else _digest_stream(stream)
        )


def _digest_stream(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def ensure_model(directory: Path, progress=None) -> Path:
    report = progress or (lambda _: None)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "model.pt"
    if target.is_file() and file_digest(target) == MODEL_SHA256:
        return target
    report("首次下载人声基音模型（约 325 MiB）；完成后可离线复用。")
    with tempfile.TemporaryDirectory(prefix="download-", dir=directory) as temporary:
        archive_path = Path(temporary) / "model.zip"
        # Release URLs can be served as stale partial responses by HTTP caches.
        request = urllib.request.Request(MODEL_URL + "?download=" + str(time.time_ns()))
        with (
            urllib.request.urlopen(request, timeout=45) as response,
            archive_path.open("wb") as stream,
        ):
            total = int(response.headers.get("Content-Length", 340638958))
            size, reported = 0, -1
            while block := response.read(1024 * 1024):
                stream.write(block)
                size += len(block)
                percent = min(99, size * 100 // max(1, total))
                if percent // 10 > reported:
                    report(f"下载人声基音模型：{percent}%")
                    reported = percent // 10
        if file_digest(archive_path) != ARCHIVE_SHA256:
            raise ValueError("人声基音模型下载不完整或校验失败，请重新转换。")
        extracted = Path(temporary) / "model.pt"
        with (
            zipfile.ZipFile(archive_path) as archive,
            archive.open("model.pt") as source,
            extracted.open("wb") as stream,
        ):
            shutil.copyfileobj(source, stream, length=1024 * 1024)
        if file_digest(extracted) != MODEL_SHA256:
            raise ValueError("人声基音模型权重校验失败，请重新转换。")
        extracted.replace(target)
    return target


def decode_salience(salience: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local averaging of the strongest RMVPE bin; never interpolate silence."""
    salience = np.asarray(salience)
    if salience.ndim != 2 or salience.shape[1] != 360:
        raise ValueError("人声模型输出形状不正确")
    centers = salience.argmax(axis=1)
    bins = np.arange(360)
    mask = abs(bins[None, :] - centers[:, None]) <= 4
    weights = salience * mask
    total = weights.sum(axis=1)
    cents = (weights * (bins * 20 + 1997.3794084376191)).sum(axis=1) / np.maximum(
        total, 1e-12
    )
    confidence = salience.max(axis=1)
    pitch = 10 * 2 ** (cents / 1200)
    pitch[confidence < 0.03] = 0
    return pitch, confidence


def _predict(
    samples: np.ndarray, model_path: Path, device: str, progress
) -> tuple[np.ndarray, np.ndarray]:
    import torch
    from torch import nn
    from torch.nn import functional as F
    from librosa.filters import mel
    from ._rmvpe.deepunet import DeepUnet0
    from ._rmvpe.seq import BiGRU

    class Network(nn.Module):
        def __init__(self):
            super().__init__()
            self.unet = DeepUnet0((2, 2), 4)
            self.cnn = nn.Conv2d(16, 3, (3, 3), padding=(1, 1))
            self.fc = nn.Sequential(
                BiGRU(384, 256, 1), nn.Linear(512, 360), nn.Dropout(0.25), nn.Sigmoid()
            )

        def forward(self, spectrum):
            values = spectrum.transpose(-1, -2).unsqueeze(1)
            return self.fc(self.cnn(self.unet(values)).transpose(1, 2).flatten(-2))

    model = Network().eval()
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model"], strict=True)
    del checkpoint
    model.to(device)
    basis = (
        torch.from_numpy(
            mel(sr=16000, n_fft=1024, n_mels=128, fmin=30, fmax=8000, htk=True)
        )
        .float()
        .to(device)
    )
    window = torch.hann_window(1024, device=device)
    count = math.ceil(len(samples) / 160)
    pitch, confidence = np.zeros(count), np.zeros(count)
    block, context = 1200, 100  # 12 seconds plus one second on both sides.
    with torch.inference_mode():
        for first in range(0, count, block):
            last = min(count, first + block)
            left, right = max(0, first - context), min(count, last + context)
            audio = (
                torch.from_numpy(samples[left * 160 : right * 160].copy())
                .float()
                .to(device)[None]
            )
            audio = F.pad(audio, (0, max(0, 1024 - audio.shape[-1])))
            spectrum = torch.stft(
                audio,
                n_fft=1024,
                hop_length=160,
                win_length=1024,
                window=window,
                return_complex=True,
            ).abs()
            features = (basis @ spectrum).clamp(min=1e-5).log()
            length = features.shape[-1]
            hidden = (
                model(F.pad(features, (0, (-length) % 32)))[0, :length]
                .float()
                .cpu()
                .numpy()
            )
            local_pitch, local_confidence = decode_salience(hidden)
            offset, size = first - left, last - first
            pitch[first:last] = local_pitch[offset : offset + size]
            confidence[first:last] = local_confidence[offset : offset + size]
            progress(f"核对人声基音：{round(last * 100 / count)}%")
    return pitch, confidence


def estimate_vocals(
    samples: np.ndarray, sample_rate: int, hop_size: int, device="auto", progress=None
):
    report = progress or (lambda _: None)
    count = math.ceil(len(samples) / hop_size)
    if not count:
        return np.zeros(0), np.zeros(0)
    signature = hashlib.sha256(
        samples.astype(np.float32).tobytes()
        + f"rmvpe-230917-fp32-12s-1s-v1:{sample_rate}:{MODEL_SHA256}".encode()
    ).hexdigest()
    cache = MODELS / "pitch-cache" / (signature + ".npz")
    cached = False
    if cache.is_file():
        try:
            with np.load(cache, allow_pickle=False) as data:
                pitch, confidence = data["pitch"], data["confidence"]
            expected = math.ceil(len(samples) * 100 / sample_rate)
            cached = (
                pitch.ndim == confidence.ndim == 1
                and pitch.shape == confidence.shape
                and len(pitch) > 0
                and abs(len(pitch) - expected) <= 1
                and np.all(np.isfinite(pitch))
                and np.all(pitch >= 0)
                and np.all(np.isfinite(confidence))
                and np.all((confidence >= 0) & (confidence <= 1))
            )
        except (OSError, ValueError, KeyError):
            cached = False
        if cached:
            report("复用人声基音核验结果。")
        else:
            report("人声基音核验缓存异常，重新计算。")
    if not cached:
        import torch
        import librosa

        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("device 必须是 auto、cpu 或 cuda")
        selected = (
            ("cuda" if torch.cuda.is_available() else "cpu")
            if device == "auto"
            else device
        )
        if selected == "cuda" and not torch.cuda.is_available():
            raise ValueError("当前环境没有可用 CUDA，请使用 CPU。")
        signal = np.asarray(samples, dtype=np.float32)
        if sample_rate != 16000:
            signal = librosa.resample(signal, orig_sr=sample_rate, target_sr=16000)
        model = ensure_model(MODELS / "rmvpe", report)
        pitch, confidence = _predict(signal, model, selected, report)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, pitch=pitch, confidence=confidence)
    indices = np.clip(
        np.rint(np.arange(count) * hop_size / sample_rate / 0.01).astype(int),
        0,
        len(pitch) - 1,
    )
    return pitch[indices], confidence[indices]


def cross_check_vocals(
    frames: list[PitchFrame], pitch: np.ndarray, confidence: np.ndarray
) -> list[PitchFrame]:
    """Use reliable singing F0; retain clear CREPE notes when RMVPE is unsure."""
    if len(frames) != len(pitch) or len(frames) != len(confidence):
        raise ValueError("两个人声音高模型的时间线长度不一致")
    result = []
    for previous, frequency, certainty in zip(frames, pitch, confidence):
        clear_previous = previous.frequency_hz > 0 and previous.confidence >= 0.8
        reliable = np.isfinite(frequency) and frequency > 0 and certainty >= 0.18
        if reliable:
            agrees = (
                previous.frequency_hz > 0
                and abs(12 * math.log2(frequency / previous.frequency_hz)) <= 0.75
            )
            if not agrees and certainty < 0.5 and clear_previous:
                result.append(previous)
            else:
                result.append(
                    PitchFrame(
                        float(frequency),
                        (
                            max(float(certainty), previous.confidence)
                            if agrees
                            else float(certainty)
                        ),
                        previous.rms,
                    )
                )
        elif clear_previous:
            result.append(previous)
        else:
            result.append(PitchFrame(0, 0, previous.rms))
    return result
