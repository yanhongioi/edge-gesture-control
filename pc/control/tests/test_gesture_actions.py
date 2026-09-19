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


def make(cooldown: float = 0.0, action_map=None, hold: float = 1.0,
         repeat: float = 0.25):
    args = argparse.Namespace(action_map=dict(action_map or DEFAULT_ACTION_MAP),
                              action_cooldown=cooldown, action_hold=hold,
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


FRAME = 1.0 / 30                         # 板子 --mqtt-hz 30，每幀約 33ms


def hold_pose(dispatcher, sender, pose, seconds, start=0.0):
    """像板子一樣用 30 Hz 餵同一個姿勢 seconds 秒，回傳每次送出動作的時間。
    用實際觸發時間斷言，而不是手算次數 —— 次數會被取樣量化影響，時間不會。"""
    name, _, pinch = pose.partition("+")
    hand = {"gesture": name, "pinch": bool(pinch)}
    fired_at, t, end = [], start, start + seconds
    while t <= end + 1e-9:
        before = len(sender.fired)
        dispatcher.update(hand, t)
        if len(sender.fired) > before:
            fired_at.append(t)
        t += FRAME
    return fired_at


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

    def test_volume_waits_for_a_stable_hold_then_repeats(self) -> None:
        """要穩定比著 --action-hold 秒才開始調整，之後每 --action-repeat 秒一階。"""
        d, sender = make(hold=1.0, repeat=0.25)
        at = hold_pose(d, sender, "rock", 2.0)
        self.assertTrue(at, "撐滿一秒之後應該要開始調整")
        self.assertGreaterEqual(at[0], 1.0)                  # 一秒之內一定不動
        self.assertLess(at[0], 1.0 + FRAME * 2)              # 撐滿就要馬上開始
        gaps = [b - a for a, b in zip(at, at[1:])]
        self.assertTrue(all(0.25 <= g < 0.25 + FRAME * 2 for g in gaps), gaps)
        self.assertEqual(set(sender.fired), {"volume_down"})

    def test_pinched_rock_repeats_volume_up(self) -> None:
        d, sender = make()
        at = hold_pose(d, sender, "rock+pinch", 2.0)
        self.assertGreaterEqual(at[0], 1.0)
        self.assertEqual(set(sender.fired), {"volume_up"})

    def test_a_gesture_flickering_past_never_touches_the_volume(self) -> None:
        """這就是 --action-hold 的目的：手在換姿勢的過程中被判成 rock 幾幀，不該調到音量。"""
        d, sender = make()
        hold_pose(d, sender, "rock", 0.24)                   # 0.24 秒就放掉
        hold_pose(d, sender, "open", 0.2, start=0.3)
        self.assertEqual(sender.fired, [])

    def test_the_hold_restarts_when_the_pose_changes(self) -> None:
        """撐了一半改成捏合 = 換了方向，要重新撐滿一秒，不能接續前面的計時。"""
        d, sender = make()
        hold_pose(d, sender, "rock", 0.7)                    # 撐了 0.7 秒就改姿勢
        self.assertEqual(sender.fired, [])
        at = hold_pose(d, sender, "rock+pinch", 1.5, start=0.7)
        self.assertGreaterEqual(at[0], 0.7 + 1.0)            # 從改姿勢那一刻重新起算
        self.assertEqual(set(sender.fired), {"volume_up"})

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
        hold_pose(d, sender, "rock", 1.1)                    # 已經開始調了
        self.assertEqual(sender.fired, ["volume_down"])
        hold_pose(d, sender, "open", 1.0, start=1.2)         # 放掉 rock
        self.assertEqual(sender.fired, ["volume_down"])
        self.assertIsNone(d.repeat_at)

    def test_toggling_the_pinch_switches_volume_direction(self) -> None:
        """調小 → 捏合 → 調大，不用中間回中立 (但各自要撐滿 --action-hold)。"""
        d, sender = make()
        hold_pose(d, sender, "rock", 1.1)                    # 開始調小
        self.assertEqual(sender.fired, ["volume_down"])
        hold_pose(d, sender, "rock+pinch", 1.5, start=1.2)   # 不用回中立，撐滿後改調大
        self.assertEqual(sender.fired[0], "volume_down")
        self.assertIn("volume_up", sender.fired)

    def test_repeat_does_not_need_rearming_but_the_first_press_does(self) -> None:
        """連發是同一次「按住」的延續，但第一次仍要通過 armed。"""
        d, sender = make()
        feed(d, ["open+pinch"], start=0.0)                   # 卸膛
        hold_pose(d, sender, "rock", 3.0, start=0.1)         # 沒回中立 -> 撐再久也不觸發
        self.assertEqual(sender.fired, ["play_pause"])

    def test_send_failure_stops_the_repeat(self) -> None:
        d, sender = make()

        def boom(action):
            raise hotkeys.HotkeyError("UIPI")

        sender.fire = boom
        hold_pose(d, sender, "rock", 1.5)
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
