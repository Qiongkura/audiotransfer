import importlib.util
import unittest

import numpy as np

from audiotransfer.analysis import AnalysisConfig
from audiotransfer.pipeline import events_from_song
from audiotransfer.song import analyze_stems


@unittest.skipUnless(importlib.util.find_spec('librosa'), 'song extra is not installed')
class WholeSongTests(unittest.TestCase):
    def test_intro_singing_outro_and_noise(self):
        sr = 16000
        time = np.arange(sr * 3) / sr
        voice = .5 * np.sin(2 * np.pi * 392 * time)
        voice[:sr] = 0
        voice[2 * sr:] = 0
        instrument = .4 * np.sin(2 * np.pi * np.where(time < 2, 440, 523.251) * time)
        config = AnalysisConfig(pitch_mode='song', song_backend='pyin')
        result = analyze_stems(voice, instrument, config)
        self.assertEqual(len(result.frames), 150)
        for start, stop, frequency, source in [(15, 35, 440, 'instruments'), (65, 85, 392, 'vocals'), (115, 135, 523.251, 'instruments')]:
            values = [f.frequency_hz for f in result.frames[start:stop]]
            self.assertAlmostEqual(float(np.median(values)), frequency, delta=10)
            self.assertTrue(all(s == source for s in result.sources[start:stop]))
        events = events_from_song(result, config, 40)
        self.assertEqual(sum(e.duration_ms for e in events), 3000)
        noise = np.random.default_rng(42).uniform(-.3, .3, sr)
        unpitched = analyze_stems(np.zeros(sr), noise, config)
        self.assertTrue(all(f.frequency_hz == 0 for f in unpitched.frames))


if __name__ == '__main__':
    unittest.main()
