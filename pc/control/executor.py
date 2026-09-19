"""將已驗證 AgentPlan 映射到明確允許的控制功能。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from pc.llm.schemas import AgentPlan, ToolAction

from .browser import BrowserSearchResult, search_web
from .media import NO_ACTIVE_MEDIA_REPLY, PlaybackController
from .youtube import YouTubePlaybackResult, play_music as play_youtube_music
from .timer import TimerController, TimerResult, cancel_timer, start_timer
from .windows_input import (
    adjust_system_volume,
    press_hotkey,
    scroll_vertical,
    set_system_volume,
)


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
        timer_canceller: Callable[[TimerResult], bool] = cancel_timer,
        playback_controller: PlaybackController | None = None,
        scroll_sender: Callable[[int], None] = scroll_vertical,
        hotkey_sender: Callable[[tuple[str, ...]], None] = press_hotkey,
        volume_adjuster: Callable[[int], None] = adjust_system_volume,
        volume_setter: Callable[[int], None] = set_system_volume,
        open_first_search_result: bool | None = None,
    ) -> None:
        self.browser_search = browser_search
        self.music_player = music_player
        self.timer_controller = TimerController(timer_launcher, timer_canceller)
        self.playback_controller = playback_controller or PlaybackController()
        self.scroll_sender = scroll_sender
        self.hotkey_sender = hotkey_sender
        self.volume_adjuster = volume_adjuster
        self.volume_setter = volume_setter
        self.open_first_search_result = open_first_search_result

    def prepare_plan(self, plan: AgentPlan) -> AgentPlan:
        """Resolve execution-state-dependent replies before TTS speaks them."""
        if any(
            action.tool == "control_playback"
            for action in plan.actions
        ) and not self.playback_controller.has_media:
            return replace(
                plan,
                intent="answer",
                reply=NO_ACTIVE_MEDIA_REPLY,
                actions=(),
                source="control",
            )
        if any(action.tool == "cancel_timer" for action in plan.actions):
            if not self.timer_controller.has_timer:
                return replace(
                    plan,
                    intent="answer",
                    reply="目前沒有正在計時喔主人",
                    actions=(),
                    source="control",
                )
        return plan

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
        if action.tool == "scroll":
            amount = action.arguments["amount"]
            self.scroll_sender(amount)
            direction = "上" if amount > 0 else "下"
            return ExecutionResult(
                tool=action.tool,
                message=f"已往{direction}捲動",
                details={"amount": amount},
            )
        if action.tool == "press_hotkey":
            keys = tuple(action.arguments["keys"])
            self.hotkey_sender(keys)
            return ExecutionResult(
                tool=action.tool,
                message=f"已送出快捷鍵：{'+'.join(keys)}",
                details={"keys": list(keys)},
            )
        if action.tool == "adjust_volume":
            steps = action.arguments["steps"]
            self.volume_adjuster(steps)
            return ExecutionResult(
                tool=action.tool,
                message="已調高音量" if steps > 0 else "已調低音量",
                details={"steps": steps},
            )
        if action.tool == "set_volume":
            level = action.arguments["level"]
            self.volume_setter(level)
            return ExecutionResult(
                tool=action.tool,
                message=f"已將音量調到 {level}",
                details={"level": level},
            )
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
            self.playback_controller.remember(result)
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
        if action.tool == "control_playback":
            result = self.playback_controller.control(
                action.arguments["operation"]
            )
            if not result.available:
                return ExecutionResult(
                    tool=action.tool,
                    message=NO_ACTIVE_MEDIA_REPLY,
                    details={
                        "operation": result.operation,
                        "available": False,
                        "changed": False,
                    },
                )
            verb = "繼續播放" if result.operation == "play" else "暫停播放"
            state = "已切換" if result.changed else "原本就是這個狀態"
            return ExecutionResult(
                tool=action.tool,
                message=f"{verb}：{state}",
                details={
                    "operation": result.operation,
                    "available": True,
                    "changed": result.changed,
                    "media_url": result.media_url,
                },
            )
        if action.tool == "set_timer":
            result = self.timer_controller.start(
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
        if action.tool == "cancel_timer":
            cancelled = self.timer_controller.cancel()
            return ExecutionResult(
                tool=action.tool,
                message="已取消計時器" if cancelled else "目前沒有正在計時",
                details={"cancelled": cancelled},
            )
        raise ControlExecutionError(f"工具尚未實作，拒絕執行：{action.tool}")
