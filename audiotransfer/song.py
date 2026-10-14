"""Whole-song melody: vocals during singing, instrumental lead elsewhere.

Separation removes drums and bass; instrumental lead tracking is still an
approximation when several instruments share the same register.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import math
import os
from pathlib import Path
from typing import Callable

import numpy as np

from .analysis import (
    AnalysisConfig,
    PitchFrame,
    PitchCandidate,
    CandidateFrame,
    analyze_samples_tracked,
    track_pitch_candidates,
)
from .audio import decode_with_ffmpeg

Progress = Callable[[str], None]


class SongAnalysisError(RuntimeError):
    """A required song-analysis dependency or model could not be used."""


@dataclass
class SongAnalysis:
    frames: list[PitchFrame]
    onsets: set[int]
    sources: list[str]
    sample_count: int
    sample_rate: int
    details: dict[str, object] = field(default_factory=dict)


def confirmed_vocals(
    voices: list[PitchFrame],
    vocal_rms: np.ndarray,
    instrument_rms: np.ndarray,
    config: AnalysisConfig,
) -> np.ndarray:
    """Require sustained vocal evidence before protecting a sung region.

    Quiet vocal-stem leakage used to create artificial rests throughout the
    instrumental intro. Weak syllables near strong vocal anchors stay valid.
    """
    hop = config.hop_size / config.sample_rate
    anchors = np.array(
        [
            f.frequency_hz > 0
            and f.confidence >= max(0.5, config.confidence_threshold)
            and vocal_rms[i] >= max(config.energy_threshold, instrument_rms[i] * 0.2)
            for i, f in enumerate(voices)
        ]
    )
    edges = np.flatnonzero(np.diff(np.r_[False, anchors, False]))
    for start, stop in zip(edges[::2], edges[1::2]):
        if (stop - start) * hop < 0.08:
            anchors[start:stop] = False
    supported = _vocal_regions(anchors, hop)
    return np.array(
        [
            supported[i]
            and f.frequency_hz > 0
            and vocal_rms[i]
            >= max(config.energy_threshold / 2, instrument_rms[i] * 0.08)
            for i, f in enumerate(voices)
        ]
    )


def foreground_instruments(samples: np.ndarray, config: AnalysisConfig) -> np.ndarray:
    """Attenuate slowly sustained backing before selecting a moving lead."""
    from scipy.ndimage import minimum_filter1d

    librosa = _librosa()
    spectrum = librosa.stft(samples, n_fft=2048, hop_length=config.hop_size)
    magnitude = np.abs(spectrum)
    width = max(3, round(3 * config.sample_rate / config.hop_size))
    background = minimum_filter1d(magnitude, size=width, axis=1, mode="nearest")
    persistent = np.quantile(magnitude, 0.2, axis=1)
    background = np.maximum(background, persistent[:, None])
    # Retain a floor: an exposed sustained lead must not disappear completely.
    foreground = np.maximum(magnitude - 0.92 * background, 0.08 * magnitude)
    filtered = librosa.istft(
        spectrum * (foreground / np.maximum(magnitude, 1e-10)),
        hop_length=config.hop_size,
        length=len(samples),
    )
    original_rms = float(np.sqrt(np.mean(samples * samples)))
    filtered_rms = float(np.sqrt(np.mean(filtered * filtered)))
    gain = min(2.5, original_rms / max(filtered_rms, 1e-10))
    return np.asarray(filtered * gain, dtype=np.float32)


def spectral_lead_candidates(
    samples: np.ndarray, config: AnalysisConfig
) -> list[CandidateFrame]:
    """Track resolved spectral peaks instead of a mixed-tone period.

    Autocorrelation of two simultaneous notes can invent a third fundamental.
    A foreground instrument candidate must have an actual prominent peak.
    """
    from scipy.signal import find_peaks

    size = config.frame_size
    nfft = 1 << (size * 4 - 1).bit_length()
    axis = np.fft.rfftfreq(nfft, 1 / config.sample_rate)
    band = (axis >= config.min_frequency_hz) & (axis <= config.max_frequency_hz)
    window = np.hanning(size)
    candidates = []
    for start in range(0, len(samples) - size + 1, config.hop_size):
        frame = samples[start : start + size] * window
        rms = float(np.sqrt(np.mean(frame * frame)))
        spectrum = np.abs(np.fft.rfft(frame, n=nfft))
        floor = max(1e-10, float(np.median(spectrum[band])))
        maximum = float(np.max(spectrum[band], initial=0))
        peaks, _ = find_peaks(spectrum)
        peaks = (
            [
                int(k)
                for k in peaks
                if band[k] and spectrum[k] >= max(maximum * 0.12, floor * 6)
            ]
            if rms >= config.energy_threshold
            else []
        )
        ranked = []
        for k in peaks:
            left, centre, right = spectrum[k - 1 : k + 2]
            denominator = left - 2 * centre + right
            delta = (
                0.5 * (left - right) / denominator if abs(denominator) > 1e-10 else 0
            )
            frequency = float(
                (k + np.clip(delta, -0.5, 0.5)) * config.sample_rate / nfft
            )
            magnitude = float(spectrum[k] / max(maximum, 1e-10))
            prominence = float(spectrum[k] / floor)
            confidence = prominence / (prominence + 2) * (0.6 + 0.4 * magnitude)
            score = magnitude
            half = round(k / 2)
            if (
                axis[half] >= config.min_frequency_hz
                and spectrum[half] > spectrum[k] * 0.65
            ):
                score *= 0.45
            if config.min_frequency_hz <= frequency <= config.max_frequency_hz:
                ranked.append(PitchCandidate(frequency, confidence, rms, score))
        ranked.sort(key=lambda p: p.score, reverse=True)
        candidates.append(CandidateFrame(tuple(ranked[: config.candidate_count]), rms))
    return candidates


def spectral_lead_track(
    samples: np.ndarray, config: AnalysisConfig
) -> list[PitchFrame]:
    return track_pitch_candidates(spectral_lead_candidates(samples, config), config)


def _masked_spectral_candidates(
    samples: np.ndarray, config: AnalysisConfig, mask: np.ndarray
) -> list[CandidateFrame]:
    size = config.frame_size
    padded = np.pad(samples, (size // 2, size))
    result = [CandidateFrame((), 0.0) for _ in mask]
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    for start, stop in zip(edges[::2], edges[1::2]):
        local = padded[start * config.hop_size : stop * config.hop_size + size]
        result[start:stop] = spectral_lead_candidates(local, config)[: stop - start]
    return result


def continuous_instrument_lead(
    samples: np.ndarray,
    config: AnalysisConfig,
    selected: np.ndarray,
    progress=None,
    device="auto",
) -> list[PitchFrame]:
    """Follow one path through observed peaks; neural evidence scores that path.

    Do not switch frame-by-frame between a neural estimate and a spectral track.
    Neither a mixed-tone period nor an unobserved fundamental is added as a note.
    """
    lead_config = replace(
        config,
        min_frequency_hz=(
            config.min_frequency_hz
            if config.content_mode == "instrumental"
            else max(config.min_frequency_hz, config.tracking_min_frequency_hz)
        ),
        pitch_mode="tracked",
        frame_ms=max(80, config.frame_ms),
        tracking_continuity_weight=max(0.22, config.tracking_continuity_weight),
        tracking_octave_penalty=max(1.2, config.tracking_octave_penalty),
    )
    if lead_config.min_frequency_hz >= lead_config.max_frequency_hz:
        raise ValueError("主奏最低频率必须小于最高频率")
    if not np.any(selected):
        return [PitchFrame(0, 0, 0) for _ in selected]
    raw = _masked_spectral_candidates(samples, lead_config, selected)
    foreground = _masked_spectral_candidates(
        foreground_instruments(samples, config), lead_config, selected
    )
    neural = (
        neural_frames(samples, lead_config, progress, device, "乐器")
        if config.song_backend == "crepe"
        else [PitchFrame(0, 0, 0) for _ in selected]
    )
    merged = []
    for original, moving, model in zip(raw, foreground, neural):
        candidates = []
        for frame, weight in ((moving, 0.85), (original, 0.35)):
            for candidate in frame.candidates:
                score = candidate.score * weight
                agrees = (
                    model.frequency_hz > 0
                    and abs(12 * math.log2(candidate.frequency_hz / model.frequency_hz))
                    < 0.75
                )
                if agrees:
                    score += 0.8 * model.confidence
                if candidate.frequency_hz < config.tracking_min_frequency_hz:
                    score *= 0.6  # A preference, not deletion of a real low solo.
                scored = replace(candidate, score=score, rms=original.rms)
                match = next(
                    (
                        i
                        for i, other in enumerate(candidates)
                        if abs(12 * math.log2(other.frequency_hz / scored.frequency_hz))
                        < 0.5
                    ),
                    None,
                )
                if match is None:
                    candidates.append(scored)
                elif candidates[match].score < scored.score:
                    candidates[match] = scored
        candidates.sort(key=lambda c: c.score, reverse=True)
        merged.append(
            CandidateFrame(tuple(candidates[: config.candidate_count]), original.rms)
        )
    return track_pitch_candidates(merged, lead_config)


def singing_section_mask(valid: np.ndarray, hop_seconds: float) -> np.ndarray:
    """Lock sung sections across breaths; leave sustained instrumental breaks.

    Pitch eligibility is still independent: protecting a section creates rests,
    never fabricated singing, where actual vocal evidence is absent.
    """
    indices = np.flatnonzero(valid)
    protected = valid.copy()
    if not len(indices):
        return protected
    gap = round(3.5 / hop_seconds)
    for first, last in zip(indices, indices[1:]):
        if last - first <= gap:
            protected[first : last + 1] = True
    margin = round(0.16 / hop_seconds)
    starts = indices[np.r_[True, np.diff(indices) > gap]]
    ends = indices[np.r_[np.diff(indices) > gap, True]]
    for first, last in zip(starts, ends):
        protected[max(0, first - margin) : min(len(valid), last + margin + 1)] = True
    return protected


def detect_content_mode(valid: np.ndarray, hop_seconds: float) -> tuple[str, dict]:
    """A conservative whole-recording heuristic, not a singing classifier."""
    duration = len(valid) * hop_seconds
    evidence = float(np.count_nonzero(valid) * hop_seconds)
    edges = np.flatnonzero(
        np.diff(np.r_[False, _vocal_regions(valid, hop_seconds), False])
    )
    longest = max(
        ((stop - start) * hop_seconds for start, stop in zip(edges[::2], edges[1::2])),
        default=0.0,
    )
    required = min(3.0, max(0.4, duration * 0.015))
    mode = (
        "vocal"
        if evidence >= required and longest >= min(1.0, max(0.3, duration * 0.005))
        else "instrumental"
    )
    return mode, {
        "vocal_evidence_seconds": round(evidence, 3),
        "recording_seconds": round(duration, 3),
        "auto_detection_method": "whole_recording_vocal_stem_evidence_heuristic",
    }


def recover_weak_vocal_hold(
    valid: np.ndarray,
    voices: list[PitchFrame],
    vocal_rms: np.ndarray,
    instrument_rms: np.ndarray,
    config: AnalysisConfig,
) -> tuple[np.ndarray, int]:
    """Keep observed weak pitch between matching anchors, without filling breaths."""
    recovered = valid.copy()
    hop = config.hop_size / config.sample_rate
    edges = np.flatnonzero(np.diff(np.r_[False, ~valid, False]))
    count = 0
    for first, stop in zip(edges[::2], edges[1::2]):
        if first == 0 or stop == len(valid) or (stop - first) * hop > 1.0:
            continue
        left, right = voices[first - 1], voices[stop]
        if (
            min(left.frequency_hz, right.frequency_hz) <= 0
            or abs(12 * math.log2(left.frequency_hz / right.frequency_hz)) > 0.75
        ):
            continue
        context = np.r_[
            vocal_rms[max(0, first - 5) : first],
            vocal_rms[stop : min(len(valid), stop + 5)],
        ]
        floor = max(config.energy_threshold / 2, float(np.median(context)) * 0.25)
        eligible = all(
            f.frequency_hz > 0
            and f.confidence >= 0.18
            and vocal_rms[i] >= max(floor, instrument_rms[i] * 0.08)
            and abs(12 * math.log2(f.frequency_hz / left.frequency_hz)) <= 1.5
            for i, f in enumerate(voices[first:stop], first)
        )
        if eligible:
            recovered[first:stop] = True
            count += stop - first
    return recovered, count


def suppress_brief_octave_alias(
    frames: list[PitchFrame], signal: np.ndarray, config: AnalysisConfig
) -> tuple[list[PitchFrame], int]:
    """Repair weak, unarticulated octave flips inside a continuous vocal note."""
    result = list(frames)
    pitches = np.array(
        [
            (
                round(69 + 12 * math.log2(f.frequency_hz / 440))
                if f.frequency_hz > 0
                else -1
            )
            for f in frames
        ]
    )
    edges = np.r_[0, np.flatnonzero(np.diff(pitches)) + 1, len(frames)]
    hop = config.hop_size / config.sample_rate
    repaired = 0
    rms = _rms_grid(
        signal, max(2, round(0.01 * config.sample_rate)), config.hop_size, len(frames)
    )
    for first, stop in zip(edges[:-1], edges[1:]):
        if first == 0 or stop == len(frames) or (stop - first) * hop > 0.16:
            continue
        left, right = frames[first - 1], frames[stop]
        if min(left.frequency_hz, right.frequency_hz, frames[first].frequency_hz) <= 0:
            continue
        if abs(12 * math.log2(left.frequency_hz / right.frequency_hz)) > 0.5:
            continue
        if (
            abs(
                abs(12 * math.log2(frames[first].frequency_hz / left.frequency_hz)) - 12
            )
            > 0.75
        ):
            continue
        if max(f.confidence for f in frames[first:stop]) >= 0.85 * min(
            left.confidence, right.confidence
        ):
            continue
        if clear_note_attack(rms, first, hop):
            continue
        for index in range(first, stop):
            result[index] = replace(frames[index], frequency_hz=left.frequency_hz)
        repaired += 1
    return result, repaired


def _selected_instrument_track(
    samples: np.ndarray,
    config: AnalysisConfig,
    mask: np.ndarray,
    spectral: bool = False,
) -> list[PitchFrame]:
    size = config.frame_size
    padded = np.pad(samples, (size // 2, size))
    result = [PitchFrame(0, 0, 0) for _ in mask]
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    for start, stop in zip(edges[::2], edges[1::2]):
        local = padded[start * config.hop_size : stop * config.hop_size + size]
        tracked = (
            spectral_lead_track(local, config)
            if spectral
            else analyze_samples_tracked(local, config)
        )[: stop - start]
        result[start:stop] = tracked
    return result


def repair_phrase_gaps(
    analysis: SongAnalysis,
    vocals: np.ndarray,
    instruments: np.ndarray,
    config: AnalysisConfig,
) -> SongAnalysis:
    """Bridge energized consonant/dropout gaps; keep real quiet breaths."""
    frames, sources = list(analysis.frames), list(analysis.sources)
    hop = config.hop_size / config.sample_rate
    envelopes = {
        "vocals": _rms_grid(
            vocals,
            max(2, round(0.02 * config.sample_rate)),
            config.hop_size,
            len(frames),
        ),
        "instruments": _rms_grid(
            instruments,
            max(2, round(0.02 * config.sample_rate)),
            config.hop_size,
            len(frames),
        ),
    }
    silent = np.array([f.frequency_hz <= 0 for f in frames])
    edges = np.flatnonzero(np.diff(np.r_[False, silent, False]))
    repaired = repaired_ms = 0
    for start, stop in zip(edges[::2], edges[1::2]):
        if start == 0 or stop == len(frames):
            continue
        left, right = frames[start - 1], frames[stop]
        source = sources[start - 1]
        if (
            source != sources[stop]
            or source not in envelopes
            or min(left.confidence, right.confidence) < 0.35
        ):
            continue
        distance = abs(12 * math.log2(left.frequency_hz / right.frequency_hz))
        duration = (stop - start) * hop
        limit = 0.22 if distance <= 0.75 else 0.14
        if duration > limit or distance > 4:
            continue
        rms = envelopes[source]
        context = np.r_[
            rms[max(0, start - 5) : start], rms[stop : min(len(rms), stop + 5)]
        ]
        reference = float(np.median(context))
        gap = rms[start:stop]
        # Energy must remain throughout the gap. A silent centre, including a
        # true breath between equal notes, prevents bridging.
        if (
            reference <= config.energy_threshold / 2
            or np.median(gap) < max(config.energy_threshold / 4, reference * 0.2)
            or np.quantile(gap, 0.2)
            < max(config.energy_threshold / 8, reference * 0.07)
        ):
            continue
        for index in range(start, stop):
            frames[index] = PitchFrame(
                left.frequency_hz,
                min(left.confidence, right.confidence),
                float(rms[index]),
            )
            sources[index] = source
        repaired += 1
        repaired_ms += round(duration * 1000)
    details = dict(
        analysis.details, repaired_gaps=repaired, repaired_gap_ms=repaired_ms
    )
    return SongAnalysis(
        frames,
        set(analysis.onsets),
        sources,
        analysis.sample_count,
        analysis.sample_rate,
        details,
    )


def _librosa():
    try:
        import librosa

        return librosa
    except ImportError as exc:
        raise SongAnalysisError(
            "完整歌曲模式缺少依赖，请运行 setup.bat 安装。"
        ) from exc


def separate_file(
    input_path: str | Path,
    config: AnalysisConfig,
    output_dir: str | Path,
    ffmpeg: str = "ffmpeg",
    device: str = "auto",
    progress: Progress | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Decode stereo, separate with Demucs, and cache aligned mono stems."""
    report = progress or (lambda _: None)
    librosa = _librosa()
    source = Path(input_path)
    if not source.is_file():
        raise SongAnalysisError(f"输入音频不存在: {source}")
    stat = source.stat()
    signature = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{config.sample_rate}:htdemucs:4.1"
    cache = (
        Path(output_dir)
        / ".cache"
        / (hashlib.sha256(signature.encode()).hexdigest() + ".npz")
    )
    if not cache.is_file():
        # Reuse older desktop jobs when upgrading to the shared stem cache.
        legacy = next(Path(output_dir).glob(f"*/melody/.cache/{cache.name}"), None)
        if legacy is not None:
            cache = legacy
    if cache.is_file():
        with np.load(cache, allow_pickle=False) as data:
            vocals, instruments = data["vocals"], data["instruments"]
        if vocals.ndim == instruments.ndim == 1 and vocals.shape == instruments.shape:
            report("复用已分离的人声与乐器轨。")
            return vocals, instruments
    try:
        import torch

        # Keep model downloads with the project rather than relying on an
        # undocumented user's home cache. Existing configured HF caches win.
        models = Path(__file__).resolve().parent.parent / ".models"
        os.environ.setdefault("HF_HOME", str(models / "huggingface"))
        torch.hub.set_dir(str(models / "torch"))
        from demucs.api import Separator
    except ImportError as exc:
        raise SongAnalysisError("缺少人声分离依赖，请运行 setup.bat 安装。") from exc
    if device not in {"auto", "cpu", "cuda"}:
        raise ValueError("device 必须是 auto、cpu 或 cuda")
    selected = (
        ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
    )
    if selected == "cuda" and not torch.cuda.is_available():
        raise SongAnalysisError(
            "当前 Python 环境没有可用的 CUDA，请使用 --device cpu。"
        )
    report(
        f"分离人声、乐器、贝斯和鼓点（{'显卡' if selected == 'cuda' else 'CPU'}）；首次使用会下载模型。"
    )
    decoded = decode_with_ffmpeg(source, 44_100, ffmpeg, channels=2)
    if decoded.samples.size == 0:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    wav = torch.from_numpy(decoded.samples.T.copy())
    previous_percent = [-10]

    def status(info: dict) -> None:
        if info.get("state") == "end":
            percent = min(
                99, round(100 * info.get("segment_offset", 0) / wav.shape[-1])
            )
            if percent >= previous_percent[0] + 10:
                report(f"分离进度：{percent}%")
                previous_percent[0] = percent

    try:
        # Native cached weights can be loaded directly, avoiding a needless hub
        # request on each new recording. LocalRepo still verifies the checksum.
        checkpoint_dir = models / "torch" / "checkpoints"
        cached = checkpoint_dir / "955717e8-8726e21a.th"
        separator = Separator(
            model="955717e8" if cached.is_file() else "htdemucs",
            repo=checkpoint_dir if cached.is_file() else None,
            device=selected,
            shifts=0,
            callback=status,
        )
        with torch.inference_mode():
            _, separated = separator.separate_tensor(wav, sr=44_100)
    except Exception as exc:
        raise SongAnalysisError(f"人声与乐器分离失败: {exc}") from exc
    stems = []
    for name in ("vocals", "other"):
        mono = separated[name].mean(dim=0).detach().cpu().numpy()
        stems.append(
            librosa.resample(
                mono, orig_sr=separator.samplerate, target_sr=config.sample_rate
            )
        )
    length = round(decoded.samples.shape[0] * config.sample_rate / 44_100)
    vocals, instruments = [
        np.pad(x[:length], (0, max(0, length - len(x)))).astype(np.float32)
        for x in stems
    ]
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, vocals=vocals, instruments=instruments)
    report("分离完成；保留完整时间线，包括前奏、间奏和尾奏。")
    return vocals, instruments


def _rms_grid(samples: np.ndarray, size: int, hop: int, count: int) -> np.ndarray:
    values = np.pad(samples.astype(np.float64), (size // 2, size))
    sums = np.concatenate(([0.0], np.cumsum(values * values)))
    starts = np.arange(count) * hop
    return np.sqrt(np.maximum(0, (sums[starts + size] - sums[starts]) / size))


def pyin_frames(
    samples: np.ndarray, config: AnalysisConfig, progress: Progress | None = None
) -> list[PitchFrame]:
    """pYIN in overlapping bounded chunks, aligned to one shared time grid."""
    librosa = _librosa()
    count = math.ceil(len(samples) / config.hop_size)
    if count == 0:
        return []
    size = max(
        2048,
        config.frame_size,
        2 * math.ceil(config.sample_rate / config.min_frequency_hz) + 1,
    )
    size = 1 << (size - 1).bit_length()
    rms = _rms_grid(samples, size, config.hop_size, count)
    result = [PitchFrame(0, 0, float(r)) for r in rms]
    # 20 seconds plus one second of context on each side limits pYIN memory.
    block = max(1, round(20 * config.sample_rate / config.hop_size))
    context = max(1, round(config.sample_rate / config.hop_size))
    report = progress or (lambda _: None)
    for first in range(0, count, block):
        last = min(count, first + block)
        left, right = max(0, first - context), min(count, last + context)
        part = samples[left * config.hop_size : right * config.hop_size]
        if np.max(rms[first:last], initial=0) < config.energy_threshold / 2:
            continue
        f0, voiced, probability = librosa.pyin(
            part,
            sr=config.sample_rate,
            fmin=config.min_frequency_hz,
            fmax=min(config.max_frequency_hz, config.sample_rate / 2 - 1),
            frame_length=size,
            hop_length=config.hop_size,
            center=True,
        )
        for index in range(first, last):
            local = index - left
            if local >= len(f0):
                break
            valid = (
                voiced[local]
                and np.isfinite(f0[local])
                and probability[local] >= config.confidence_threshold
            )
            result[index] = PitchFrame(
                float(f0[local]) if valid else 0,
                float(probability[local]),
                float(rms[index]),
            )
        report(f"识别人声旋律：{round(100 * last / count)}%")
    report("人声旋律识别完成。")
    return result


def refine_neural_pitch(
    pitch: np.ndarray,
    confidence: np.ndarray,
    rms: np.ndarray,
    config: AnalysisConfig,
) -> list[PitchFrame]:
    """Retain weak parts of reliable sung notes without admitting silence.

    One hard confidence threshold used at every hop cuts a sustained vowel
    into many rests. Hysteresis keeps connected voiced phrases; short gaps
    are filled only when there is energy and both sides agree in pitch.
    """
    from scipy.ndimage import label, median_filter

    pitch, confidence = (
        np.asarray(pitch, float).copy(),
        np.asarray(confidence, float).copy(),
    )
    low = min(0.18, config.confidence_threshold * 0.5)
    eligible = (
        np.isfinite(pitch)
        & np.isfinite(confidence)
        & (pitch >= config.min_frequency_hz * 0.99)
        & (pitch <= config.max_frequency_hz * 1.01)
        & (confidence >= low)
        & (rms >= config.energy_threshold / 2)
    )
    regions, _ = label(eligible)
    keep = np.zeros(len(pitch), dtype=bool)
    for region in range(1, int(regions.max(initial=0)) + 1):
        indices = np.flatnonzero(regions == region)
        if np.max(confidence[indices]) >= config.confidence_threshold:
            keep[indices] = True
    indices = np.flatnonzero(keep)
    max_gap = round(0.08 * config.sample_rate / config.hop_size)
    for first, last in zip(indices, indices[1:]):
        if 1 < last - first <= max_gap + 1:
            distance = abs(12 * math.log2(pitch[first] / pitch[last]))
            if distance <= 1.5 and np.all(
                rms[first + 1 : last] >= config.energy_threshold / 4
            ):
                pitch[first : last + 1] = np.exp(
                    np.linspace(
                        np.log(pitch[first]), np.log(pitch[last]), last - first + 1
                    )
                )
                keep[first : last + 1] = True
    regions, _ = label(keep)
    for region in range(1, int(regions.max(initial=0)) + 1):
        indices = np.flatnonzero(regions == region)
        # A median in logarithmic pitch removes hop-sized jitter without
        # averaging two real notes into an out-of-tune intermediate pitch.
        pitch[indices] = np.exp(
            median_filter(np.log(pitch[indices]), size=3, mode="nearest")
        )
    return [
        PitchFrame(
            float(pitch[i]) if keep[i] else 0, float(confidence[i]), float(rms[i])
        )
        for i in range(len(pitch))
    ]


def neural_frames(
    samples: np.ndarray,
    config: AnalysisConfig,
    progress: Progress | None = None,
    device: str = "auto",
    source_name: str = "人声",
) -> list[PitchFrame]:
    count = math.ceil(len(samples) / config.hop_size)
    if not count:
        return []
    rms = _rms_grid(samples, round(config.sample_rate * 0.064), config.hop_size, count)
    if np.max(rms, initial=0) < config.energy_threshold / 2:
        return [PitchFrame(0, 0, float(r)) for r in rms]
    if not 50 <= config.min_frequency_hz < config.max_frequency_hz <= 2006:
        raise ValueError(
            "神经音高识别的频率范围应在 50–2006 Hz 内；更大范围可用 --song-backend pyin"
        )
    report = progress or (lambda _: None)
    signature = hashlib.sha256(
        samples.astype(np.float32).tobytes()
        + f"crepe-full:0.0.24:{config.sample_rate}:{config.hop_size}:{float(config.min_frequency_hz):.6g}:{float(config.max_frequency_hz):.6g}".encode()
    ).hexdigest()
    cache = (
        Path(__file__).resolve().parent.parent
        / ".models"
        / "pitch-cache"
        / (signature + ".npz")
    )
    if cache.is_file():
        with np.load(cache, allow_pickle=False) as saved:
            pitch, confidence = saved["pitch"], saved["confidence"]
        report(f"复用{source_name}音高识别结果。")
    else:
        try:
            import torch
            import torchcrepe
        except ImportError as exc:
            raise SongAnalysisError(
                "缺少新版音高识别依赖，请运行 setup.bat 安装。"
            ) from exc
        selected = (
            ("cuda" if torch.cuda.is_available() else "cpu")
            if device == "auto"
            else device
        )
        report(f"识别{source_name}旋律；正在保留连续音高与演唱节奏。")
        audio = torch.from_numpy(np.asarray(samples, dtype=np.float32)).unsqueeze(0)
        try:
            pitch, confidence = torchcrepe.predict(
                audio,
                config.sample_rate,
                config.hop_size,
                config.min_frequency_hz,
                config.max_frequency_hz,
                "full",
                batch_size=512 if selected == "cuda" else 128,
                device=selected,
                return_periodicity=True,
            )
        except Exception as exc:
            raise SongAnalysisError(f"{source_name}音高识别失败: {exc}") from exc
        pitch, confidence = (
            pitch[0].cpu().numpy()[:count],
            confidence[0].cpu().numpy()[:count],
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, pitch=pitch, confidence=confidence)
    if len(pitch) != count or len(confidence) != count:
        raise SongAnalysisError(
            "缓存的音高时间线与原曲长度不一致，请清除 .models/pitch-cache 后重试。"
        )
    return refine_neural_pitch(pitch, confidence, rms, config)


def vocal_frames(
    samples: np.ndarray,
    config: AnalysisConfig,
    progress: Progress | None = None,
    device: str = "auto",
) -> list[PitchFrame]:
    if config.song_backend == "crepe":
        return neural_frames(samples, config, progress, device)
    return pyin_frames(samples, config, progress)


def _vocal_regions(mask: np.ndarray, hop_seconds: float) -> np.ndarray:
    """Preserve breaths and short rests inside sung phrases.

    Instruments take over only during sustained absence of singing, avoiding
    a jump to accompaniment on every consonant or breath.
    """
    indices = np.flatnonzero(mask)
    protected = mask.copy()
    if not len(indices):
        return protected
    bridge = round(0.6 / hop_seconds)
    margin = round(0.12 / hop_seconds)
    for first, last in zip(indices, indices[1:]):
        if last - first <= bridge:
            protected[first : last + 1] = True
    for index in indices:
        protected[max(0, index - margin) : min(len(mask), index + margin + 1)] = True
    return protected


def clear_note_attack(envelope: np.ndarray, index: int, step_seconds: float) -> bool:
    """Require a real energy dip and recovery before repeating a held pitch.

    Spectral flux also responds to vibrato and changing vowel formants. Those
    changes alone should not repeatedly interrupt a sustained buzzer tone.
    """

    def window(first: float, last: float) -> np.ndarray:
        start = max(0, index + round(first / step_seconds))
        stop = min(len(envelope), index + round(last / step_seconds))
        return envelope[start:stop]

    before = window(-0.12, -0.025)
    valley = window(-0.02, 0.035)
    after = window(0.035, 0.12)
    if min(len(before), len(valley), len(after)) == 0:
        return False
    level_before = float(np.median(before))
    level_after = float(np.median(after))
    bottom = float(np.min(valley))
    return bottom < 0.55 * max(level_before, level_after) and level_after > 1.8 * max(
        bottom, 1e-8
    )


def analyze_stems(
    vocals: np.ndarray,
    instruments: np.ndarray,
    config: AnalysisConfig,
    progress: Progress | None = None,
    device: str = "auto",
) -> SongAnalysis:
    """Prefer reliable singing; select a continuous instrument lead elsewhere."""
    config.validate()
    if len(vocals) != len(instruments):
        raise ValueError("人声与乐器轨必须具有相同长度")
    count = math.ceil(len(vocals) / config.hop_size)
    if not count:
        return SongAnalysis([], set(), [], 0, config.sample_rate)
    instrument_config = replace(
        config, pitch_mode="tracked", frame_ms=max(80, config.frame_ms)
    )
    size = instrument_config.frame_size
    vocal_rms = _rms_grid(vocals, size, config.hop_size, count)
    instrument_rms = _rms_grid(instruments, size, config.hop_size, count)
    hop = config.hop_size / config.sample_rate
    details = {"requested_content_mode": config.content_mode}
    aliases = 0
    if config.content_mode == "instrumental":
        voices = [PitchFrame(0, 0, 0) for _ in range(count)]
        valid = np.zeros(count, dtype=bool)
        detected = "instrumental"
    else:
        voices = vocal_frames(vocals, config, progress, device)
        valid = confirmed_vocals(voices, vocal_rms, instrument_rms, config)
        inferred, evidence = detect_content_mode(valid, hop)
        details.update(evidence)
        detected = inferred if config.content_mode == "auto" else "vocal"
        if detected == "vocal":
            if not np.any(valid):
                raise SongAnalysisError(
                    "未找到可靠主唱。请检查音频，或手动选择自动／纯音乐模式。"
                )
            voices, aliases = suppress_brief_octave_alias(voices, vocals, config)
    details.update(
        detected_content_mode=detected, corrected_vocal_octave_aliases=aliases
    )
    if detected == "vocal":
        valid, recovered = recover_weak_vocal_hold(
            valid, voices, vocal_rms, instrument_rms, config
        )
        details["recovered_weak_vocal_ms"] = round(recovered * hop * 1000)
        protected = singing_section_mask(valid, hop)
        lead_signal = instruments
        if progress:
            progress(
                "按演唱段锁定主唱，换气与弱音处保留休止；只在独立前奏、间奏和尾奏跟踪乐器。"
            )
        details.update(
            vocal_section_bridge_ms=3500,
            protected_vocal_seconds=round(float(np.sum(protected) * hop), 3),
        )
    else:
        valid[:] = False
        protected = np.zeros(count, dtype=bool)
        # Instrument solos can leak into Demucs' vocal stem. Recombine those
        # two aligned stems instead of throwing a solo away as if it were singing.
        lead_signal = np.asarray(instruments + vocals, dtype=np.float32)
        if progress:
            progress("全曲按纯音乐主奏旋律跟踪；沿连续音符路径选择实际频谱峰。")
    selected = ~protected
    music = continuous_instrument_lead(
        lead_signal, replace(config, content_mode=detected), selected, progress, device
    )
    details.update(
        instrument_tracking_method="continuous_observed_peak_path",
        instrument_selected_seconds=round(float(np.sum(selected) * hop), 3),
    )
    frames, sources = [], []
    for i in range(count):
        if valid[i]:
            frames.append(voices[i])
            sources.append("vocals")
        elif protected[i]:
            frames.append(PitchFrame(0, 0, float(vocal_rms[i])))
            sources.append("rest")
        else:
            frames.append(music[i])
            sources.append("instruments" if music[i].frequency_hz > 0 else "rest")
    # Detect attacks in each selected source rather than the drum-filled mix.
    librosa = _librosa()
    onsets = set()
    for signal, name in ((vocals, "vocals"), (lead_signal, "instruments")):
        envelope_hop = max(1, round(config.sample_rate * 0.005))
        envelope = _rms_grid(
            signal,
            max(2, round(config.sample_rate * 0.01)),
            envelope_hop,
            math.ceil(len(signal) / envelope_hop),
        )
        detected = librosa.onset.onset_detect(
            y=signal,
            sr=config.sample_rate,
            hop_length=config.hop_size,
            backtrack=True,
            units="frames",
        )
        previous = -round(0.08 * config.sample_rate / config.hop_size)
        for detected_index in detected:
            detected_index = int(detected_index)
            envelope_index = round(detected_index * config.hop_size / envelope_hop)
            if not clear_note_attack(
                envelope, envelope_index, envelope_hop / config.sample_rate
            ):
                continue
            # Backtracking can place the attack just before voiced pitch
            # resumes. Snap to its first valid source frame within 60 ms.
            index = next(
                (
                    i
                    for i in range(
                        detected_index,
                        min(
                            count,
                            detected_index
                            + 1
                            + math.ceil(0.06 * config.sample_rate / config.hop_size),
                        ),
                    )
                    if i > 0 and sources[i] == name and frames[i].frequency_hz > 0
                ),
                None,
            )
            if (
                index is not None
                and (index - previous) * config.hop_size / config.sample_rate >= 0.12
            ):
                onsets.add(index)
                previous = index
    if progress:
        progress("主唱旋律与前奏、间奏、尾奏已连接；正在生成音符和试听。")
    analysis = SongAnalysis(
        frames, onsets, sources, len(vocals), config.sample_rate, details
    )
    return repair_phrase_gaps(analysis, vocals, lead_signal, config)
