from __future__ import annotations

import unittest

from pc.llm.agent import AgentPlanner
from pc.llm.client import ChatResult
from pc.llm.schemas import ToolAction


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

    def test_scroll_distance_phrases_use_three_levels(self) -> None:
        cases = {
            "往下": -600,
            "幫我往下一點": -400,
            "往下一點點": -200,
            "往上": 600,
            "請往上一點": 400,
            "麻煩幫我往上一點點": 200,
        }
        for text, amount in cases.items():
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.actions[0].tool, "scroll")
                self.assertEqual(plan.actions[0].arguments["amount"], amount)
                self.assertEqual(client.calls, 0)

    def test_scroll_to_top_and_bottom_use_safe_hotkeys(self) -> None:
        for text, key in (("最上面", "home"), ("幫我到最下面", "end")):
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.actions[0].tool, "press_hotkey")
                self.assertEqual(plan.actions[0].arguments["keys"], ["ctrl", key])
                self.assertEqual(client.calls, 0)

    def test_relative_volume_phrases_use_three_levels(self) -> None:
        cases = {
            "大聲點": 15,
            "幫我大聲一點": 10,
            "大聲一點點": 5,
            "小聲點": -15,
            "請小聲一點": -10,
            "麻煩幫我小聲一點點": -5,
        }
        for text, steps in cases.items():
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.actions[0].tool, "adjust_volume")
                self.assertEqual(plan.actions[0].arguments["steps"], steps)
                self.assertEqual(client.calls, 0)

    def test_captain_volume_commands_use_fixed_levels_and_reply(self) -> None:
        for text, level in (("閉嘴", 0), ("太小聲喽", 100), ("太小聲囉", 100)):
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.reply, "是的船長!")
                self.assertEqual(plan.actions[0].tool, "set_volume")
                self.assertEqual(plan.actions[0].arguments["level"], level)
                self.assertEqual(plan.speak_reply, level != 0)
                self.assertEqual(client.calls, 0)

    def test_absolute_volume_accepts_digits_and_chinese_numbers(self) -> None:
        for text, level in (
            ("幫我把音量調到50", 50),
            ("請把音量設定成百分之八十", 80),
            ("音量設到100%", 100),
        ):
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.actions[0].tool, "set_volume")
                self.assertEqual(plan.actions[0].arguments["level"], level)
                self.assertEqual(client.calls, 0)

    def test_absolute_volume_rejects_missing_or_out_of_range_level(self) -> None:
        for text in ("幫我把音量調到多少", "把音量調到101"):
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.intent, "clarify")
                self.assertEqual(plan.actions, ())
                self.assertEqual(client.calls, 0)

    def test_pause_and_resume_use_stateful_playback_tool(self) -> None:
        for text, operation in (
            ("暫停", "pause"),
            ("幫我暫停影片", "pause"),
            ("播放", "play"),
            ("請幫我播放音樂", "play"),
            ("繼續播放", "play"),
        ):
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.source, "rule")
                self.assertEqual(plan.actions[0].tool, "control_playback")
                self.assertEqual(plan.actions[0].arguments["operation"], operation)
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

    def test_cancel_timer_uses_fast_rule(self) -> None:
        for text in ("取消計時器", "停止計時", "關掉計時器"):
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.source, "rule")
                self.assertEqual(plan.actions[0], ToolAction("cancel_timer", {}))
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

    def test_cooking_question_with_punctuation_skips_model(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("雞胸肉要怎麼煮?")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "rule")
        self.assertEqual(plan.actions[0].tool, "search_web")
        self.assertEqual(plan.actions[0].arguments["query"], "雞胸肉怎麼煮")
        self.assertFalse(plan.actions[0].arguments["open_first_result"])
        self.assertEqual(client.calls, 0)

    def test_cooking_desire_uses_recipe_search_and_skips_model(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("我想做海鮮義大利麵。")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "rule")
        self.assertEqual(plan.actions[0].tool, "search_web")
        self.assertEqual(plan.actions[0].arguments["query"], "海鮮義大利麵 食譜")
        self.assertFalse(plan.actions[0].arguments["open_first_result"])
        self.assertEqual(client.calls, 0)

    def test_non_food_desire_is_not_assumed_to_be_a_recipe(self) -> None:
        client = FakeClient(
            '{"intent":"answer","reply":"可以先列出作業需求。","actions":[]}'
        )
        plan = AgentPlanner(client=client).plan("我想做作業")  # type: ignore[arg-type]
        self.assertEqual(plan.source, "llm")
        self.assertEqual(plan.actions, ())
        self.assertEqual(client.calls, 1)

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

    def test_cooking_request_misclassified_as_music_is_redirected_to_search(self) -> None:
        client = FakeClient(
            '{"intent":"action","reply":"播放。","actions":'
            '[{"tool":"play_music","arguments":'
            '{"query":"海鮮義大利麵","selection":"track"}}]}'
        )
        plan = AgentPlanner(client=client).plan("海鮮義大利麵有哪些做法")  # type: ignore[arg-type]
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

    def test_yu_ai_dj_homophones_use_pinned_music_rule(self) -> None:
        for text in (
            "播放與愛DJ版",
            "幫我放雨愛 DJ 版",
            "我想聽語愛低階版",
            "來首魚愛迪傑版",
            "播放瑜愛低機版",
        ):
            with self.subTest(text=text):
                client = FakeClient("not used")
                plan = AgentPlanner(client=client).plan(text)  # type: ignore[arg-type]
                self.assertEqual(plan.source, "rule")
                self.assertEqual(plan.actions[0].tool, "play_music")
                self.assertEqual(
                    plan.actions[0].arguments,
                    {"query": "雨愛 DJ版", "selection": "track"},
                )
                self.assertEqual(client.calls, 0)

    def test_yu_ai_dj_search_does_not_trigger_pinned_playback(self) -> None:
        client = FakeClient("not used")
        plan = AgentPlanner(client=client).plan("搜尋與愛DJ版")  # type: ignore[arg-type]
        self.assertEqual(plan.actions[0].tool, "search_web")
        self.assertEqual(client.calls, 0)

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
