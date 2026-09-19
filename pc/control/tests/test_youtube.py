from __future__ import annotations

import unittest

from unittest.mock import MagicMock, patch

from pc.control.youtube import (
    YouTubeError,
    build_youtube_search_url,
    play_music,
    resolve_youtube_result,
)


class YouTubeTests(unittest.TestCase):
    def test_direct_video_is_used(self) -> None:
        result = play_music(
            "周杰倫 晴天",
            open_browser=False,
            video_resolver=lambda query: "https://www.youtube.com/watch?v=test123",
        )
        self.assertTrue(result.direct_result)
        self.assertEqual(result.selection, "track")
        self.assertEqual(result.url, "https://www.youtube.com/watch?v=test123")

    def test_non_youtube_result_falls_back_to_search(self) -> None:
        result = play_music(
            "周杰倫 晴天",
            open_browser=False,
            video_resolver=lambda query: "https://example.com/not-youtube",
        )
        self.assertFalse(result.direct_result)
        self.assertTrue(result.url.startswith("https://www.youtube.com/results?"))
        self.assertIn("%E5%91%A8%E6%9D%B0%E5%80%AB", result.url)

    def test_http_video_is_rejected(self) -> None:
        result = play_music(
            "晴天",
            open_browser=False,
            video_resolver=lambda query: "http://www.youtube.com/watch?v=test123",
        )
        self.assertFalse(result.direct_result)

    def test_direct_playlist_is_used(self) -> None:
        result = play_music(
            "K-pop 熱門歌曲 playlist",
            selection="playlist",
            open_browser=False,
            video_resolver=lambda query: (
                "https://www.youtube.com/playlist?list=PLtest123"
            ),
        )
        self.assertTrue(result.direct_result)
        self.assertEqual(result.selection, "playlist")
        self.assertIn("list=PLtest123", result.url)

    def test_playlist_fallback_stays_on_youtube(self) -> None:
        result = play_music(
            "韓文歌",
            selection="playlist",
            open_browser=False,
            video_resolver=lambda query: "https://example.com/playlist",
        )
        self.assertFalse(result.direct_result)
        self.assertTrue(result.url.startswith("https://www.youtube.com/results?"))
        self.assertIn("playlist", result.url)

    @patch("pc.control.youtube.urlopen")
    def test_youtube_html_resolver_selects_playlist_id(self, mocked_open) -> None:
        response = mocked_open.return_value.__enter__.return_value
        response.read.return_value = (
            b'{"videoId":"video12345","playlistId":"PL_playlist123"}'
        )
        url = resolve_youtube_result("K-pop playlist", "playlist")
        self.assertEqual(
            url,
            "https://www.youtube.com/watch?"
            "v=video12345&list=PL_playlist123&autoplay=1",
        )
        self.assertEqual(mocked_open.call_count, 2)

    @patch("pc.control.youtube.urlopen")
    def test_playlist_page_is_fallback_when_first_video_is_missing(
        self, mocked_open
    ) -> None:
        search_response = MagicMock()
        search_response.__enter__.return_value.read.return_value = (
            b'{"playlistId":"PL_playlist123"}'
        )
        playlist_response = MagicMock()
        playlist_response.__enter__.return_value.read.return_value = b"{}"
        mocked_open.side_effect = [search_response, playlist_response]
        url = resolve_youtube_result("K-pop playlist", "playlist")
        self.assertEqual(
            url,
            "https://www.youtube.com/playlist?list=PL_playlist123",
        )

    @patch("pc.control.youtube.urlopen")
    def test_youtube_html_resolver_selects_video_id(self, mocked_open) -> None:
        response = mocked_open.return_value.__enter__.return_value
        response.read.return_value = b'{"videoId":"video12345"}'
        url = resolve_youtube_result("周杰倫 晴天", "track")
        self.assertEqual(url, "https://www.youtube.com/watch?v=video12345")

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaises(YouTubeError):
            build_youtube_search_url("  ")


if __name__ == "__main__":
    unittest.main()
