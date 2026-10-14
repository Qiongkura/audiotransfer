"""High-level orchestration from an input file to all output artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

import numpy as np

from .analysis import AnalysisConfig, analyze_samples, analyze_samples_tracked
from .audio import decode_with_ffmpeg
from .events import NoteEvent, NoteFrame, merge_note_frames, scale_note_events
from .notes import (
    quantize_frequency,
    frequency_to_midi,
    midi_to_frequency,
    midi_to_note_name,
)
from .output import OutputPaths, write_outputs
from .song import Progress, SongAnalysis, analyze_stems, separate_file, vocal_frames


class PlatformIOUploadError(RuntimeError):
    """Raised when PlatformIO cannot be started or upload exits unsuccessfully."""


@dataclass(frozen=True)
class PlatformIOUploadResult:
    """Captured result from a successful PlatformIO upload command."""

    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        """Return combined command output for CLI/GUI logging."""

        return "\n".join(
            part for part in (self.stdout.strip(), self.stderr.strip()) if part
        )


def upload_platformio(
    output_dir: str | Path,
    pio_command: str | Path = "pio",
) -> PlatformIOUploadResult:
    """Upload a generated project through PlatformIO and ST-Link.

    The generated ``platformio.ini`` carries the selected ``upload_protocol``.
    Keeping the command protocol-agnostic here lets users change that setting
    in the generated project while retaining the required ``pio run -t upload``
    flow.
    """

    output_path = Path(output_dir)
    if not output_path.is_dir():
        raise PlatformIOUploadError(f"PlatformIO 工程目录不存在: {output_path}")

    command = [
        str(pio_command),
        "run",
        "-d",
        str(output_path),
        "-t",
        "upload",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise PlatformIOUploadError(
            f"无法启动 PlatformIO 命令 {pio_command!r}: {exc}。"
            "请安装 PlatformIO，或通过 --pio 指定 pio 可执行文件。"
        ) from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    result = PlatformIOUploadResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if completed.returncode != 0:
        detail = result.output or f"PlatformIO 退出码 {completed.returncode}"
        raise PlatformIOUploadError(
            f"PlatformIO 上传失败（退出码 {completed.returncode}）: {detail}"
        )
    return result


def events_from_samples(
    samples: np.ndarray,
    config: AnalysisConfig | None = None,
    min_note_ms: int = 40,
    max_notes: int | None = None,
    speed: float = 1.0,
) -> list[NoteEvent]:
    """Analyze samples, quantize frames, merge them, and scale playback speed."""

    config = config or AnalysisConfig()
    if config.pitch_mode == "song":
        if config.content_mode == "instrumental":
            analysis = analyze_stems(np.zeros_like(samples), samples, config)
        else:
            pitch_frames = vocal_frames(samples, config)
            analysis = SongAnalysis(
                pitch_frames,
                set(),
                ["vocals"] * len(pitch_frames),
                len(samples),
                config.sample_rate,
            )
        events = events_from_song(analysis, config, min_note_ms)
        if max_notes is not None:
            events = events[:max_notes]
        return scale_note_events(events, speed)
    elif config.pitch_mode == "tracked":
        pitch_frames = analyze_samples_tracked(samples, config)
    else:
        pitch_frames = analyze_samples(samples, config)
    note_frames = [
        NoteFrame(
            note=quantized.note,
            frequency_hz=quantized.frequency_hz,
            confidence=quantized.confidence,
        )
        for pitch in pitch_frames
        for quantized in [
            quantize_frequency(
                pitch.frequency_hz,
                confidence=pitch.confidence,
                confidence_threshold=config.confidence_threshold,
            )
        ]
    ]
    events = merge_note_frames(
        note_frames,
        hop_ms=config.hop_size * 1000 / config.sample_rate,
        min_note_ms=min_note_ms,
        max_notes=None,
    )
    events = fit_event_duration(events, round(len(samples) * 1000 / config.sample_rate))
    if max_notes is not None:
        events = events[:max_notes]
    return scale_note_events(events, speed)


def fit_event_duration(events: list[NoteEvent], duration_ms: int) -> list[NoteEvent]:
    """Fit the last hop to the exact decoded duration, without stretching notes."""
    result = list(events)
    if not result:
        return [NoteEvent(0, duration_ms, "rest")] if duration_ms > 0 else []
    delta = duration_ms - sum(e.duration_ms for e in result)
    if delta > 0:
        last = result[-1]
        result[-1] = NoteEvent(
            last.frequency_hz, last.duration_ms + delta, last.note, last.legato
        )
    while delta < 0 and result:
        last = result.pop()
        remaining = last.duration_ms + delta
        if remaining > 0:
            result.append(
                NoteEvent(last.frequency_hz, remaining, last.note, last.legato)
            )
            break
        delta = remaining
    return result


def stable_song_notes(
    analysis: SongAnalysis, config: AnalysisConfig, min_note_ms: int
) -> list[NoteFrame]:
    """Use a local pitch centre and persistence, without a latched semitone."""
    import math
    from scipy.ndimage import median_filter

    hop_ms = config.hop_size * 1000 / config.sample_rate
    confirmation = max(2, math.ceil(min(80, max(40, min_note_ms)) / hop_ms))
    labels: list[int | None] = []
    current = None
    pending = None
    pending_start = 0
    continuous_pitches = np.array(
        [
            (
                69 + 12 * math.log2(p.frequency_hz / 440)
                if math.isfinite(p.frequency_hz) and p.frequency_hz > 0
                else np.nan
            )
            for p in analysis.frames
        ]
    )
    # Smooth each voiced region separately. A 100-ms median suppresses
    # oscillating vibrato; persistence accepts a sustained 0.6-semitone
    # change. The former 0.8 threshold could hold the wrong note forever.
    voiced = np.isfinite(continuous_pitches)
    edges = np.flatnonzero(np.diff(np.r_[False, voiced, False]))
    window = max(3, round(100 / hop_ms) | 1)
    for start, stop in zip(edges[::2], edges[1::2]):
        continuous_pitches[start:stop] = median_filter(
            continuous_pitches[start:stop], size=window, mode="nearest"
        )
    for index, pitch in enumerate(analysis.frames):
        frequency = pitch.frequency_hz
        if not math.isfinite(frequency) or frequency <= 0:
            labels.append(None)
            current = pending = None
            continue
        continuous = continuous_pitches[index]
        nearest = max(0, min(127, round(continuous)))
        if current is None:
            current = nearest
        labels.append(current)
        if nearest != current:
            if pending != nearest:
                pending, pending_start = nearest, index
            if index - pending_start + 1 >= confirmation:
                current = nearest
                labels[pending_start : index + 1] = [current] * (
                    index - pending_start + 1
                )
                pending = None
        else:
            pending = None
    # Follow the observed vocal notes as a sequence. Confidence alone does not
    # establish that a fleeting pitch is a separate sung note.
    mask = np.array(
        [
            midi is not None
            and (not analysis.sources or analysis.sources[i] == "vocals")
            for i, midi in enumerate(labels)
        ]
    )
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    changed = 0
    for start, stop in zip(edges[::2], edges[1::2]):
        observed = np.array(labels[start:stop], dtype=int)
        confidence = np.array([f.confidence for f in analysis.frames[start:stop]])
        attacks = {i - start for i in analysis.onsets if start <= i < stop}
        decoded = _decode_vocal_note_path(observed, confidence, attacks, hop_ms)
        changed += int(np.count_nonzero(decoded != observed))
        labels[start:stop] = decoded.tolist()
    if np.any(mask):
        analysis.details.update(
            vocal_note_decoder="confidence_weighted_observed_note_path",
            vocal_note_context_ms=200,
            regularized_vocal_ms=round(changed * hop_ms),
        )
    return [
        (
            NoteFrame("rest", 0, 0)
            if midi is None
            else NoteFrame(
                midi_to_note_name(midi),
                midi_to_frequency(midi),
                analysis.frames[index].confidence,
            )
        )
        for index, midi in enumerate(labels)
    ]


def _decode_vocal_note_path(
    observed: np.ndarray, confidence: np.ndarray, attacks: set[int], hop_ms: float
) -> np.ndarray:
    """Choose a continuous sung-note path without proposing new pitches.

    Weak short islands should not outweigh their reliable surrounding melody.
    Strong short notes and clear attacks can pay the change cost. Candidate
    notes must be observed within 200 ms, and callers split at rests and source
    boundaries; the decoder cannot borrow accompaniment or fill real silence.
    """
    from scipy.ndimage import maximum_filter1d

    states = np.unique(observed)
    if len(states) <= 1:
        return observed.copy()
    distance = np.abs(states[:, None] - states[None, :])
    transition = 1.7 + 0.1 * np.minimum(distance, 12)
    np.fill_diagonal(transition, 0.0)
    weights = np.clip(np.nan_to_num(confidence, nan=0), 0, 1) ** 3
    emission = (
        np.minimum((observed[:, None] - states[None, :]) ** 2, 3.0)
        * weights[:, None]
        * hop_ms
        / 20
    )
    context = max(1, round(200 / hop_ms))
    available = maximum_filter1d(
        observed[:, None] == states[None, :],
        size=2 * context + 1,
        axis=0,
        mode="constant",
        cval=0,
    )
    emission[~available] = np.inf
    attacked = np.zeros(len(observed), dtype=bool)
    margin = max(1, round(40 / hop_ms))
    for index in attacks:
        attacked[max(0, index - margin) : min(len(observed), index + margin + 1)] = True
    score = emission[0]
    back = np.zeros((len(observed), len(states)), dtype=np.uint8)
    for index in range(1, len(observed)):
        options = score[:, None] + transition * (0.35 if attacked[index] else 1.0)
        back[index] = options.argmin(axis=0)
        score = options.min(axis=0) + emission[index]
    state = int(score.argmin())
    path = np.empty_like(observed)
    for index in range(len(observed) - 1, -1, -1):
        path[index] = states[state]
        state = int(back[index, state])
    return path


def events_from_song(
    analysis: SongAnalysis, config: AnalysisConfig, min_note_ms: int
) -> list[NoteEvent]:
    # Voicing has already been decided by the backend. Do not reapply its
    # anchor threshold when converting weak portions of a reliable note.
    frames = stable_song_notes(analysis, config, min_note_ms)
    hop_ms = config.hop_size * 1000 / config.sample_rate
    events = merge_note_frames(frames, hop_ms, min_note_ms, None)
    events = fit_event_duration(
        events, round(analysis.sample_count * 1000 / analysis.sample_rate)
    )
    # First smooth the continuous contour, then re-articulate repeated notes.
    # Splitting at every attack before smoothing used to leave 40 ms fragments
    # stranded at segment boundaries.
    minimum = max(80, min_note_ms)
    attacks = sorted({round(index * hop_ms) for index in analysis.onsets})
    # Simplify an unarticulated, brief semitone dip returning to a held note.
    # Actual attacked short notes and wider melodic turns remain untouched.
    removed_returns = 0
    index = 1
    while index < len(events) - 1:
        left, middle, right = events[index - 1 : index + 2]
        start = sum(e.duration_ms for e in events[:index])
        end = start + middle.duration_ms
        attacked = any(abs(t - start) <= 40 or abs(t - end) <= 40 for t in attacks)
        before = max(0, min(len(analysis.sources) - 1, round(start / hop_ms) - 1))
        after = max(0, min(len(analysis.sources) - 1, round(end / hop_ms)))
        same_source = (
            bool(analysis.sources)
            and analysis.sources[before] == analysis.sources[after] != "rest"
        )
        if (
            left.frequency_hz > 0
            and middle.frequency_hz > 0
            and right.frequency_hz > 0
            and left.note == right.note
            and left.note != middle.note
            and abs(
                frequency_to_midi(left.frequency_hz)
                - frequency_to_midi(middle.frequency_hz)
            )
            == 1
            and middle.duration_ms <= 160
            and left.duration_ms + right.duration_ms >= 400
            and not attacked
            and same_source
        ):
            events[index - 1 : index + 2] = [
                NoteEvent(
                    left.frequency_hz,
                    left.duration_ms + middle.duration_ms + right.duration_ms,
                    left.note,
                )
            ]
            removed_returns += 1
            index = max(1, index - 1)
        else:
            index += 1
    analysis.details["removed_pitch_returns"] = removed_returns
    result = []
    start = 0
    for event in events:
        end = start + event.duration_ms
        cursor = start
        if event.frequency_hz > 0:
            for attack in attacks:
                if cursor + minimum <= attack <= end - minimum:
                    result.append(
                        NoteEvent(event.frequency_hz, attack - cursor, event.note)
                    )
                    cursor = attack
        result.append(NoteEvent(event.frequency_hz, end - cursor, event.note))
        start = end
    return result


def process_file(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    config: AnalysisConfig | None = None,
    min_note_ms: int = 80,
    max_notes: int | None = None,
    speed: float = 1.0,
    pin: int = 8,
    gap_ms: int = 6,
    ffmpeg: str = "ffmpeg",
    target: str = "arduino",
    board: str = "bluepill_f103c8",
    stm32_pin: str | int = "PA8",
    upload_protocol: str = "stlink",
    upload: bool = False,
    pio: str | Path = "pio",
    device: str = "auto",
    progress: Progress | None = None,
    arrangement: str = "score",
    transpose: int | None = None,
    sound_mode: str = "auto",
    score_profile: str | Path | None = None,
    active_low: bool = False,
    separation_cache_dir: str | Path | None = None,
) -> OutputPaths:
    """Decode, analyze, and write all artifacts for one audio file.

    ``upload=True`` is a convenience for callers outside the CLI.  It invokes
    PlatformIO only for the ``platformio-stm32`` target; Arduino output never
    triggers an upload command.
    """

    config = config or AnalysisConfig(pitch_mode="song")
    config.validate()
    if score_profile is not None and (
        config.pitch_mode != "song" or arrangement != "score"
    ):
        raise ValueError("已校对乐谱只用于完整歌曲的score编排模式")
    if arrangement not in ("score", "raw"):
        raise ValueError("arrangement 必须是 score 或 raw")
    if sound_mode == "auto":
        sound_mode = "soft" if target == "platformio-stm32" else "classic"
    if sound_mode not in ("classic", "soft", "harmonic", "pulse") or (
        (sound_mode != "classic" or active_low) and target != "platformio-stm32"
    ):
        raise ValueError(
            "模块音色及低电平控制只用于STM32；可选auto、classic、soft、harmonic、pulse"
        )
    velocities = None
    arrangement_metadata = None
    if max_notes is not None and max_notes <= 0:
        raise ValueError("max_notes 必须为正数或 None")
    if config.pitch_mode == "song":
        vocals, instruments = separate_file(
            input_path,
            config,
            separation_cache_dir or output_dir,
            ffmpeg,
            device,
            progress,
        )
        analysis = analyze_stems(vocals, instruments, config, progress, device)
        events = events_from_song(analysis, config, min_note_ms)
        if arrangement == "score":
            from .arrange import arrange_events, detect_beats, measure_beat_strengths

            if progress:
                progress("正在整理节拍、合并滑音碎片和设置乐句力度。")
            original = decode_with_ffmpeg(input_path, config.sample_rate, ffmpeg)
            beats = detect_beats(original.samples, config.sample_rate)
            arranged = arrange_events(
                events,
                beats,
                onsets_ms={
                    round(i * config.hop_size * 1000 / config.sample_rate)
                    for i in analysis.onsets
                },
                transpose=transpose,
                analysis=analysis,
                beat_strengths=measure_beat_strengths(
                    original.samples, config.sample_rate, beats
                ),
                phrasing=True,
            )
            events, velocities, arrangement_metadata = (
                arranged.events,
                arranged.velocities,
                arranged.metadata,
            )
            if progress:
                progress(
                    f"编排完成：移调 {arranged.metadata['transpose_semitones']:+d} 半音，合并 {arranged.metadata['removed_slide_steps']} 个滑音碎片。"
                )
            if score_profile is not None:
                import json
                from .scored_song import build

                profile = json.loads(Path(score_profile).read_text(encoding="utf-8"))
                profile["baseline_transpose"] = arrangement_metadata[
                    "transpose_semitones"
                ]
                profile["hop_seconds"] = config.hop_size / config.sample_rate
                baseline = [
                    {**e.as_dict(), "velocity": v} for e, v in zip(events, velocities)
                ]
                pitch_analysis = {
                    "pitch": np.array([f.frequency_hz for f in analysis.frames]),
                    "confidence": np.array([f.confidence for f in analysis.frames]),
                    "sources": np.array(analysis.sources),
                    "sample_count": analysis.sample_count,
                    "sample_rate": analysis.sample_rate,
                }
                events, velocities, arrangement_metadata = build(
                    profile,
                    baseline,
                    pitch_analysis,
                    0 if transpose is None else transpose,
                )
                if progress:
                    progress("已用这首歌的校对乐谱还原音符和节奏，并与原曲对齐。")
    else:
        decoded = decode_with_ffmpeg(input_path, config.sample_rate, ffmpeg)
        events = events_from_samples(decoded.samples, config, min_note_ms, None)
    if max_notes is not None and len(events) > max_notes:
        if progress:
            progress(
                f"警告：--max-notes 将 {len(events)} 个事件截断为 {max_notes} 个，输出不再包含全曲。"
            )
        events = events[:max_notes]
        if velocities is not None:
            velocities = velocities[:max_notes]
    events = scale_note_events(events, speed)
    if arrangement_metadata:
        if arrangement_metadata["tempo_bpm"]:
            arrangement_metadata["tempo_bpm"] = round(
                arrangement_metadata["tempo_bpm"] * speed, 3
            )
        arrangement_metadata["duration_ms"] = sum(e.duration_ms for e in events)
    paths = write_outputs(
        events,
        output_dir=output_dir,
        pin=pin,
        gap_ms=gap_ms,
        target=target,
        board=board,
        stm32_pin=stm32_pin,
        upload_protocol=upload_protocol,
        sound_mode=sound_mode,
        velocities=velocities,
        arrangement=arrangement_metadata,
        active_low=active_low,
    )
    if config.pitch_mode == "song":
        import json

        spans = []
        hop_ms = config.hop_size * 1000 / config.sample_rate
        total_ms = round(analysis.sample_count * 1000 / config.sample_rate)
        for i, source in enumerate(analysis.sources):
            end = min(total_ms, round((i + 1) * hop_ms))
            if spans and spans[-1]["source"] == source:
                spans[-1]["end_ms"] = end
            else:
                spans.append(
                    {"source": source, "start_ms": round(i * hop_ms), "end_ms": end}
                )
        diagnostic = {
            **analysis.details,
            "timeline": "input_ms_before_speed",
            "duration_ms": total_ms,
            "regions": spans,
        }
        (Path(output_dir) / "source-regions.json").write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if upload and target == "platformio-stm32":
        upload_platformio(output_dir, pio_command=pio)
    return paths
