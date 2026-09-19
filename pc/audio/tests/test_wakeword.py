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

    def test_cancel_returns_to_idle(self) -> None:
        gate = WakeWordGate("嘿小黑鬼")
        gate.process("嘿小黑鬼", now=1.0)
        self.assertEqual(gate.process("取消", now=2.0).status, "cancelled")
        self.assertFalse(gate.armed)


if __name__ == "__main__":
    unittest.main()
