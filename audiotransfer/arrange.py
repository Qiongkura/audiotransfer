"""Conservative score phrasing for a monophonic song transcription."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .events import NoteEvent
from .notes import frequency_to_midi, midi_to_frequency, midi_to_note_name


@dataclass
class Arrangement:
    events: list[NoteEvent]
    velocities: list[int]
    metadata: dict[str, object]


def beat_grid(beats: np.ndarray, duration_ms: int) -> np.ndarray:
    """Interpolate sixteenths locally; extrapolate untracked intro/outro."""
    beats = np.unique(np.asarray(beats, dtype=float) * 1000)
    beats = beats[np.isfinite(beats)]
    if len(beats) < 4:
        return np.array([], dtype=float)
    period = float(np.median(np.diff(beats)))
    if not 250 <= period <= 1500:
        return np.array([], dtype=float)
    before = np.arange(beats[0] - period, -period, -period)[::-1]
    after = np.arange(beats[-1] + period, duration_ms + period, period)
    anchors = np.concatenate((before, beats, after))
    return np.unique(np.concatenate([
        np.linspace(a, b, 4, endpoint=False) for a, b in zip(anchors, anchors[1:])
    ]))


def nearest_grid(grid: np.ndarray, position: int) -> float:
    index = int(np.searchsorted(grid, position))
    options = grid[max(0, index - 1):min(len(grid), index + 1)]
    return float(options[np.argmin(np.abs(options - position))]) if len(options) else float(position)


def arrange_events(
    events: list[NoteEvent], beats: np.ndarray, *,
    onsets_ms: set[int] | None = None, transpose: int | None = None,
    analysis=None, beat_strengths: np.ndarray | None = None, phrasing: bool = False,
    preserve_timing: bool = False,
) -> Arrangement:
    """Remove short, off-beat slide steps without changing clear note attacks.

    Pitches are never forced into an inferred key. The automatic register
    change is a single octave for the entire score, preserving all intervals.
    Boundary snapping is bounded and leaves genuine off-grid phrasing alone.
    """
    if transpose is not None and not -36 <= transpose <= 36:
        raise ValueError("transpose 必须在 -36 到 36 个半音之间")
    total = sum(e.duration_ms for e in events)
    grid = beat_grid(beats, total)
    period = float(np.median(np.diff(beats)) * 1000) if len(grid) else 0
    result = list(events)
    removed = 0
    start = 0
    index = 0
    attacks = sorted(onsets_ms or set())
    while index < len(result):
        event = result[index]
        if 0 < index < len(result) - 1 and len(grid) and not preserve_timing:
            left, right = result[index - 1], result[index + 1]
            protected = any(abs(attack - start) <= 40 for attack in attacks)
            if all(e.frequency_hz > 0 for e in (left, event, right)):
                a, b, c = [frequency_to_midi(e.frequency_hz) for e in (left, event, right)]
                slide = (a < b < c or a > b > c) and max(abs(b - a), abs(c - b)) <= 3
                offbeat = abs(nearest_grid(grid, start) - start) > 25
                if (slide and offbeat and not protected
                        and event.duration_ms < min(115, period * .22)
                        and max(left.duration_ms, right.duration_ms) >= period * .5):
                    # The transitional pitch belongs to the destination note.
                    result[index + 1] = NoteEvent(right.frequency_hz, event.duration_ms + right.duration_ms, right.note)
                    del result[index]
                    removed += 1
                    continue
        start += event.duration_ms
        index += 1

    boundaries = np.cumsum([0] + [e.duration_ms for e in result]).tolist()
    snapped = 0
    for i in range(1, len(boundaries) - 1):
        if preserve_timing:
            break
        original = boundaries[i]
        candidate = round(nearest_grid(grid, original))
        limit = 25 if result[i - 1].frequency_hz == 0 or result[i].frequency_hz == 0 else 45
        # Do not collapse two real attacks or turn a short rest into a note.
        minimum = min(60, result[i - 1].duration_ms, result[i].duration_ms)
        if abs(candidate - original) <= limit and boundaries[i - 1] + minimum <= candidate <= boundaries[i + 1] - minimum:
            boundaries[i] = candidate
            snapped += int(candidate != original)
    result = [NoteEvent(e.frequency_hz, boundaries[i + 1] - boundaries[i], e.note) for i, e in enumerate(result)]

    voiced = sorted((e.frequency_hz, e.duration_ms) for e in result if e.frequency_hz > 0)
    if transpose is None:
        half = sum(duration for _, duration in voiced) / 2
        cumulative = 0
        median = 0.0
        for frequency, duration in voiced:
            cumulative += duration
            if cumulative >= half:
                median = frequency
                break
        transpose = 12 if voiced and median < 440 and voiced[-1][0] <= 1000 else 0
    if any(not 0 <= frequency_to_midi(e.frequency_hz) + transpose <= 127 for e in result if e.frequency_hz > 0):
        raise ValueError("移调后音符超出 MIDI 音域")
    shifted = []
    start = 0
    for i, event in enumerate(result):
        if event.frequency_hz > 0:
            midi = frequency_to_midi(event.frequency_hz) + transpose
            event = NoteEvent(midi_to_frequency(midi), event.duration_ms, midi_to_note_name(midi))
        shifted.append(event)
        start += event.duration_ms
    velocities, expression = phrase_expression(shifted, beats, beat_strengths, analysis)
    if phrasing:
        start = 0
        for i, event in enumerate(shifted):
            continues = False
            if i and event.frequency_hz > 0 and shifted[i - 1].frequency_hz > 0:
                interval = abs(frequency_to_midi(event.frequency_hz) - frequency_to_midi(shifted[i - 1].frequency_hz))
                attack = any(abs(t - start) <= 60 for t in attacks)
                same_source = True
                if analysis is not None and analysis.sources:
                    hop = analysis.sample_count * 1000 / analysis.sample_rate / max(1, len(analysis.sources))
                    before = min(len(analysis.sources) - 1, max(0, round((start - 60) / hop)))
                    after = min(len(analysis.sources) - 1, max(0, round((start + 60) / hop)))
                    same_source = analysis.sources[before] == analysis.sources[after] and analysis.sources[before] != 'rest'
                continues = 0 < interval <= 7 and not attack and same_source
            shifted[i] = NoteEvent(event.frequency_hz, event.duration_ms, event.note, continues)
            start += event.duration_ms
    return Arrangement(shifted, velocities, {
        "tempo_bpm": round(60000 / period, 3) if period else None,
        "transpose_semitones": transpose, "removed_slide_steps": removed,
        "snapped_boundaries": snapped, "max_snap_ms": 0 if preserve_timing else 45,
        "duration_ms": total, "grid": None if preserve_timing else "sixteenth",
        "timing": "original" if preserve_timing else "beat_grid",
        "legato_transitions": sum(e.legato for e in shifted),
        **expression,
        **(analysis.details if analysis is not None else {}),
    })


def phrase_expression(events: list[NoteEvent], beats: np.ndarray, strengths: np.ndarray | None, analysis=None):
    """Shape phrases, estimate a four-beat accent phase, and match repeats.

    Repeats share expression only when melody and relative timings agree.
    Musical variants retain their pitches; no chorus is overwritten by an
    assumed template or a guessed key.
    """
    positions = np.cumsum([0] + [e.duration_ms for e in events])
    quarter = np.asarray(beats, dtype=float) * 1000
    beat_ms = float(np.median(np.diff(quarter))) if len(quarter) >= 2 else 500
    strengths = np.ones(len(quarter)) if strengths is None else np.asarray(strengths, float)
    if len(strengths) != len(quarter) or not np.all(np.isfinite(strengths)):
        raise ValueError("拍点力度与拍点数量不匹配")
    accent_phase = None
    if len(strengths) >= 16 and np.std(strengths) > .1:
        phases = [float(np.mean(strengths[i::4])) for i in range(4)]
        if max(phases) - min(phases) > np.std(strengths) * .35:
            accent_phase = int(np.argmax(phases))
    phrases, current = [], []
    for i, event in enumerate(events):
        if event.frequency_hz > 0:
            if current and positions[i] - positions[current[0]] >= beat_ms * 12:
                phrases.append(current)
                current = []
            current.append(i)
        elif current and event.duration_ms >= max(240, beat_ms * .5):
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    velocities = [84] * len(events)
    for phrase in phrases:
        begin, end = positions[phrase[0]], positions[phrase[-1] + 1]
        pitches = [frequency_to_midi(events[i].frequency_hz) for i in phrase]
        centre = float(np.median(pitches))
        for n, i in enumerate(phrase):
            progress = (positions[i] - begin) / max(1, end - begin)
            arch = 7 * np.sin(np.pi * progress) - 6 * progress
            contour = 3 * np.tanh((pitches[n] - centre) / 6)
            accent = 0.0
            if len(quarter):
                k = int(np.argmin(np.abs(quarter - positions[i])))
                closeness = max(0, 1 - abs(quarter[k] - positions[i]) / 90)
                accent = closeness * (4 + 8 * strengths[k])
                if accent_phase is not None:
                    accent += closeness * (5 if k % 4 == accent_phase else 2 if k % 4 == (accent_phase + 2) % 4 else 0)
            velocities[i] = int(np.clip(round(85 + arch + contour + accent + (7 if n == 0 else 0)), 74, 112))
    repeated = 0
    previous = []
    for phrase in phrases:
        if not 6 <= len(phrase) <= 24:
            continue
        if analysis is not None and analysis.sources:
            a = round(positions[phrase[0]] * analysis.sample_rate / (1000 * max(1, analysis.sample_count / len(analysis.sources))))
            b = round(positions[phrase[-1] + 1] * analysis.sample_rate / (1000 * max(1, analysis.sample_count / len(analysis.sources))))
            source = analysis.sources[a:b]
            if not source or source.count('vocals') / len(source) < .7:
                continue
        notes = np.array([frequency_to_midi(events[i].frequency_hz) for i in phrase])
        relative = positions[phrase] - positions[phrase[0]]
        span = positions[phrase[-1] + 1] - positions[phrase[0]]
        match = None
        for candidate, old_notes, old_relative, old_span in previous:
            if len(candidate) == len(phrase) and np.array_equal(notes, old_notes) and .88 <= span / old_span <= 1.12:
                if np.max(np.abs(relative / span - old_relative / old_span)) <= .045:
                    match = candidate
                    break
        if match is not None:
            for old, new in zip(match, phrase):
                velocities[new] = velocities[old]
            repeated += 1
        else:
            previous.append((phrase, notes, relative, span))
    # Breath locations may differ between repetitions of the same chorus.
    # Match eight-note motifs inside phrases as well as complete phrases.
    motifs = {}
    inherited = set()
    matched_motifs = 0
    for phrase in phrases:
        for offset in range(0, len(phrase) - 7):
            window = phrase[offset:offset + 8]
            begin, end = positions[window[0]], positions[window[-1] + 1]
            if analysis is not None and analysis.sources:
                frame_ms = analysis.sample_count * 1000 / analysis.sample_rate / len(analysis.sources)
                source = analysis.sources[round(begin / frame_ms):round(end / frame_ms)]
                if not source or source.count('vocals') / len(source) < .7:
                    continue
            signature = tuple(frequency_to_midi(events[i].frequency_hz) for i in window)
            key = signature[:2]
            relative = (positions[window] - begin) / max(1, end - begin)
            reference = next((old for old in motifs.get(key, [])
                if begin - old[0] >= beat_ms * 8 and .85 <= (end - begin) / old[1] <= 1.15
                and np.mean(np.array(signature) == old[4]) >= .875
                and np.max(np.abs(np.array(signature) - old[4])) <= 2
                and np.max(np.abs(relative - old[2])) <= .085), None)
            if reference is not None and sum(i in inherited for i in window) <= 4:
                for n, (old, new) in enumerate(zip(reference[3], window)):
                    if signature[n] == reference[4][n]:
                        velocities[new] = velocities[old]
                        inherited.add(new)
                matched_motifs += 1
            else:
                motifs.setdefault(key, []).append((begin, end - begin, relative, window, np.array(signature)))
    return velocities, {"phrases": len(phrases), "matched_repeat_phrases": repeated,
                        "matched_repeat_motifs": matched_motifs,
                        "estimated_accent_phase": accent_phase,
                        "velocity_min": min(velocities, default=0), "velocity_max": max(velocities, default=0)}


def detect_beats(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    import librosa
    _, beats = librosa.beat.beat_track(
        y=samples, sr=sample_rate, hop_length=max(1, round(sample_rate * .01)),
        units="time", trim=False,
    )
    return np.asarray(beats, dtype=float)


def measure_beat_strengths(samples: np.ndarray, sample_rate: int, beats: np.ndarray) -> np.ndarray:
    import librosa
    hop = max(1, round(sample_rate * .01))
    envelope = librosa.onset.onset_strength(y=samples, sr=sample_rate, hop_length=hop)
    levels = np.array([
        float(np.max(envelope[max(0, round(t * sample_rate / hop) - 5):min(len(envelope), round(t * sample_rate / hop) + 6)], initial=0))
        for t in beats
    ])
    if not len(levels):
        return levels
    low, high = np.quantile(levels, [.1, .9])
    return np.clip((levels - low) / max(1e-8, high - low), 0, 1)
