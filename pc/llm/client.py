"""零第三方相依的 Ollama HTTP client。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "auto"
PREFERRED_MODEL = "qwen3:4b-instruct"
FALLBACK_MODEL = "qwen3:4b"


class OllamaError(RuntimeError):
    """Ollama 連線、模型或回應錯誤。"""


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    wall_seconds: float
    total_seconds: float | None
    load_seconds: float | None
    eval_seconds: float | None
    eval_count: int | None

    @property
    def tokens_per_second(self) -> float | None:
        if self.eval_seconds and self.eval_count is not None:
            return self.eval_count / self.eval_seconds
        return None


def _nanoseconds_to_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value / 1_000_000_000


def _extract_content(content: str) -> str:
    """相容舊 Qwen3 把 thinking 混進 content 的情況。"""
    if "</think>" in content:
        return content.rsplit("</think>", maxsplit=1)[1].strip()
    return content.strip()


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.requested_model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self._resolved_model: str | None = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama API HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", str(exc))
            raise OllamaError(f"無法連線到 Ollama（{self.base_url}）：{reason}") from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama 回傳的內容不是有效 JSON") from exc
        if not isinstance(result, dict):
            raise OllamaError("Ollama 回傳格式不是 JSON object")
        return result

    def list_models(self) -> tuple[str, ...]:
        result = self._request_json("GET", "/api/tags", timeout=10)
        names = {
            item.get("name")
            for item in result.get("models", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        return tuple(sorted(names))

    def resolve_model(self) -> str:
        if self._resolved_model is not None:
            return self._resolved_model
        installed = self.list_models()
        if self.requested_model == "auto":
            for candidate in (PREFERRED_MODEL, FALLBACK_MODEL):
                if candidate in installed:
                    self._resolved_model = candidate
                    return candidate
            raise OllamaError(
                f"找不到可用模型；請安裝 {PREFERRED_MODEL} 或 {FALLBACK_MODEL}"
            )
        if self.requested_model not in installed:
            raise OllamaError(
                f"模型 {self.requested_model!r} 尚未安裝；目前模型："
                f"{', '.join(installed) or '無'}"
            )
        self._resolved_model = self.requested_model
        return self.requested_model

    def chat_json(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> ChatResult:
        model = self.resolve_model()
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "format": json_schema,
            "messages": messages,
            "options": {
                "num_ctx": 4096,
                "num_predict": 256,
                "temperature": 0.1,
            },
        }
        started_at = time.perf_counter()
        result = self._request_json("POST", "/api/chat", payload)
        wall_seconds = time.perf_counter() - started_at
        message = result.get("message")
        raw_content = message.get("content", "") if isinstance(message, dict) else ""
        content = _extract_content(raw_content)
        if not content:
            raise OllamaError("Ollama 沒有回傳回答內容")
        eval_count = result.get("eval_count")
        return ChatResult(
            content=content,
            model=str(result.get("model") or model),
            wall_seconds=wall_seconds,
            total_seconds=_nanoseconds_to_seconds(result.get("total_duration")),
            load_seconds=_nanoseconds_to_seconds(result.get("load_duration")),
            eval_seconds=_nanoseconds_to_seconds(result.get("eval_duration")),
            eval_count=eval_count if isinstance(eval_count, int) else None,
        )

    def unload(self, model: str | None = None) -> None:
        """供冷啟動 benchmark 卸載模型；不刪除模型。"""
        target = model or self.resolve_model()
        self._request_json(
            "POST", "/api/generate", {"model": target, "keep_alive": 0}
        )
