"""規則快速路徑與 Ollama 結構化規劃的統一入口。"""

from __future__ import annotations

from dataclasses import replace
import json
import time

from .client import OllamaClient, OllamaError
from .prompts import build_messages
from .rules import match_fast_rule
from .schemas import AgentPlan, PLAN_JSON_SCHEMA, PlanValidationError, validate_plan


CLARIFY_REPLY = "抱歉，我無法安全判斷這個指令，請換個方式再說一次。"


class AgentPlanner:
    """產生安全計畫；不包含任何工具執行能力。"""

    def __init__(self, client: OllamaClient | None = None) -> None:
        self.client = client or OllamaClient()

    def plan(self, text: str, screen_context: str | None = None) -> AgentPlan:
        started_at = time.perf_counter()
        if not isinstance(text, str) or not text.strip():
            return AgentPlan(
                intent="clarify",
                reply="請告訴我你想做什麼。",
                source="validation",
                latency_seconds=time.perf_counter() - started_at,
                error="輸入文字為空",
            )

        rule_plan = match_fast_rule(text)
        if rule_plan is not None:
            return replace(
                rule_plan,
                latency_seconds=time.perf_counter() - started_at,
            )

        try:
            result = self.client.chat_json(
                build_messages(text, screen_context), PLAN_JSON_SCHEMA
            )
            payload = json.loads(result.content)
            safe_plan = validate_plan(payload)
            return replace(
                safe_plan,
                source="llm",
                model=result.model,
                latency_seconds=result.wall_seconds,
                tokens_per_second=result.tokens_per_second,
            )
        except (OllamaError, PlanValidationError, json.JSONDecodeError) as exc:
            return AgentPlan(
                intent="clarify",
                reply=CLARIFY_REPLY,
                source="fallback",
                latency_seconds=time.perf_counter() - started_at,
                error=str(exc),
            )
