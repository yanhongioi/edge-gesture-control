"""接收板子 VIT 的喚醒訊號 (MQTT edge/voice)，喚醒後才讓下一句進 Whisper。

板端：board/voice/run_voice.sh (NXP AFE + VIT「HEY NXP」) → vit_notify.py → MQTT
訊息：{"type": "wakeword", "id": 1, "wakeword": "HEY NXP", "ts": ...}
     {"type": "command", ...} 是 VIT 內建的 12 個英文指令，這裡不用 (指令交給 Whisper + LLM)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


DEFAULT_WAKE_TOPIC = "edge/voice"


class BoardWakeError(RuntimeError):
    """MQTT 套件或 broker 無法使用。"""


def parse_wake_message(payload: bytes | str) -> str | None:
    """是喚醒訊息就回傳喚醒詞 (例如 "HEY NXP")，其他訊息回傳 None。"""
    try:
        message = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(message, dict) or message.get("type") != "wakeword":
        return None
    return str(message.get("wakeword") or "wakeword")


class BoardWakeListener:
    """在背景執行緒訂閱 MQTT；收到喚醒訊息就呼叫 on_wake(喚醒詞)。"""

    def __init__(
        self,
        host: str,
        on_wake: Callable[[str], None],
        *,
        port: int = 1883,
        topic: str = DEFAULT_WAKE_TOPIC,
    ) -> None:
        self.host = host
        self.port = port
        self.topic = topic
        self.on_wake = on_wake
        self._client: Any = None

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        wakeword = parse_wake_message(message.payload)
        if wakeword is not None:
            self.on_wake(wakeword)

    def __enter__(self) -> "BoardWakeListener":
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise BoardWakeError(
                "尚未安裝 paho-mqtt；請執行 py -m pip install -r pc/requirements.txt"
            ) from exc
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client()
        client.on_message = self._on_message
        client.on_connect = lambda c, *_args: c.subscribe(self.topic)
        try:
            client.connect(self.host, self.port, keepalive=30)
        except OSError as exc:
            raise BoardWakeError(
                f"無法連線到 MQTT broker {self.host}:{self.port}：{exc}"
            ) from exc
        client.loop_start()
        self._client = client
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
