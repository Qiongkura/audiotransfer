"""Twelve-tone equal-temperament note conversion."""

from __future__ import annotations

from dataclasses import dataclass
import math


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


@dataclass(frozen=True)
class QuantizedNote:
    """A pitch mapped to the nearest equal-tempered note."""

    note: str
    frequency_hz: float
    midi: int | None
    source_frequency_hz: float
    confidence: float


def midi_to_frequency(midi: int) -> float:
    """Convert a MIDI note number to Hz using A4 = 440 Hz."""

    if not 0 <= midi <= 127:
        raise ValueError("MIDI 音符必须在 0 到 127 之间")
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def frequency_to_midi(frequency_hz: float) -> int:
    """Return the nearest MIDI note number for a positive frequency."""

    if frequency_hz <= 0 or not math.isfinite(frequency_hz):
        raise ValueError("frequency_hz 必须为有限正数")
    midi = round(69 + 12 * math.log2(frequency_hz / 440.0))
    return max(0, min(127, midi))


def midi_to_note_name(midi: int) -> str:
    """Format MIDI as names such as C4 or F#5."""

    if not 0 <= midi <= 127:
        raise ValueError("MIDI 音符必须在 0 到 127 之间")
    octave = midi // 12 - 1
    return f"{_NOTE_NAMES[midi % 12]}{octave}"


def quantize_frequency(
    frequency_hz: float,
    confidence: float = 1.0,
    confidence_threshold: float = 0.35,
) -> QuantizedNote:
    """Map a measured frequency to the nearest semitone.

    Silence, invalid frequencies, or confidence below the threshold become a
    rest with 0 Hz.  ``frequency_hz`` in a non-rest result is the ideal
    equal-tempered frequency used by the Arduino output.
    """

    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold 应在 0 到 1 之间")
    source = float(frequency_hz) if math.isfinite(frequency_hz) else 0.0
    if source <= 0 or confidence < confidence_threshold:
        return QuantizedNote("rest", 0.0, None, max(0.0, source), float(confidence))
    midi = frequency_to_midi(source)
    return QuantizedNote(
        note=midi_to_note_name(midi),
        frequency_hz=midi_to_frequency(midi),
        midi=midi,
        source_frequency_hz=source,
        confidence=float(confidence),
    )
