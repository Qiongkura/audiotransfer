"""Continuous note-event grouping, smoothing, and event-count limiting."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class NoteFrame:
    """Quantized note for one analysis hop."""

    note: str

    frequency_hz: float
    confidence: float = 1.0
    onset: bool = False


@dataclass(frozen=True)
class NoteEvent:
    """A contiguous output event suitable for JSON/CSV/Arduino."""

    frequency_hz: float
    duration_ms: int
    note: str
    # Continue the voice from the previous event without a new attack.
    legato: bool = False

    def as_dict(self) -> dict[str, object]:
        result = {
            "frequency_hz": round(float(self.frequency_hz), 6),
            "duration_ms": int(self.duration_ms),
            "note": self.note,
        }
        if self.legato:
            result["legato"] = True
        return result


def _group_contiguous(frames: list[NoteFrame], hop_ms: float) -> list[NoteEvent]:
    if not frames:
        return []
    events: list[NoteEvent] = []
    current = frames[0]
    duration = hop_ms
    for frame in frames[1:]:
        if frame.note == current.note:
            duration += hop_ms
            continue
        events.append(
            NoteEvent(
                frequency_hz=0.0 if current.note == "rest" else float(current.frequency_hz),
                duration_ms=max(1, int(round(duration))),
                note=current.note,
            )
        )
        current = frame
        duration = hop_ms
    events.append(
        NoteEvent(
            frequency_hz=0.0 if current.note == "rest" else float(current.frequency_hz),
            duration_ms=max(1, int(round(duration))),
            note=current.note,
        )
    )
    return events


def _combine(left: NoteEvent, right: NoteEvent) -> NoteEvent:
    # Both inputs represent the same note after grouping, or one is being
    # absorbed as a short transition into the other.  Keep the destination's
    # pitch so Arduino output remains semantically stable.
    return NoteEvent(
        frequency_hz=left.frequency_hz,
        duration_ms=left.duration_ms + right.duration_ms,
        note=left.note,
        legato=left.legato,
    )


def _is_rest(event: NoteEvent) -> bool:
    return event.note == "rest"


def _merge_short_events(events: list[NoteEvent], min_note_ms: int) -> list[NoteEvent]:
    """Remove short transitions without turning an isolated bad note into tone.

    The semantic cases are handled before the duration-based fallback:

    * a short note surrounded by rests belongs to the rest;
    * a short rest between equal notes belongs to that note;
    * a short note with any rest neighbor prefers the rest neighbor;
    * otherwise the short event is absorbed by the longer neighbor.
    """

    if min_note_ms <= 0 or len(events) <= 1:
        return events
    result = list(events)
    while len(result) > 1:
        # Resolve all interior equal-neighbor cases first.  This prevents a
        # short event at the sequence edge from consuming a short rest that is
        # semantically part of the same note on its other side.
        semantic_index = next(
            (
                index
                for index in range(1, len(result) - 1)
                if result[index].duration_ms < min_note_ms
                and result[index - 1].note == result[index + 1].note
            ),
            None,
        )
        if semantic_index is not None:
            left = result[semantic_index - 1]
            short = result[semantic_index]
            right = result[semantic_index + 1]
            result[semantic_index - 1] = NoteEvent(
                frequency_hz=left.frequency_hz,
                duration_ms=left.duration_ms + short.duration_ms + right.duration_ms,
                note=left.note,
            )
            del result[semantic_index : semantic_index + 2]
            continue

        short_index = next(
            (index for index, event in enumerate(result) if event.duration_ms < min_note_ms),
            None,
        )
        if short_index is None:
            break
        short = result[short_index]

        if short_index == 0:
            result[1] = _combine(result[1], short)
            del result[0]
            continue

        if short_index == len(result) - 1:
            result[short_index - 1] = _combine(result[short_index - 1], short)
            del result[short_index]
            continue

        left = result[short_index - 1]
        right = result[short_index + 1]

        if not _is_rest(short):
            # A short pitched event touching rest is more likely an analysis
            # glitch than a note that should lengthen its pitched neighbor.
            if _is_rest(left):
                result[short_index - 1] = _combine(left, short)
                del result[short_index]
                continue
            if _is_rest(right):
                result[short_index + 1] = _combine(right, short)
                del result[short_index]
                continue

        if _is_rest(left) and not _is_rest(right):
            result[short_index - 1] = _combine(left, short)
            del result[short_index]
        elif _is_rest(right) and not _is_rest(left):
            result[short_index + 1] = _combine(right, short)
            del result[short_index]
        elif left.duration_ms >= right.duration_ms:
            result[short_index - 1] = _combine(left, short)
            del result[short_index]
        else:
            result[short_index + 1] = _combine(right, short)
            del result[short_index]

    # Smoothing can create adjacent equal notes; normalize those boundaries.
    normalized: list[NoteEvent] = []
    for event in result:
        if normalized and normalized[-1].note == event.note:
            normalized[-1] = _combine(normalized[-1], event)
        else:
            normalized.append(event)
    return normalized


def merge_note_frames(
    frames: Iterable[NoteFrame],
    hop_ms: float,
    min_note_ms: int = 40,
    max_notes: int | None = 3000,
) -> list[NoteEvent]:
    """Merge equal neighboring notes and smooth events shorter than a minimum.

    ``hop_ms`` is used as the duration represented by each analyzed frame.  If
    ``max_notes`` is set, the final sequence is truncated after that many
    events.  Empty input always returns an empty list.
    """

    if hop_ms <= 0:
        raise ValueError("hop_ms 必须为正数")
    if min_note_ms < 0:
        raise ValueError("min_note_ms 不能为负数")
    if max_notes is not None and max_notes <= 0:
        raise ValueError("max_notes 必须为正数或 None")
    frame_list = list(frames)
    # Smooth each articulation separately: normalization must never merge
    # two repetitions of the same pitch across a detected attack.
    boundaries = [0] + [i for i, frame in enumerate(frame_list) if i > 0 and frame.onset]
    boundaries.append(len(frame_list))
    events = []
    for start, stop in zip(boundaries, boundaries[1:]):
        grouped = _group_contiguous(frame_list[start:stop], float(hop_ms))
        events.extend(_merge_short_events(grouped, int(min_note_ms)))
    if max_notes is not None:
        events = events[:max_notes]
    return events


def scale_note_events(events: Iterable[NoteEvent], speed: float) -> list[NoteEvent]:
    """Scale event durations by playback speed while preserving their pitches."""

    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("speed 必须为正数")
    return [
        NoteEvent(
            frequency_hz=event.frequency_hz,
            duration_ms=max(1, int(round(event.duration_ms / speed))),
            note=event.note,
            legato=event.legato,
        )
        for event in events
    ]
