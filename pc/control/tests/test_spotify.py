from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pc.control.spotify import SpotifyAuthRequired, SpotifyClient


class FakeSpotifyClient(SpotifyClient):
    def __init__(self, token_path: Path) -> None:
        super().__init__(
            client_id="test-client",
            token_path=token_path,
            config_path=token_path.with_name("config.json"),
        )
        self.play_payload = None

    def _api_request(self, method, path, **kwargs):  # type: ignore[no-untyped-def]
        if path == "/search":
            return {
                "tracks": {
                    "items": [
                        {
                            "name": "晴天",
                            "uri": "spotify:track:test",
                            "artists": [{"name": "周杰倫"}],
                            "external_urls": {
                                "spotify": "https://open.spotify.com/track/test"
                            },
                        }
                    ]
                }
            }
        if path == "/me/player/devices":
            return {
                "devices": [
                    {
                        "id": "device-1",
                        "name": "測試電腦",
                        "is_active": True,
                        "is_restricted": False,
                    }
                ]
            }
        if path == "/me/player/play":
            self.play_payload = kwargs.get("payload")
            return None
        raise AssertionError(f"unexpected Spotify path: {path}")


class SpotifyTests(unittest.TestCase):
    def test_client_id_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(SpotifyAuthRequired):
                SpotifyClient(
                    client_id="",
                    token_path=Path(temp_dir) / "token.json",
                    config_path=Path(temp_dir) / "config.json",
                )

    def test_saved_config_is_used_without_environment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = SpotifyClient(
                client_id="saved-client",
                redirect_uri="http://127.0.0.1:9999/callback",
                token_path=root / "token.json",
                config_path=root / "config.json",
            )
            first.save_config()
            with patch.dict("os.environ", {}, clear=True):
                restored = SpotifyClient(
                    token_path=root / "token.json",
                    config_path=root / "config.json",
                )
            self.assertEqual(restored.client_id, "saved-client")
            self.assertEqual(
                restored.redirect_uri, "http://127.0.0.1:9999/callback"
            )

    def test_search_and_play_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeSpotifyClient(Path(temp_dir) / "token.json")
            result = client.play_music("周杰倫 晴天")
        self.assertEqual(result.track.name, "晴天")
        self.assertEqual(result.track.artists, ("周杰倫",))
        self.assertEqual(result.device_name, "測試電腦")
        self.assertEqual(client.play_payload, {"uris": ["spotify:track:test"]})


if __name__ == "__main__":
    unittest.main()
