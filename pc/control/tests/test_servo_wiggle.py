"""Tests for the non-blocking camera acknowledgement wiggle."""

from __future__ import annotations

import unittest

from board.servo import WiggleMotion


class WiggleMotionTests(unittest.TestCase):
    def test_wiggle_moves_to_both_sides_and_returns_to_origin(self) -> None:
        motion = WiggleMotion(0.2)
        motion.start(10.0, initial_direction=1)
        self.assertEqual(motion.direction(10.1), 1)
        self.assertEqual(motion.direction(10.3), -1)
        self.assertEqual(motion.direction(10.7), 1)
        self.assertIsNone(motion.direction(10.81))

    def test_disabled_wiggle_never_moves(self) -> None:
        motion = WiggleMotion(0)
        motion.start(10.0)
        self.assertIsNone(motion.direction(10.0))

    def test_wiggle_can_start_toward_the_other_side(self) -> None:
        motion = WiggleMotion(0.2)
        motion.start(10.0, initial_direction=-1)
        self.assertEqual(motion.direction(10.1), -1)
        self.assertEqual(motion.direction(10.3), 1)


if __name__ == "__main__":
    unittest.main()
