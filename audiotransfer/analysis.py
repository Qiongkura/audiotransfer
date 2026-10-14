"""Frame-based monophonic fundamental-frequency estimation."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class PitchFrame:
    """One analysis frame before note quantization."""

    frequency_hz: float
    confidence: float
    rms: float


@dataclass(frozen=True)
class PitchCandidate:
    """One possible fundamental frequency for an analysis frame."""

    frequency_hz: float
    confidence: float
    rms: float
    score: float


@dataclass(frozen=True)
class CandidateFrame:
    """All candidate pitches found in one analysis frame."""

    candidates: tuple[PitchCandidate, ...]
    rms: float


@dataclass(frozen=True)
class AnalysisConfig:
    """Settings for frame construction and autocorrelation pitch detection."""

    sample_rate: int = 16_000
    frame_ms: int = 40
    hop_ms: int = 20
    min_frequency_hz: float = 55.0
    max_frequency_hz: float = 1_000.0
    energy_threshold: float = 0.01
    confidence_threshold: float = 0.35
    octave_fold_tolerance_semitones: float = 1.0
    pitch_mode: str = "tracked"
    candidate_count: int = 5
    tracking_min_frequency_hz: float = 140.0
    tracking_continuity_weight: float = 0.10
    tracking_octave_penalty: float = 0.80
    tracking_rest_penalty: float = 2.0
    song_backend: str = "rmvpe"
    content_mode: str = "auto"

    @property
    def frame_size(self) -> int:
        return max(2, round(self.sample_rate * self.frame_ms / 1000.0))

    @property
    def hop_size(self) -> int:
        return max(1, round(self.sample_rate * self.hop_ms / 1000.0))

    def validate(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate 必须为正数")
        if self.frame_ms <= 0 or self.hop_ms <= 0:
            raise ValueError("frame_ms 和 hop_ms 必须为正数")
        if not math.isfinite(self.min_frequency_hz) or self.min_frequency_hz <= 0:
            raise ValueError("min_frequency_hz 必须为正数")
        if (
            not math.isfinite(self.max_frequency_hz)
            or self.max_frequency_hz <= self.min_frequency_hz
        ):
            raise ValueError("max_frequency_hz 必须大于 min_frequency_hz")
        if not math.isfinite(self.energy_threshold) or self.energy_threshold < 0:
            raise ValueError("energy_threshold 不能为负数")
        if not 0 <= self.confidence_threshold <= 1:
            raise ValueError("confidence_threshold 应在 0 到 1 之间")
        if (
            not math.isfinite(self.octave_fold_tolerance_semitones)
            or self.octave_fold_tolerance_semitones <= 0
        ):
            raise ValueError("octave_fold_tolerance_semitones 必须为正数")
        if self.pitch_mode not in {"legacy", "tracked", "song"}:
            raise ValueError("pitch_mode 必须是 song、legacy 或 tracked")
        if self.song_backend not in {"rmvpe", "crepe", "pyin"}:
            raise ValueError("song_backend 必须是 rmvpe、crepe 或 pyin")
        if self.content_mode not in {"auto", "vocal", "instrumental"}:
            raise ValueError("content_mode 必须是 auto、vocal 或 instrumental")
        if self.candidate_count <= 0:
            raise ValueError("candidate_count 必须为正数")
        if (
            not math.isfinite(self.tracking_min_frequency_hz)
            or self.tracking_min_frequency_hz <= 0
        ):
            raise ValueError("tracking_min_frequency_hz 必须为正数")
        for name, value in (
            ("tracking_continuity_weight", self.tracking_continuity_weight),
            ("tracking_octave_penalty", self.tracking_octave_penalty),
            ("tracking_rest_penalty", self.tracking_rest_penalty),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} 必须为非负有限数")


def _parabolic_peak(values: np.ndarray, index: int) -> float:
    """Interpolate the position of a local maximum by one sample."""

    if index <= 0 or index >= len(values) - 1:
        return float(index)
    left, center, right = map(float, values[index - 1 : index + 2])
    denominator = left - 2.0 * center + right
    if abs(denominator) < 1e-12:
        return float(index)
    offset = 0.5 * (left - right) / denominator
    return float(index) + max(-0.5, min(0.5, offset))


def _autocorrelation_local_peaks(
    autocorrelation: np.ndarray,
    min_lag: int,
    max_lag: int,
) -> np.ndarray:
    """Return only strict/interior local maxima in the requested lag range.

    The old implementation selected the largest value in the whole lag range.
    A monotonically falling autocorrelation therefore promoted ``min_lag`` to a
    pitch, and parabolic interpolation could turn that boundary into a very
    high note. Neighbors must exist in the array, including outside the
    frequency interval, so valid endpoint peaks remain eligible.
    """

    first = max(1, min_lag)
    last = min(len(autocorrelation) - 2, max_lag)
    if first > last:
        return np.empty(0, dtype=np.intp)
    indices = np.arange(first, last + 1, dtype=np.intp)
    left = autocorrelation[indices - 1]
    center = autocorrelation[indices]
    right = autocorrelation[indices + 1]
    finite = np.isfinite(left) & np.isfinite(center) & np.isfinite(right)
    # Permit a flat side of a sampled peak, but reject a completely flat
    # plateau; at least one side must be strictly lower.
    local_maximum = (
        (center >= left) & (center >= right) & ((center > left) | (center > right))
    )
    return indices[finite & local_maximum]


def estimate_pitch(
    frame: np.ndarray,
    sample_rate: int,
    min_frequency_hz: float = 55.0,
    max_frequency_hz: float = 1_000.0,
    energy_threshold: float = 0.01,
) -> PitchFrame:
    """Estimate a single dominant f0 using windowed normalized autocorrelation.

    A frequency of 0 indicates silence or an unusable/low-energy frame.  The
    confidence is the normalized autocorrelation peak, not a probability.
    """

    values = np.asarray(frame, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return PitchFrame(0.0, 0.0, 0.0)
    values = values - float(np.mean(values))
    window = np.hanning(values.size)
    windowed = values * window
    rms = float(np.sqrt(np.mean(windowed * windowed)))
    if rms < energy_threshold:
        return PitchFrame(0.0, 0.0, rms)

    # Full autocorrelation is efficient enough for the default 640-sample frame
    # and avoids requiring an additional signal-processing dependency.
    autocorrelation = np.correlate(windowed, windowed, mode="full")[values.size - 1 :]
    zero_lag = float(autocorrelation[0])
    if zero_lag <= 1e-12:
        return PitchFrame(0.0, 0.0, rms)
    autocorrelation = autocorrelation / zero_lag

    min_lag = max(1, int(math.floor(sample_rate / max_frequency_hz)))
    max_lag = min(values.size - 2, int(math.ceil(sample_rate / min_frequency_hz)))
    if max_lag <= min_lag:
        return PitchFrame(0.0, 0.0, rms)

    peak_indices = _autocorrelation_local_peaks(autocorrelation, min_lag, max_lag)
    if peak_indices.size == 0:
        return PitchFrame(0.0, 0.0, rms)
    peak_index = int(peak_indices[np.argmax(autocorrelation[peak_indices])])
    peak_position = _parabolic_peak(autocorrelation, peak_index)
    peak_value = float(
        np.interp(peak_position, np.arange(len(autocorrelation)), autocorrelation)
    )
    if peak_position <= 0 or peak_value <= 0:
        return PitchFrame(0.0, 0.0, rms)
    frequency = float(sample_rate / peak_position)
    # Windowing/interpolation can move an exact boundary tone by a fraction
    # of a percent. Clamp that estimation error instead of losing its f0.
    if min_frequency_hz * 0.995 <= frequency <= max_frequency_hz * 1.005:
        frequency = min(max_frequency_hz, max(min_frequency_hz, frequency))
    if not min_frequency_hz <= frequency <= max_frequency_hz:
        return PitchFrame(0.0, max(0.0, min(1.0, peak_value)), rms)
    return PitchFrame(frequency, max(0.0, min(1.0, peak_value)), rms)


def estimate_pitch_candidates(
    frame: np.ndarray,
    sample_rate: int,
    min_frequency_hz: float = 55.0,
    max_frequency_hz: float = 1_000.0,
    energy_threshold: float = 0.01,
    candidate_count: int = 5,
    tracking_min_frequency_hz: float = 140.0,
) -> tuple[PitchCandidate, ...]:
    """Return several plausible f0 candidates for one frame.

    The legacy estimator keeps only the strongest autocorrelation peak.  For a
    mixed song that peak is often a bass or chord component.  This function
    keeps several local peaks so the offline tracker can choose one continuous
    melody path later.
    """

    if candidate_count <= 0:
        raise ValueError("candidate_count 必须为正数")
    if tracking_min_frequency_hz <= 0:
        raise ValueError("tracking_min_frequency_hz 必须为正数")
    values = np.asarray(frame, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return ()
    values = values - float(np.mean(values))
    windowed = values * np.hanning(values.size)
    rms = float(np.sqrt(np.mean(windowed * windowed)))
    if rms < energy_threshold:
        return ()

    autocorrelation = np.correlate(windowed, windowed, mode="full")[values.size - 1 :]
    zero_lag = float(autocorrelation[0])
    if zero_lag <= 1e-12:
        return ()
    autocorrelation = autocorrelation / zero_lag
    min_lag = max(1, int(math.floor(sample_rate / max_frequency_hz)))
    max_lag = min(values.size - 2, int(math.ceil(sample_rate / min_frequency_hz)))
    if max_lag <= min_lag:
        return ()

    candidates: list[PitchCandidate] = []
    peak_indices = _autocorrelation_local_peaks(autocorrelation, min_lag, max_lag)
    axis = np.arange(len(autocorrelation))
    for peak_index in peak_indices:
        peak_position = _parabolic_peak(autocorrelation, int(peak_index))
        if peak_position <= 0:
            continue
        frequency = float(sample_rate / peak_position)
        if min_frequency_hz * 0.995 <= frequency <= max_frequency_hz * 1.005:
            frequency = min(max_frequency_hz, max(min_frequency_hz, frequency))
        if not min_frequency_hz <= frequency <= max_frequency_hz:
            continue
        confidence = float(np.interp(peak_position, axis, autocorrelation))
        if not math.isfinite(confidence) or confidence <= 0:
            continue
        confidence = max(0.0, min(1.0, confidence))
        # Keep bass candidates available for continuity, but make the vocal
        # range more competitive when a mixed frame contains a strong bass.
        score = confidence * (0.25 if frequency < tracking_min_frequency_hz else 1.0)
        candidates.append(PitchCandidate(frequency, confidence, rms, score))

    # Autocorrelation may hide a quieter melody under a strong bass period.
    # Add a few spectral peaks so the tracker can compare both sources.
    nfft = 1 << max(10, (values.size * 4 - 1).bit_length())
    spectrum = np.abs(np.fft.rfft(windowed, n=nfft))
    frequencies = np.fft.rfftfreq(nfft, 1.0 / sample_rate)
    band = np.flatnonzero(
        (frequencies >= min_frequency_hz) & (frequencies <= max_frequency_hz)
    )
    if band.size >= 3:
        band_peak = float(np.max(spectrum[band]))
        if band_peak > 1e-12:
            inner = band[(band > 0) & (band < len(spectrum) - 1)]
            spectral_peaks = inner[
                (spectrum[inner] >= spectrum[inner - 1])
                & (spectrum[inner] >= spectrum[inner + 1])
            ]
            ranked_spectral = sorted(
                spectral_peaks.tolist(),
                key=lambda index: float(spectrum[index]),
                reverse=True,
            )
            background = max(float(np.median(spectrum[band])), 1e-12)
            for peak_index in ranked_spectral[: max(candidate_count * 2, 4)]:
                frequency = float(frequencies[peak_index])
                magnitude = float(spectrum[peak_index] / band_peak)
                if magnitude < 0.04:
                    continue
                prominence = float(spectrum[peak_index]) / background
                if prominence < 6.0:
                    continue
                confidence = prominence / (prominence + 2.0)
                score = 0.8 * magnitude
                if frequency < tracking_min_frequency_hz:
                    score *= 0.25
                candidates.append(PitchCandidate(frequency, confidence, rms, score))

    # Merge near-duplicate autocorrelation/spectral candidates.  Keep the
    # stronger evidence, otherwise one pitch would occupy several state slots.
    deduplicated: list[PitchCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        duplicate = False
        for existing in deduplicated:
            if (
                abs(12.0 * math.log2(candidate.frequency_hz / existing.frequency_hz))
                < 0.5
            ):
                duplicate = True
                break
        if not duplicate:
            deduplicated.append(candidate)
    candidates = deduplicated
    if not candidates:
        return ()
    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    selected = ranked[:candidate_count]
    high_candidates = [
        item for item in ranked if item.frequency_hz >= tracking_min_frequency_hz
    ]
    if high_candidates and not any(
        item.frequency_hz >= tracking_min_frequency_hz for item in selected
    ):
        if len(selected) < candidate_count:
            selected.append(high_candidates[0])
        else:
            selected[-1] = high_candidates[0]
    return tuple(selected)


def _frame_rms(frame: np.ndarray) -> float:
    values = np.asarray(frame, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return 0.0
    values = values - float(np.mean(values))
    windowed = values * np.hanning(values.size)
    return float(np.sqrt(np.mean(windowed * windowed)))


def _candidate_frames(
    samples: np.ndarray,
    config: AnalysisConfig,
) -> list[CandidateFrame]:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size < config.frame_size:
        return []
    frames: list[CandidateFrame] = []
    for start in range(0, values.size - config.frame_size + 1, config.hop_size):
        frame = values[start : start + config.frame_size]
        candidates = estimate_pitch_candidates(
            frame,
            config.sample_rate,
            min_frequency_hz=config.min_frequency_hz,
            max_frequency_hz=config.max_frequency_hz,
            energy_threshold=config.energy_threshold,
            candidate_count=config.candidate_count,
            tracking_min_frequency_hz=config.tracking_min_frequency_hz,
        )
        candidates = tuple(
            c for c in candidates if c.confidence >= config.confidence_threshold
        )
        frames.append(CandidateFrame(candidates=candidates, rms=_frame_rms(frame)))
    return frames


def _tracking_emission(
    candidate: PitchCandidate | None,
    rms: float,
    config: AnalysisConfig,
) -> float:
    if candidate is None:
        return 0.0 if rms < config.energy_threshold else -config.tracking_rest_penalty
    score = max(candidate.score, 1e-6)
    emission = math.log(score)
    return emission


def _tracking_transition(
    previous: PitchCandidate | None,
    current: PitchCandidate | None,
    config: AnalysisConfig,
) -> float:
    if previous is None and current is None:
        return 0.0
    if previous is None or current is None:
        return -0.5 * config.tracking_rest_penalty
    semitones = abs(12.0 * math.log2(current.frequency_hz / previous.frequency_hz))
    penalty = config.tracking_continuity_weight * min(semitones, 24.0)
    octave_distance = abs(semitones - 12.0)
    if octave_distance <= 1.5:
        penalty += config.tracking_octave_penalty
    elif semitones > 12.0:
        penalty += 0.5 * config.tracking_octave_penalty
    return -penalty


def analyze_samples_tracked(
    samples: np.ndarray,
    config: AnalysisConfig | None = None,
) -> list[PitchFrame]:
    """Track one continuous melody path through multiple frame candidates."""

    config = config or AnalysisConfig()
    config.validate()
    candidate_frames = _candidate_frames(samples, config)
    return track_pitch_candidates(candidate_frames, config)


def track_pitch_candidates(
    candidate_frames: list[CandidateFrame], config: AnalysisConfig
) -> list[PitchFrame]:
    """Select a continuous path from externally supplied pitch candidates."""
    if not candidate_frames:
        return []

    state_frames: list[list[PitchCandidate | None]] = [
        [None, *frame.candidates] for frame in candidate_frames
    ]
    previous_scores = [
        _tracking_emission(state, candidate_frames[0].rms, config)
        for state in state_frames[0]
    ]
    backpointers: list[list[int]] = []

    for frame_index in range(1, len(state_frames)):
        current_states = state_frames[frame_index]
        current_scores: list[float] = []
        current_backpointers: list[int] = []
        for current in current_states:
            transition_scores = [
                previous_scores[previous_index]
                + _tracking_transition(previous, current, config)
                for previous_index, previous in enumerate(state_frames[frame_index - 1])
            ]
            best_previous = int(np.argmax(transition_scores))
            current_backpointers.append(best_previous)
            current_scores.append(
                transition_scores[best_previous]
                + _tracking_emission(current, candidate_frames[frame_index].rms, config)
            )
        backpointers.append(current_backpointers)
        previous_scores = current_scores

    state_index = int(np.argmax(previous_scores))
    selected: list[PitchCandidate | None] = [None] * len(state_frames)
    selected[-1] = state_frames[-1][state_index]
    for frame_index in range(len(state_frames) - 1, 0, -1):
        state_index = backpointers[frame_index - 1][state_index]
        selected[frame_index - 1] = state_frames[frame_index - 1][state_index]

    result: list[PitchFrame] = []
    for candidate_frame, candidate in zip(candidate_frames, selected):
        if candidate is None:
            result.append(PitchFrame(0.0, 0.0, candidate_frame.rms))
        else:
            result.append(
                PitchFrame(
                    candidate.frequency_hz,
                    candidate.confidence,
                    candidate_frame.rms,
                )
            )
    return _fold_isolated_octaves(result, config.octave_fold_tolerance_semitones)


def _fold_isolated_octaves(
    frames: list[PitchFrame],
    tolerance_semitones: float,
) -> list[PitchFrame]:
    """Fold a one-frame octave outlier toward matching neighboring frames.

    Autocorrelation can occasionally choose the second harmonic for one hop.
    Only an isolated frame whose immediate neighbors agree is folded.  Two or
    more consecutive frames are left alone, so a real sustained octave jump is
    not flattened into the previous note.
    """

    if len(frames) < 3:
        return frames
    result = list(frames)
    neighbor_tolerance = max(tolerance_semitones, 1.5)
    octave_tolerance = tolerance_semitones / 12.0
    for index in range(1, len(frames) - 1):
        previous = frames[index - 1]
        current = frames[index]
        following = frames[index + 1]
        if (
            previous.frequency_hz <= 0
            or current.frequency_hz <= 0
            or following.frequency_hz <= 0
        ):
            continue
        if not all(
            math.isfinite(value)
            for value in (
                previous.frequency_hz,
                current.frequency_hz,
                following.frequency_hz,
            )
        ):
            continue
        neighbors_semitones = abs(
            12.0 * math.log2(previous.frequency_hz / following.frequency_hz)
        )
        if neighbors_semitones > neighbor_tolerance:
            continue
        reference = math.sqrt(previous.frequency_hz * following.frequency_hz)
        octave_distance = abs(math.log2(current.frequency_hz / reference))
        if abs(octave_distance - 1.0) > octave_tolerance:
            continue
        folded_frequency = (
            current.frequency_hz / 2.0
            if current.frequency_hz > reference
            else current.frequency_hz * 2.0
        )
        result[index] = PitchFrame(folded_frequency, current.confidence, current.rms)
    return result


def analyze_samples(
    samples: np.ndarray, config: AnalysisConfig | None = None
) -> list[PitchFrame]:
    """Analyze an array of mono float samples using overlapping frames.

    Empty or shorter-than-one-frame input returns an empty list.  The caller can
    turn this into an empty output sketch without special casing the pipeline.
    """

    config = config or AnalysisConfig()
    config.validate()
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    frame_size = config.frame_size
    hop_size = config.hop_size
    if values.size < frame_size:
        return []

    result: list[PitchFrame] = []
    for start in range(0, values.size - frame_size + 1, hop_size):
        frame_result = estimate_pitch(
            values[start : start + frame_size],
            config.sample_rate,
            min_frequency_hz=config.min_frequency_hz,
            max_frequency_hz=config.max_frequency_hz,
            energy_threshold=config.energy_threshold,
        )
        if frame_result.confidence < config.confidence_threshold:
            frame_result = PitchFrame(0.0, frame_result.confidence, frame_result.rms)
        result.append(frame_result)
    return _fold_isolated_octaves(result, config.octave_fold_tolerance_semitones)
