import tempfile
import unittest
from pathlib import Path

import numpy as np

from audiotransfer.arrange import arrange_events
from audiotransfer.events import NoteEvent
from audiotransfer.notes import midi_to_frequency, midi_to_note_name
from audiotransfer.output import write_outputs
from audiotransfer.preview import load_events, render_events


def note(midi, duration):
    return NoteEvent(midi_to_frequency(midi), duration, midi_to_note_name(midi))


class ArrangementTests(unittest.TestCase):
    beats = np.arange(0, 5, .5)

    def test_register_changes_all_intervals_together_and_keeps_full_duration(self):
        events = [note(60, 271), NoteEvent(0, 59, 'rest'), note(67, 477), note(64, 531)]
        result = arrange_events(events, self.beats)
        self.assertEqual(sum(e.duration_ms for e in result.events), 1338)
        self.assertEqual([round(e.frequency_hz / old.frequency_hz, 5) for e, old in zip(result.events, events) if old.frequency_hz], [2, 2, 2])
        self.assertEqual(result.events[1].frequency_hz, 0)
        self.assertTrue(all(e.duration_ms > 0 for e in result.events))

    def test_off_grid_phrase_is_not_forced_to_beats(self):
        events = [note(72, 310), note(74, 690)]
        result = arrange_events(events, self.beats, transpose=0)
        self.assertEqual(result.events, events)  # 60 ms away from any sixteenth

    def test_clear_attacks_protect_short_notes_and_repeated_notes(self):
        events = [note(60, 310), note(61, 80), note(62, 610), note(62, 500)]
        result = arrange_events(events, self.beats, onsets_ms={310}, transpose=0)
        self.assertEqual(len(result.events), 4)
        self.assertEqual([e.note for e in result.events], [e.note for e in events])

    def test_short_offbeat_slide_step_is_absorbed_without_losing_time(self):
        events = [note(60, 310), note(61, 80), note(62, 610)]
        result = arrange_events(events, self.beats, transpose=0)
        self.assertEqual([e.note for e in result.events], ['C4', 'D4'])
        self.assertEqual(sum(e.duration_ms for e in result.events), 1000)

    def test_missing_beats_and_silence_have_safe_outputs(self):
        events = [NoteEvent(0, 1000, 'rest')]
        result = arrange_events(events, np.array([]))
        self.assertEqual(result.events, events)
        self.assertEqual(result.metadata['transpose_semitones'], 0)

    def test_soft_voice_reduces_harmonics_and_preserves_silent_tail(self):
        events = [{'frequency_hz': 500, 'duration_ms': 500, 'velocity': 100}, {'frequency_hz': 0, 'duration_ms': 100}]
        soft = render_events(events, sample_rate=16000, sound_mode='soft', gap_ms=6)
        square = render_events(events, sample_rate=16000, sound_mode='classic', gap_ms=6)
        self.assertEqual(len(soft), 9600)
        self.assertTrue(np.all(soft[round(.494 * 16000):] == 0))
        def harmonics(samples):
            spectrum = np.abs(np.fft.rfft(samples[3200:6400].astype(float)))
            return spectrum[300] / spectrum[100]  # 1500 Hz / 500 Hz
        self.assertLess(harmonics(soft), .01)
        self.assertGreater(harmonics(square), .25)
        self.assertLess(np.max(np.abs(soft[:80])), np.max(np.abs(soft[3200:3280])))

    def test_soft_project_uses_pwm_and_preserves_velocities_in_all_media(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = write_outputs([note(72, 250), note(74, 250)], folder,
                                  target='platformio-stm32', sound_mode='soft', velocities=[100, 84])
            loaded = load_events(paths.json_path)
            self.assertEqual([e['velocity'] for e in loaded], [100, 84])
            source = paths.main_cpp_path.read_text(encoding='utf-8')
            self.assertIn('SAMPLE_RATE = 16000', source)
            self.assertIn('TIMER_OUTPUT_COMPARE_PWM1', source)
            self.assertIn('deadline += note.duration_ms', source)
            self.assertIn(bytes((0x90, 72, 100)), paths.midi_path.read_bytes())
            self.assertIn(bytes((0x90, 74, 84)), paths.midi_path.read_bytes())
            self.assertIn('"sound_mode": "soft"', paths.playback_path.read_text())


if __name__ == '__main__':
    unittest.main()
