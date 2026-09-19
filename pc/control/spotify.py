"""Spotify Web API：PKCE 授權、搜尋歌曲與 Premium 播放。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.error
import urllib.request
import webbrowser


ACCOUNTS_URL = "https://accounts.spotify.com"
API_URL = "https://api.spotify.com/v1"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = ("user-read-playback-state", "user-modify-playback-state")


class SpotifyError(RuntimeError):
    """Spotify 授權或 API 操作失敗。"""


class SpotifyAuthRequired(SpotifyError):
    """尚未完成 Spotify PKCE 授權。"""


@dataclass(frozen=True)
class SpotifyTrack:
    name: str
    artists: tuple[str, ...]
    uri: str
    external_url: str | None


@dataclass(frozen=True)
class SpotifyPlaybackResult:
    track: SpotifyTrack
    device_name: str


def default_token_path() -> Path:
    configured = os.getenv("SPOTIFY_TOKEN_PATH")
    if configured:
        return Path(configured)
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        raise SpotifyError("找不到 LOCALAPPDATA，請設定 SPOTIFY_TOKEN_PATH")
    return Path(local_app_data) / "edge-gesture-control" / "spotify-token.json"


def default_config_path() -> Path:
    configured = os.getenv("SPOTIFY_CONFIG_PATH")
    if configured:
        return Path(configured)
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        raise SpotifyError("找不到 LOCALAPPDATA，請設定 SPOTIFY_CONFIG_PATH")
    return Path(local_app_data) / "edge-gesture-control" / "spotify-config.json"


def _load_config(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SpotifyError(f"Spotify 設定檔損壞：{path}") from exc
    if not isinstance(raw, dict):
        raise SpotifyError(f"Spotify 設定檔格式錯誤：{path}")
    return {
        key: value
        for key, value in raw.items()
        if key in {"client_id", "redirect_uri"} and isinstance(value, str)
    }


class SpotifyClient:
    def __init__(
        self,
        client_id: str | None = None,
        redirect_uri: str | None = None,
        token_path: Path | None = None,
        config_path: Path | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.config_path = config_path or default_config_path()
        saved_config = _load_config(self.config_path)
        configured_client_id = client_id
        if configured_client_id is None:
            configured_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        if configured_client_id is None:
            configured_client_id = saved_config.get("client_id", "")
        self.client_id = configured_client_id.strip()
        self.redirect_uri = (
            redirect_uri
            or os.getenv("SPOTIFY_REDIRECT_URI")
            or saved_config.get("redirect_uri")
            or DEFAULT_REDIRECT_URI
        )
        self.token_path = token_path or default_token_path()
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.client_id:
            raise SpotifyAuthRequired(
                "尚未設定 SPOTIFY_CLIENT_ID；請先在 Spotify Developer Dashboard 建立 App"
            )
        parsed = urlparse(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise SpotifyError("SPOTIFY_REDIRECT_URI 必須是 http://127.0.0.1:PORT/callback")

    def save_config(self) -> None:
        """保存公開設定至 repo 外；Spotify PKCE 不需要 Client Secret。"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {"client_id": self.client_id, "redirect_uri": self.redirect_uri},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load_token(self) -> dict[str, Any]:
        try:
            raw = self.token_path.read_text(encoding="utf-8")
            token = json.loads(raw)
        except FileNotFoundError as exc:
            raise SpotifyAuthRequired(
                "尚未授權 Spotify；請先執行 py -m pc.control.spotify_auth"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SpotifyAuthRequired("Spotify token 檔案損壞，請重新授權") from exc
        if not isinstance(token, dict):
            raise SpotifyAuthRequired("Spotify token 格式不正確，請重新授權")
        return token

    def _save_token(self, token: dict[str, Any]) -> None:
        token = dict(token)
        expires_in = token.get("expires_in")
        if isinstance(expires_in, (int, float)):
            token["expires_at"] = time.time() + float(expires_in)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(
            json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _form_request(self, url: str, fields: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=urlencode(fields).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SpotifyError(f"Spotify 授權失敗（HTTP {exc.code}）：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SpotifyError(f"無法連線至 Spotify 授權服務：{exc}") from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpotifyError("Spotify token 回應不是有效 JSON") from exc
        if not isinstance(result, dict) or "access_token" not in result:
            raise SpotifyError("Spotify token 回應格式不正確")
        return result

    def authenticate(self, *, open_browser: bool = True, timeout: float = 180.0) -> None:
        """啟動一次性 PKCE 登入，token 寫入 LOCALAPPDATA，不寫進 repo。"""
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
        }
        auth_url = f"{ACCOUNTS_URL}/authorize?{urlencode(params)}"
        callback_result: dict[str, str] = {}
        expected_path = urlparse(self.redirect_uri).path or "/"

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != expected_path:
                    self.send_error(404)
                    return
                values = parse_qs(parsed.query)
                for key in ("code", "state", "error"):
                    if values.get(key):
                        callback_result[key] = values[key][0]
                body = "Spotify 授權完成，可以關閉這個頁面。".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        parsed_redirect = urlparse(self.redirect_uri)
        server = HTTPServer(("127.0.0.1", parsed_redirect.port or 8888), CallbackHandler)
        deadline = time.monotonic() + timeout
        try:
            if open_browser and not webbrowser.open(auth_url, new=2):
                raise SpotifyError("無法開啟 Spotify 授權頁面")
            while "code" not in callback_result and "error" not in callback_result:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                server.timeout = min(1.0, remaining)
                server.handle_request()
        finally:
            server.server_close()

        if callback_result.get("error"):
            raise SpotifyAuthRequired(f"Spotify 授權被拒絕：{callback_result['error']}")
        if callback_result.get("state") != state or not callback_result.get("code"):
            raise SpotifyAuthRequired("Spotify callback 無效或等待授權逾時")
        token = self._form_request(
            f"{ACCOUNTS_URL}/api/token",
            {
                "grant_type": "authorization_code",
                "code": callback_result["code"],
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            },
        )
        self._save_token(token)

    def _refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        refresh_token = token.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise SpotifyAuthRequired("Spotify refresh token 不存在，請重新授權")
        refreshed = self._form_request(
            f"{ACCOUNTS_URL}/api/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            },
        )
        refreshed.setdefault("refresh_token", refresh_token)
        self._save_token(refreshed)
        return refreshed

    def _access_token(self, *, force_refresh: bool = False) -> str:
        token = self._load_token()
        expires_at = token.get("expires_at", 0)
        if force_refresh or not isinstance(expires_at, (int, float)) or expires_at <= time.time() + 60:
            token = self._refresh_token(token)
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise SpotifyAuthRequired("Spotify access token 不存在，請重新授權")
        return access_token

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str | int] | None = None,
        payload: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any] | None:
        url = f"{API_URL}{path}"
        if query:
            url += "?" + urlencode(query)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry_auth:
                self._access_token(force_refresh=True)
                return self._api_request(
                    method, path, query=query, payload=payload, retry_auth=False
                )
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After", "未知")
                raise SpotifyError(f"Spotify 流量限制，請在 {retry_after} 秒後重試") from exc
            if exc.code == 403:
                raise SpotifyError(
                    "Spotify 拒絕播放；請確認帳號是 Premium，且登入帳號可使用此 Developer App"
                ) from exc
            raise SpotifyError(f"Spotify API HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SpotifyError(f"無法連線至 Spotify API：{exc}") from exc
        if not raw:
            return None
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise SpotifyError("Spotify API 回傳格式不正確")
        return result

    def search_track(self, query: str) -> SpotifyTrack:
        if not query.strip() or len(query.strip()) > 200:
            raise SpotifyError("歌曲搜尋內容必須介於 1 到 200 字")
        result = self._api_request(
            "GET", "/search", query={"q": query.strip(), "type": "track", "limit": 1}
        )
        tracks = result.get("tracks", {}) if result else {}
        items = tracks.get("items", []) if isinstance(tracks, dict) else []
        if not items or not isinstance(items[0], dict):
            raise SpotifyError(f"Spotify 找不到歌曲：{query}")
        item = items[0]
        artists_data = item.get("artists", [])
        artists = tuple(
            artist["name"]
            for artist in artists_data
            if isinstance(artist, dict) and isinstance(artist.get("name"), str)
        )
        uri = item.get("uri")
        name = item.get("name")
        if not isinstance(uri, str) or not isinstance(name, str):
            raise SpotifyError("Spotify 搜尋結果缺少歌曲資料")
        external_urls = item.get("external_urls")
        external_url = (
            external_urls.get("spotify") if isinstance(external_urls, dict) else None
        )
        return SpotifyTrack(name=name, artists=artists, uri=uri, external_url=external_url)

    def _available_device(self) -> dict[str, Any] | None:
        result = self._api_request("GET", "/me/player/devices")
        devices = result.get("devices", []) if result else []
        usable = [
            device
            for device in devices
            if isinstance(device, dict)
            and isinstance(device.get("id"), str)
            and device.get("id")
            and not device.get("is_restricted", False)
        ]
        return next((device for device in usable if device.get("is_active")), None) or (
            usable[0] if usable else None
        )

    def _launch_spotify(self) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile("spotify:")  # type: ignore[attr-defined]
            elif not webbrowser.open("spotify:"):
                raise OSError("系統無法處理 spotify: URI")
        except OSError as exc:
            raise SpotifyError("無法啟動 Spotify 桌面程式") from exc

    def play_music(self, query: str) -> SpotifyPlaybackResult:
        track = self.search_track(query)
        device = self._available_device()
        if device is None:
            self._launch_spotify()
            for _ in range(6):
                time.sleep(1)
                device = self._available_device()
                if device is not None:
                    break
        if device is None:
            raise SpotifyError("找不到 Spotify 播放裝置；請先開啟 Spotify 桌面程式")
        self._api_request(
            "PUT",
            "/me/player/play",
            query={"device_id": device["id"]},
            payload={"uris": [track.uri]},
        )
        return SpotifyPlaybackResult(
            track=track,
            device_name=str(device.get("name") or "Spotify 裝置"),
        )
