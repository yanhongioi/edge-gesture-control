"""Small allowlisted Windows input primitives used by validated tool actions."""

from __future__ import annotations

import ctypes
import sys
import time
import uuid


MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002

VK_CODES = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "delete": 0x2E,
    "win": 0x5B,
    "volumemute": 0xAD,
    "volumedown": 0xAE,
    "volumeup": 0xAF,
    "nexttrack": 0xB0,
    "prevtrack": 0xB1,
    "playpause": 0xB3,
    **{character: ord(character.upper()) for character in "abcdefghijklmnopqrstuvwxyz"},
    **{character: ord(character) for character in "0123456789"},
}


class WindowsInputError(RuntimeError):
    """A validated Windows input event could not be sent."""


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    )


def _guid(value: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _check_hresult(result: int, operation: str) -> None:
    if result < 0:
        raise WindowsInputError(f"Windows Core Audio {operation}失敗：0x{result & 0xFFFFFFFF:08X}")


def _com_method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):  # type: ignore[no-untyped-def]
    vtable = ctypes.cast(
        pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])


def _with_endpoint_volume(callback):  # type: ignore[no-untyped-def]
    if sys.platform != "win32":
        raise WindowsInputError("系統音量查詢目前只支援 Windows")
    ole32 = ctypes.windll.ole32
    coinit_result = int(ole32.CoInitializeEx(None, 0x2))
    rpc_changed_mode = -2147417850
    should_uninitialize = coinit_result in (0, 1)
    if coinit_result < 0 and coinit_result != rpc_changed_mode:
        _check_hresult(coinit_result, "初始化")

    enumerator = ctypes.c_void_p()
    device = ctypes.c_void_p()
    endpoint = ctypes.c_void_p()
    try:
        clsid_enumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
        iid_enumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
        iid_endpoint = _guid("5CDF2C82-841E-4546-9722-0CF74078229A")
        result = int(
            ole32.CoCreateInstance(
                ctypes.byref(clsid_enumerator),
                None,
                23,
                ctypes.byref(iid_enumerator),
                ctypes.byref(enumerator),
            )
        )
        _check_hresult(result, "建立裝置列舉器")
        get_default = _com_method(
            enumerator,
            4,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _check_hresult(
            int(get_default(enumerator, 0, 1, ctypes.byref(device))),
            "取得預設輸出裝置",
        )
        activate = _com_method(
            device,
            3,
            ctypes.c_long,
            ctypes.POINTER(_GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _check_hresult(
            int(
                activate(
                    device,
                    ctypes.byref(iid_endpoint),
                    23,
                    None,
                    ctypes.byref(endpoint),
                )
            ),
            "開啟音量控制",
        )
        return callback(endpoint)
    except OSError as exc:
        raise WindowsInputError(f"無法使用 Windows Core Audio：{exc}") from exc
    finally:
        for pointer in (endpoint, device, enumerator):
            if pointer.value:
                release = _com_method(pointer, 2, ctypes.c_ulong)
                release(pointer)
        if should_uninitialize:
            ole32.CoUninitialize()


def get_system_volume() -> int:
    """Read the default multimedia output volume as an integer percentage."""

    def read(endpoint: ctypes.c_void_p) -> int:
        scalar = ctypes.c_float()
        getter = _com_method(
            endpoint,
            9,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_float),
        )
        _check_hresult(int(getter(endpoint, ctypes.byref(scalar))), "讀取音量")
        return max(0, min(100, round(float(scalar.value) * 100)))

    return int(_with_endpoint_volume(read))


def set_system_volume_exact(level: int) -> None:
    """Set the default multimedia output volume without simulated key presses."""
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        raise WindowsInputError("音量必須介於 0 到 100")

    def write(endpoint: ctypes.c_void_p) -> None:
        setter = _com_method(
            endpoint,
            7,
            ctypes.c_long,
            ctypes.c_float,
            ctypes.c_void_p,
        )
        _check_hresult(
            int(setter(endpoint, ctypes.c_float(level / 100), None)),
            "設定音量",
        )

    _with_endpoint_volume(write)


def _user32():  # type: ignore[no-untyped-def]
    if sys.platform != "win32":
        raise WindowsInputError("電腦控制目前只支援 Windows")
    try:
        return ctypes.windll.user32
    except AttributeError as exc:
        raise WindowsInputError("找不到 Windows user32 API") from exc


def scroll_vertical(amount: int) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
        raise WindowsInputError("捲動量必須是非零整數")
    try:
        _user32().mouse_event(
            MOUSEEVENTF_WHEEL,
            0,
            0,
            ctypes.c_uint32(amount & 0xFFFFFFFF),
            0,
        )
    except (OSError, OverflowError) as exc:
        raise WindowsInputError(f"無法送出 Windows 捲動事件：{exc}") from exc


def press_hotkey(keys: tuple[str, ...]) -> None:
    if not keys:
        raise WindowsInputError("快捷鍵不可為空")
    try:
        codes = tuple(VK_CODES[key] for key in keys)
    except KeyError as exc:
        raise WindowsInputError(f"不支援的 Windows 按鍵：{exc.args[0]}") from exc

    user32 = _user32()
    pressed: list[int] = []
    try:
        for code in codes:
            user32.keybd_event(code, 0, 0, 0)
            pressed.append(code)
    finally:
        for code in reversed(pressed):
            user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)


def _press_repeated(key: str, count: int) -> None:
    for _ in range(count):
        press_hotkey((key,))
        time.sleep(0.005)


def adjust_system_volume(steps: int) -> None:
    if isinstance(steps, bool) or not isinstance(steps, int) or steps == 0:
        raise WindowsInputError("音量調整格數必須是非零整數")
    key = "volumeup" if steps > 0 else "volumedown"
    _press_repeated(key, abs(steps))


def set_system_volume(level: int) -> None:
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        raise WindowsInputError("音量必須介於 0 到 100")
    # Windows 音量鍵通常每次調整約 2%。先碰到端點，便不需要讀取系統狀態。
    if level == 0:
        _press_repeated("volumedown", 60)
        return
    if level == 100:
        _press_repeated("volumeup", 60)
        return
    _press_repeated("volumedown", 60)
    _press_repeated("volumeup", round(level / 2))
