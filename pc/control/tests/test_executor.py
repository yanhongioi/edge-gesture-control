from __future__ import annotations

import unittest

from pc.control.browser import BrowserSearchResult
from pc.control.executor import ControlExecutionError, ControlExecutor
from pc.control.media import NO_ACTIVE_MEDIA_REPLY, PlaybackController
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
        url="https://www.youtube.com/watch?v=test&autoplay=1",
        selection=selection,
        direct_result=True,
    )


def fake_timer_launcher(seconds: int, label: str) -> TimerResult:
    return TimerResult(seconds=seconds, label=label, process_id=1234)


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.opened: list[str] = []
        self.media_toggles: list[str] = []
        self.scroll_events: list[int] = []
        self.hotkey_events: list[tuple[str, ...]] = []
        self.volume_adjustments: list[int] = []
        self.volume_levels: list[int] = []
        self.cancelled_timers: list[int] = []

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
            timer_canceller=lambda timer: not self.cancelled_timers.append(
                timer.process_id
            ),
            playback_controller=PlaybackController(
                toggle_sender=lambda: self.media_toggles.append("toggle")
            ),
            scroll_sender=self.scroll_events.append,
            hotkey_sender=self.hotkey_events.append,
            volume_adjuster=self.volume_adjustments.append,
            volume_setter=self.volume_levels.append,
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

    def test_playback_control_without_recent_media_becomes_answer(self) -> None:
        plan = AgentPlan(
            "action",
            "是的，主人。",
            (ToolAction("control_playback", {"operation": "pause"}),),
        )
        prepared = self.executor.prepare_plan(plan)
        self.assertEqual(prepared.intent, "answer")
        self.assertEqual(prepared.reply, NO_ACTIVE_MEDIA_REPLY)
        self.assertEqual(prepared.actions, ())
        self.assertEqual(self.media_toggles, [])

    def test_pause_and_play_control_most_recent_youtube_media(self) -> None:
        play_music_plan = AgentPlan(
            "action",
            "準備播放。",
            (
                ToolAction(
                    "play_music",
                    {"query": "周杰倫 晴天", "selection": "track"},
                ),
            ),
        )
        self.executor.execute(play_music_plan, confirmed=True)

        pause_plan = AgentPlan(
            "action",
            "是的，主人。",
            (ToolAction("control_playback", {"operation": "pause"}),),
        )
        first_pause = self.executor.execute(pause_plan, confirmed=True)[0]
        second_pause = self.executor.execute(pause_plan, confirmed=True)[0]
        self.assertTrue(first_pause.details["changed"])
        self.assertFalse(second_pause.details["changed"])
        self.assertEqual(self.media_toggles, ["toggle"])

        resume_plan = AgentPlan(
            "action",
            "是的，主人。",
            (ToolAction("control_playback", {"operation": "play"}),),
        )
        resumed = self.executor.execute(resume_plan, confirmed=True)[0]
        self.assertTrue(resumed.details["changed"])
        self.assertEqual(self.media_toggles, ["toggle", "toggle"])

    def test_media_without_autoplay_starts_in_paused_state(self) -> None:
        toggles: list[str] = []
        controller = PlaybackController(
            toggle_sender=lambda: toggles.append("toggle")
        )
        controller.remember(
            YouTubePlaybackResult(
                query="K-pop playlist",
                url="https://www.youtube.com/playlist?list=PLtest123",
                selection="playlist",
                direct_result=True,
            )
        )
        self.assertFalse(controller.playing)
        paused = controller.control("pause")
        self.assertFalse(paused.changed)
        resumed = controller.control("play")
        self.assertTrue(resumed.changed)
        self.assertEqual(toggles, ["toggle"])

    def test_scroll_is_dispatched_after_confirmation(self) -> None:
        plan = AgentPlan(
            "action", "準備捲動。", (ToolAction("scroll", {"amount": -240}),)
        )
        result = self.executor.execute(plan, confirmed=True)[0]
        self.assertEqual(self.scroll_events, [-240])
        self.assertEqual(result.details["amount"], -240)

    def test_hotkey_is_dispatched_after_confirmation(self) -> None:
        plan = AgentPlan(
            "action",
            "準備移到最上面。",
            (ToolAction("press_hotkey", {"keys": ["ctrl", "home"]}),),
        )
        result = self.executor.execute(plan, confirmed=True)[0]
        self.assertEqual(self.hotkey_events, [("ctrl", "home")])
        self.assertEqual(result.details["keys"], ["ctrl", "home"])

    def test_relative_and_absolute_volume_are_dispatched(self) -> None:
        relative = AgentPlan(
            "action",
            "是的，主人。",
            (ToolAction("adjust_volume", {"steps": 3}),),
        )
        absolute = AgentPlan(
            "action",
            "是的，主人。",
            (ToolAction("set_volume", {"level": 42}),),
        )
        relative_result = self.executor.execute(relative, confirmed=True)[0]
        absolute_result = self.executor.execute(absolute, confirmed=True)[0]
        self.assertEqual(self.volume_adjustments, [3])
        self.assertEqual(self.volume_levels, [42])
        self.assertEqual(relative_result.details["steps"], 3)
        self.assertEqual(absolute_result.details["level"], 42)

    def test_unimplemented_tool_is_rejected(self) -> None:
        plan = AgentPlan(
            "action",
            "準備開啟。",
            (ToolAction("open_url", {"url": "https://example.com"}),),
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

    def test_active_timer_can_be_cancelled(self) -> None:
        start = AgentPlan(
            "action",
            "開始計時。",
            (ToolAction("set_timer", {"seconds": 300, "label": "煮蛋"}),),
        )
        cancel = AgentPlan(
            "action",
            "取消計時。",
            (ToolAction("cancel_timer", {}),),
        )
        self.executor.execute(start, confirmed=True)
        prepared = self.executor.prepare_plan(cancel)
        result = self.executor.execute(prepared, confirmed=True)[0]
        self.assertTrue(result.details["cancelled"])
        self.assertEqual(self.cancelled_timers, [1234])

    def test_cancel_without_active_timer_becomes_answer(self) -> None:
        cancel = AgentPlan(
            "action",
            "取消計時。",
            (ToolAction("cancel_timer", {}),),
        )
        prepared = self.executor.prepare_plan(cancel)
        self.assertEqual(prepared.intent, "answer")
        self.assertEqual(prepared.reply, "目前沒有正在計時喔主人")
        self.assertEqual(prepared.actions, ())


if __name__ == "__main__":
    unittest.main()
