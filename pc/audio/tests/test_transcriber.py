from __future__ import annotations

import unittest

from pc.audio.transcriber import TranscriptionError, WhisperTranscriber


class TranscriberTests(unittest.TestCase):
    def test_beam_size_must_stay_in_safe_range(self) -> None:
        with self.assertRaises(TranscriptionError):
            WhisperTranscriber(beam_size=0)
        with self.assertRaises(TranscriptionError):
            WhisperTranscriber(beam_size=6)


if __name__ == "__main__":
    unittest.main()
