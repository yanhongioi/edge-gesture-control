from __future__ import annotations

import unittest

from pc.llm.schemas import PlanValidationError, validate_plan


class ValidatePlanTests(unittest.TestCase):
    def test_valid_action(self) -> None:
        plan = validate_plan(
            {
                "intent": "action",
                "reply": "準備搜尋食譜。",
                "actions": [
                    {
                        "tool": "open_url",
                        "arguments": {"url": "https://example.com/search?q=food"},
                    }
                ],
            }
        )
        self.assertEqual(plan.actions[0].tool, "open_url")

    def test_search_and_music_queries(self) -> None:
        arguments_by_tool = {
            "search_web": {"query": "晴天 周杰倫", "open_first_result": False},
            "play_music": {"query": "晴天 周杰倫", "selection": "track"},
        }
        for tool, arguments in arguments_by_tool.items():
            plan = validate_plan(
                {
                    "intent": "action",
                    "reply": "準備執行。",
                    "actions": [{"tool": tool, "arguments": arguments}],
                }
            )
            self.assertEqual(plan.actions[0].tool, tool)

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "錯誤。",
                    "actions": [
                        {
                            "tool": "play_music",
                            "arguments": {"query": " ", "selection": "track"},
                        }
                    ],
                }
            )

    def test_invalid_music_selection_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "錯誤。",
                    "actions": [
                        {
                            "tool": "play_music",
                            "arguments": {"query": "韓文歌", "selection": "random"},
                        }
                    ],
                }
            )

    def test_search_first_result_flag_must_be_boolean(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "錯誤。",
                    "actions": [
                        {
                            "tool": "search_web",
                            "arguments": {
                                "query": "雞胸肉食譜",
                                "open_first_result": "false",
                            },
                        }
                    ],
                }
            )

    def test_valid_timer(self) -> None:
        plan = validate_plan(
            {
                "intent": "action",
                "reply": "開始計時。",
                "actions": [
                    {
                        "tool": "set_timer",
                        "arguments": {"seconds": 300, "label": "煮蛋"},
                    }
                ],
            }
        )
        self.assertEqual(plan.actions[0].arguments["seconds"], 300)

    def test_invalid_timer_duration_is_rejected(self) -> None:
        for seconds in (0, 86_401, True):
            with self.subTest(seconds=seconds), self.assertRaises(PlanValidationError):
                validate_plan(
                    {
                        "intent": "action",
                        "reply": "錯誤。",
                        "actions": [
                            {
                                "tool": "set_timer",
                                "arguments": {"seconds": seconds, "label": "計時器"},
                            }
                        ],
                    }
                )

    def test_invalid_json_shape(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan([])

    def test_unknown_tool(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "不應通過。",
                    "actions": [{"tool": "run_shell", "arguments": {}}],
                }
            )

    def test_more_than_three_actions(self) -> None:
        action = {"tool": "scroll", "arguments": {"amount": -100}}
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {"intent": "action", "reply": "太多動作。", "actions": [action] * 4}
            )

    def test_http_url_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "不安全網址。",
                    "actions": [
                        {"tool": "open_url", "arguments": {"url": "http://example.com"}}
                    ],
                }
            )

    def test_url_credentials_are_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "不安全網址。",
                    "actions": [
                        {
                            "tool": "open_url",
                            "arguments": {"url": "https://user:pass@example.com"},
                        }
                    ],
                }
            )

    def test_invalid_hotkey_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "不安全按鍵。",
                    "actions": [
                        {"tool": "press_hotkey", "arguments": {"keys": ["f13"]}}
                    ],
                }
            )

    def test_dangerous_hotkey_combination_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "不安全按鍵。",
                    "actions": [
                        {
                            "tool": "press_hotkey",
                            "arguments": {"keys": ["ctrl", "alt", "delete"]},
                        }
                    ],
                }
            )

    def test_non_string_intent_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan({"intent": [], "reply": "錯誤。", "actions": []})

    def test_non_string_tool_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "錯誤。",
                    "actions": [{"tool": [], "arguments": {}}],
                }
            )

    def test_unlisted_app_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "未知程式。",
                    "actions": [
                        {"tool": "launch_app", "arguments": {"name": "cmd"}}
                    ],
                }
            )

    def test_long_text_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "文字過長。",
                    "actions": [
                        {"tool": "type_text", "arguments": {"text": "字" * 201}}
                    ],
                }
            )

    def test_shell_text_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "action",
                    "reply": "危險文字。",
                    "actions": [
                        {"tool": "type_text", "arguments": {"text": "Remove-Item C:\\"}}
                    ],
                }
            )

    def test_empty_reply_is_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan({"intent": "answer", "reply": "", "actions": []})

    def test_answer_cannot_have_actions(self) -> None:
        with self.assertRaises(PlanValidationError):
            validate_plan(
                {
                    "intent": "answer",
                    "reply": "回答。",
                    "actions": [{"tool": "scroll", "arguments": {"amount": 100}}],
                }
            )


if __name__ == "__main__":
    unittest.main()
