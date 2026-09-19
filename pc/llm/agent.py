"""規則快速路徑與 Ollama 結構化規劃的統一入口。"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import json
import re
import time

from .client import OllamaClient, OllamaError
from .normalization import normalize_command_text
from .prompts import build_messages
from .rules import match_fast_rule
from .schemas import (
    AgentPlan,
    PLAN_JSON_SCHEMA,
    PlanValidationError,
    ToolAction,
    validate_plan,
)


CLARIFY_REPLY = "抱歉，我無法安全判斷這個指令，請換個方式再說一次。"
MAX_CONTEXT_TURNS = 3
MAX_CONTEXT_TEXT_LENGTH = 300
MUSIC_PLAYBACK_PATTERN = re.compile(
    r"播放|播(?:放)?|放(?:一|首|點|些)?(?:歌|音樂)|想聽|我要聽|來點|來首",
    re.IGNORECASE,
)
WEB_INFORMATION_PATTERN = re.compile(
    r"搜尋|查詢|查找|查|找|比較|推薦|食譜|作法|教學|新聞|資料|資訊",
    re.IGNORECASE,
)
COOKING_INFORMATION_PATTERN = re.compile(
    r"食物|食材|料理|食譜|作法|做法|怎麼(?:做|煮)|"
    r"(?:我)?(?:想(?:要)?|要)(?:做|煮)",
    re.IGNORECASE,
)


def _enforce_semantic_policy(text: str, plan: AgentPlan) -> AgentPlan:
    """Prevent a small model from routing non-music requests to YouTube."""
    has_music_action = any(action.tool == "play_music" for action in plan.actions)
    if not has_music_action or MUSIC_PLAYBACK_PATTERN.search(text):
        return plan

    if WEB_INFORMATION_PATTERN.search(text) or COOKING_INFORMATION_PATTERN.search(text):
        query = text.strip()[:200]
        return replace(
            plan,
            reply=f"是的，主人，準備搜尋 {query} 的資料。",
            actions=(
                ToolAction(
                    "search_web",
                    {"query": query, "open_first_result": False},
                ),
            ),
            source="policy",
        )

    return AgentPlan(
        intent="clarify",
        reply="請問你要搜尋資料，還是播放音樂？",
        source="policy",
        model=plan.model,
        latency_seconds=plan.latency_seconds,
        tokens_per_second=plan.tokens_per_second,
        error="非音樂播放指令被模型規劃成 play_music",
    )


class AgentPlanner:
    """產生安全計畫；不包含任何工具執行能力。"""

    def __init__(self, client: OllamaClient | None = None) -> None:
        self.client = client or OllamaClient()
        self._history: deque[tuple[str, AgentPlan]] = deque(
            maxlen=MAX_CONTEXT_TURNS
        )

    @property
    def context_size(self) -> int:
        return len(self._history)

    def reset_context(self) -> None:
        """清除記憶中的文字計畫；不影響 Ollama 或喚醒詞狀態。"""
        self._history.clear()

    def _remember(self, text: str, plan: AgentPlan) -> AgentPlan:
        # 僅保存已通過驗證的短文字與公開計畫，不保存音訊或模型原始輸出。
        if plan.error is None:
            self._history.append((text[:MAX_CONTEXT_TEXT_LENGTH], plan))
        return plan

    def _message_history(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (
                user_text,
                json.dumps(
                    plan.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            for user_text, plan in self._history
        )

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

        normalized_text = normalize_command_text(text)

        # 對上一輪的明確追問先走規則快路徑。例如：
        # 「幫我設定計時器」→「請告訴我要計時多久」→「十分鐘」。
        if self._history:
            previous_text, previous_plan = self._history[-1]
            if previous_plan.intent == "clarify" and previous_plan.error is None:
                contextual_plan = match_fast_rule(
                    f"{previous_text} {normalized_text}"
                )
                if contextual_plan is not None and contextual_plan.intent != "clarify":
                    return self._remember(
                        normalized_text,
                        replace(
                            contextual_plan,
                            source="context_rule",
                            latency_seconds=time.perf_counter() - started_at,
                        ),
                    )

        rule_plan = match_fast_rule(normalized_text)
        if rule_plan is not None:
            return self._remember(
                normalized_text,
                replace(
                    rule_plan,
                    latency_seconds=time.perf_counter() - started_at,
                ),
            )

        try:
            result = self.client.chat_json(
                build_messages(
                    normalized_text,
                    screen_context,
                    history=self._message_history(),
                ),
                PLAN_JSON_SCHEMA,
            )
            payload = json.loads(result.content)
            safe_plan = validate_plan(payload)
            model_plan = replace(
                safe_plan,
                source="llm",
                model=result.model,
                latency_seconds=result.wall_seconds,
                tokens_per_second=result.tokens_per_second,
            )
            return self._remember(
                normalized_text,
                _enforce_semantic_policy(normalized_text, model_plan),
            )
        except (OllamaError, PlanValidationError, json.JSONDecodeError) as exc:
            return AgentPlan(
                intent="clarify",
                reply=CLARIFY_REPLY,
                source="fallback",
                latency_seconds=time.perf_counter() - started_at,
                error=str(exc),
            )
