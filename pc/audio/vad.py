"""使用 Silero VAD 將連續音訊切成獨立語句。"""

from __future__ import annotations

from collections import deque
from typing import Any


class VadError(RuntimeError):
    """Silero VAD 無法載入或處理音訊。"""


class SileroSpeechSegmenter:
    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        threshold: float = 0.5,
        min_silence_ms: int = 700,
        speech_pad_ms: int = 300,
        max_utterance_seconds: float = 12.0,
        block_samples: int = 512,
    ) -> None:
        try:
            import torch
            from silero_vad import VADIterator, load_silero_vad

            model = load_silero_vad(onnx=True)
            self._torch = torch
            self._iterator = VADIterator(
                model,
                threshold=threshold,
                sampling_rate=sample_rate,
                min_silence_duration_ms=min_silence_ms,
                speech_pad_ms=speech_pad_ms,
            )
        except Exception as exc:
            raise VadError(f"無法載入 Silero VAD：{exc}") from exc

        self.sample_rate = sample_rate
        self.max_samples = int(max_utterance_seconds * sample_rate)
        pre_roll_blocks = max(1, round(speech_pad_ms * sample_rate / 1000 / block_samples))
        self._pre_roll: deque[Any] = deque(maxlen=pre_roll_blocks)
        self._speech: list[Any] = []
        self._speech_samples = 0
        self._active = False

    def reset(self) -> None:
        self._iterator.reset_states()
        self._pre_roll.clear()
        self._speech.clear()
        self._speech_samples = 0
        self._active = False

    def accept(self, samples: Any) -> Any | None:
        try:
            import numpy as np

            chunk = np.asarray(samples, dtype=np.float32).reshape(-1)
            event = self._iterator(self._torch.from_numpy(chunk), return_seconds=False)
        except Exception as exc:
            raise VadError(f"Silero VAD 處理音訊失敗：{exc}") from exc

        if self._active:
            self._speech.append(chunk.copy())
            self._speech_samples += len(chunk)
        else:
            self._pre_roll.append(chunk.copy())

        if event and "start" in event and not self._active:
            self._active = True
            self._speech = list(self._pre_roll)
            self._speech_samples = sum(len(part) for part in self._speech)
            self._pre_roll.clear()

        ended = bool(event and "end" in event and self._active)
        too_long = self._active and self._speech_samples >= self.max_samples
        if not ended and not too_long:
            return None

        utterance = np.concatenate(self._speech) if self._speech else None
        self.reset()
        return utterance
