# --------------------------------------------------------------------------------------
# Windows 按鍵注入 (SendInput)：給手勢 / 語音端共用的「按一下快捷鍵」
#
#   play_pause  媒體鍵 VK_MEDIA_PLAY_PAUSE。系統會轉成 WM_APPCOMMAND 交給目前的媒體
#               session (SMTC)，跟前景視窗無關 —— 比手勢時焦點通常不在播放器上，所以
#               這是唯一可靠的做法。副作用：同時開 YouTube 和 Spotify 時，送到「最後
#               播放的那個」，不保證是你想要的。
#   alt_tab     Alt 按住 → Tab 點一下 → Alt 放開，切回上一個視窗。
#
# 為什麼所有事件都塞進同一次 SendInput：
#   Alt 按下去之後如果 Tab 沒送成 (被其他輸入插隊、程式中途掛掉)，系統會停在「Alt 一直
#   按著」的狀態，之後每個按鍵都變成選單快速鍵，整台電腦像壞掉。一次 SendInput 傳整個
#   陣列是原子的，中間插不進別的輸入；再加上 release_modifiers() 收尾當第二層保險。
# --------------------------------------------------------------------------------------

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

# Virtual-Key codes (WinUser.h)
VK_TAB = 0x09
VK_LMENU = 0xA4          # 左 Alt
VK_LSHIFT = 0xA0
VK_LCONTROL = 0xA2
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

# 程式結束 / 動作失敗時要確保放開的修飾鍵 (避免 Alt 卡住)
MODIFIER_VKS = (VK_LMENU, VK_LCONTROL, VK_LSHIFT, VK_LWIN, VK_RWIN)


class HotkeyError(RuntimeError):
    """按鍵送不出去 (通常是前景視窗以系統管理員身分執行，注入被 UIPI 丟掉)。"""


@dataclass(frozen=True)
class KeyEvent:
    """一個按鍵事件。extended=True 用於 E0 前綴的鍵 (媒體鍵、方向鍵)。"""

    vk: int
    up: bool = False
    extended: bool = False


def tap(vk: int, *, extended: bool = False) -> tuple[KeyEvent, ...]:
    """按一下 (按下 + 放開)。"""
    return (KeyEvent(vk, extended=extended), KeyEvent(vk, up=True, extended=extended))


def chord(modifier_vk: int, vk: int) -> tuple[KeyEvent, ...]:
    """按住修飾鍵 → 點一下主鍵 → 放開修飾鍵。"""
    return (KeyEvent(modifier_vk), KeyEvent(vk), KeyEvent(vk, up=True),
            KeyEvent(modifier_vk, up=True))


# 手勢 / 語音可以觸發的動作。要加新的就加在這裡，dispatcher 只認這張表的 key。
ACTIONS: dict[str, tuple[tuple[KeyEvent, ...], str]] = {
    "play_pause": (tap(VK_MEDIA_PLAY_PAUSE, extended=True), "播放/暫停"),
    "alt_tab": (chord(VK_LMENU, VK_TAB), "切換視窗 (Alt+Tab)"),
    "next_track": (tap(VK_MEDIA_NEXT_TRACK, extended=True), "下一首"),
    "prev_track": (tap(VK_MEDIA_PREV_TRACK, extended=True), "上一首"),
    "mute": (tap(VK_VOLUME_MUTE, extended=True), "靜音"),
    "volume_up": (tap(VK_VOLUME_UP, extended=True), "音量+"),
    "volume_down": (tap(VK_VOLUME_DOWN, extended=True), "音量-"),
}


def describe(action: str) -> str:
    """動作的中文說明；未知動作回傳原名。"""
    entry = ACTIONS.get(action)
    return entry[1] if entry else action


# --------------------------------------------------------------------------------------
# Windows SendInput
# --------------------------------------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

_structs = None


def _win_structs():
    """延遲建立 ctypes 結構：非 Windows 上 import ctypes.wintypes 會失敗，
    但測試 (注入假的 send) 不該因此跑不起來。"""
    global _structs
    if _structs is not None:
        return _structs
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t        # ULONG_PTR：32/64 位元都跟著指標大小走

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ulong_ptr)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    # MOUSEINPUT 是三者中最大的，一定要一起定義：SendInput 會檢查 cbSize 等於
    # 真正的 sizeof(INPUT)，只放 KEYBDINPUT 的話大小不對，整批會被拒絕。
    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    _structs = (ctypes, INPUT, KEYBDINPUT)
    return _structs


def send_windows(events: Sequence[KeyEvent]) -> None:
    """把整批事件用一次 SendInput 送出去 (原子，中間插不進其他輸入)。"""
    if not events:
        return
    ctypes, INPUT, KEYBDINPUT = _win_structs()
    buf = (INPUT * len(events))()
    for i, ev in enumerate(events):
        flags = 0
        if ev.up:
            flags |= KEYEVENTF_KEYUP
        if ev.extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        buf[i].type = INPUT_KEYBOARD
        buf[i].u.ki = KEYBDINPUT(wVk=ev.vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    sent = ctypes.windll.user32.SendInput(len(events), ctypes.byref(buf),
                                          ctypes.sizeof(INPUT))
    if sent != len(events):
        err = ctypes.get_last_error()
        raise HotkeyError(
            f"SendInput 只送出 {sent}/{len(events)} 個事件 (GetLastError={err})。"
            "前景視窗如果是以系統管理員身分執行，注入會被 UIPI 丟掉；"
            "這時請也用系統管理員身分執行本程式。"
        )


class HotkeySender:
    """送出 ACTIONS 裡的快捷鍵。send 可以換掉 (測試 / --dry-run)。"""

    def __init__(self, send: Callable[[Sequence[KeyEvent]], None] | None = None,
                 dry_run: bool = False) -> None:
        if send is None and not dry_run and sys.platform != "win32":
            raise HotkeyError("按鍵注入目前只支援 Windows")
        self.dry_run = dry_run
        self.send = send or ((lambda events: None) if dry_run else send_windows)

    def fire(self, action: str) -> str:
        """送出一個動作，回傳中文說明。未知動作或送不出去時丟 HotkeyError。"""
        entry = ACTIONS.get(action)
        if entry is None:
            raise HotkeyError(f"沒有這個動作：{action}（可用：{', '.join(sorted(ACTIONS))}）")
        events, label = entry
        try:
            self.send(events)
        except HotkeyError:
            self.release_modifiers()     # Alt 可能已經按下去了，一定要補放開
            raise
        return label

    def release_modifiers(self) -> None:
        """補送所有修飾鍵的「放開」。程式結束時一定要呼叫，避免 Alt 卡在按住的狀態。
        本來就沒按著的鍵送 keyup 是無害的 no-op。"""
        try:
            self.send([KeyEvent(vk, up=True) for vk in MODIFIER_VKS])
        except HotkeyError:
            pass                          # 收尾用的，失敗就算了，不要蓋掉原本的錯誤


def iter_actions() -> Iterable[tuple[str, str]]:
    """(動作名稱, 中文說明)，給 --help 用。"""
    return ((name, label) for name, (_, label) in sorted(ACTIONS.items()))
