from __future__ import annotations

import unittest

from pc.llm.agent import AgentPlanner
from pc.llm.client import ChatResult


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def chat_json(self, messages, json_schema) -> ChatResult:  # type: ignore[no-untyped-def]
        self.calls += 1
        return ChatResult(
            content=self.content,
            model="fake",
            wall_seconds=0.25,
            total_seconds=0.2,
            load_seconds=0.0,
            eval_seconds=0.1,
            eval_count=10,
        )


class AgentPlannerTests(unittest.TestCase):
    def test_fast_rule_does_not_call_model(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("往下滑")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "rule")
        self.assertEqual(plan.actions[0].tool, "scroll")
        self.assertEqual(client.calls, 0)

    def test_ambiguous_rule_asks_for_clarification(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("搜尋一下")  # type: ignore[arg-type]
        self.assertEqual(plan.intent, "clarify")
        self.assertEqual(plan.actions, ())
        self.assertEqual(client.calls, 0)

    def test_valid_model_plan(self) -> None:
        client = FakeClient(
            '{"intent":"answer","reply":"這是回答。","actions":[]}'
        )
        plan = AgentPlanner(client=client).plan("什麼是邊緣運算")  # type: ignore[arg-type]
        self.assertEqual(plan.intent, "answer")
        self.assertEqual(plan.model, "fake")
        self.assertIsNone(plan.error)

    def test_invalid_json_returns_clarify_without_retry(self) -> None:
        client = FakeClient("not json")
        plan = AgentPlanner(client=client).plan("測試")  # type: ignore[arg-type]
        self.assertEqual(plan.intent, "clarify")
        self.assertEqual(plan.source, "fallback")
        self.assertEqual(client.calls, 1)
        self.assertIsNotNone(plan.error)

    def test_unsafe_plan_returns_clarify(self) -> None:
        client = FakeClient(
            '{"intent":"action","reply":"執行。","actions":'
            '[{"tool":"run_shell","arguments":{}}]}'
        )
        plan = AgentPlanner(client=client).plan("刪除檔案")  # type: ignore[arg-type]
        self.assertEqual(plan.intent, "clarify")
        self.assertEqual(plan.actions, ())

    def test_empty_input_returns_clarify_without_model(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("   ")  # type: ignore[arg-type]
        self.assertEqual(plan.intent, "clarify")
        self.assertEqual(client.calls, 0)


if __name__ == "__main__":
    unittest.main()
