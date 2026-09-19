"""把喚醒後文字接到既有 AgentPlanner 與安全控制執行器。"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import time
from typing import Any

from pc.llm.schemas import AgentPlan

from .wakeword import CANCEL_PHRASES, WakeDecision, WakeWordGate, normalize_text


DEFAULT_FOLLOWUP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class VoicePipelineResult:
    decision: WakeDecision
    plan: AgentPlan | None = None
    executions: tuple[Any, ...] = ()


class VoiceCommandPipeline:
    def __init__(
        self,
        planner: Any,
        executor: Any,
        *,
        wake_gate: WakeWordGate | None = None,
        execute: bool = False,
        reply_speaker: Callable[[str], None] | None = None,
        followup_timeout_seconds: float = DEFAULT_FOLLOWUP_TIMEOUT_SECONDS,
    ) -> None:
        if followup_timeout_seconds <= 0:
            raise ValueError("追問等待秒數必須大於零")
        self.planner = planner
        self.executor = executor
        self.wake_gate = wake_gate or WakeWordGate()
        self.execute = execute
        self.reply_speaker = reply_speaker
        self.followup_timeout_seconds = followup_timeout_seconds
        self._followup_until: float | None = None

    @property
    def awaiting_followup(self) -> bool:
        return self.is_awaiting_followup()

    def is_awaiting_followup(self, *, now: float | None = None) -> bool:
        """回報追問窗口是否仍有效，並順便清掉已逾時的狀態。"""
        if self._followup_until is None:
            return False
        current_time = time.monotonic() if now is None else now
        if current_time > self._followup_until:
            self._followup_until = None
            return False
        return True

    def renew_followup_window(self, *, now: float | None = None) -> bool:
        """TTS 與殘響處理完成後，重新給使用者完整的追問時間。"""
        if self._followup_until is None:
            return False
        current_time = time.monotonic() if now is None else now
        self._followup_until = current_time + self.followup_timeout_seconds
        return True

    def handle_text(
        self,
        text: str,
        *,
        screen_context: str = "語音助理",
        now: float | None = None,
    ) -> VoicePipelineResult:
        current_time = time.monotonic() if now is None else now
        cleaned = text.strip() if isinstance(text, str) else ""

        if self._followup_until is not None and current_time <= self._followup_until:
            if normalize_text(cleaned) in CANCEL_PHRASES:
                self._followup_until = None
                return VoicePipelineResult(decision=WakeDecision("cancelled"))

            # 追問期間仍接受「喚醒詞＋指令」，並避免把喚醒詞送進 planner。
            wake_decision = self.wake_gate.process(cleaned, now=current_time)
            if wake_decision.status == "command":
                decision = wake_decision
            elif wake_decision.status == "armed":
                self._followup_until = None
                return VoicePipelineResult(decision=wake_decision)
            elif cleaned:
                decision = WakeDecision("command", cleaned)
            else:
                decision = WakeDecision("ignored")
            self._followup_until = None
        else:
            self._followup_until = None
            decision = self.wake_gate.process(cleaned, now=current_time)

        if decision.status != "command" or decision.command is None:
            return VoicePipelineResult(decision=decision)

        plan = self.planner.plan(decision.command, screen_context=screen_context)
        if self.execute and plan.error is None:
            prepare_plan = getattr(self.executor, "prepare_plan", None)
            if callable(prepare_plan):
                plan = prepare_plan(plan)
        executions: tuple[Any, ...] = ()
        if (
            self.execute
            and plan.error is None
            and plan.intent == "action"
            and plan.actions
        ):
            executions = self.executor.execute(plan, confirmed=True)
        if (
            self.execute
            and plan.error is None
            and plan.speak_reply
            and self.reply_speaker is not None
        ):
            self.reply_speaker(plan.reply)
        if plan.intent == "clarify" and plan.error is None:
            # 真實執行時從語音回覆播放完畢後開始計算等待時間。
            followup_started_at = current_time if now is not None else time.monotonic()
            self._followup_until = followup_started_at + self.followup_timeout_seconds
        return VoicePipelineResult(
            decision=decision,
            plan=plan,
            executions=executions,
        )
