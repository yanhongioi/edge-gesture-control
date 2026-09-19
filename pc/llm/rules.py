"""不需 LLM 的明確指令快速路徑。"""

from __future__ import annotations

import re

from .schemas import AgentPlan, ToolAction


def _normalized(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?]+", "", text).casefold()


EXACT_HOTKEY_RULES: dict[str, tuple[str, str]] = {
    "下一首": ("nexttrack", "是的，主人。"),
    "上一首": ("prevtrack", "是的，主人。"),
}

EXACT_PLAYBACK_RULES: dict[str, str] = {
    "暫停": "pause",
    "暫停播放": "pause",
    "暫停音樂": "pause",
    "暫停影片": "pause",
    "播放": "play",
    "播放音樂": "play",
    "播放影片": "play",
    "繼續": "play",
    "繼續播放": "play",
    "繼續播": "play",
    "繼續播放音樂": "play",
    "繼續播放影片": "play",
}

SCROLL_RULES: dict[str, tuple[int, str]] = {
    "往下": (-600, "是的，主人。"),
    "往下滑": (-600, "是的，主人。"),
    "向下滑": (-600, "是的，主人。"),
    "往下捲": (-600, "是的，主人。"),
    "往下一點": (-400, "是的，主人。"),
    "往下一點點": (-200, "是的，主人。"),
    "往上": (600, "是的，主人。"),
    "往上滑": (600, "是的，主人。"),
    "向上滑": (600, "是的，主人。"),
    "往上捲": (600, "是的，主人。"),
    "往上一點": (400, "是的，主人。"),
    "往上一點點": (200, "是的，主人。"),
}

EXTREME_SCROLL_RULES: dict[str, str] = {
    "最上面": "home",
    "到最上面": "home",
    "回最上面": "home",
    "往最上面": "home",
    "最下面": "end",
    "到最下面": "end",
    "往最下面": "end",
}

CANCEL_TIMER_RULES = frozenset(
    {
        "取消計時",
        "取消計時器",
        "停止計時",
        "停止計時器",
        "關掉計時器",
        "不要計時了",
    }
)

RELATIVE_VOLUME_RULES: dict[str, int] = {
    "大聲點": 15,
    "音量大聲點": 15,
    "把音量調高": 15,
    "調高音量": 15,
    "大聲一點": 10,
    "音量大一點": 10,
    "音量大聲一點": 10,
    "大聲一點點": 5,
    "音量大一點點": 5,
    "音量大聲一點點": 5,
    "小聲點": -15,
    "音量小聲點": -15,
    "把音量調低": -15,
    "調低音量": -15,
    "小聲一點": -10,
    "音量小一點": -10,
    "音量小聲一點": -10,
    "小聲一點點": -5,
    "音量小一點點": -5,
    "音量小聲一點點": -5,
}

CAPTAIN_VOLUME_LEVELS: dict[str, int] = {
    "閉嘴": 0,
    "太小聲囉": 100,
    "太小聲喽": 100,
    "太小聲咯": 100,
    "太小聲了": 100,
    "太小聲啦": 100,
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
PINNED_YU_AI_QUERY = "雨愛 DJ版"
MUSIC_PLAYBACK_HINT_PATTERN = re.compile(
    r"播放|播(?:放)?|放(?:一|首|點|些)?(?:歌|音樂)?|想聽|我要聽|來點|來首",
    re.IGNORECASE,
)
YU_AI_DJ_PATTERN = re.compile(
    r"[雨與与予語语羽宇魚鱼玉遇禹余於于娛娱愉瑜渝逾育浴欲預预御郁寓譽誉]"
    r"[愛爱](?:(?:dj|dg|迪傑|迪杰|低階|低阶|低機|低机|低級|低级)(?:版)?)?",
    re.IGNORECASE,
)
TIMER_HINT_PATTERN = re.compile(r"計時|倒數|計時器|提醒", re.IGNORECASE)
DURATION_PATTERN = re.compile(
    r"(?P<value>\d+|[零〇一二兩三四五六七八九十百]+)\s*"
    r"(?P<unit>小時|鐘頭|分鐘|分|秒鐘|秒)",
    re.IGNORECASE,
)
VOLUME_LEVEL_PATTERN = re.compile(
    r"^(?:把)?音量(?:調|設定|設)(?:到|成)?(?:百分之)?"
    r"(?P<level>\d+|[零〇一二兩三四五六七八九十百]+)(?:%|％|趴)?$",
    re.IGNORECASE,
)
VOLUME_UNSPECIFIED_PATTERN = re.compile(
    r"^(?:把)?音量(?:調|設定|設)(?:到|成)?多少$",
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


def _strip_polite_prefix(normalized: str) -> str:
    command = normalized
    for prefix in ("請", "麻煩"):
        if command.startswith(prefix):
            command = command[len(prefix) :]
    if command.startswith("幫我"):
        command = command[2:]
    return command


def _playback_operation(normalized: str) -> str | None:
    return EXACT_PLAYBACK_RULES.get(_strip_polite_prefix(normalized))


def _volume_plan(command: str) -> AgentPlan | None:
    if command in CAPTAIN_VOLUME_LEVELS:
        level = CAPTAIN_VOLUME_LEVELS[command]
        return AgentPlan(
            intent="action",
            reply="是的船長!",
            actions=(
                ToolAction(
                    "set_volume",
                    {"level": level},
                ),
            ),
            source="rule",
            speak_reply=level != 0,
        )
    if command in RELATIVE_VOLUME_RULES:
        return AgentPlan(
            intent="action",
            reply="是的，主人。",
            actions=(
                ToolAction(
                    "adjust_volume",
                    {"steps": RELATIVE_VOLUME_RULES[command]},
                ),
            ),
            source="rule",
        )
    if VOLUME_UNSPECIFIED_PATTERN.match(command) is not None:
        return AgentPlan(
            intent="clarify",
            reply="請告訴我要把音量調到零到一百之間的多少。",
            source="rule",
        )
    level_match = VOLUME_LEVEL_PATTERN.match(command)
    if level_match is None:
        return None
    level = _parse_number(level_match.group("level"))
    if level is None or not 0 <= level <= 100:
        return AgentPlan(
            intent="clarify",
            reply="音量只能設定在零到一百之間。",
            source="rule",
        )
    return AgentPlan(
        intent="action",
        reply="是的，主人。",
        actions=(ToolAction("set_volume", {"level": level}),),
        source="rule",
    )


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
    plain_command = _strip_polite_prefix(normalized)
    if (
        MUSIC_PLAYBACK_HINT_PATTERN.search(normalized) is not None
        and YU_AI_DJ_PATTERN.search(normalized) is not None
    ):
        return AgentPlan(
            intent="action",
            reply="是的，主人。",
            actions=(
                ToolAction(
                    "play_music",
                    {"query": PINNED_YU_AI_QUERY, "selection": "track"},
                ),
            ),
            source="rule",
        )
    if normalized in AMBIGUOUS_RULES:
        return AgentPlan(
            intent="clarify",
            reply="請再告訴我你要搜尋或播放的內容。",
            source="rule",
        )
    playback_operation = _playback_operation(normalized)
    if playback_operation is not None:
        return AgentPlan(
            intent="action",
            reply="是的，主人。",
            actions=(
                ToolAction(
                    "control_playback",
                    {"operation": playback_operation},
                ),
            ),
            source="rule",
        )
    volume_plan = _volume_plan(plain_command)
    if volume_plan is not None:
        return volume_plan
    if normalized in EXACT_HOTKEY_RULES:
        key, reply = EXACT_HOTKEY_RULES[normalized]
        return AgentPlan(
            intent="action",
            reply=reply,
            actions=(ToolAction("press_hotkey", {"keys": [key]}),),
            source="rule",
        )
    if plain_command in SCROLL_RULES:
        amount, reply = SCROLL_RULES[plain_command]
        return AgentPlan(
            intent="action",
            reply=reply,
            actions=(ToolAction("scroll", {"amount": amount}),),
            source="rule",
        )
    if plain_command in EXTREME_SCROLL_RULES:
        return AgentPlan(
            intent="action",
            reply="是的，主人。",
            actions=(
                ToolAction(
                    "press_hotkey",
                    {"keys": ["ctrl", EXTREME_SCROLL_RULES[plain_command]]},
                ),
            ),
            source="rule",
        )
    if plain_command in CANCEL_TIMER_RULES:
        return AgentPlan(
            intent="action",
            reply="好的，已取消計時器。",
            actions=(ToolAction("cancel_timer", {}),),
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
