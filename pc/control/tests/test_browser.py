from __future__ import annotations

import unittest

from pc.control.browser import (
    BrowserControlError,
    build_google_search_url,
    extract_external_target,
    search_web,
)


class BrowserTests(unittest.TestCase):
    def test_build_url_encodes_chinese_query(self) -> None:
        url = build_google_search_url("雞胸肉 食譜")
        self.assertTrue(url.startswith("https://www.google.com/search?"))
        self.assertIn("%E9%9B%9E", url)
        self.assertIn("+", url)
        self.assertIn("btnI=1", url)

    def test_results_only_omits_first_result_redirect(self) -> None:
        url = build_google_search_url("雞胸肉 食譜", first_result=False)
        self.assertNotIn("btnI", url)

    def test_preview_does_not_open_browser(self) -> None:
        result = search_web(
            "番茄炒蛋",
            open_browser=False,
            first_result_resolver=lambda query: "https://example.com/recipe",
        )
        self.assertEqual(result.query, "番茄炒蛋")
        self.assertTrue(result.first_result)
        self.assertEqual(result.url, "https://example.com/recipe")

    def test_extracts_https_target_from_google_redirect(self) -> None:
        redirect = (
            "https://www.google.com/url?"
            "q=https%3A%2F%2Fexample.com%2F%25E9%259B%259E%25E8%2583%25B8"
        )
        self.assertEqual(
            extract_external_target(redirect),
            "https://example.com/%E9%9B%9E%E8%83%B8",
        )

    def test_rejects_unsafe_redirect_targets(self) -> None:
        self.assertIsNone(
            extract_external_target(
                "https://www.google.com/url?q=http%3A%2F%2Fexample.com"
            )
        )
        self.assertIsNone(
            extract_external_target(
                "https://www.google.com/url?q=https%3A%2F%2Fuser%40example.com"
            )
        )

    def test_failed_resolution_falls_back_to_results_page(self) -> None:
        result = search_web(
            "番茄炒蛋",
            open_browser=False,
            first_result_resolver=lambda query: None,
        )
        self.assertFalse(result.first_result)
        self.assertTrue(result.url.startswith("https://www.google.com/search?"))
        self.assertNotIn("btnI", result.url)

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaises(BrowserControlError):
            build_google_search_url("  ")


if __name__ == "__main__":
    unittest.main()
