from __future__ import annotations

import unittest

from pc.control.hotkeys import (
    ACTIONS,
    VK_LMENU,
    VK_MEDIA_PLAY_PAUSE,
    VK_TAB,
    HotkeyError,
    HotkeySender,
    describe,
)


class Recorder:
    """記下每次 send 收到的事件批次。"""

    def __init__(self, fail: bool = False) -> None:
        self.batches: list[list] = []
        self.fail = fail

    def __call__(self, events) -> None:
        self.batches.append(list(events))
        if self.fail:
            raise HotkeyError("模擬 UIPI 擋掉")


class HotkeyTests(unittest.TestCase):
    def test_play_pause_is_a_single_media_key_tap(self) -> None:
        rec = Recorder()
        self.assertEqual(HotkeySender(send=rec).fire("play_pause"), "播放/暫停")
        (batch,) = rec.batches
        self.assertEqual([(e.vk, e.up) for e in batch],
                         [(VK_MEDIA_PLAY_PAUSE, False), (VK_MEDIA_PLAY_PAUSE, True)])
        # 媒體鍵是 E0 前綴的延伸鍵
        self.assertTrue(all(e.extended for e in batch))

    def test_alt_tab_sends_all_four_events_in_one_batch(self) -> None:
        """關鍵：四個事件必須在同一次 SendInput，中間插得進別的輸入就可能讓 Alt 卡住。"""
        rec = Recorder()
        HotkeySender(send=rec).fire("alt_tab")
        self.assertEqual(len(rec.batches), 1)
        self.assertEqual([(e.vk, e.up) for e in rec.batches[0]],
                         [(VK_LMENU, False), (VK_TAB, False), (VK_TAB, True), (VK_LMENU, True)])

    def test_every_action_releases_what_it_presses(self) -> None:
        """任何動作都不可以留下按著沒放的鍵。"""
        for name, (events, _) in ACTIONS.items():
            with self.subTest(action=name):
                held: list[int] = []
                for ev in events:
                    if ev.up:
                        self.assertIn(ev.vk, held, f"{name}: 放開了沒按過的鍵")
                        held.remove(ev.vk)
                    else:
                        held.append(ev.vk)
                self.assertEqual(held, [], f"{name}: 結束時還有鍵按著")

    def test_failure_releases_modifiers(self) -> None:
        """送不出去時要補放開修飾鍵，否則 Alt 可能卡在按住的狀態。"""
        rec = Recorder(fail=True)
        with self.assertRaises(HotkeyError):
            HotkeySender(send=rec).fire("alt_tab")
        self.assertEqual(len(rec.batches), 2)                  # 動作本身 + 補放開
        self.assertTrue(all(e.up for e in rec.batches[1]))

    def test_unknown_action_is_rejected(self) -> None:
        rec = Recorder()
        with self.assertRaises(HotkeyError):
            HotkeySender(send=rec).fire("self_destruct")
        self.assertEqual(rec.batches, [])

    def test_dry_run_sends_nothing(self) -> None:
        sender = HotkeySender(dry_run=True)
        self.assertEqual(sender.fire("alt_tab"), "切換視窗 (Alt+Tab)")

    def test_describe_falls_back_to_the_raw_name(self) -> None:
        self.assertEqual(describe("play_pause"), "播放/暫停")
        self.assertEqual(describe("nope"), "nope")


if __name__ == "__main__":
    unittest.main()
