"""Add an OLED screen and two volume keys to an existing STM32 melody export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from .events import NoteEvent
from .module_voice import generate_module_main
from .output import validate_stm32_pin, write_outputs

FONT_ROWS = {
    '0': ['01110','10001','10011','10101','11001','10001','01110'],
    '1': ['00100','01100','00100','00100','00100','00100','01110'],
    '2': ['01110','10001','00001','00010','00100','01000','11111'],
    '3': ['11110','00001','00001','01110','00001','00001','11110'],
    '4': ['00010','00110','01010','10010','11111','00010','00010'],
    '5': ['11111','10000','10000','11110','00001','00001','11110'],
    '6': ['01110','10000','10000','11110','10001','10001','01110'],
    '7': ['11111','00001','00010','00100','01000','01000','01000'],
    '8': ['01110','10001','10001','01110','10001','10001','01110'],
    '9': ['01110','10001','10001','01111','00001','00001','01110'],
    ':': ['00000','00100','00100','00000','00100','00100','00000'],
    '/': ['00001','00001','00010','00100','01000','10000','10000'],
    '%': ['11001','11010','00100','00100','00100','01011','10011'],
}


def font_columns() -> list[int]:
    return [sum((row[x] == '1') << y for y, row in enumerate(rows))
            for rows in FONT_ROWS.values() for x in range(5)]


def static_frame(title: str, artist: str, font_path: Path) -> tuple[list[int], object]:
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('1', (128, 64), 0)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(str(font_path), 18)
    detail_font = ImageFont.truetype(str(font_path), 12)
    if draw.textlength(title, font=title_font) > 105 or draw.textlength(artist, font=detail_font) > 66:
        raise ValueError('当前128×64布局的歌名或歌手过长，请使用简短显示名称')
    draw.text((3, 0), title, font=title_font, fill=1, anchor='lt')
    draw.line((3, 20, 124, 20), fill=1)
    draw.text((3, 23), artist, font=detail_font, fill=1, anchor='lt')
    draw.text((74, 23), '音量', font=detail_font, fill=1, anchor='lt')
    data = [sum(int(image.getpixel((x, page * 8 + bit)) != 0) << bit for bit in range(8))
            for page in range(8) for x in range(128)]
    return data, image


def array(name: str, values: list[int]) -> str:
    lines = ['  ' + ', '.join(f'0x{value:02X}' for value in values[i:i + 16]) + ','
             for i in range(0, len(values), 16)]
    return f'const uint8_t {name}[] = {{\n' + '\n'.join(lines) + '\n};\n'


def write_player_project(source: Path, output: Path, *, title: str = '茶汤', artist: str = '郁可唯',
                         controller: str = 'ssd1306', scl: str = 'PB6', sda: str = 'PB7',
                         volume_up: str = 'PA0', volume_down: str = 'PA1',
                         font_path: Path = Path(r'C:\Windows\Fonts\msyh.ttc')) -> Path:
    if source.resolve() == output.resolve():
        raise ValueError('请使用独立输出目录以保留原来的纯播放版本')
    if controller not in ('ssd1306', 'sh1106'):
        raise ValueError('屏幕驱动应为ssd1306或sh1106')
    pins = [validate_stm32_pin(p).upper() for p in (scl, sda, volume_up, volume_down)]
    if any(p in ('PA8', 'PA13', 'PA14') for p in pins) or len(set(pins)) != 4:
        raise ValueError('屏幕与按键引脚不能重复或占用蜂鸣器PA8、调试PA13/PA14')
    if pins[:2] not in (['PB6', 'PB7'], ['PB8', 'PB9']):
        raise ValueError('本板使用I2C1：SCL/SDA请选择PB6/PB7或PB8/PB9')
    raw = json.loads((source / 'notes.json').read_text(encoding='utf-8'))
    playback = json.loads((source / 'playback.json').read_text(encoding='utf-8'))
    duration = sum(i['duration_ms'] for i in raw)
    if not raw or not 0 < duration < 0x80000000:
        raise ValueError('歌曲应有音符且总时长小于约24.8天')
    if playback.get('sound_mode') not in ('soft', 'harmonic', 'pulse'):
        raise ValueError('屏幕播放器需使用soft、harmonic或pulse模块发声工程')
    base, _ = static_frame(title, artist, font_path)
    events = [NoteEvent(i['frequency_hz'], i['duration_ms'], i['note'], i.get('legato', False)) for i in raw]
    strengths = [i.get('velocity', 100) for i in raw]
    mode = playback['sound_mode']
    main = generate_module_main(events, 'PA8', playback['gap_ms'], strengths, mode,
                                active_low=playback.get('active_low', False), player_ui=True)
    result = write_outputs(events, output, target='platformio-stm32', stm32_pin='PA8',
                           gap_ms=playback['gap_ms'], sound_mode=mode, velocities=strengths,
                           active_low=playback.get('active_low', False), arrangement=playback.get('arrangement'))
    result.main_cpp_path.write_text(main, encoding='utf-8')
    src = output / 'src'
    for name in ('player_ui.h', 'player_logic.h'):
        shutil.copy2(Path(__file__).with_name(name), src / name)
    config = ('#pragma once\n#include <stdint.h>\n'
              f'const uint32_t PLAYER_DURATION_MS = {duration}UL;\n'
              f'const bool PLAYER_SH1106 = {str(controller == "sh1106").lower()};\n'
              f'const uint32_t PLAYER_SCL = {pins[0]}, PLAYER_SDA = {pins[1]};\n'
              f'const uint32_t PLAYER_VOLUME_UP = {pins[2]}, PLAYER_VOLUME_DOWN = {pins[3]};\n'
              f'const char PLAYER_ASCII_CHARS[] = "{"".join(FONT_ROWS)}";\n'
              + array('PLAYER_ASCII', font_columns()) + array('PLAYER_BASE', base))
    (src / 'player_config.h').write_text(config, encoding='utf-8')
    ini = result.platformio_ini_path.read_text(encoding='utf-8')
    # Wire uses this timeout in its STM32 bus driver, including address probes.
    result.platformio_ini_path.write_text(ini + 'build_flags = -DI2C_TIMEOUT_TICK=5\n', encoding='utf-8')
    screen = {'title': title, 'artist': artist, 'width': 128, 'height': 64, 'controller': controller,
              'addresses': ['0x3C', '0x3D'], 'scl': pins[0], 'sda': pins[1],
              'volume_up': pins[2], 'volume_down': pins[3], 'key_active_level': 'LOW',
              'volume_initial_percent': 50, 'volume_step_policy': '1_below_20__5_above',
              'volume_gain_curve': 'logarithmic_60db_control_gain_not_spl', 'debounce_ms': 25,
              'hold_delay_ms': 350, 'hold_repeat_ms': 150, 'volume_ramp_ms': 20,
              'refresh_ms': 250, 'i2c_chunk_bytes': 16, 'i2c_timeout_ms': 5,
              'display_failure_policy': 'keep_playing_retry_every_3s', 'duration_ms': duration}
    (output / 'player.json').write_text(json.dumps(screen, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='给现有STM32旋律工程添加OLED屏幕和音量按键')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('-o', '--output', type=Path, required=True)
    parser.add_argument('--title', default='茶汤')
    parser.add_argument('--artist', default='郁可唯')
    parser.add_argument('--controller', choices=['ssd1306', 'sh1106'], default='ssd1306')
    parser.add_argument('--scl', default='PB6')
    parser.add_argument('--sda', default='PB7')
    parser.add_argument('--volume-up', default='PA0')
    parser.add_argument('--volume-down', default='PA1')
    parser.add_argument('--font', type=Path, default=Path(r'C:\Windows\Fonts\msyh.ttc'))
    args = parser.parse_args(argv)
    output = write_player_project(args.source, args.output, title=args.title, artist=args.artist,
                                  controller=args.controller, scl=args.scl, sda=args.sda,
                                  volume_up=args.volume_up, volume_down=args.volume_down, font_path=args.font)
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
