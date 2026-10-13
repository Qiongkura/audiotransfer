"""Render notes.json to a WAV file and optionally play it on Windows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import wave

import numpy as np


def load_events(path: str | Path) -> list[dict[str, object]]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"找不到音符文件: {source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("notes.json 必须是数组")
    events: list[dict[str, object]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 个事件不是对象")
        try:
            frequency = float(item.get("frequency_hz", 0.0))
            duration_ms = int(item.get("duration_ms", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"第 {index + 1} 个事件的频率或时长无效") from exc
        if not np.isfinite(frequency) or frequency < 0:
            raise ValueError(f"第 {index + 1} 个事件的频率无效")
        if duration_ms < 0:
            raise ValueError(f"第 {index + 1} 个事件的时长无效")
        velocity = item.get("velocity", 100)
        if not isinstance(velocity, int) or not 1 <= velocity <= 127:
            raise ValueError(f"第 {index + 1} 个事件的力度无效")
        legato = item.get("legato", False)
        if not isinstance(legato, bool):
            raise ValueError(f"第 {index + 1} 个事件的连奏设置无效")
        events.append({"frequency_hz": frequency, "duration_ms": duration_ms, "velocity": velocity, "legato": legato})
    return events


def playback_gap(path: str | Path) -> int:
    """Read the same articulation setting used by generated firmware."""
    metadata = Path(path).parent / "playback.json"
    if not metadata.is_file():
        return 0
    data = json.loads(metadata.read_text(encoding="utf-8"))
    gap = data.get("gap_ms", 0)
    if data.get("gap_policy") != "within_note" or not isinstance(gap, int) or gap < 0:
        raise ValueError("playback.json 的音符间隔设置无效")
    return gap


def render_events(
    events: list[dict[str, object]],
    sample_rate: int = 22_050,
    amplitude: float = 0.25,
    gap_ms: int = 0,
    fade_ms: int = 5,
    sound_mode: str = "sine",
) -> np.ndarray:
    """Render monophonic note events as signed 16-bit PCM samples."""

    if sample_rate <= 0:
        raise ValueError("sample_rate 必须为正数")
    if not 0 < amplitude <= 1:
        raise ValueError("amplitude 必须在 0 到 1 之间")
    if gap_ms < 0 or fade_ms < 0:
        raise ValueError("gap_ms 和 fade_ms 不能为负数")
    if sound_mode not in ("sine", "soft", "classic", "harmonic", "pulse"):
        raise ValueError("sound_mode 必须是 sine、soft、classic、harmonic或pulse")

    chunks: list[np.ndarray] = []
    elapsed_ms = 0
    previous_samples = 0
    phase = 0.0
    last_gain = 0.0
    last_color2 = last_color3 = 0.0
    for index, item in enumerate(events):
        frequency = float(item["frequency_hz"])
        duration_ms = int(item["duration_ms"])
        if not np.isfinite(frequency) or frequency < 0 or duration_ms < 0:
            raise ValueError("音符的频率或时长无效")
        elapsed_ms += duration_ms
        end_samples = round(sample_rate * elapsed_ms / 1000.0)
        sample_count = end_samples - previous_samples
        previous_samples = end_samples
        if sample_count == 0:
            continue
        if frequency <= 0:
            chunk = np.zeros(sample_count, dtype=np.float32)
            last_gain = 0.0
        else:
            continued = index > 0 and bool(item.get("legato", False)) and float(events[index - 1]["frequency_hz"]) > 0
            join_next = index + 1 < len(events) and bool(events[index + 1].get("legato", False)) and float(events[index + 1]["frequency_hz"]) > 0
            actual_gap = 0 if join_next else min(gap_ms, duration_ms // 5)
            gap_samples = min(sample_count, round(sample_rate * actual_gap / 1000.0))
            tone_samples = sample_count - gap_samples
            time = np.arange(tone_samples, dtype=np.float32) / sample_rate
            # Match firmware integer-Hz quantization.
            angles = 2.0 * np.pi * round(frequency) * time + (phase if sound_mode in ("soft", "harmonic") else 0)
            chunk = np.sin(angles).astype(np.float32)
            phase = float((angles[-1] + 2.0 * np.pi * round(frequency) / sample_rate) % (2 * np.pi)) if len(angles) else phase
            if sound_mode in ("soft", "harmonic"):
                phase = (phase + 2 * np.pi * round(frequency) * gap_samples / sample_rate) % (2 * np.pi)
            if sound_mode == "classic":
                # A band-limited square approximates tone(), including its
                # audible odd harmonics. The buzzer's resonance is unknown.
                chunk = np.zeros(tone_samples, dtype=np.float32)
                for harmonic in range(1, min(31, int(sample_rate / (2 * round(frequency)))) + 1, 2):
                    chunk += np.sin(harmonic * angles) * (4 / (np.pi * harmonic))
            elif sound_mode in ("soft", "harmonic", "pulse"):
                sounding = duration_ms - actual_gap
                elapsed = np.floor(time * 1000)
                attack = 0 if continued else min(12, sounding // 3)
                release = 0 if join_next else min(24, sounding // 3)
                envelope = np.full(tone_samples, .8) if continued else 1 - np.minimum(120, np.maximum(0, elapsed - attack)) * .2 / 120
                if attack:
                    envelope[elapsed < attack] = elapsed[elapsed < attack] / attack
                envelope *= int(item.get("velocity", 100)) / 127
                if continued:
                    smooth = elapsed < 8
                    envelope[smooth] = last_gain + (envelope[smooth] - last_gain) * elapsed[smooth] / 8
                if release:
                    envelope *= np.minimum(1, np.maximum(0, sounding - elapsed) / release)
                if sound_mode == "harmonic":
                    color2 = np.full(tone_samples, 2621. / 32768) if continued else (10485 - np.floor(np.minimum(400, elapsed) * 7864 / 400)) / 32768
                    color3 = np.full(tone_samples, 655. / 32768) if continued else (3932 - np.floor(np.minimum(160, elapsed) * 3277 / 160)) / 32768
                    if continued:
                        blend = elapsed < 8
                        color2[blend] = last_color2 + (color2[blend] - last_color2) * elapsed[blend] / 8
                        color3[blend] = last_color3 + (color3[blend] - last_color3) * elapsed[blend] / 8
                    if 2 * round(frequency) >= 8000: color2[:] = 0
                    if 3 * round(frequency) >= 8000: color3[:] = 0
                    chunk = ((chunk + color2 * np.sin(2 * angles) + color3 * np.sin(3 * angles)) * (22755. / 32768)).astype(np.float32)
                    last_color2 = float(color2[-1]) if len(color2) else 0
                    last_color3 = float(color3[-1]) if len(color3) else 0
                if sound_mode == "pulse":
                    # AC component of a pulse whose width follows the same
                    # envelope as the hardware. DC and module response are
                    # omitted; this remains an ideal, band-limited preview.
                    duty = envelope / 2
                    chunk = np.zeros(tone_samples, dtype=np.float32)
                    for harmonic in range(1, min(31, int(min(sample_rate / 2, 8000) / round(frequency))) + 1):
                        chunk += (2 * np.sin(np.pi * harmonic * duty) / (np.pi * harmonic)
                                  * np.cos(harmonic * angles - np.pi * harmonic * duty))
                    chunk *= .5
                else:
                    chunk *= envelope
                last_gain = float(envelope[-1]) if len(envelope) and join_next else 0.0
            fade_samples = min(tone_samples // 2, round(sample_rate * fade_ms / 1000.0))
            if fade_samples > 0 and sound_mode == "sine":
                envelope = np.ones(tone_samples, dtype=np.float32)
                ramp = np.linspace(0.0, 1.0, fade_samples, endpoint=False, dtype=np.float32)
                envelope[:fade_samples] = ramp
                envelope[-fade_samples:] = ramp[::-1]
                chunk *= envelope
            chunk = np.pad(chunk, (0, gap_samples))
        chunks.append(chunk * float(amplitude))

    if not chunks:
        return np.zeros(0, dtype=np.int16)
    samples = np.concatenate(chunks)
    return np.clip(samples * 32767.0, -32768, 32767).astype("<i2")


def write_wav(samples: np.ndarray, path: str | Path, sample_rate: int) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audiotransfer-preview",
        description="把 notes.json 合成为电脑可以试听的 WAV 单音旋律。",
    )
    parser.add_argument("notes", type=Path, help="生成的 notes.json")
    parser.add_argument("-o", "--output", type=Path, help="WAV 输出路径")
    parser.add_argument("--sample-rate", type=int, default=22_050)
    parser.add_argument("--amplitude", type=float, default=0.25)
    parser.add_argument("--gap-ms", type=int, help="音符内部停顿；默认读取生成工程的 playback.json")
    parser.add_argument("--fade-ms", type=int, default=5, help="每个音符的淡入淡出，默认 5")
    parser.add_argument("--play", action="store_true", help="生成后使用 Windows 扬声器播放")
    parser.add_argument("--sound-mode", choices=("soft", "classic", "sine", "harmonic", "pulse"), help="默认匹配工程的发声模式")
    args = parser.parse_args(argv)

    try:
        events = load_events(args.notes)
        output = args.output or args.notes.with_suffix(".preview.wav")
        metadata_path = args.notes.parent / "playback.json"
        mode = args.sound_mode or (json.loads(metadata_path.read_text(encoding="utf-8")).get("sound_mode", "sine") if metadata_path.is_file() else "sine")
        samples = render_events(
            events,
            sample_rate=args.sample_rate,
            amplitude=args.amplitude,
            gap_ms=args.gap_ms if args.gap_ms is not None else playback_gap(args.notes),
            fade_ms=args.fade_ms,
            sound_mode=mode,
        )
        path = write_wav(samples, output, args.sample_rate)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}")
        return 2

    duration = len(samples) / args.sample_rate
    print(f"试听 WAV: {path}")
    print(f"时长: {duration:.1f} 秒")
    if args.play:
        try:
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME)
        except ImportError:
            print("当前系统没有 winsound，请直接打开生成的 WAV 文件播放。")
            return 2
        except OSError as exc:
            print(f"播放失败，但 WAV 已生成: {exc}")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
