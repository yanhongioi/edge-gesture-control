"""把喚醒後文字接到既有 AgentPlanner 與安全控制執行器。"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from pc.llm.schemas import AgentPlan

from .wakeword import WakeDecision, WakeWordGate


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
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.wake_gate = wake_gate or WakeWordGate()
        self.execute = execute
        self.reply_speaker = reply_speaker

    def handle_text(
        self,
        text: str,
        *,
        screen_context: str = "語音助理",
        now: float | None = None,
    ) -> VoicePipelineResult:
        decision = self.wake_gate.process(text, now=now)
        if decision.status != "command" or decision.command is None:
            return VoicePipelineResult(decision=decision)

        plan = self.planner.plan(decision.command, screen_context=screen_context)
        executions: tuple[Any, ...] = ()
        if self.execute and plan.error is None and self.reply_speaker is not None:
            self.reply_speaker(plan.reply)
        if (
            self.execute
            and plan.error is None
            and plan.intent == "action"
            and plan.actions
        ):
            executions = self.executor.execute(plan, confirmed=True)
        return VoicePipelineResult(
            decision=decision,
            plan=plan,
            executions=executions,
        )
