import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

from audiotransfer.events import NoteEvent
from audiotransfer.module_voice import generate_module_main
from audiotransfer.preview import render_events
from audiotransfer.output import write_outputs


class ModuleVoiceTests(unittest.TestCase):
    def test_added_harmonics_darkening_preserves_fundamental(self):
        events=[{'frequency_hz':400,'duration_ms':1000,'velocity':100}]
        signal=render_events(events,sample_rate=16000,sound_mode='harmonic').astype(float)
        def components(start):
            x=signal[start:start+1600]*np.hanning(1600)
            spectrum=np.abs(np.fft.rfft(x))
            return spectrum[40],spectrum[80],spectrum[120]
        early,late=components(480),components(8000)
        self.assertGreater(early[0],early[1]*2)
        self.assertGreater(early[1]/early[0],late[1]/late[0]*1.5)
        self.assertGreater(early[2]/early[0],late[2]/late[0]*1.5)

    def test_harmonic_and_pulse_keep_exact_time_and_silent_tail(self):
        events=[{'frequency_hz':440,'duration_ms':301,'velocity':100},
                {'frequency_hz':493.883,'duration_ms':207,'velocity':90,'legato':True},
                {'frequency_hz':0,'duration_ms':131}]
        for mode in ['harmonic','pulse']:
            pcm=render_events(events,sample_rate=16000,sound_mode=mode,gap_ms=12)
            self.assertEqual(len(pcm),10224)
            self.assertTrue(np.all(pcm[8128:]==0))
            self.assertLess(np.max(np.abs(pcm)),32767)

    def test_harmonics_above_hardware_nyquist_are_excluded(self):
        events=[{'frequency_hz':3000,'duration_ms':1000}]
        pcm=render_events(events,sample_rate=22050,sound_mode='harmonic')
        time=np.arange(2205)/22050
        x=pcm[11025:13230].astype(float)
        powers=[abs(np.sum(x*np.exp(-2j*np.pi*f*time))) for f in [3000,6000,9000]]
        self.assertGreater(powers[1]/powers[0], .04)
        self.assertLess(powers[2]/powers[0], .001)

    def test_module_export_preserves_notes_and_records_polarity(self):
        events=[NoteEvent(440,300,'A4'),NoteEvent(0,100,'rest')]
        with tempfile.TemporaryDirectory() as folder:
            result=write_outputs(events,folder,target='platformio-stm32',sound_mode='pulse',active_low=True)
            notes=json.loads(result.json_path.read_text())
            metadata=json.loads(result.playback_path.read_text())
            self.assertEqual(sum(e['duration_ms'] for e in notes),400)
            self.assertEqual(notes[0]['frequency_hz'],440)
            self.assertTrue(metadata['active_low'])
            self.assertEqual(metadata['inactive_level'],'HIGH')
            self.assertEqual(metadata['hardware_carrier_hz'],'note_frequency')

    def test_invalid_module_parameters_rejected(self):
        for events,gains in [([NoteEvent(float('nan'),100,'x')],[100]),
                             ([NoteEvent(4000,100,'x')],[100]),
                             ([NoteEvent(440,0,'A4')],[100]),
                             ([NoteEvent(440,100,'A4')],[0])]:
            with self.assertRaises(ValueError):
                generate_module_main(events,'PA8',12,gains)


if __name__ == '__main__':unittest.main()
