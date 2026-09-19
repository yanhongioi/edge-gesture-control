"""GestureActionDispatcher 的狀態機：每個手勢剛好觸發一次。不需要鏡頭或板子。"""

from __future__ import annotations

import argparse
import unittest

from pc.gesture_control import (
    DEFAULT_ACTION_MAP,
    GestureActionDispatcher,
    parse_action_map,
)


class FakeSender:
    def __init__(self) -> None:
        self.fired: list[str] = []
        self.released = 0

    def fire(self, action: str) -> str:
        self.fired.append(action)
        return action

    def release_modifiers(self) -> None:
        self.released += 1


def make(cooldown: float = 0.0, action_map=None):
    args = argparse.Namespace(action_map=dict(action_map or DEFAULT_ACTION_MAP),
                              action_cooldown=cooldown, dry_run=True)
    sender = FakeSender()
    return GestureActionDispatcher(args, sender=sender), sender


def feed(dispatcher, gestures, start=0.0, step=1.0):
    """把一串確認手勢餵進去，None 代表手不見了。"""
    for i, g in enumerate(gestures):
        dispatcher.update(None if g is None else {"gesture": g}, start + i * step)


class DispatcherTests(unittest.TestCase):
    def test_held_gesture_fires_exactly_once(self) -> None:
        """最重要的一條：比著 thumbs_up 不放，不可以連發。"""
        d, sender = make()
        feed(d, ["thumbs_up"] * 30)
        self.assertEqual(sender.fired, ["play_pause"])

    def test_neutral_gesture_rearms(self) -> None:
        d, sender = make()
        feed(d, ["thumbs_up"] * 5 + ["open"] * 3 + ["thumbs_up"] * 5)
        self.assertEqual(sender.fired, ["play_pause", "play_pause"])

    def test_hand_disappearing_rearms(self) -> None:
        d, sender = make()
        feed(d, ["thumbs_up", "thumbs_up", None, None, "thumbs_up"])
        self.assertEqual(sender.fired, ["play_pause", "play_pause"])

    def test_cooldown_blocks_flicker_through_neutral(self) -> None:
        """thumbs_up → 手不見 → thumbs_up 只差幾十毫秒：中間的 None 會重新上膛，
        所以擋住它的必須是 cooldown。"""
        d, sender = make(cooldown=1.0)
        feed(d, ["thumbs_up", None, "thumbs_up"], step=0.05)
        self.assertEqual(sender.fired, ["play_pause"])
        feed(d, ["open", "thumbs_up"], start=5.0)             # 冷卻過了就能再觸發
        self.assertEqual(sender.fired, ["play_pause", "play_pause"])

    def test_switching_between_two_actions_needs_neutral(self) -> None:
        d, sender = make()
        feed(d, ["thumbs_up", "ok"])                          # 沒回中立，第二個不觸發
        self.assertEqual(sender.fired, ["play_pause"])
        feed(d, ["open", "ok"], start=10.0)
        self.assertEqual(sender.fired, ["play_pause", "alt_tab"])

    def test_unmapped_and_reserved_gestures_do_nothing(self) -> None:
        d, sender = make()
        feed(d, ["point", "two", "three", "six", "rock", "four", "fist"])
        self.assertEqual(sender.fired, [])

    def test_fist_never_fires_even_if_someone_maps_it(self) -> None:
        """fist 是拿刀具的手，設計上永遠不能觸發動作 —— 它被歸在中立手勢裡，
        所以就算硬塞進 action_map 也不會送出去。"""
        d, sender = make(action_map={"fist": "play_pause"})
        feed(d, ["fist"] * 10)
        self.assertEqual(sender.fired, [])

    def test_stop_releases_modifiers(self) -> None:
        d, sender = make()
        d.stop()
        self.assertEqual(sender.released, 1)


class ActionMapParsingTests(unittest.TestCase):
    def test_valid_map(self) -> None:
        self.assertEqual(parse_action_map("thumbs_up=play_pause,ok=alt_tab"),
                         {"thumbs_up": "play_pause", "ok": "alt_tab"})

    def test_rejects_unknown_gesture(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_action_map("shaka=play_pause")

    def test_rejects_unknown_action(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_action_map("ok=launch_missiles")

    def test_rejects_gestures_that_already_have_a_job(self) -> None:
        for reserved in ("point", "two", "fist", "open"):
            with self.subTest(gesture=reserved):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_action_map(f"{reserved}=play_pause")

    def test_rejects_malformed_pairs(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_action_map("thumbs_up")


if __name__ == "__main__":
    unittest.main()
