"""Conservative normalization for colloquial Mandarin voice commands."""

from __future__ import annotations

import re


NUMBER_TEXT = r"(?:\d+|[零〇一二兩三四五六七八九十百]+)"
TIMER_CONTEXT = re.compile(r"計時|倒數|計時器|提醒", re.IGNORECASE)
SHORT_HOUR = re.compile(
    rf"(?P<value>{NUMBER_TEXT})\s*小(?=\s*(?:後|鐘|$))",
    re.IGNORECASE,
)
GENERIC_MUSIC = re.compile(
    r"^(?P<prefix>\s*(?:(?:請|麻煩)\s*)?(?:幫我\s*)?)"
    r"放(?:點|些|一首|首)?歌\s*$",
    re.IGNORECASE,
)
SPECIFIC_MUSIC = re.compile(
    r"^(?P<prefix>\s*(?:(?:請|麻煩)\s*)?(?:幫我\s*)?)放"
    r"(?=(?:點|些|一首|首)?[^\n]*(?:歌|音樂|歌單|playlist))",
    re.IGNORECASE,
)


def normalize_command_text(text: str) -> str:
    """Expand a small allowlist of common omissions without rewriting nouns."""
    normalized = text.strip()
    if not normalized:
        return normalized

    generic_music = GENERIC_MUSIC.match(normalized)
    if generic_music is not None:
        normalized = f"{generic_music.group('prefix')}播放熱門歌曲"
    else:
        normalized = SPECIFIC_MUSIC.sub(r"\g<prefix>播放", normalized, count=1)

    normalized = re.sub(r"怎\s*煮", "怎麼煮", normalized)
    normalized = re.sub(r"怎\s*做", "怎麼做", normalized)

    if TIMER_CONTEXT.search(normalized):
        normalized = SHORT_HOUR.sub(r"\g<value>小時", normalized)
    return normalized
