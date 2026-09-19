"""以 sounddevice 從 Windows 麥克風讀取 16 kHz 單聲道 PCM。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SAMPLE_RATE = 16_000
BLOCK_SAMPLES = 512


class AudioInputError(RuntimeError):
    """音訊套件、裝置或串流無法使用。"""


def _sounddevice() -> Any:
    try:
        import sounddevice
    except ImportError as exc:
        raise AudioInputError(
            "尚未安裝 sounddevice；請執行 py -m pip install -r pc/requirements.txt"
        ) from exc
    return sounddevice


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    channels: int
    default_sample_rate: float


def list_input_devices() -> tuple[AudioDevice, ...]:
    sounddevice = _sounddevice()
    devices: list[AudioDevice] = []
    try:
        for index, raw in enumerate(sounddevice.query_devices()):
            channels = int(raw.get("max_input_channels", 0))
            if channels > 0:
                devices.append(
                    AudioDevice(
                        index=index,
                        name=str(raw.get("name", f"裝置 {index}")),
                        channels=channels,
                        default_sample_rate=float(raw.get("default_samplerate", 0)),
                    )
                )
    except Exception as exc:
        raise AudioInputError(f"無法列出麥克風：{exc}") from exc
    return tuple(devices)


class MicrophoneStream:
    def __init__(
        self,
        *,
        device: int | str | None = None,
        sample_rate: int = SAMPLE_RATE,
        block_samples: int = BLOCK_SAMPLES,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.block_samples = block_samples
        self._stream: Any | None = None

    def __enter__(self) -> "MicrophoneStream":
        sounddevice = _sounddevice()
        try:
            self._stream = sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_samples,
                device=self.device,
                channels=1,
                dtype="int16",
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioInputError(f"無法開啟麥克風：{exc}") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def read_chunk(self) -> Any:
        if self._stream is None:
            raise AudioInputError("麥克風串流尚未開啟")
        try:
            import numpy as np

            raw, overflowed = self._stream.read(self.block_samples)
            if overflowed:
                raise AudioInputError("麥克風資料溢位，請關閉其他錄音程式後重試")
            return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        except AudioInputError:
            raise
        except Exception as exc:
            raise AudioInputError(f"讀取麥克風失敗：{exc}") from exc

    def flush(self) -> None:
        """丟棄 LLM／工具執行期間累積的舊音訊。"""
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.start()
        except Exception as exc:
            raise AudioInputError(f"重設麥克風串流失敗：{exc}") from exc
