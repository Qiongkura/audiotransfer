import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from audiotransfer.events import NoteEvent
from audiotransfer.output import write_outputs
from audiotransfer.player import write_player_project


FONT = Path(r'C:\Windows\Fonts\msyh.ttc')


class PlayerExportTests(unittest.TestCase):
    @unittest.skipUnless(FONT.is_file() and importlib.util.find_spec('PIL'), 'UI font generation needs Pillow and a Chinese font')
    def test_adding_display_keeps_original_melody_timeline(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'source', Path(folder) / 'player'
            events = [NoteEvent(440, 300, 'A4'), NoteEvent(493.883301, 207, 'B4', True),
                      NoteEvent(0, 100, 'rest')]
            write_outputs(events, source, target='platformio-stm32', sound_mode='pulse',
                          active_low=True, gap_ms=12, velocities=[90, 110, 100])
            original = (source / 'notes.json').read_text(encoding='utf-8')
            write_player_project(source, output)
            self.assertEqual(json.loads((output / 'notes.json').read_text(encoding='utf-8')), json.loads(original))
            settings = json.loads((output / 'player.json').read_text(encoding='utf-8'))
            self.assertEqual(settings['duration_ms'], 607)
            self.assertEqual(settings['title'], '茶汤')
            self.assertEqual(settings['artist'], '郁可唯')
            self.assertEqual(settings['pause'], 'PA2')
            ini = (output / 'platformio.ini').read_text(encoding='utf-8')
            self.assertIn('debug_speed = 100\n', ini)
            main = (output / 'src/main.cpp').read_text(encoding='utf-8')
            self.assertIn('playerUi.musicNow()', main)
            self.assertIn('if (playerUi.paused())', main)
            self.assertEqual((source / 'notes.json').read_text(encoding='utf-8'), original)

    def test_conflicting_pins_do_not_overwrite_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'missing-source', Path(folder) / 'player'
            output.mkdir()
            marker = output / 'notes.json'
            marker.write_text('preserve-existing-project', encoding='utf-8')
            for options in [dict(volume_up='PA8'), dict(volume_down='PA13'),
                            dict(volume_up='PA0', volume_down='PA0'), dict(scl='PB7', sda='PB6'),
                            dict(pause='PA0'), dict(pause='PA8'), dict(pause='PA14')]:
                with self.assertRaises(ValueError):
                    write_player_project(source, output, **options)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve-existing-project')


if __name__ == '__main__':
    unittest.main()
