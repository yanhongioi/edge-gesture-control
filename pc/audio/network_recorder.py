"""Receive 16 kHz mono S16_LE PCM from the board over TCP."""

from __future__ import annotations

import socket
from typing import Any

from .recorder import AudioInputError, BLOCK_SAMPLES, SAMPLE_RATE


DEFAULT_AUDIO_PORT = 8765


class NetworkAudioStream:
    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_AUDIO_PORT,
        sample_rate: int = SAMPLE_RATE,
        block_samples: int = BLOCK_SAMPLES,
        connect_timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.sample_rate = sample_rate
        self.block_samples = block_samples
        self.connect_timeout = connect_timeout
        self._socket: socket.socket | None = None

    def __enter__(self) -> "NetworkAudioStream":
        try:
            self._socket = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout
            )
            self._socket.settimeout(None)
        except OSError as exc:
            self._socket = None
            raise AudioInputError(
                f"無法連線到板端音訊 {self.host}:{self.port}：{exc}"
            ) from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def _receive_exact(self, size: int) -> bytes:
        if self._socket is None:
            raise AudioInputError("板端音訊串流尚未連線")
        data = bytearray()
        try:
            while len(data) < size:
                chunk = self._socket.recv(size - len(data))
                if not chunk:
                    raise AudioInputError("板端音訊連線已中斷")
                data.extend(chunk)
        except AudioInputError:
            raise
        except OSError as exc:
            raise AudioInputError(f"讀取板端音訊失敗：{exc}") from exc
        return bytes(data)

    def read_chunk(self) -> Any:
        try:
            import numpy as np

            raw = self._receive_exact(self.block_samples * 2)
            return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        except AudioInputError:
            raise
        except Exception as exc:
            raise AudioInputError(f"轉換板端音訊失敗：{exc}") from exc

    def flush(self) -> None:
        """Discard audio accumulated while ASR, planning, tools, or TTS ran."""
        if self._socket is None:
            return
        try:
            self._socket.setblocking(False)
            while self._socket.recv(65_536):
                pass
        except BlockingIOError:
            pass
        except OSError as exc:
            raise AudioInputError(f"清除板端音訊緩衝失敗：{exc}") from exc
        finally:
            self._socket.setblocking(True)
