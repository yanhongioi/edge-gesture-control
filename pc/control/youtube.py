"""免 API Key 的 YouTube 音樂搜尋與瀏覽器播放。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from .browser import resolve_first_result


MAX_QUERY_LENGTH = 200
YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)


class YouTubeError(RuntimeError):
    """YouTube 搜尋內容不合法或瀏覽器無法開啟。"""


@dataclass(frozen=True)
class YouTubePlaybackResult:
    query: str
    url: str
    direct_video: bool


def _normalize_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise YouTubeError("歌曲搜尋內容不可為空")
    normalized = query.strip()
    if len(normalized) > MAX_QUERY_LENGTH:
        raise YouTubeError(f"歌曲搜尋內容不可超過 {MAX_QUERY_LENGTH} 字")
    return normalized


def build_youtube_search_url(query: str) -> str:
    normalized = _normalize_query(query)
    return "https://www.youtube.com/results?" + urlencode(
        {"search_query": normalized}
    )


def _is_direct_youtube_video(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or hostname not in YOUTUBE_HOSTS:
        return False
    if hostname == "youtu.be":
        return bool(parsed.path.strip("/"))
    return parsed.path.rstrip("/") == "/watch" and bool(
        parse_qs(parsed.query).get("v")
    )


def play_music(
    query: str,
    *,
    open_browser: bool = True,
    video_resolver: Callable[[str], str | None] = resolve_first_result,
) -> YouTubePlaybackResult:
    """開啟最相關的 YouTube 影片；找不到直達網址時顯示搜尋結果。"""
    normalized = _normalize_query(query)
    candidate = video_resolver(f"site:youtube.com/watch {normalized}")
    direct_video = _is_direct_youtube_video(candidate)
    url = candidate if direct_video and candidate is not None else build_youtube_search_url(normalized)
    if open_browser and not webbrowser.open(url, new=2):
        raise YouTubeError("系統預設瀏覽器無法開啟 YouTube")
    return YouTubePlaybackResult(
        query=normalized,
        url=url,
        direct_video=direct_video,
    )
