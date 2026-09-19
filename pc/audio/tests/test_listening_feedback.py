from __future__ import annotations

import unittest

from pc.audio.listening_feedback import ListeningFeedback


class FakeIndicator:
    def __init__(self) -> None:
        self.events: list[str] = []

    def show(self) -> None:
        self.events.append("show")

    def hide(self) -> None:
        self.events.append("hide")

    def close(self) -> None:
        self.events.append("close")


class ListeningFeedbackTests(unittest.TestCase):
    def test_volume_above_limit_is_lowered_then_restored(self) -> None:
        indicator = FakeIndicator()
        levels: list[int] = []
        feedback = ListeningFeedback(
            indicator=indicator,  # type: ignore[arg-type]
            volume_limit=35,
            volume_getter=lambda: 72,
            volume_setter=levels.append,
        )
        feedback.start()
        self.assertTrue(feedback.active)
        self.assertEqual(levels, [35])
        self.assertEqual(indicator.events, ["show"])
        feedback.stop()
        self.assertFalse(feedback.active)
        self.assertEqual(levels, [35, 72])
        self.assertEqual(indicator.events, ["show", "hide"])

    def test_volume_at_or_below_limit_is_not_changed(self) -> None:
        for current in (20, 35):
            with self.subTest(current=current):
                levels: list[int] = []
                feedback = ListeningFeedback(
                    indicator=FakeIndicator(),  # type: ignore[arg-type]
                    volume_limit=35,
                    volume_getter=lambda: current,
                    volume_setter=levels.append,
                )
                feedback.start()
                feedback.stop()
                self.assertEqual(levels, [])

    def test_repeated_start_and_stop_are_idempotent(self) -> None:
        indicator = FakeIndicator()
        levels: list[int] = []
        feedback = ListeningFeedback(
            indicator=indicator,  # type: ignore[arg-type]
            volume_limit=35,
            volume_getter=lambda: 80,
            volume_setter=levels.append,
        )
        feedback.start()
        feedback.start()
        feedback.stop()
        feedback.stop()
        self.assertEqual(levels, [35, 80])
        self.assertEqual(indicator.events, ["show", "hide"])

    def test_close_restores_volume_and_closes_indicator(self) -> None:
        indicator = FakeIndicator()
        levels: list[int] = []
        feedback = ListeningFeedback(
            indicator=indicator,  # type: ignore[arg-type]
            volume_limit=35,
            volume_getter=lambda: 50,
            volume_setter=levels.append,
        )
        feedback.start()
        feedback.close()
        self.assertEqual(levels, [35, 50])
        self.assertEqual(indicator.events, ["show", "hide", "close"])

    def test_dry_run_keeps_volume_unchanged(self) -> None:
        indicator = FakeIndicator()
        levels: list[int] = []
        feedback = ListeningFeedback(
            indicator=indicator,  # type: ignore[arg-type]
            volume_getter=lambda: 90,
            volume_setter=levels.append,
            manage_volume=False,
        )
        feedback.start()
        feedback.stop()
        self.assertEqual(levels, [])
        self.assertEqual(indicator.events, ["show", "hide"])

if __name__ == "__main__":
    unittest.main()
