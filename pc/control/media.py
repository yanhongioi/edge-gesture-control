"""Track and control the most recent media opened by this assistant session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import ctypes
import sys

from .youtube import YouTubePlaybackResult


NO_ACTIVE_MEDIA_REPLY = "目前沒有正在播放的音樂喔主人"
PLAYBACK_OPERATIONS = frozenset({"play", "pause"})
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002


class MediaControlError(RuntimeError):
    """The system media key could not be sent safely."""


def send_play_pause_key() -> None:
    if sys.platform != "win32":
        raise MediaControlError("媒體播放控制目前只支援 Windows")
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)
    except (AttributeError, OSError) as exc:
        raise MediaControlError(f"無法送出 Windows 媒體鍵：{exc}") from exc


@dataclass(frozen=True)
class PlaybackControlResult:
    operation: str
    available: bool
    changed: bool
    media_url: str | None


class PlaybackController:
    """Remember one successfully opened media item and avoid toggle inversion."""

    def __init__(
        self,
        *,
        toggle_sender: Callable[[], None] = send_play_pause_key,
    ) -> None:
        self.toggle_sender = toggle_sender
        self._media_url: str | None = None
        self._playing = False

    @property
    def has_media(self) -> bool:
        return self._media_url is not None

    @property
    def playing(self) -> bool:
        return self.has_media and self._playing

    def remember(self, playback: YouTubePlaybackResult) -> None:
        # 搜尋結果頁並未開始播放，因此只有直達影片／歌單才算有效媒體。
        if playback.direct_result:
            self._media_url = playback.url
            self._playing = True

    def control(self, operation: str) -> PlaybackControlResult:
        if operation not in PLAYBACK_OPERATIONS:
            raise MediaControlError(f"不支援的播放控制：{operation}")
        if not self.has_media:
            return PlaybackControlResult(operation, False, False, None)

        desired_playing = operation == "play"
        changed = desired_playing != self._playing
        if changed:
            self.toggle_sender()
            self._playing = desired_playing
        return PlaybackControlResult(
            operation,
            True,
            changed,
            self._media_url,
        )
