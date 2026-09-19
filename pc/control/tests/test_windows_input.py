from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from pc.control.windows_input import (
    WindowsInputError,
    adjust_system_volume,
    get_system_volume,
    press_hotkey,
    scroll_vertical,
    set_system_volume,
    set_system_volume_exact,
)


class WindowsInputTests(unittest.TestCase):
    @patch("pc.control.windows_input._user32")
    def test_negative_scroll_uses_signed_wheel_payload(self, mocked_user32) -> None:
        user32 = MagicMock()
        mocked_user32.return_value = user32
        scroll_vertical(-120)
        arguments = user32.mouse_event.call_args.args
        self.assertEqual(arguments[0], 0x0800)
        self.assertEqual(arguments[3].value, (-120) & 0xFFFFFFFF)

    @patch("pc.control.windows_input._user32")
    def test_hotkey_releases_keys_in_reverse_order(self, mocked_user32) -> None:
        user32 = MagicMock()
        mocked_user32.return_value = user32
        press_hotkey(("ctrl", "home"))
        self.assertEqual(
            user32.keybd_event.call_args_list,
            [
                call(0x11, 0, 0, 0),
                call(0x24, 0, 0, 0),
                call(0x24, 0, 0x0002, 0),
                call(0x11, 0, 0x0002, 0),
            ],
        )

    @patch("pc.control.windows_input.time.sleep")
    @patch("pc.control.windows_input.press_hotkey")
    def test_relative_volume_repeats_correct_key(
        self, mocked_hotkey, _mocked_sleep
    ) -> None:
        adjust_system_volume(-3)
        self.assertEqual(
            mocked_hotkey.call_args_list,
            [call(("volumedown",)), call(("volumedown",)), call(("volumedown",))],
        )

    @patch("pc.control.windows_input.time.sleep")
    @patch("pc.control.windows_input.press_hotkey")
    def test_absolute_volume_anchors_at_zero_then_raises(
        self, mocked_hotkey, _mocked_sleep
    ) -> None:
        set_system_volume(50)
        calls = mocked_hotkey.call_args_list
        self.assertEqual(calls[:60], [call(("volumedown",))] * 60)
        self.assertEqual(calls[60:], [call(("volumeup",))] * 25)

    @patch("pc.control.windows_input._com_method")
    @patch("pc.control.windows_input._with_endpoint_volume")
    def test_exact_volume_getter_returns_percentage(
        self, mocked_endpoint, mocked_method
    ) -> None:
        def invoke(callback):  # type: ignore[no-untyped-def]
            return callback(MagicMock())

        def getter(_endpoint, scalar_pointer):  # type: ignore[no-untyped-def]
            scalar_pointer._obj.value = 0.42
            return 0

        mocked_endpoint.side_effect = invoke
        mocked_method.return_value = getter
        self.assertEqual(get_system_volume(), 42)

    @patch("pc.control.windows_input._com_method")
    @patch("pc.control.windows_input._with_endpoint_volume")
    def test_exact_volume_setter_uses_scalar(
        self, mocked_endpoint, mocked_method
    ) -> None:
        received: list[float] = []

        def invoke(callback):  # type: ignore[no-untyped-def]
            return callback(MagicMock())

        def setter(_endpoint, scalar, _context):  # type: ignore[no-untyped-def]
            received.append(float(scalar.value))
            return 0

        mocked_endpoint.side_effect = invoke
        mocked_method.return_value = setter
        set_system_volume_exact(35)
        self.assertAlmostEqual(received[0], 0.35, places=5)

    def test_exact_volume_setter_rejects_invalid_level(self) -> None:
        for level in (-1, 101, True):
            with self.subTest(level=level), self.assertRaises(WindowsInputError):
                set_system_volume_exact(level)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
