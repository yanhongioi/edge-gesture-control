"""不需 LLM 的明確指令快速路徑。"""

from __future__ import annotations

import re

from .schemas import AgentPlan, ToolAction


def _normalized(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?]+", "", text).casefold()


EXACT_HOTKEY_RULES: dict[str, tuple[str, str]] = {
    "暫停": ("playpause", "是的，主人。"),
    "繼續播放": ("playpause", "是的，主人。"),
    "下一首": ("nexttrack", "是的，主人。"),
    "上一首": ("prevtrack", "是的，主人。"),
    "靜音": ("volumemute", "是的，主人。"),
    "音量大一點": ("volumeup", "是的，主人。"),
    "把音量調高": ("volumeup", "是的，主人。"),
    "調高音量": ("volumeup", "是的，主人。"),
    "音量小一點": ("volumedown", "是的，主人。"),
    "把音量調低": ("volumedown", "是的，主人。"),
    "調低音量": ("volumedown", "是的，主人。"),
}

SCROLL_RULES: dict[str, tuple[int, str]] = {
    "往下滑": (-500, "是的，主人。"),
    "向下滑": (-500, "是的，主人。"),
    "往下捲": (-500, "是的，主人。"),
    "往上滑": (500, "是的，主人。"),
    "向上滑": (500, "是的，主人。"),
    "往上捲": (500, "是的，主人。"),
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

SEARCH_PATTERN = re.compile(
    r"^\s*(?:請|麻煩)?\s*(?:幫我)?\s*"
    r"(?:搜尋|查詢|查找|查|找)(?:一下)?\s*(?P<query>.+?)\s*$",
    re.IGNORECASE,
)
COOKING_QUESTION_PATTERN = re.compile(
    r"^\s*(?P<subject>.+?)\s*(?:要)?怎麼(?P<method>做|煮)\s*$",
    re.IGNORECASE,
)
VAGUE_SUBJECTS = frozenset({"這個", "那個", "它", "這", "那"})
TIMER_HINT_PATTERN = re.compile(r"計時|倒數|計時器|提醒", re.IGNORECASE)
DURATION_PATTERN = re.compile(
    r"(?P<value>\d+|[零〇一二兩三四五六七八九十百]+)\s*"
    r"(?P<unit>小時|鐘頭|分鐘|分|秒鐘|秒)",
    re.IGNORECASE,
)
CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _parse_number(value: str) -> int | None:
    if value.isdecimal():
        return int(value)
    total = 0
    current = 0
    for character in value:
        if character in CHINESE_DIGITS:
            current = CHINESE_DIGITS[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def _timer_plan(text: str) -> AgentPlan | None:
    if TIMER_HINT_PATTERN.search(text) is None:
        return None
    seconds = 0
    for match in DURATION_PATTERN.finditer(text):
        value = _parse_number(match.group("value"))
        if value is None:
            continue
        unit = match.group("unit")
        multiplier = 3600 if unit in {"小時", "鐘頭"} else 60 if unit in {"分鐘", "分"} else 1
        seconds += value * multiplier
    if seconds <= 0:
        return AgentPlan(
            intent="clarify",
            reply="請告訴我要計時多久。",
            source="rule",
        )
    if seconds > 86_400:
        return AgentPlan(
            intent="clarify",
            reply="本機計時器最多只能設定二十四小時。",
            source="rule",
        )
    return AgentPlan(
        intent="action",
        reply="是的，主人。",
        actions=(
            ToolAction(
                "set_timer",
                {"seconds": seconds, "label": "計時器"},
            ),
        ),
        source="rule",
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
    timer_plan = _timer_plan(text)
    if timer_plan is not None:
        return timer_plan
    cooking_match = COOKING_QUESTION_PATTERN.match(text)
    if cooking_match is not None:
        subject = cooking_match.group("subject").strip()
        if subject in VAGUE_SUBJECTS:
            return AgentPlan(
                intent="clarify",
                reply="請告訴我要查哪一道料理或食材。",
                source="rule",
            )
        query = f"{subject}怎麼{cooking_match.group('method')}"
        return AgentPlan(
            intent="action",
            reply="是的，主人。",
            actions=(
                ToolAction(
                    "search_web",
                    {"query": query, "open_first_result": False},
                ),
            ),
            source="rule",
        )
    search_match = SEARCH_PATTERN.match(text)
    if search_match is not None:
        query = search_match.group("query").strip(" ，,。.!！?？、：:；;")
        if query:
            return AgentPlan(
                intent="action",
                reply="是的，主人。",
                actions=(
                    ToolAction(
                        "search_web",
                        {"query": query, "open_first_result": False},
                    ),
                ),
                source="rule",
            )
    return None
