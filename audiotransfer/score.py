"""Editable MIDI and numbered-note timelines without extra dependencies."""
from __future__ import annotations

import struct
from typing import Iterable

from .events import NoteEvent
from .notes import frequency_to_midi


def _variable_length(value: int) -> bytes:
    if not 0 <= value <= 0x0FFFFFFF:
        raise ValueError("MIDI 时间间隔过长")
    parts = [value & 127]
    while value >> 7:
        value >>= 7
        parts.insert(0, (value & 127) | 128)
    return bytes(parts)


def generate_midi(events: Iterable[NoteEvent], gap_ms: int, velocities: list[int] | None = None) -> bytes:
    # 500 ticks/quarter with a 500000 us tempo gives exactly 1 tick/ms.
    track = bytearray(b"\x00\xff\x51\x03\x07\xa1\x20\x00\xc0\x50")
    pending = 0
    events = list(events)
    for index, event in enumerate(events):
        if event.frequency_hz <= 0:
            pending += event.duration_ms
            continue
        note = frequency_to_midi(event.frequency_hz)
        join_next = index + 1 < len(events) and events[index + 1].legato and events[index + 1].frequency_hz > 0
        gap = 0 if join_next else min(gap_ms, event.duration_ms // 5)
        velocity = velocities[index] if velocities is not None else 100
        track.extend(_variable_length(pending) + bytes((0x90, note, velocity)))
        track.extend(_variable_length(event.duration_ms - gap) + bytes((0x80, note, 0)))
        pending = gap
    track.extend(_variable_length(pending) + b"\xff\x2f\x00")
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 500) + b"MTrk" + struct.pack(">I", len(track)) + track


def generate_numbered_score(events: Iterable[NoteEvent], arrangement: dict[str, object] | None = None) -> str:
    """Use fixed C-major numbering, explicit accidentals and exact durations."""
    names = ("1", "#1", "2", "#2", "3", "4", "#4", "5", "#5", "6", "#6", "7")
    lines = [
        "单声部主旋律音符表（固定 1=C，C4 为中音 1）",
        "' 表示高八度，, 表示低八度；0 为休止符；时长保留实际毫秒。",
        "这是未做节拍量化的音符时间线。melody.mid 可导入制谱软件编辑。",
        "开始时间(s)\t简谱音\t音名\t时长(ms)\t演奏",
    ]
    if arrangement:
        lines[2] = (f"节拍参考：{arrangement.get('tempo_bpm')} BPM；靠近节拍的边界已整理。"
                    f"整体移调：{arrangement.get('transpose_semitones', 0):+d} 半音。melody.mid 可编辑。")
    start = 0
    for event in events:
        number = "0"
        if event.frequency_hz > 0:
            midi = frequency_to_midi(event.frequency_hz)
            octave = midi // 12 - 5
            number = names[midi % 12] + ("'" * octave if octave > 0 else "," * -octave)
        style = "休止" if event.frequency_hz <= 0 else "连奏" if event.legato else "起音"
        lines.append(f"{start / 1000:.3f}\t{number}\t{event.note}\t{event.duration_ms}\t{style}")
        start += event.duration_ms
    lines.append(f"全曲时长：{start / 1000:.3f} 秒")
    return "\n".join(lines) + "\n"
