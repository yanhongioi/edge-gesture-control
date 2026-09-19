"""ScrollJoystick：拇指捏合決定捲動方向，手推離起點決定速度。不需要鏡頭或板子。"""

from __future__ import annotations

import argparse
import unittest

from pc.gesture_control import PALM, SCROLL_GESTURE, ScrollJoystick


def make(**overrides):
    args = argparse.Namespace(scroll_base=360.0, scroll_gain=12000.0, scroll_dead=0.03,
                              scroll_max=3000.0, scroll_hold=0.5, scroll_invert=False)
    for k, v in overrides.items():
        setattr(args, k, v)
    return ScrollJoystick(args)


def hand(pinch=False, y=0.5, gesture=SCROLL_GESTURE):
    """一隻手：手掌的點都放在同一個高度 y，方便直接控制「推離起點」的量。"""
    lm = [[0.5, 0.5, 0.0] for _ in range(21)]
    for i in PALM:
        lm[i] = [0.5, y, 0.0]
    return {"gesture": gesture, "pinch": pinch, "landmarks": lm}


class DirectionTests(unittest.TestCase):
    def test_pinch_scrolls_up(self) -> None:
        self.assertEqual(ScrollJoystick.direction({"pinch": True}), 1)

    def test_no_pinch_scrolls_down(self) -> None:
        self.assertEqual(ScrollJoystick.direction({"pinch": False}), -1)

    def test_missing_pinch_key_scrolls_down(self) -> None:
        self.assertEqual(ScrollJoystick.direction({}), -1)


class ScrollTests(unittest.TestCase):
    def test_two_without_pinch_scrolls_down_at_base_speed(self) -> None:
        s = make()
        s.update(hand(pinch=False), 0.0)
        self.assertEqual(s.rate, -360.0)

    def test_pinching_scrolls_up_at_base_speed(self) -> None:
        s = make()
        s.update(hand(pinch=True), 0.0)
        self.assertEqual(s.rate, 360.0)

    def test_pushing_up_while_pinched_accelerates_upward(self) -> None:
        s = make()
        s.update(hand(pinch=True, y=0.5), 0.0)          # 起點
        s.update(hand(pinch=True, y=0.5 - 0.13), 0.1)   # 手往上 0.13，扣掉不加速區 0.03
        self.assertAlmostEqual(s.rate, 360.0 + 12000.0 * 0.10, places=3)

    def test_pushing_down_while_not_pinched_accelerates_downward(self) -> None:
        s = make()
        s.update(hand(pinch=False, y=0.5), 0.0)
        s.update(hand(pinch=False, y=0.5 + 0.13), 0.1)
        self.assertAlmostEqual(s.rate, -(360.0 + 12000.0 * 0.10), places=3)

    def test_pushing_the_wrong_way_does_not_go_below_base_speed(self) -> None:
        s = make()
        s.update(hand(pinch=True, y=0.5), 0.0)
        s.update(hand(pinch=True, y=0.9), 0.1)          # 捏著卻把手往下移
        self.assertEqual(s.rate, 360.0)

    def test_deadzone_near_the_anchor(self) -> None:
        s = make()
        s.update(hand(pinch=True, y=0.5), 0.0)
        s.update(hand(pinch=True, y=0.5 - 0.02), 0.1)   # 還在 --scroll-dead 0.03 以內
        self.assertEqual(s.rate, 360.0)

    def test_speed_is_capped(self) -> None:
        s = make()
        s.update(hand(pinch=True, y=0.9), 0.0)
        s.update(hand(pinch=True, y=0.0), 0.1)
        self.assertEqual(s.rate, 3000.0)

    def test_changing_direction_resets_the_anchor(self) -> None:
        """捏著把手舉高 (快速往上) 之後放開：要從基本速度重新往下捲，
        不能拿舊起點去算，否則一放開就從最高速往下衝。"""
        s = make()
        s.update(hand(pinch=True, y=0.5), 0.0)
        s.update(hand(pinch=True, y=0.2), 0.1)
        self.assertGreater(s.rate, 360.0)
        s.update(hand(pinch=False, y=0.2), 0.2)
        self.assertEqual(s.rate, -360.0)
        self.assertAlmostEqual(s.anchor, 0.2, places=6)

    def test_invert_flips_both_directions(self) -> None:
        s = make(scroll_invert=True)
        s.update(hand(pinch=True), 0.0)
        self.assertEqual(s.rate, -360.0)
        s.update(hand(pinch=False), 0.1)
        self.assertEqual(s.rate, 360.0)

    def test_other_gestures_do_not_start_scrolling(self) -> None:
        s = make()
        for g in ("point", "open", "fist", "thumbs_up"):
            with self.subTest(gesture=g):
                s.update(hand(gesture=g), 0.0)
                self.assertIsNone(s.anchor)
                self.assertEqual(s.rate, 0.0)

    def test_brief_dropout_keeps_scrolling(self) -> None:
        """掉幀期間速度不變，起點和方向都保留。"""
        s = make()
        s.update(hand(pinch=True, y=0.5), 0.0)
        s.update(hand(pinch=True, y=0.3), 0.1)
        fast = s.rate
        s.update(None, 0.2)
        self.assertEqual(s.rate, fast)
        self.assertAlmostEqual(s.anchor, 0.5, places=6)

    def test_long_dropout_stops(self) -> None:
        """--scroll-hold 從「手第一次不見」那一刻開始算，所以第一個掉幀只是起算點。"""
        s = make()
        s.update(hand(pinch=True), 0.0)
        s.update(None, 1.0)                              # 掉幀開始計時，還在捲
        self.assertIsNotNone(s.anchor)
        s.update(None, 1.4)                              # 0.4 秒，還在 --scroll-hold 0.5 以內
        self.assertIsNotNone(s.anchor)
        s.update(None, 1.6)                              # 0.6 秒，超時
        self.assertIsNone(s.anchor)
        self.assertEqual(s.rate, 0.0)
        self.assertEqual(s.dir, 0)


if __name__ == "__main__":
    unittest.main()
