"""Command-line interface for audiotransfer."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

from .analysis import AnalysisConfig
from .audio import AudioDecodeError
from .output import validate_board, validate_stm32_pin, validate_upload_protocol
from .pipeline import PlatformIOUploadError, process_file, upload_platformio
from .song import SongAnalysisError

TARGET_ARDUINO = "arduino"
TARGET_PLATFORMIO_STM32 = "platformio-stm32"


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def _nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return number


def _nonnegative_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("必须是非负数")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须是正数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiotransfer",
        description=(
            "将音频单旋律近似提取为 notes.json、notes.csv、Arduino melody.ino，"
            "或生成 STM32 Blue Pill 的 PlatformIO 工程。"
        ),
    )
    parser.add_argument("input", type=Path, help="输入音频文件，例如 input.mp3")
    parser.add_argument("-o", "--output-dir", type=Path, required=True, help="输出目录")
    parser.add_argument("--pin", type=int, default=8, help="Arduino 蜂鸣器引脚，默认 8")
    parser.add_argument(
        "--target",
        choices=(TARGET_ARDUINO, TARGET_PLATFORMIO_STM32),
        default=TARGET_PLATFORMIO_STM32,
        help="输出目标，默认 platformio-stm32",
    )
    parser.add_argument(
        "--board",
        default="bluepill_f103c8",
        help="PlatformIO board ID，默认 bluepill_f103c8",
    )
    parser.add_argument(
        "--stm32-pin",
        default="PA8",
        help="STM32 Arduino 引脚表达式，默认 PA8",
    )
    parser.add_argument(
        "--upload-protocol",
        default="stlink",
        help="写入 platformio.ini 的上传协议，默认 stlink",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="STM32 目标生成后运行 pio run -t upload",
    )
    parser.add_argument(
        "--pio",
        default="pio",
        help="PlatformIO 命令名或完整路径，默认 pio",
    )
    parser.add_argument(
        "--gap-ms",
        type=_nonnegative_int,
        default=6,
        help="音符内部停顿上限，默认 6 ms；不延长歌曲",
    )
    parser.add_argument(
        "--sound-mode",
        choices=("auto", "soft", "classic", "harmonic", "pulse"),
        default="auto",
        help="soft柔和、harmonic丰满、pulse音符PWM、classic原方波",
    )
    parser.add_argument(
        "--active-low",
        action="store_true",
        help="用于已确认低电平驱动的STM32蜂鸣器模块，HIGH为关闭",
    )
    parser.add_argument(
        "--arrangement",
        choices=("score", "raw"),
        default="score",
        help="完整歌曲默认整理节拍及滑音，raw 保留识别音符",
    )
    parser.add_argument(
        "--transpose",
        type=int,
        help="完整歌曲编排移调半音数；默认自动选择蜂鸣器音域，0 保持原调",
    )
    parser.add_argument(
        "--score-profile",
        type=Path,
        help="可选：使用这首歌已校对的乐谱JSON，校谱模式默认保持原音区",
    )
    parser.add_argument("--sample-rate", type=_positive_int, default=16_000)
    parser.add_argument("--frame-ms", type=_positive_int, default=40)
    parser.add_argument("--hop-ms", type=_positive_int, default=20)
    parser.add_argument(
        "--speed", type=_positive_float, default=1.0, help="播放速度，默认 1.0"
    )
    parser.add_argument(
        "--pitch-mode",
        choices=("song", "tracked", "legacy"),
        default="song",
        help="song 为完整歌曲模式（默认），tracked/legacy 为轻量模式",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="人声分离设备；默认自动选择显卡或 CPU",
    )
    parser.add_argument(
        "--song-backend",
        choices=("game", "rmvpe", "crepe", "pyin"),
        default="rmvpe",
        help="完整歌曲音高识别；game按唱音起止识别，默认rmvpe为兼容方式",
    )
    parser.add_argument(
        "--content-mode",
        choices=("auto", "vocal", "instrumental"),
        default="auto",
        help="完整歌曲类型：auto整曲判断、vocal有演唱、instrumental纯音乐",
    )
    parser.add_argument(
        "--candidate-count", type=_positive_int, default=5, help="主旋律候选数，默认 5"
    )
    parser.add_argument(
        "--tracking-min-frequency-hz",
        type=_positive_int,
        default=140,
        help="主旋律最低频率，默认 140 Hz",
    )
    parser.add_argument("--min-note-ms", type=int, default=80)
    parser.add_argument(
        "--max-notes",
        type=_nonnegative_int,
        default=0,
        help="最多输出的事件数；0 表示保留全曲（默认）",
    )
    parser.add_argument("--min-frequency-hz", type=_positive_int, default=55)
    parser.add_argument("--max-frequency-hz", type=_positive_int, default=1000)
    parser.add_argument("--energy-threshold", type=_nonnegative_float, default=0.01)
    parser.add_argument("--confidence-threshold", type=float, default=0.35)
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 命令名或完整路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.min_note_ms < 0:
        parser.error("--min-note-ms 不能为负数")
    if args.transpose is not None and not -36 <= args.transpose <= 36:
        parser.error("--transpose 必须在 -36 到 36 之间")
    if not 0 <= args.pin <= 255:
        parser.error("--pin 必须在 0 到 255 之间")
    if not 0 <= args.confidence_threshold <= 1:
        parser.error("--confidence-threshold 应在 0 到 1 之间")
    if args.max_frequency_hz <= args.min_frequency_hz:
        parser.error("--max-frequency-hz 必须大于 --min-frequency-hz")
    if args.tracking_min_frequency_hz >= args.max_frequency_hz:
        parser.error("--tracking-min-frequency-hz 必须小于 --max-frequency-hz")
    try:
        validate_board(args.board)
        validate_stm32_pin(args.stm32_pin)
        validate_upload_protocol(args.upload_protocol)
    except ValueError as exc:
        parser.error(str(exc))

    config = AnalysisConfig(
        sample_rate=args.sample_rate,
        frame_ms=args.frame_ms,
        hop_ms=args.hop_ms,
        min_frequency_hz=args.min_frequency_hz,
        max_frequency_hz=args.max_frequency_hz,
        energy_threshold=args.energy_threshold,
        confidence_threshold=args.confidence_threshold,
        pitch_mode=args.pitch_mode,
        candidate_count=args.candidate_count,
        tracking_min_frequency_hz=args.tracking_min_frequency_hz,
        song_backend=args.song_backend,
        content_mode=args.content_mode,
    )
    try:
        paths = process_file(
            args.input,
            args.output_dir,
            config=config,
            min_note_ms=args.min_note_ms,
            max_notes=args.max_notes or None,
            speed=args.speed,
            pin=args.pin,
            gap_ms=args.gap_ms,
            ffmpeg=args.ffmpeg,
            target=args.target,
            board=args.board,
            stm32_pin=args.stm32_pin,
            upload_protocol=args.upload_protocol,
            device=args.device,
            progress=lambda message: print(message, flush=True),
            sound_mode=args.sound_mode,
            arrangement=args.arrangement,
            transpose=args.transpose,
            score_profile=args.score_profile,
            active_low=args.active_low,
        )
    except (AudioDecodeError, SongAnalysisError, ValueError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print("处理完成。")
    print(f"JSON: {paths.json_path}")
    print(f"CSV:  {paths.csv_path}")
    print(f"INO:  {paths.ino_path}")
    print(f"试听: {paths.preview_path}")
    print(f"MIDI: {paths.midi_path}")
    print(f"简谱音符表: {paths.score_path}")
    if paths.platformio_ini_path is not None:
        print(f"PlatformIO 工程: {paths.platformio_ini_path.parent}")
        print(f"platformio.ini: {paths.platformio_ini_path}")
    if paths.main_cpp_path is not None:
        print(f"STM32 main.cpp: {paths.main_cpp_path}")

    if args.upload:
        if args.target != TARGET_PLATFORMIO_STM32:
            print("未执行 PlatformIO 上传：target=arduino。")
        else:
            try:
                result = upload_platformio(args.output_dir, pio_command=args.pio)
            except PlatformIOUploadError as exc:
                print(f"错误: {exc}", file=sys.stderr)
                return 2
            print("PlatformIO 上传成功。")
            if result.output:
                print(result.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
