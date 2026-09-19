from __future__ import annotations

import unittest

from pc.audio.pipeline import VoiceCommandPipeline
from pc.audio.wakeword import WakeWordGate
from pc.llm.schemas import AgentPlan, ToolAction


class FakePlanner:
    def __init__(self) -> None:
        self.received: list[str] = []

    def plan(self, text: str, screen_context: str | None = None) -> AgentPlan:
        self.received.append(text)
        return AgentPlan(
            "action",
            "準備播放。",
            (ToolAction("play_music", {"query": "周杰倫 晴天"}),),
            source="test",
        )


class FakeExecutor:
    def __init__(self) -> None:
        self.confirmed: list[bool] = []

    def execute(self, plan: AgentPlan, *, confirmed: bool = False) -> tuple[str, ...]:
        self.confirmed.append(confirmed)
        return ("executed",)


class ClarifyingPlanner:
    def __init__(self) -> None:
        self.received: list[str] = []

    def plan(self, text: str, screen_context: str | None = None) -> AgentPlan:
        self.received.append(text)
        if len(self.received) == 1:
            return AgentPlan("clarify", "請告訴我要計時多久。", source="test")
        return AgentPlan(
            "action",
            "好的，開始計時。",
            (ToolAction("set_timer", {"seconds": 600, "label": "計時器"}),),
            source="test",
        )


class VoicePipelineTests(unittest.TestCase):
    def test_without_wake_word_never_reaches_planner(self) -> None:
        planner = FakePlanner()
        executor = FakeExecutor()
        pipeline = VoiceCommandPipeline(
            planner, executor, wake_gate=WakeWordGate("嘿小黑鬼"), execute=True
        )
        result = pipeline.handle_text("播放周杰倫的晴天", now=1.0)
        self.assertEqual(result.decision.status, "ignored")
        self.assertEqual(planner.received, [])
        self.assertEqual(executor.confirmed, [])

    def test_dry_run_plans_but_does_not_execute(self) -> None:
        planner = FakePlanner()
        executor = FakeExecutor()
        pipeline = VoiceCommandPipeline(
            planner, executor, wake_gate=WakeWordGate("嘿小黑鬼"), execute=False
        )
        result = pipeline.handle_text("嘿小黑鬼播放周杰倫的晴天", now=1.0)
        self.assertIsNotNone(result.plan)
        self.assertEqual(planner.received, ["播放周杰倫的晴天"])
        self.assertEqual(executor.confirmed, [])

    def test_execute_uses_explicit_confirmation(self) -> None:
        planner = FakePlanner()
        executor = FakeExecutor()
        pipeline = VoiceCommandPipeline(
            planner, executor, wake_gate=WakeWordGate("嘿小黑鬼"), execute=True
        )
        result = pipeline.handle_text("嘿小黑鬼播放周杰倫的晴天", now=1.0)
        self.assertEqual(result.executions, ("executed",))
        self.assertEqual(executor.confirmed, [True])

    def test_execute_speaks_reply_before_dispatch(self) -> None:
        events: list[str] = []

        class OrderedExecutor(FakeExecutor):
            def execute(self, plan, *, confirmed=False):  # type: ignore[no-untyped-def]
                events.append("execute")
                return super().execute(plan, confirmed=confirmed)

        pipeline = VoiceCommandPipeline(
            FakePlanner(),
            OrderedExecutor(),
            wake_gate=WakeWordGate("嘿小黑鬼"),
            execute=True,
            reply_speaker=lambda reply: events.append(f"speak:{reply}"),
        )
        pipeline.handle_text("嘿小黑鬼播放晴天", now=1.0)
        self.assertEqual(events, ["speak:準備播放。", "execute"])

    def test_clarification_followup_does_not_require_wake_word(self) -> None:
        planner = ClarifyingPlanner()
        pipeline = VoiceCommandPipeline(
            planner,
            FakeExecutor(),
            wake_gate=WakeWordGate("嘿小黑鬼"),
            execute=False,
            followup_timeout_seconds=20.0,
        )
        first = pipeline.handle_text("嘿小黑鬼幫我設定計時器", now=1.0)
        second = pipeline.handle_text("十分鐘", now=5.0)
        self.assertEqual(first.plan.intent, "clarify")  # type: ignore[union-attr]
        self.assertEqual(second.decision.status, "command")
        self.assertEqual(second.plan.intent, "action")  # type: ignore[union-attr]
        self.assertEqual(planner.received, ["幫我設定計時器", "十分鐘"])
        self.assertFalse(pipeline.awaiting_followup)

    def test_followup_requires_wake_word_after_timeout(self) -> None:
        planner = ClarifyingPlanner()
        pipeline = VoiceCommandPipeline(
            planner,
            FakeExecutor(),
            wake_gate=WakeWordGate("嘿小黑鬼"),
            execute=False,
            followup_timeout_seconds=20.0,
        )
        pipeline.handle_text("嘿小黑鬼幫我設定計時器", now=1.0)
        result = pipeline.handle_text("十分鐘", now=22.0)
        self.assertEqual(result.decision.status, "ignored")
        self.assertEqual(planner.received, ["幫我設定計時器"])

    def test_followup_can_be_cancelled_without_wake_word(self) -> None:
        planner = ClarifyingPlanner()
        pipeline = VoiceCommandPipeline(
            planner,
            FakeExecutor(),
            wake_gate=WakeWordGate("嘿小黑鬼"),
            execute=False,
        )
        pipeline.handle_text("嘿小黑鬼幫我設定計時器", now=1.0)
        result = pipeline.handle_text("取消", now=2.0)
        self.assertEqual(result.decision.status, "cancelled")
        self.assertEqual(planner.received, ["幫我設定計時器"])
        self.assertFalse(pipeline.awaiting_followup)


if __name__ == "__main__":
    unittest.main()
