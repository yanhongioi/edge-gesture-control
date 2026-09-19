from __future__ import annotations

import unittest

from pc.control.browser import BrowserSearchResult
from pc.control.executor import ControlExecutionError, ControlExecutor
from pc.control.youtube import YouTubePlaybackResult
from pc.llm.schemas import AgentPlan, ToolAction


def fake_music_player(query: str, *, open_browser: bool) -> YouTubePlaybackResult:
    if not open_browser:
        raise AssertionError("expected browser playback")
    return YouTubePlaybackResult(
        query=query,
        url="https://www.youtube.com/watch?v=test",
        direct_video=True,
    )


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.opened: list[str] = []

        def fake_search(
            query: str, *, open_browser: bool, first_result: bool
        ) -> BrowserSearchResult:
            self.opened.append(query)
            self.assertTrue(open_browser)
            self.assertTrue(first_result)
            return BrowserSearchResult(
                query, "https://www.google.com/search?q=test&btnI=1", first_result
            )

        self.executor = ControlExecutor(
            browser_search=fake_search,
            music_player=fake_music_player,
        )

    def test_confirmation_is_required(self) -> None:
        plan = AgentPlan(
            "action", "準備搜尋。", (ToolAction("search_web", {"query": "食譜"}),)
        )
        with self.assertRaises(ControlExecutionError):
            self.executor.execute(plan)
        self.assertEqual(self.opened, [])

    def test_search_is_dispatched_after_confirmation(self) -> None:
        plan = AgentPlan(
            "action", "準備搜尋。", (ToolAction("search_web", {"query": "食譜"}),)
        )
        results = self.executor.execute(plan, confirmed=True)
        self.assertEqual(results[0].tool, "search_web")
        self.assertEqual(self.opened, ["食譜"])

    def test_youtube_is_dispatched_after_confirmation(self) -> None:
        plan = AgentPlan(
            "action",
            "準備播放。",
            (ToolAction("play_music", {"query": "周杰倫 晴天"}),),
        )
        results = self.executor.execute(plan, confirmed=True)
        self.assertIn("YouTube 影片", results[0].message)
        self.assertEqual(results[0].details["query"], "周杰倫 晴天")

    def test_unimplemented_tool_is_rejected(self) -> None:
        plan = AgentPlan(
            "action", "準備捲動。", (ToolAction("scroll", {"amount": -500}),)
        )
        with self.assertRaises(ControlExecutionError):
            self.executor.execute(plan, confirmed=True)


if __name__ == "__main__":
    unittest.main()
