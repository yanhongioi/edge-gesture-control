from __future__ import annotations

import unittest
from pathlib import Path

from pc.control.timer import TimerError, start_timer


class FakeProcess:
    pid = 4321


class TimerTests(unittest.TestCase):
    def test_timer_alarm_asset_is_bundled_and_used_by_worker(self) -> None:
        control_dir = Path(__file__).resolve().parents[1]
        alarm = control_dir / "assets" / "timer_sound.mp3"
        worker_text = (control_dir / "timer_window.ps1").read_text(encoding="utf-8")
        self.assertTrue(alarm.is_file())
        self.assertGreater(alarm.stat().st_size, 0)
        self.assertIn("assets\\timer_sound.mp3", worker_text)
        self.assertIn("System.Windows.Media.MediaPlayer", worker_text)

    def test_starts_detached_worker_with_validated_arguments(self) -> None:
        calls = []

        def fake_process(command, **kwargs):  # type: ignore[no-untyped-def]
            calls.append((command, kwargs))
            return FakeProcess()

        result = start_timer(300, "煮蛋", process_factory=fake_process)
        self.assertEqual(result.seconds, 300)
        self.assertEqual(result.label, "煮蛋")
        self.assertEqual(result.process_id, 4321)
        self.assertIn("powershell", calls[0][0][0].lower())
        self.assertIn("timer_window.ps1", calls[0][0][5])
        self.assertEqual(calls[0][0][-4:], ["-Seconds", "300", "-Label", "煮蛋"])

    def test_rejects_invalid_duration(self) -> None:
        for seconds in (0, -1, 86_401, True):
            with self.subTest(seconds=seconds), self.assertRaises(TimerError):
                start_timer(seconds)  # type: ignore[arg-type]

    def test_rejects_empty_or_long_label(self) -> None:
        with self.assertRaises(TimerError):
            start_timer(1, "  ")
        with self.assertRaises(TimerError):
            start_timer(1, "字" * 81)


if __name__ == "__main__":
    unittest.main()
