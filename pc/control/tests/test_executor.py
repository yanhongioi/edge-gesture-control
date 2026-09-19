from __future__ import annotations

import unittest

from pc.control.browser import BrowserSearchResult
from pc.control.executor import ControlExecutionError, ControlExecutor
from pc.control.youtube import YouTubePlaybackResult
from pc.control.timer import TimerResult
from pc.llm.schemas import AgentPlan, ToolAction


def fake_music_player(
    query: str, *, selection: str, open_browser: bool
) -> YouTubePlaybackResult:
    if not open_browser:
        raise AssertionError("expected browser playback")
    return YouTubePlaybackResult(
        query=query,
        url="https://www.youtube.com/watch?v=test",
        selection=selection,
        direct_result=True,
    )


def fake_timer_launcher(seconds: int, label: str) -> TimerResult:
    return TimerResult(seconds=seconds, label=label, process_id=1234)


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.opened: list[str] = []

        def fake_search(
            query: str, *, open_browser: bool, first_result: bool
        ) -> BrowserSearchResult:
            self.opened.append(query)
            self.assertTrue(open_browser)
            return BrowserSearchResult(
                query, "https://www.google.com/search?q=test", first_result
            )

        self.executor = ControlExecutor(
            browser_search=fake_search,
            music_player=fake_music_player,
            timer_launcher=fake_timer_launcher,
        )

    def test_confirmation_is_required(self) -> None:
        plan = AgentPlan(
            "action",
            "準備搜尋。",
            (ToolAction("search_web", {"query": "食譜", "open_first_result": False}),),
        )
        with self.assertRaises(ControlExecutionError):
            self.executor.execute(plan)
        self.assertEqual(self.opened, [])

    def test_search_is_dispatched_after_confirmation(self) -> None:
        plan = AgentPlan(
            "action",
            "準備搜尋。",
            (ToolAction("search_web", {"query": "食譜", "open_first_result": False}),),
        )
        results = self.executor.execute(plan, confirmed=True)
        self.assertEqual(results[0].tool, "search_web")
        self.assertEqual(self.opened, ["食譜"])
        self.assertFalse(results[0].details["first_result"])

    def test_youtube_is_dispatched_after_confirmation(self) -> None:
        plan = AgentPlan(
            "action",
            "準備播放。",
            (
                ToolAction(
                    "play_music",
                    {"query": "周杰倫 晴天", "selection": "track"},
                ),
            ),
        )
        results = self.executor.execute(plan, confirmed=True)
        self.assertIn("YouTube 影片", results[0].message)
        self.assertEqual(results[0].details["query"], "周杰倫 晴天")

    def test_playlist_is_dispatched_to_youtube(self) -> None:
        plan = AgentPlan(
            "action",
            "準備播放歌單。",
            (
                ToolAction(
                    "play_music",
                    {"query": "K-pop 熱門歌曲 playlist", "selection": "playlist"},
                ),
            ),
        )
        results = self.executor.execute(plan, confirmed=True)
        self.assertIn("YouTube 播放清單", results[0].message)
        self.assertEqual(results[0].details["selection"], "playlist")

    def test_unimplemented_tool_is_rejected(self) -> None:
        plan = AgentPlan(
            "action", "準備捲動。", (ToolAction("scroll", {"amount": -500}),)
        )
        with self.assertRaises(ControlExecutionError):
            self.executor.execute(plan, confirmed=True)

    def test_local_timer_is_dispatched_after_confirmation(self) -> None:
        plan = AgentPlan(
            "action",
            "開始計時。",
            (
                ToolAction(
                    "set_timer",
                    {"seconds": 300, "label": "煮蛋"},
                ),
            ),
        )
        results = self.executor.execute(plan, confirmed=True)
        self.assertEqual(results[0].tool, "set_timer")
        self.assertEqual(results[0].details["seconds"], 300)
        self.assertEqual(results[0].details["process_id"], 1234)


if __name__ == "__main__":
    unittest.main()
