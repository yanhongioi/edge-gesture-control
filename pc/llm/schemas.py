"""Agent 計畫的資料結構與安全驗證。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


MAX_ACTIONS = 3
MAX_REPLY_LENGTH = 160
MAX_TYPED_TEXT_LENGTH = 200
MAX_URL_LENGTH = 2048
MAX_QUERY_LENGTH = 200

ALLOWED_INTENTS = frozenset({"answer", "action", "clarify"})
ALLOWED_TOOLS = frozenset(
    {
        "scroll", "press_hotkey", "open_url", "launch_app", "type_text",
        "search_web", "play_music",
    }
)
ALLOWED_APPS = frozenset(
    {"browser", "music", "spotify", "vlc", "timer", "clock", "calculator", "notepad"}
)
ALLOWED_KEYS = frozenset(
    {
        "ctrl", "alt", "shift", "win", "tab", "enter", "escape", "space",
        "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
        "playpause", "nexttrack", "prevtrack", "volumeup", "volumedown",
        "volumemute", "backspace", "delete",
        *tuple("abcdefghijklmnopqrstuvwxyz"),
        *tuple("0123456789"),
    }
)
ALLOWED_HOTKEY_COMBINATIONS = frozenset(
    {
        ("enter",), ("escape",), ("tab",), ("space",),
        ("up",), ("down",), ("left",), ("right",),
        ("home",), ("end",), ("pageup",), ("pagedown",),
        ("playpause",), ("nexttrack",), ("prevtrack",),
        ("volumeup",), ("volumedown",), ("volumemute",),
        ("ctrl", "a"), ("ctrl", "c"), ("ctrl", "f"),
        ("ctrl", "l"), ("ctrl", "t"), ("ctrl", "v"), ("ctrl", "w"),
        ("ctrl", "shift", "t"),
        ("alt", "left"), ("alt", "right"),
    }
)

DANGEROUS_TYPED_FRAGMENTS = (
    "rm -rf",
    "remove-item",
    "del /",
    "format ",
    "shutdown",
    "cmd.exe",
    "powershell",
    "reg delete",
)


class PlanValidationError(ValueError):
    """模型產生的計畫未通過結構或安全驗證。"""


@dataclass(frozen=True)
class ToolAction:
    tool: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class AgentPlan:
    intent: str
    reply: str
    actions: tuple[ToolAction, ...] = ()
    source: str = field(default="llm", compare=False)
    model: str | None = field(default=None, compare=False)
    latency_seconds: float = field(default=0.0, compare=False)
    tokens_per_second: float | None = field(default=None, compare=False)
    error: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """只輸出未來控制層需要的公開資料。"""
        return {
            "intent": self.intent,
            "reply": self.reply,
            "actions": [action.to_dict() for action in self.actions],
        }


def _require_exact_keys(data: dict[str, Any], expected: set[str], location: str) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise PlanValidationError(
            f"{location} 欄位不符；缺少={missing or '無'}，多餘={extra or '無'}"
        )


def _validate_scroll(arguments: dict[str, Any]) -> dict[str, Any]:
    _require_exact_keys(arguments, {"amount"}, "scroll.arguments")
    amount = arguments["amount"]
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise PlanValidationError("scroll.amount 必須是整數")
    if amount == 0 or not -1000 <= amount <= 1000:
        raise PlanValidationError("scroll.amount 必須介於 -1000 到 1000 且不可為 0")
    return {"amount": amount}


def _validate_hotkey(arguments: dict[str, Any]) -> dict[str, Any]:
    _require_exact_keys(arguments, {"keys"}, "press_hotkey.arguments")
    keys = arguments["keys"]
    if not isinstance(keys, list) or not 1 <= len(keys) <= 4:
        raise PlanValidationError("press_hotkey.keys 必須包含 1 到 4 個按鍵")
    normalized: list[str] = []
    for key in keys:
        if not isinstance(key, str) or key.lower() not in ALLOWED_KEYS:
            raise PlanValidationError(f"不允許的快捷鍵：{key!r}")
        normalized.append(key.lower())
    if tuple(normalized) not in ALLOWED_HOTKEY_COMBINATIONS:
        raise PlanValidationError(f"不允許的快捷鍵組合：{normalized!r}")
    return {"keys": normalized}


def _validate_url(arguments: dict[str, Any]) -> dict[str, Any]:
    _require_exact_keys(arguments, {"url"}, "open_url.arguments")
    url = arguments["url"]
    if not isinstance(url, str) or not 1 <= len(url) <= MAX_URL_LENGTH:
        raise PlanValidationError("open_url.url 長度不合法")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise PlanValidationError("open_url.url 只允許完整的 HTTPS 網址")
    if parsed.username is not None or parsed.password is not None:
        raise PlanValidationError("open_url.url 不允許內嵌帳號或密碼")
    return {"url": url}


def _validate_app(arguments: dict[str, Any]) -> dict[str, Any]:
    _require_exact_keys(arguments, {"name"}, "launch_app.arguments")
    name = arguments["name"]
    if not isinstance(name, str) or name.lower() not in ALLOWED_APPS:
        raise PlanValidationError(f"不允許啟動的應用程式：{name!r}")
    return {"name": name.lower()}


def _validate_typed_text(arguments: dict[str, Any]) -> dict[str, Any]:
    _require_exact_keys(arguments, {"text"}, "type_text.arguments")
    text = arguments["text"]
    if not isinstance(text, str) or not text.strip():
        raise PlanValidationError("type_text.text 不可為空")
    if len(text) > MAX_TYPED_TEXT_LENGTH:
        raise PlanValidationError(f"type_text.text 不可超過 {MAX_TYPED_TEXT_LENGTH} 字")
    lowered = text.casefold()
    if any(fragment in lowered for fragment in DANGEROUS_TYPED_FRAGMENTS):
        raise PlanValidationError("type_text.text 含有禁止的系統指令")
    return {"text": text}


def _validate_query(arguments: dict[str, Any], tool: str) -> dict[str, Any]:
    _require_exact_keys(arguments, {"query"}, f"{tool}.arguments")
    query = arguments["query"]
    if not isinstance(query, str) or not query.strip():
        raise PlanValidationError(f"{tool}.query 不可為空")
    if len(query) > MAX_QUERY_LENGTH:
        raise PlanValidationError(f"{tool}.query 不可超過 {MAX_QUERY_LENGTH} 字")
    return {"query": query.strip()}


ARGUMENT_VALIDATORS = {
    "scroll": _validate_scroll,
    "press_hotkey": _validate_hotkey,
    "open_url": _validate_url,
    "launch_app": _validate_app,
    "type_text": _validate_typed_text,
    "search_web": lambda arguments: _validate_query(arguments, "search_web"),
    "play_music": lambda arguments: _validate_query(arguments, "play_music"),
}


def validate_plan(data: Any) -> AgentPlan:
    """把不可信任的模型 JSON 轉成已驗證、不可變的 AgentPlan。"""
    if not isinstance(data, dict):
        raise PlanValidationError("最外層必須是 JSON object")
    _require_exact_keys(data, {"intent", "reply", "actions"}, "plan")

    intent = data["intent"]
    reply = data["reply"]
    actions_data = data["actions"]
    if not isinstance(intent, str) or intent not in ALLOWED_INTENTS:
        raise PlanValidationError(f"不允許的 intent：{intent!r}")
    if not isinstance(reply, str) or not reply.strip():
        raise PlanValidationError("reply 不可為空")
    if len(reply) > MAX_REPLY_LENGTH:
        raise PlanValidationError(f"reply 不可超過 {MAX_REPLY_LENGTH} 字")
    if not isinstance(actions_data, list):
        raise PlanValidationError("actions 必須是陣列")
    if len(actions_data) > MAX_ACTIONS:
        raise PlanValidationError(f"actions 最多只能有 {MAX_ACTIONS} 個動作")
    if intent == "action" and not actions_data:
        raise PlanValidationError("action intent 至少需要一個動作")
    if intent != "action" and actions_data:
        raise PlanValidationError("answer/clarify intent 不可包含動作")

    actions: list[ToolAction] = []
    for index, action_data in enumerate(actions_data):
        if not isinstance(action_data, dict):
            raise PlanValidationError(f"actions[{index}] 必須是 object")
        _require_exact_keys(action_data, {"tool", "arguments"}, f"actions[{index}]")
        tool = action_data["tool"]
        arguments = action_data["arguments"]
        if not isinstance(tool, str) or tool not in ALLOWED_TOOLS:
            raise PlanValidationError(f"不允許的工具：{tool!r}")
        if not isinstance(arguments, dict):
            raise PlanValidationError(f"{tool}.arguments 必須是 object")
        safe_arguments = ARGUMENT_VALIDATORS[tool](arguments)
        actions.append(ToolAction(tool=tool, arguments=safe_arguments))

    return AgentPlan(intent=intent, reply=reply.strip(), actions=tuple(actions))


PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "reply", "actions"],
    "properties": {
        "intent": {"type": "string", "enum": sorted(ALLOWED_INTENTS)},
        "reply": {"type": "string", "minLength": 1, "maxLength": MAX_REPLY_LENGTH},
        "actions": {
            "type": "array",
            "maxItems": MAX_ACTIONS,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tool", "arguments"],
                        "properties": {
                            "tool": {"const": "scroll"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["amount"],
                                "properties": {
                                    "amount": {
                                        "type": "integer",
                                        "minimum": -1000,
                                        "maximum": 1000,
                                    }
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tool", "arguments"],
                        "properties": {
                            "tool": {"const": "press_hotkey"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["keys"],
                                "properties": {
                                    "keys": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 4,
                                        "items": {
                                            "type": "string",
                                            "enum": sorted(ALLOWED_KEYS),
                                        },
                                    }
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tool", "arguments"],
                        "properties": {
                            "tool": {"const": "open_url"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["url"],
                                "properties": {
                                    "url": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": MAX_URL_LENGTH,
                                    }
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tool", "arguments"],
                        "properties": {
                            "tool": {"const": "launch_app"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name"],
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "enum": sorted(ALLOWED_APPS),
                                    }
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tool", "arguments"],
                        "properties": {
                            "tool": {"const": "type_text"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["text"],
                                "properties": {
                                    "text": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": MAX_TYPED_TEXT_LENGTH,
                                    }
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tool", "arguments"],
                        "properties": {
                            "tool": {"const": "search_web"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["query"],
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": MAX_QUERY_LENGTH,
                                    }
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["tool", "arguments"],
                        "properties": {
                            "tool": {"const": "play_music"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["query"],
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": MAX_QUERY_LENGTH,
                                    }
                                },
                            },
                        },
                    },
                ]
            },
        },
    },
}
