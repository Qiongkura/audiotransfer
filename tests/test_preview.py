import json
from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

from audiotransfer.preview import load_events, render_events, write_wav


class PreviewTests(unittest.TestCase):
    def test_render_and_write_wav(self) -> None:
        events = [
            {"frequency_hz": 440.0, "duration_ms": 100},
            {"frequency_hz": 0.0, "duration_ms": 50},
        ]
        samples = render_events(events, sample_rate=8000, fade_ms=2)
        self.assertEqual(samples.dtype, np.dtype("<i2"))
        self.assertGreater(len(samples), 0)
        with tempfile.TemporaryDirectory() as directory:
            path = write_wav(samples, Path(directory) / "preview.wav", 8000)
            with wave.open(str(path), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), 8000)
                self.assertEqual(handle.getnframes(), len(samples))

    def test_load_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.json"
            path.write_text(
                json.dumps([{"frequency_hz": 440, "duration_ms": 100}]),
                encoding="utf-8",
            )
            self.assertEqual(load_events(path)[0]["frequency_hz"], 440.0)


if __name__ == "__main__":
    unittest.main()
