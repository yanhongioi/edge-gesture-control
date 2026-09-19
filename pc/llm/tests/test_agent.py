from __future__ import annotations

import unittest

from pc.llm.agent import AgentPlanner
from pc.llm.client import ChatResult


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    def chat_json(self, messages, json_schema) -> ChatResult:  # type: ignore[no-untyped-def]
        self.calls += 1
        self.messages.append(messages)
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

    def test_explicit_search_stays_on_results_page_without_model(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("幫我找雞胸肉食譜")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "rule")
        self.assertEqual(plan.actions[0].tool, "search_web")
        self.assertEqual(
            plan.actions[0].arguments,
            {"query": "雞胸肉食譜", "open_first_result": False},
        )
        self.assertEqual(client.calls, 0)

    def test_numeric_timer_uses_fast_rule(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("幫我計時5分鐘")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "rule")
        self.assertEqual(plan.actions[0].tool, "set_timer")
        self.assertEqual(plan.actions[0].arguments["seconds"], 300)
        self.assertEqual(client.calls, 0)

    def test_chinese_timer_and_combined_units(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("倒數一小時二十分鐘")  # type: ignore[arg-type]
        self.assertEqual(plan.actions[0].arguments["seconds"], 4800)
        self.assertEqual(client.calls, 0)

    def test_timer_without_duration_asks_for_clarification(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("幫我設定計時器")  # type: ignore[arg-type]
        self.assertEqual(plan.intent, "clarify")
        self.assertEqual(plan.actions, ())
        self.assertEqual(client.calls, 0)

    def test_timer_followup_uses_context_without_calling_model(self) -> None:
        client = FakeClient("not used")
        planner = AgentPlanner(client=client)  # type: ignore[arg-type]
        first = planner.plan("幫我設定計時器")
        second = planner.plan("十分鐘")
        self.assertEqual(first.intent, "clarify")
        self.assertEqual(second.source, "context_rule")
        self.assertEqual(second.actions[0].tool, "set_timer")
        self.assertEqual(second.actions[0].arguments["seconds"], 600)
        self.assertEqual(client.calls, 0)

    def test_colloquial_cooking_question_uses_noun_and_skips_model(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("雞胸肉怎煮")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "rule")
        self.assertEqual(plan.actions[0].tool, "search_web")
        self.assertEqual(plan.actions[0].arguments["query"], "雞胸肉怎麼煮")
        self.assertFalse(plan.actions[0].arguments["open_first_result"])
        self.assertEqual(client.calls, 0)

    def test_cooking_question_without_concrete_noun_asks_for_clarification(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("這個怎做")  # type: ignore[arg-type]
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

    def test_non_music_request_cannot_reach_music_tool(self) -> None:
        client = FakeClient(
            '{"intent":"action","reply":"播放。","actions":'
            '[{"tool":"play_music","arguments":'
            '{"query":"iPhone Pixel 比較","selection":"playlist"}}]}'
        )
        plan = AgentPlanner(client=client).plan("比較 iPhone 和 Pixel")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "policy")
        self.assertEqual(plan.actions[0].tool, "search_web")
        self.assertFalse(plan.actions[0].arguments["open_first_result"])

    def test_explicit_music_request_keeps_music_tool(self) -> None:
        client = FakeClient(
            '{"intent":"action","reply":"播放。","actions":'
            '[{"tool":"play_music","arguments":'
            '{"query":"K-pop playlist","selection":"playlist"}}]}'
        )
        plan = AgentPlanner(client=client).plan("幫我播韓文歌")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "llm")
        self.assertEqual(plan.actions[0].tool, "play_music")

    def test_empty_input_returns_clarify_without_model(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("   ")  # type: ignore[arg-type]
        self.assertEqual(plan.intent, "clarify")
        self.assertEqual(client.calls, 0)

    def test_model_context_is_bounded_to_three_completed_turns(self) -> None:
        client = FakeClient(
            '{"intent":"answer","reply":"這是測試回答。","actions":[]}'
        )
        planner = AgentPlanner(client=client)  # type: ignore[arg-type]
        for text in ("甲項定義", "乙項定義", "丙項定義", "丁項定義", "戊項定義"):
            planner.plan(text)

        self.assertEqual(planner.context_size, 3)
        fifth_call = client.messages[-1]
        combined = "\n".join(message["content"] for message in fifth_call)
        self.assertNotIn("甲項定義", combined)
        self.assertIn("乙項定義", combined)
        self.assertIn("丙項定義", combined)
        self.assertIn("丁項定義", combined)
        self.assertIn("戊項定義", combined)

    def test_reset_context_removes_prior_turns(self) -> None:
        client = FakeClient(
            '{"intent":"answer","reply":"這是測試回答。","actions":[]}'
        )
        planner = AgentPlanner(client=client)  # type: ignore[arg-type]
        planner.plan("先前問題")
        planner.reset_context()
        planner.plan("目前問題")
        combined = "\n".join(
            message["content"] for message in client.messages[-1]
        )
        self.assertNotIn("先前問題", combined)


if __name__ == "__main__":
    unittest.main()
