from __future__ import annotations

import unittest

from pc.audio.board_wake import parse_wake_message


class BoardWakeMessageTests(unittest.TestCase):
    def test_wakeword_message(self) -> None:
        payload = b'{"type": "wakeword", "id": 1, "wakeword": "HEY NXP", "ts": 1.0}'
        self.assertEqual(parse_wake_message(payload), "HEY NXP")

    def test_vit_command_is_not_a_wake(self) -> None:
        payload = '{"type": "command", "wakeword_id": 1, "id": 2, "command": "NEXT"}'
        self.assertIsNone(parse_wake_message(payload))

    def test_invalid_payload(self) -> None:
        for payload in (b"", b"not json", b"[1, 2]", b'{"type": "test"}'):
            self.assertIsNone(parse_wake_message(payload))


if __name__ == "__main__":
    unittest.main()
