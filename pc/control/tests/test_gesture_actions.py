"""GestureActionDispatcher 的狀態機：每個姿勢剛好觸發一次。不需要鏡頭或板子。"""

from __future__ import annotations

import argparse
import unittest

from pc.control import hotkeys
from pc.gesture_control import (
    DEFAULT_ACTION_MAP,
    GestureActionDispatcher,
    gesture_token,
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


def make(cooldown: float = 0.0, action_map=None, repeat_delay: float = 0.4,
         repeat: float = 0.15):
    args = argparse.Namespace(action_map=dict(action_map or DEFAULT_ACTION_MAP),
                              action_cooldown=cooldown, action_repeat_delay=repeat_delay,
                              action_repeat=repeat, dry_run=True)
    sender = FakeSender()
    return GestureActionDispatcher(args, sender=sender), sender


def feed(dispatcher, poses, start=0.0, step=1.0):
    """把一串姿勢餵進去。每個元素是手勢名稱、"名稱+pinch"，或 None (手不見了)。"""
    for i, pose in enumerate(poses):
        if pose is None:
            hand = None
        else:
            name, _, pinch = pose.partition("+")
            hand = {"gesture": name, "pinch": bool(pinch)}
        dispatcher.update(hand, start + i * step)


class TokenTests(unittest.TestCase):
    def test_pinch_state_is_part_of_the_token(self) -> None:
        self.assertEqual(gesture_token({"gesture": "open", "pinch": False}), "open")
        self.assertEqual(gesture_token({"gesture": "open", "pinch": True}), "open+pinch")
        self.assertIsNone(gesture_token(None))
        self.assertIsNone(gesture_token({"gesture": None, "pinch": True}))


class DispatcherTests(unittest.TestCase):
    def test_held_pinch_fires_exactly_once(self) -> None:
        """最重要的一條：捏著不放，不可以連發。"""
        d, sender = make()
        feed(d, ["open+pinch"] * 30)
        self.assertEqual(sender.fired, ["play_pause"])

    def test_releasing_the_pinch_rearms(self) -> None:
        """open / open+pinch 的核心節奏：張開 → 捏 → 放開 → 再捏。"""
        d, sender = make()
        feed(d, ["open", "open+pinch", "open", "open+pinch", "open", "open+pinch"])
        self.assertEqual(sender.fired, ["play_pause"] * 3)

    def test_open_without_pinch_never_fires(self) -> None:
        d, sender = make()
        feed(d, ["open"] * 20)
        self.assertEqual(sender.fired, [])

    def test_thumbs_up_switches_windows(self) -> None:
        d, sender = make()
        feed(d, ["thumbs_up"] * 5 + ["open"] * 3 + ["thumbs_up"] * 5)
        self.assertEqual(sender.fired, ["alt_tab", "alt_tab"])

    def test_hand_disappearing_rearms(self) -> None:
        d, sender = make()
        feed(d, ["thumbs_up", "thumbs_up", None, None, "thumbs_up"])
        self.assertEqual(sender.fired, ["alt_tab", "alt_tab"])

    def test_cooldown_blocks_pinch_flicker(self) -> None:
        """捏合在門檻附近抖動時，中間的 open 會重新上膛，所以擋住它的必須是 cooldown。"""
        d, sender = make(cooldown=1.0)
        feed(d, ["open+pinch", "open", "open+pinch"], step=0.05)
        self.assertEqual(sender.fired, ["play_pause"])
        feed(d, ["open", "open+pinch"], start=5.0)            # 冷卻過了就能再觸發
        self.assertEqual(sender.fired, ["play_pause", "play_pause"])

    def test_switching_between_two_actions_needs_neutral(self) -> None:
        d, sender = make()
        feed(d, ["open+pinch", "thumbs_up"])                  # 沒回中立，第二個不觸發
        self.assertEqual(sender.fired, ["play_pause"])
        feed(d, ["open", "thumbs_up"], start=10.0)
        self.assertEqual(sender.fired, ["play_pause", "alt_tab"])

    def test_unmapped_and_reserved_gestures_do_nothing(self) -> None:
        d, sender = make()
        feed(d, ["point", "point+pinch", "two", "three", "six", "ok", "four", "fist"])
        self.assertEqual(sender.fired, [])

    def test_pointing_and_clicking_never_fires_a_hotkey(self) -> None:
        """point+pinch 是滑鼠左鍵，絕對不可以同時觸發快捷鍵。"""
        d, sender = make()
        feed(d, ["point", "point+pinch", "point", "point+pinch"])
        self.assertEqual(sender.fired, [])

    def test_fist_never_fires_even_if_someone_maps_it(self) -> None:
        """fist 是拿刀具的手，設計上永遠不能觸發動作 —— 它是中立 token，
        所以就算硬塞進 action_map 也不會送出去。"""
        d, sender = make(action_map={"fist": "play_pause"})
        feed(d, ["fist"] * 10)
        self.assertEqual(sender.fired, [])

    def test_volume_repeats_while_held(self) -> None:
        """音量是連發動作：比著 rock 不放，每隔 --action-repeat 秒再送一次。"""
        d, sender = make(repeat_delay=0.4, repeat=0.15)
        feed(d, ["rock"], start=0.0)
        self.assertEqual(sender.fired, ["volume_down"])      # 第一次
        feed(d, ["rock"] * 10, start=0.03, step=0.03)        # 到 0.30，還沒到連發延遲
        self.assertEqual(sender.fired, ["volume_down"])
        # 0.33 ~ 0.99 每 30ms 一幀 (板子送 30 Hz)：0.4 開始連發，之後每 0.15 秒一次
        feed(d, ["rock"] * 23, start=0.33, step=0.03)
        self.assertEqual(sender.fired, ["volume_down"] * 5)

    def test_pinched_rock_repeats_volume_up(self) -> None:
        d, sender = make()
        feed(d, ["rock+pinch"] * 30, step=0.03)              # 0.87 秒
        self.assertEqual(sender.fired, ["volume_up"] * 5)

    def test_one_shot_actions_never_repeat(self) -> None:
        """播放/暫停連發等於沒按，Alt+Tab 連發會在兩個視窗之間狂跳。"""
        d, sender = make()
        feed(d, ["open+pinch"] * 50, step=0.1)
        self.assertEqual(sender.fired, ["play_pause"])
        d2, sender2 = make()
        feed(d2, ["thumbs_up"] * 50, step=0.1)
        self.assertEqual(sender2.fired, ["alt_tab"])

    def test_releasing_the_gesture_stops_the_repeat(self) -> None:
        d, sender = make()
        feed(d, ["rock"], start=0.0)
        feed(d, ["open"], start=0.4)                         # 放掉 rock
        feed(d, ["open"], start=1.0)
        self.assertEqual(sender.fired, ["volume_down"])
        self.assertIsNone(d.repeat_at)

    def test_toggling_the_pinch_switches_volume_direction(self) -> None:
        """rock 捏一下放一下 = 音量上下切換，不用回中立 (中間 token 變了 = 新的觸發)。"""
        d, sender = make()
        feed(d, ["rock", "rock+pinch", "rock"], step=0.05)
        self.assertEqual(sender.fired, ["volume_down", "volume_up", "volume_down"])

    def test_repeat_does_not_need_rearming_but_the_first_press_does(self) -> None:
        """連發是同一次「按住」的延續，但第一次仍要通過 armed。"""
        d, sender = make()
        feed(d, ["open+pinch"], start=0.0)                   # 卸膛
        feed(d, ["rock"], start=0.1)                         # 沒回中立 -> 不觸發
        self.assertEqual(sender.fired, ["play_pause"])
        feed(d, ["rock"], start=1.0)                         # 持續中也不會補觸發
        self.assertEqual(sender.fired, ["play_pause"])

    def test_send_failure_stops_the_repeat(self) -> None:
        d, sender = make()

        def boom(action):
            raise hotkeys.HotkeyError("UIPI")

        feed(d, ["rock"], start=0.0)
        sender.fire = boom
        feed(d, ["rock"], start=0.4)
        self.assertIsNone(d.repeat_at)

    def test_stop_releases_modifiers(self) -> None:
        d, sender = make()
        d.stop()
        self.assertEqual(sender.released, 1)


class ActionMapParsingTests(unittest.TestCase):
    def test_valid_map(self) -> None:
        self.assertEqual(parse_action_map("open+pinch=play_pause,thumbs_up=alt_tab"),
                         {"open+pinch": "play_pause", "thumbs_up": "alt_tab"})

    def test_default_map_round_trips(self) -> None:
        text = ",".join(f"{t}={a}" for t, a in DEFAULT_ACTION_MAP.items())
        self.assertEqual(parse_action_map(text), DEFAULT_ACTION_MAP)

    def test_rejects_unknown_gesture(self) -> None:
        for bad in ("shaka=play_pause", "shaka+pinch=play_pause"):
            with self.subTest(text=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_action_map(bad)

    def test_rejects_unknown_action(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_action_map("ok=launch_missiles")

    def test_rejects_gestures_that_already_have_a_job(self) -> None:
        """point / two / fist 連 +pinch 的版本都不能綁。"""
        for reserved in ("point", "point+pinch", "two", "two+pinch", "fist", "fist+pinch"):
            with self.subTest(token=reserved):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_action_map(f"{reserved}=play_pause")

    def test_rejects_bare_open_because_it_is_neutral(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_action_map("open=play_pause")
        self.assertEqual(parse_action_map("open+pinch=play_pause"), {"open+pinch": "play_pause"})

    def test_rejects_malformed_pairs(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_action_map("thumbs_up")



class HelpTests(unittest.TestCase):
    """--help 不在任何其他測試的路徑上，但它是使用者第一個會下的指令。"""

    def test_help_renders(self) -> None:
        """argparse 會對 help 字串做 % 格式化，裸的 % 會在 --help 時才爆掉
        (例如「推離 10% 畫面高度」)，程式其餘部分完全正常，所以只有這裡抓得到。"""
        import contextlib
        import io as _io

        from pc import gesture_control

        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            gesture_control.main.__globals__  # 確保模組載入
            import sys
            argv = sys.argv
            sys.argv = ["gesture_control.py", "--help"]
            try:
                gesture_control.main()
            finally:
                sys.argv = argv
        self.assertIn("--action-map", buf.getvalue())



if __name__ == "__main__":
    unittest.main()
