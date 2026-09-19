"""安全地以系統預設瀏覽器搜尋網頁。"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, build_opener
import webbrowser


MAX_QUERY_LENGTH = 200
MAX_URL_LENGTH = 2048
FIRST_RESULT_TIMEOUT_SECONDS = 5.0


class BrowserControlError(RuntimeError):
    """瀏覽器無法開啟或搜尋文字不合法。"""


@dataclass(frozen=True)
class BrowserSearchResult:
    query: str
    url: str
    first_result: bool


def build_google_search_url(query: str, *, first_result: bool = True) -> str:
    if not isinstance(query, str) or not query.strip():
        raise BrowserControlError("搜尋內容不可為空")
    normalized = query.strip()
    if len(normalized) > MAX_QUERY_LENGTH:
        raise BrowserControlError(f"搜尋內容不可超過 {MAX_QUERY_LENGTH} 字")
    parameters = {"q": normalized}
    if first_result:
        # Google 的 I'm Feeling Lucky 參數會嘗試直接轉到第一個自然搜尋結果。
        parameters["btnI"] = "1"
    return "https://www.google.com/search?" + urlencode(parameters)


def _is_google_host(hostname: str) -> bool:
    hostname = hostname.lower().rstrip(".")
    return (
        hostname == "google.com"
        or hostname.endswith(".google.com")
        or hostname == "google.com.tw"
        or hostname.endswith(".google.com.tw")
    )


def _safe_external_https_url(url: str) -> str | None:
    if not isinstance(url, str) or len(url) > MAX_URL_LENGTH:
        return None
    parsed = urlparse(url)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or _is_google_host(parsed.hostname)
    ):
        return None
    return url


def extract_external_target(candidate_url: str) -> str | None:
    """從 Google 重新導向網址取出經驗證的外部 HTTPS 目標。"""
    direct_target = _safe_external_https_url(candidate_url)
    if direct_target is not None:
        return direct_target

    parsed = urlparse(candidate_url)
    if not parsed.hostname or not _is_google_host(parsed.hostname):
        return None
    if parsed.path.rstrip("/") != "/url":
        return None

    parameters = parse_qs(parsed.query)
    for key in ("q", "url"):
        values = parameters.get(key, [])
        if values:
            target = _safe_external_https_url(values[0])
            if target is not None:
                return target
    return None


def resolve_first_result(
    query: str, *, timeout: float = FIRST_RESULT_TIMEOUT_SECONDS
) -> str | None:
    """在背景解析 Google 第一筆結果，不讓瀏覽器停在重新導向通知頁。"""
    lucky_url = build_google_search_url(query, first_result=True)
    request = Request(
        lucky_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with build_opener().open(request, timeout=timeout) as response:
            return extract_external_target(response.geturl())
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def search_web(
    query: str,
    *,
    open_browser: bool = True,
    first_result: bool = True,
    first_result_resolver: Callable[[str], str | None] = resolve_first_result,
) -> BrowserSearchResult:
    """建立 Google 搜尋網址；只有 open_browser=True 時才真的開啟。"""
    normalized = query.strip() if isinstance(query, str) else query
    # 先呼叫 builder 完成空字串與長度驗證。
    results_url = build_google_search_url(normalized, first_result=False)
    resolved_first_result = first_result_resolver(normalized) if first_result else None
    url = resolved_first_result or results_url
    opened_first_result = resolved_first_result is not None
    if open_browser and not webbrowser.open(url, new=2):
        raise BrowserControlError("系統預設瀏覽器無法開啟搜尋網址")
    return BrowserSearchResult(
        query=normalized, url=url, first_result=opened_first_result
    )
