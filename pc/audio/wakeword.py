"""嚴格的喚醒詞比對與短暫待命狀態。"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
import unicodedata


DEFAULT_WAKE_PHRASE = "NXP"
DEFAULT_ARMED_TIMEOUT_SECONDS = 8.0
CANCEL_PHRASES = frozenset({"取消", "算了", "不用了"})


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "S"))
    )


def _build_prefix_pattern(phrase: str) -> re.Pattern[str]:
    characters = [
        character
        for character in unicodedata.normalize("NFKC", phrase)
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "S"))
    ]
    if not characters:
        raise ValueError("喚醒詞不可為空")
    separator = r"[^\w]*"
    return re.compile(r"^\s*" + separator.join(map(re.escape, characters)), re.IGNORECASE)


@dataclass(frozen=True)
class WakeDecision:
    status: str
    command: str | None = None


class WakeWordGate:
    """只有句首喚醒詞或已喚醒期間的下一句可通過。"""

    def __init__(
        self,
        phrase: str = DEFAULT_WAKE_PHRASE,
        *,
        armed_timeout_seconds: float = DEFAULT_ARMED_TIMEOUT_SECONDS,
    ) -> None:
        if armed_timeout_seconds <= 0:
            raise ValueError("喚醒等待時間必須大於零")
        self.phrase = phrase
        self.armed_timeout_seconds = armed_timeout_seconds
        self._pattern = _build_prefix_pattern(phrase)
        self._armed_until: float | None = None

    @property
    def armed(self) -> bool:
        return self._armed_until is not None

    def reset(self) -> None:
        self._armed_until = None

    def process(self, text: str, *, now: float | None = None) -> WakeDecision:
        current_time = time.monotonic() if now is None else now
        cleaned = text.strip() if isinstance(text, str) else ""
        if not cleaned:
            return WakeDecision("ignored")

        if self._armed_until is not None:
            if current_time <= self._armed_until:
                self.reset()
                if normalize_text(cleaned) in CANCEL_PHRASES:
                    return WakeDecision("cancelled")
                return WakeDecision("command", cleaned)
            self.reset()

        match = self._pattern.match(cleaned)
        if match is None:
            return WakeDecision("ignored")

        command = cleaned[match.end() :].lstrip(" \t，,。.!！?？、：:；;")
        if command:
            return WakeDecision("command", command)

        self._armed_until = current_time + self.armed_timeout_seconds
        return WakeDecision("armed")
