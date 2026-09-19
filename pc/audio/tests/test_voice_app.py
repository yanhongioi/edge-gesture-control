from __future__ import annotations

import unittest

from pc.voice_app import _discard_audio_for, _should_transcribe


class FakeWakeGate:
    def __init__(self, armed: bool) -> None:
        self.armed = armed

    def is_armed(self) -> bool:
        return self.armed


class FakePipeline:
    def __init__(self, awaiting_followup: bool) -> None:
        self.awaiting_followup = awaiting_followup


class VoiceAppPrefilterTests(unittest.TestCase):
    def test_discards_buffered_audio_and_echo_tail(self) -> None:
        class FakeAudioInput:
            def __init__(self) -> None:
                self.flushes = 0
                self.reads = 0

            def flush(self) -> None:
                self.flushes += 1

            def read_chunk(self) -> None:
                self.reads += 1

        times = iter((0.0, 0.0, 0.4, 0.9))
        audio_input = FakeAudioInput()
        _discard_audio_for(audio_input, 0.8, clock=lambda: next(times))
        self.assertEqual(audio_input.flushes, 2)
        self.assertEqual(audio_input.reads, 2)

    def test_mqtt_mode_accepts_audio_during_followup(self) -> None:
        self.assertTrue(
            _should_transcribe(
                True,
                FakeWakeGate(False),  # type: ignore[arg-type]
                FakePipeline(True),  # type: ignore[arg-type]
            )
        )

    def test_mqtt_mode_skips_audio_without_wake_or_followup(self) -> None:
        self.assertFalse(
            _should_transcribe(
                True,
                FakeWakeGate(False),  # type: ignore[arg-type]
                FakePipeline(False),  # type: ignore[arg-type]
            )
        )

    def test_software_wake_mode_always_sends_audio_to_whisper(self) -> None:
        self.assertTrue(
            _should_transcribe(
                False,
                FakeWakeGate(False),  # type: ignore[arg-type]
                FakePipeline(False),  # type: ignore[arg-type]
            )
        )


if __name__ == "__main__":
    unittest.main()
