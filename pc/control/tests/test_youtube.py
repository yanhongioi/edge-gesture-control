from __future__ import annotations

import unittest

from pc.control.youtube import YouTubeError, build_youtube_search_url, play_music


class YouTubeTests(unittest.TestCase):
    def test_direct_video_is_used(self) -> None:
        result = play_music(
            "周杰倫 晴天",
            open_browser=False,
            video_resolver=lambda query: "https://www.youtube.com/watch?v=test123",
        )
        self.assertTrue(result.direct_video)
        self.assertEqual(result.url, "https://www.youtube.com/watch?v=test123")

    def test_non_youtube_result_falls_back_to_search(self) -> None:
        result = play_music(
            "周杰倫 晴天",
            open_browser=False,
            video_resolver=lambda query: "https://example.com/not-youtube",
        )
        self.assertFalse(result.direct_video)
        self.assertTrue(result.url.startswith("https://www.youtube.com/results?"))
        self.assertIn("%E5%91%A8%E6%9D%B0%E5%80%AB", result.url)

    def test_http_video_is_rejected(self) -> None:
        result = play_music(
            "晴天",
            open_browser=False,
            video_resolver=lambda query: "http://www.youtube.com/watch?v=test123",
        )
        self.assertFalse(result.direct_video)

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaises(YouTubeError):
            build_youtube_search_url("  ")


if __name__ == "__main__":
    unittest.main()
