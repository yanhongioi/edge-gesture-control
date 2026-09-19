from __future__ import annotations

import unittest
from unittest.mock import patch

from pc.speech.windows_tts import WindowsSpeaker, WindowsTtsError


class FakeProcess:
    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code
        self.terminated = False

    def poll(self):  # type: ignore[no-untyped-def]
        return None if not self.terminated else self.return_code

    def wait(self, timeout=None):  # type: ignore[no-untyped-def]
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


class WindowsTtsTests(unittest.TestCase):
    @patch("pc.speech.windows_tts.sys.platform", "win32")
    def test_invalid_settings_are_rejected(self) -> None:
        with self.assertRaises(WindowsTtsError):
            WindowsSpeaker(rate=11)
        with self.assertRaises(WindowsTtsError):
            WindowsSpeaker(volume=101)

    @patch("pc.speech.windows_tts.subprocess.Popen")
    @patch("pc.speech.windows_tts.sys.platform", "win32")
    def test_reply_is_passed_through_environment(self, popen) -> None:  # type: ignore[no-untyped-def]
        popen.return_value = FakeProcess()
        speaker = WindowsSpeaker(voice="測試語音")
        speaker.speak("準備播放晴天。")
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["EDGE_TTS_TEXT"], "準備播放晴天。")
        self.assertEqual(environment["EDGE_TTS_VOICE"], "測試語音")

    @patch("pc.speech.windows_tts.sys.platform", "win32")
    def test_empty_reply_is_rejected(self) -> None:
        speaker = WindowsSpeaker()
        with self.assertRaises(WindowsTtsError):
            speaker.speak_async("  ")


if __name__ == "__main__":
    unittest.main()
