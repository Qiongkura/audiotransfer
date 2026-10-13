"""Write extracted note events as JSON, CSV, Arduino, and PlatformIO outputs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable

from .events import NoteEvent
from .score import generate_midi, generate_numbered_score


SUPPORTED_TARGETS = ("arduino", "platformio-stm32")
_DEFAULT_BOARD = "bluepill_f103c8"
_DEFAULT_STM32_PIN = "PA8"
_DEFAULT_UPLOAD_PROTOCOL = "stlink"
_BOARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_PIN_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)$")


@dataclass(frozen=True)
class OutputPaths:
    json_path: Path
    csv_path: Path
    ino_path: Path
    platformio_ini_path: Path | None = None
    main_cpp_path: Path | None = None
    target: str = "arduino"
    preview_path: Path | None = None
    midi_path: Path | None = None
    score_path: Path | None = None
    playback_path: Path | None = None


def validate_target(target: str) -> str:
    """Validate and return a supported output target."""

    if target not in SUPPORTED_TARGETS:
        choices = ", ".join(SUPPORTED_TARGETS)
        raise ValueError(f"target 必须是 {choices} 之一")
    return target


def validate_board(board: str) -> str:
    """Validate a PlatformIO board identifier before writing it to an INI file."""

    if not isinstance(board, str):
        raise ValueError("board 必须是字符串")
    value = board.strip()
    if not value or _BOARD_RE.fullmatch(value) is None:
        raise ValueError("board 只能包含字母、数字、下划线、点或连字符")
    return value


def validate_stm32_pin(stm32_pin: str | int) -> str:
    """Validate a safe STM32 Arduino pin token such as ``PA8`` or ``PB6``.

    STM32 Arduino cores expose symbolic pin names (for example ``PA8``), while
    numeric pin expressions are also useful for compatible board variants.  A
    restricted identifier/number grammar prevents punctuation from becoming
    C++ source code when the generated ``main.cpp`` is written.
    """

    if isinstance(stm32_pin, bool):
        raise ValueError("stm32_pin 必须是数字或字母引脚表达式")
    value = str(stm32_pin).strip()
    if not value or _PIN_RE.fullmatch(value) is None:
        raise ValueError("stm32_pin 只能是数字或字母引脚表达式，例如 PA8 或 PB6")
    return value


def validate_upload_protocol(upload_protocol: str) -> str:
    """Validate the PlatformIO upload protocol token written to ``platformio.ini``."""

    if not isinstance(upload_protocol, str):
        raise ValueError("upload_protocol 必须是字符串")
    value = upload_protocol.strip()
    if not value or _BOARD_RE.fullmatch(value) is None:
        raise ValueError("upload_protocol 只能包含字母、数字、下划线、点或连字符")
    return value


def _validate_gap_ms(gap_ms: int) -> None:
    if gap_ms < 0:
        raise ValueError("gap_ms 不能为负数")


def generate_arduino(events: Iterable[NoteEvent], pin: int = 8, gap_ms: int = 20) -> str:
    """Generate a standalone Arduino sketch using PROGMEM and tone/noTone."""

    if not 0 <= pin <= 255:
        raise ValueError("蜂鸣器引脚必须在 0 到 255 之间")
    _validate_gap_ms(gap_ms)
    event_list = list(events)
    rows: list[str] = []
    for event in event_list:
        frequency = max(0, min(65_535, int(round(event.frequency_hz))))
        duration = max(0, int(event.duration_ms))
        rows.append(f"  {{{frequency}, {duration}UL, {int(event.legato)}}},")
    if not rows:
        # A zero-length C++ array is not portable.  Keep one harmless sentinel
        # while exposing a logical length of zero to loop().
        rows = ["  {0, 0UL},"]

    return "\n".join(
        [
            "#include <Arduino.h>",
            "#include <avr/pgmspace.h>",
            "",
            f"const uint8_t BUZZER_PIN = {pin};",
            f"const uint32_t MELODY_GAP_MS = {gap_ms}UL;",
            "",
            "struct MelodyNote {",
            "  uint16_t frequency_hz;",
            "  uint32_t duration_ms;",
            "  uint8_t flags;",
            "};",
            "",
            "const MelodyNote melody[] PROGMEM = {",
            *rows,
            "};",
            f"const uint32_t MELODY_LENGTH = {len(event_list)}UL;",
            "",
            "void setup() {",
            "  pinMode(BUZZER_PIN, OUTPUT);",
            "  noTone(BUZZER_PIN);",
            "}",
            "",
            "void loop() {",
            "  for (uint32_t i = 0; i < MELODY_LENGTH; ++i) {",
            "    const uint16_t frequency = pgm_read_word(&melody[i].frequency_hz);",
            "    const uint32_t duration = pgm_read_dword(&melody[i].duration_ms);",
            "    if (frequency > 0) {",
            "      tone(BUZZER_PIN, frequency);",
            "    } else {",
            "      noTone(BUZZER_PIN);",
            "    }",
            "    const bool joinNext = frequency > 0 && i + 1 < MELODY_LENGTH && pgm_read_word(&melody[i + 1].frequency_hz) > 0 && (pgm_read_byte(&melody[i + 1].flags) & 1);",
            "    const uint32_t gap = frequency > 0 && !joinNext ? min(MELODY_GAP_MS, duration / 5) : 0;",
            "    delay(duration - gap);",
            "    if (!joinNext) noTone(BUZZER_PIN);",
            "    delay(gap);",
            "  }",
            "  noTone(BUZZER_PIN);",
            "  delay(100);",
            "}",
            "",
        ]
    )


def generate_platformio_ini(
    board: str = _DEFAULT_BOARD,
    upload_protocol: str = _DEFAULT_UPLOAD_PROTOCOL,
) -> str:
    """Generate the default Arduino Core for STM32 PlatformIO configuration."""

    board = validate_board(board)
    upload_protocol = validate_upload_protocol(upload_protocol)
    return "\n".join(
        [
            f"[env:{board}]",
            "platform = ststm32",
            f"board = {board}",
            "framework = arduino",
            f"upload_protocol = {upload_protocol}",
            "",
        ]
    )


def generate_stm32_main(
    events: Iterable[NoteEvent],
    stm32_pin: str | int = _DEFAULT_STM32_PIN,
    gap_ms: int = 20,
    sound_mode: str = "classic",
    velocities: list[int] | None = None,
    active_low: bool = False,
) -> str:
    """Generate an Arduino-Core-for-STM32 ``src/main.cpp``.

    The STM32 sketch deliberately uses ordinary ``const`` data.  It does not
    include AVR-only headers or accessors, so the same generated project can be
    built by PlatformIO's STM32 Arduino framework.
    """

    pin = validate_stm32_pin(stm32_pin)
    _validate_gap_ms(gap_ms)
    event_list = list(events)
    strengths = validate_velocities(velocities, len(event_list))
    if sound_mode in ('harmonic', 'pulse') or (sound_mode == 'soft' and active_low):
        from .module_voice import generate_module_main
        return generate_module_main(event_list, pin, gap_ms, strengths, sound_mode, active_low=active_low)
    if sound_mode == "soft":
        from .soft_stm32 import generate_soft_main
        return generate_soft_main(event_list, pin, gap_ms, strengths)
    if sound_mode != "classic":
        raise ValueError("sound_mode 必须是 soft、harmonic、pulse 或 classic")
    stop = "noTone(BUZZER_PIN); digitalWrite(BUZZER_PIN, HIGH);" if active_low else "noTone(BUZZER_PIN);"
    rows: list[str] = []
    for event in event_list:
        frequency = max(0, min(65_535, int(round(event.frequency_hz))))
        duration = max(0, int(event.duration_ms))
        rows.append(f"  {{{frequency}, {duration}UL, {int(event.legato)}}},")
    if not rows:
        # Keep the array valid C++ while MELODY_LENGTH remains logically zero.
        rows = ["  {0, 0UL},"]

    return "\n".join(
        [
            "#include <Arduino.h>",
            "",
            f"const uint32_t BUZZER_PIN = {pin};",
            f"const uint32_t MELODY_GAP_MS = {gap_ms}UL;",
            "",
            "struct MelodyNote {",
            "  uint16_t frequency_hz;",
            "  uint32_t duration_ms;",
            "  uint8_t flags;",
            "};",
            "",
            "const MelodyNote melody[] = {",
            *rows,
            "};",
            f"const uint32_t MELODY_LENGTH = {len(event_list)}UL;",
            "",
            "void setup() {",
            *(["  digitalWrite(BUZZER_PIN, HIGH);"] if active_low else []),
            "  pinMode(BUZZER_PIN, OUTPUT);",
            "  " + stop,
            "}",
            "",
            "void loop() {",
            "  for (uint32_t i = 0; i < MELODY_LENGTH; ++i) {",
            "    const uint16_t frequency = melody[i].frequency_hz;",
            "    const uint32_t duration = melody[i].duration_ms;",
            "    if (frequency > 0) {",
            "      tone(BUZZER_PIN, frequency);",
            "    } else {",
            "      " + stop,
            "    }",
            "    const bool joinNext = frequency > 0 && i + 1 < MELODY_LENGTH && melody[i + 1].frequency_hz > 0 && (melody[i + 1].flags & 1);",
            "    const uint32_t gap = frequency > 0 && !joinNext ? min(MELODY_GAP_MS, duration / 5) : 0;",
            "    delay(duration - gap);",
            "    if (!joinNext) { " + stop + " }",
            "    delay(gap);",
            "  }",
            "  " + stop,
            "  delay(100);",
            "}",
            "",
        ]
    )


def validate_velocities(velocities: list[int] | None, count: int) -> list[int]:
    values = [100] * count if velocities is None else list(velocities)
    if len(values) != count or any(not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 127 for v in values):
        raise ValueError("音符力度必须为 1 到 127 的整数，且数量与事件一致")
    return values


def write_outputs(
    events: Iterable[NoteEvent],
    output_dir: str | Path,
    pin: int = 8,
    gap_ms: int = 20,
    target: str = "arduino",
    board: str = _DEFAULT_BOARD,
    stm32_pin: str | int = _DEFAULT_STM32_PIN,
    upload_protocol: str = _DEFAULT_UPLOAD_PROTOCOL,
    sound_mode: str = "classic",
    velocities: list[int] | None = None,
    arrangement: dict[str, object] | None = None,
    active_low: bool = False,
) -> OutputPaths:
    """Write JSON/CSV/Arduino outputs and optional STM32 PlatformIO files.

    ``melody.ino`` is retained for every target for compatibility.  Selecting
    ``platformio-stm32`` additionally creates ``platformio.ini`` and
    ``src/main.cpp`` in the output directory.
    """

    target = validate_target(target)
    board = validate_board(board)
    stm32_pin = validate_stm32_pin(stm32_pin)
    upload_protocol = validate_upload_protocol(upload_protocol)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    event_list = list(events)
    strengths = validate_velocities(velocities, len(event_list))
    if sound_mode not in ("classic", "soft", "harmonic", "pulse") or ((sound_mode != "classic" or active_low) and target != "platformio-stm32"):
        raise ValueError("模块音色及低电平控制只用于STM32；可选classic、soft、harmonic、pulse")
    # Validate/generate before overwriting any existing deliverable.
    arduino = generate_arduino(event_list, pin=pin, gap_ms=gap_ms)
    midi = generate_midi(event_list, gap_ms, strengths)
    stm32_source = generate_stm32_main(event_list, stm32_pin, gap_ms, sound_mode, strengths, active_low) if target == "platformio-stm32" else None
    json_path = output_path / "notes.json"
    csv_path = output_path / "notes.csv"
    ino_path = output_path / "melody.ino"

    serializable = [event.as_dict() for event in event_list]
    has_legato = any(e.legato for e in event_list)
    if has_legato:
        for item, event in zip(serializable, event_list):
            item["legato"] = event.legato
    if velocities is not None:
        for item, velocity in zip(serializable, strengths):
            item["velocity"] = velocity
    json_path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["frequency_hz", "duration_ms", "note"] + (["legato"] if has_legato else []) + (["velocity"] if velocities is not None else [])
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(serializable)
    ino_path.write_text(arduino, encoding="utf-8")
    midi_path = output_path / "melody.mid"
    score_path = output_path / "jianpu.txt"
    playback_path = output_path / "playback.json"
    midi_path.write_bytes(midi)
    score_path.write_text(generate_numbered_score(event_list, arrangement), encoding="utf-8")
    playback_path.write_text(json.dumps({
        "gap_ms": gap_ms, "gap_policy": "within_note",
        "duration_ms": sum(e.duration_ms for e in event_list),
        "sound_mode": sound_mode, "active_low": active_low, "inactive_level": "HIGH" if active_low else "LOW",
        "attack_ms": 12 if sound_mode != "classic" else 0,
        "release_ms": 24 if sound_mode != "classic" else 0,
        "hardware_carrier_hz": "note_frequency" if sound_mode == "pulse" else 48000 if sound_mode in ("soft", "harmonic") else "tone",
        "harmonic_colour": "linear_decay_400ms_160ms" if sound_mode == "harmonic" else None,
        "legato_policy": "continuous_voice", "legato_transitions": sum(e.legato for e in event_list),
        "arrangement": arrangement,
    }, indent=2) + "\n", encoding="utf-8")
    from .preview import render_events, write_wav
    preview_path = output_path / "melody.preview.wav"
    write_wav(render_events(serializable, gap_ms=gap_ms, sound_mode=sound_mode), preview_path, 22_050)

    platformio_ini_path: Path | None = None
    main_cpp_path: Path | None = None
    if target == "platformio-stm32":
        src_dir = output_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        platformio_ini_path = output_path / "platformio.ini"
        main_cpp_path = src_dir / "main.cpp"
        platformio_ini_path.write_text(
            generate_platformio_ini(board=board, upload_protocol=upload_protocol),
            encoding="utf-8",
        )
        main_cpp_path.write_text(
            stm32_source,
            encoding="utf-8",
        )

    return OutputPaths(
        json_path=json_path,
        csv_path=csv_path,
        ino_path=ino_path,
        platformio_ini_path=platformio_ini_path,
        main_cpp_path=main_cpp_path,
        target=target,
        preview_path=preview_path,
        midi_path=midi_path,
        score_path=score_path,
        playback_path=playback_path,
    )
