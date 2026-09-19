"""免 API Key 的 YouTube 音樂搜尋與瀏覽器播放。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import webbrowser


MAX_QUERY_LENGTH = 200
YOUTUBE_RESULT_TIMEOUT_SECONDS = 6.0
YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)


class YouTubeError(RuntimeError):
    """YouTube 搜尋內容不合法或瀏覽器無法開啟。"""


@dataclass(frozen=True)
class YouTubePlaybackResult:
    query: str
    url: str
    selection: str
    direct_result: bool


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


def _fetch_youtube_html(url: str, timeout: float) -> str | None:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(6_000_000).decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def resolve_youtube_result(
    query: str,
    selection: str,
    *,
    timeout: float = YOUTUBE_RESULT_TIMEOUT_SECONDS,
) -> str | None:
    """Resolve the first result of the requested YouTube media type."""
    search_url = build_youtube_search_url(query)
    html = _fetch_youtube_html(search_url, timeout)
    if html is None:
        return None

    if selection == "playlist":
        match = re.search(r'"playlistId":"([A-Za-z0-9_-]{10,80})"', html)
        if match:
            playlist_id = match.group(1)
            playlist_url = "https://www.youtube.com/playlist?" + urlencode(
                {"list": playlist_id}
            )
            playlist_html = _fetch_youtube_html(playlist_url, timeout)
            if playlist_html is not None:
                video_match = re.search(
                    r'"videoId":"([A-Za-z0-9_-]{6,20})"', playlist_html
                )
                if video_match:
                    return "https://www.youtube.com/watch?" + urlencode(
                        {
                            "v": video_match.group(1),
                            "list": playlist_id,
                            "autoplay": "1",
                        }
                    )
            return playlist_url
    elif selection == "track":
        match = re.search(r'"videoId":"([A-Za-z0-9_-]{6,20})"', html)
        if match:
            return "https://www.youtube.com/watch?" + urlencode(
                {"v": match.group(1)}
            )
    return None


def _is_direct_youtube_result(url: str | None, selection: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or hostname not in YOUTUBE_HOSTS:
        return False
    parameters = parse_qs(parsed.query)
    if selection == "playlist":
        return (
            parsed.path.rstrip("/") in {"/playlist", "/watch"}
            and bool(parameters.get("list"))
        )
    if selection == "track":
        if hostname == "youtu.be":
            return bool(parsed.path.strip("/"))
        return parsed.path.rstrip("/") == "/watch" and bool(parameters.get("v"))
    return False


def play_music(
    query: str,
    *,
    selection: str = "track",
    open_browser: bool = True,
    video_resolver: Callable[[str], str | None] | None = None,
) -> YouTubePlaybackResult:
    """開啟 YouTube 單曲或播放清單；找不到直達網址時顯示搜尋結果。"""
    normalized = _normalize_query(query)
    if selection not in {"track", "playlist"}:
        raise YouTubeError("播放類型只允許 track 或 playlist")
    if selection == "playlist":
        resolver_query = f"site:youtube.com/playlist {normalized}"
        fallback_query = normalized if "playlist" in normalized.casefold() else f"{normalized} playlist"
    else:
        resolver_query = f"site:youtube.com/watch {normalized}"
        fallback_query = normalized
    candidate = (
        video_resolver(resolver_query)
        if video_resolver is not None
        else resolve_youtube_result(normalized, selection)
    )
    direct_result = _is_direct_youtube_result(candidate, selection)
    url = (
        candidate
        if direct_result and candidate is not None
        else build_youtube_search_url(fallback_query)
    )
    if open_browser and not webbrowser.open(url, new=2):
        raise YouTubeError("系統預設瀏覽器無法開啟 YouTube")
    return YouTubePlaybackResult(
        query=normalized,
        url=url,
        selection=selection,
        direct_result=direct_result,
    )
