"""將已驗證 AgentPlan 映射到明確允許的控制功能。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pc.llm.schemas import AgentPlan, ToolAction

from .browser import BrowserSearchResult, search_web
from .youtube import YouTubePlaybackResult, play_music as play_youtube_music
from .timer import TimerResult, start_timer


class ControlExecutionError(RuntimeError):
    """計畫未確認、工具未實作或執行失敗。"""


@dataclass(frozen=True)
class ExecutionResult:
    tool: str
    message: str
    details: dict[str, Any]


class ControlExecutor:
    """只執行明確註冊的工具；未支援的工具一律拒絕。"""

    def __init__(
        self,
        browser_search: Callable[..., BrowserSearchResult] = search_web,
        music_player: Callable[..., YouTubePlaybackResult] = play_youtube_music,
        timer_launcher: Callable[..., TimerResult] = start_timer,
        open_first_search_result: bool | None = None,
    ) -> None:
        self.browser_search = browser_search
        self.music_player = music_player
        self.timer_launcher = timer_launcher
        self.open_first_search_result = open_first_search_result

    def execute(self, plan: AgentPlan, *, confirmed: bool = False) -> tuple[ExecutionResult, ...]:
        if not confirmed:
            raise ControlExecutionError("計畫尚未確認，不會執行任何動作")
        if plan.intent != "action" or not plan.actions:
            raise ControlExecutionError("只有含動作的 action 計畫可以執行")
        if len(plan.actions) > 3:
            raise ControlExecutionError("單次最多執行三個動作")

        results: list[ExecutionResult] = []
        for action in plan.actions:
            results.append(self._execute_action(action))
        return tuple(results)

    def _execute_action(self, action: ToolAction) -> ExecutionResult:
        if action.tool == "search_web":
            open_first_result = action.arguments["open_first_result"]
            if self.open_first_search_result is not None:
                open_first_result = self.open_first_search_result
            result = self.browser_search(
                action.arguments["query"],
                open_browser=True,
                first_result=open_first_result,
            )
            destination = "第一個結果" if result.first_result else "搜尋結果頁"
            return ExecutionResult(
                tool=action.tool,
                message=f"已開啟瀏覽器{destination}：{result.query}",
                details={
                    "query": result.query,
                    "url": result.url,
                    "first_result": result.first_result,
                },
            )
        if action.tool == "play_music":
            result = self.music_player(
                action.arguments["query"],
                selection=action.arguments["selection"],
                open_browser=True,
            )
            media_name = "播放清單" if result.selection == "playlist" else "影片"
            destination = media_name if result.direct_result else "搜尋結果頁"
            return ExecutionResult(
                tool=action.tool,
                message=f"已開啟 YouTube {destination}：{result.query}",
                details={
                    "query": result.query,
                    "url": result.url,
                    "selection": result.selection,
                    "direct_result": result.direct_result,
                },
            )
        if action.tool == "set_timer":
            result = self.timer_launcher(
                action.arguments["seconds"],
                action.arguments["label"],
            )
            return ExecutionResult(
                tool=action.tool,
                message=f"已開始計時 {result.seconds} 秒：{result.label}",
                details={
                    "seconds": result.seconds,
                    "label": result.label,
                    "process_id": result.process_id,
                },
            )
        raise ControlExecutionError(f"工具尚未實作，拒絕執行：{action.tool}")
