"""不需 LLM 的明確指令快速路徑。"""

from __future__ import annotations

import re

from .schemas import AgentPlan, ToolAction


def _normalized(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?]+", "", text).casefold()


EXACT_HOTKEY_RULES: dict[str, tuple[str, str]] = {
    "暫停": ("playpause", "好的，準備暫停播放。"),
    "繼續播放": ("playpause", "好的，準備繼續播放。"),
    "下一首": ("nexttrack", "好的，準備播放下一首。"),
    "上一首": ("prevtrack", "好的，準備播放上一首。"),
    "靜音": ("volumemute", "好的，準備切換靜音。"),
    "音量大一點": ("volumeup", "好的，準備調高音量。"),
    "把音量調高": ("volumeup", "好的，準備調高音量。"),
    "調高音量": ("volumeup", "好的，準備調高音量。"),
    "音量小一點": ("volumedown", "好的，準備調低音量。"),
    "把音量調低": ("volumedown", "好的，準備調低音量。"),
    "調低音量": ("volumedown", "好的，準備調低音量。"),
}

SCROLL_RULES: dict[str, tuple[int, str]] = {
    "往下滑": (-500, "好的，準備往下捲動。"),
    "向下滑": (-500, "好的，準備往下捲動。"),
    "往下捲": (-500, "好的，準備往下捲動。"),
    "往上滑": (500, "好的，準備往上捲動。"),
    "向上滑": (500, "好的，準備往上捲動。"),
    "往上捲": (500, "好的，準備往上捲動。"),
}

AMBIGUOUS_RULES = frozenset(
    {
        "搜尋一下",
        "幫我搜尋",
        "幫我找一下",
        "幫我播放那一首",
        "播放那一首",
    }
)


def match_fast_rule(text: str) -> AgentPlan | None:
    normalized = _normalized(text)
    if normalized in AMBIGUOUS_RULES:
        return AgentPlan(
            intent="clarify",
            reply="請再告訴我你要搜尋或播放的內容。",
            source="rule",
        )
    if normalized in EXACT_HOTKEY_RULES:
        key, reply = EXACT_HOTKEY_RULES[normalized]
        return AgentPlan(
            intent="action",
            reply=reply,
            actions=(ToolAction("press_hotkey", {"keys": [key]}),),
            source="rule",
        )
    if normalized in SCROLL_RULES:
        amount, reply = SCROLL_RULES[normalized]
        return AgentPlan(
            intent="action",
            reply=reply,
            actions=(ToolAction("scroll", {"amount": amount}),),
            source="rule",
        )
    return None
