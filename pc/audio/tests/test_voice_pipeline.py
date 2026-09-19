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


if __name__ == "__main__":
    unittest.main()
