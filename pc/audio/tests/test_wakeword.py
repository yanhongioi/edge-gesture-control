from __future__ import annotations

import unittest

from pc.audio.wakeword import WakeWordGate


class WakeWordTests(unittest.TestCase):
    def test_same_utterance_returns_command(self) -> None:
        gate = WakeWordGate("嘿小黑鬼")
        decision = gate.process("嘿，小黑鬼，播放周杰倫的晴天", now=10.0)
        self.assertEqual(decision.status, "command")
        self.assertEqual(decision.command, "播放周杰倫的晴天")

    def test_phrase_alone_arms_next_utterance(self) -> None:
        gate = WakeWordGate("嘿小黑鬼", armed_timeout_seconds=8.0)
        self.assertEqual(gate.process("嘿小黑鬼", now=10.0).status, "armed")
        decision = gate.process("幫我找雞胸肉食譜", now=17.9)
        self.assertEqual(decision.status, "command")
        self.assertEqual(decision.command, "幫我找雞胸肉食譜")

    def test_armed_state_expires(self) -> None:
        gate = WakeWordGate("嘿小黑鬼", armed_timeout_seconds=8.0)
        gate.process("嘿小黑鬼", now=10.0)
        self.assertEqual(gate.process("播放晴天", now=18.1).status, "ignored")

    def test_phrase_must_be_at_start(self) -> None:
        gate = WakeWordGate("嘿小黑鬼")
        self.assertEqual(gate.process("我剛才說嘿小黑鬼", now=1.0).status, "ignored")

    def test_external_arm_accepts_next_utterance(self) -> None:
        gate = WakeWordGate("NXP", armed_timeout_seconds=8.0)
        self.assertFalse(gate.is_armed(now=1.0))
        gate.arm(now=10.0)
        self.assertTrue(gate.is_armed(now=17.9))
        self.assertFalse(gate.is_armed(now=18.1))
        decision = gate.process("播放周杰倫的晴天", now=12.0)
        self.assertEqual(decision.status, "command")
        self.assertEqual(decision.command, "播放周杰倫的晴天")
        self.assertFalse(gate.is_armed(now=12.0))

    def test_armed_strips_board_wake_phrase(self) -> None:
        for text in ("Hey NXP，播放音樂", "hey, N X P 播放音樂", "嘿 NXP 播放音樂", "NXP. 播放音樂"):
            gate = WakeWordGate("NXP")
            gate.arm(now=0.0)
            decision = gate.process(text, now=1.0)
            self.assertEqual(decision.status, "command", text)
            self.assertEqual(decision.command, "播放音樂", text)

    def test_armed_wake_phrase_alone_keeps_waiting(self) -> None:
        gate = WakeWordGate("NXP", armed_timeout_seconds=8.0)
        gate.arm(now=0.0)
        self.assertEqual(gate.process("Hey NXP.", now=7.0).status, "armed")
        decision = gate.process("下一首", now=14.0)
        self.assertEqual(decision.status, "command")
        self.assertEqual(decision.command, "下一首")

    def test_armed_keeps_text_not_starting_with_wake(self) -> None:
        gate = WakeWordGate("NXP")
        gate.arm(now=0.0)
        self.assertEqual(gate.process("Hey 你好嗎", now=1.0).command, "Hey 你好嗎")

    def test_cancel_returns_to_idle(self) -> None:
        gate = WakeWordGate("嘿小黑鬼")
        gate.process("嘿小黑鬼", now=1.0)
        self.assertEqual(gate.process("取消", now=2.0).status, "cancelled")
        self.assertFalse(gate.armed)


if __name__ == "__main__":
    unittest.main()
