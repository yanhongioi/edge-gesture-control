"""Small non-window-like listening indicator and temporary volume guard."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pc.control.windows_input import get_system_volume, set_system_volume_exact


DEFAULT_LISTENING_VOLUME = 30


class WaveformIndicator:
    """Show an animated transparent waveform icon in the bottom-right corner."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[Any] | None = None
        self._closed = False
        self._lock = threading.RLock()

    def show(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._process is not None and self._process.poll() is None:
                return
            try:
                self._process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("waveform_worker.py")),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                self._process = None

    def hide(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is None or process.poll() is not None:
                return
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.hide()


class ListeningFeedback:
    """Coordinate the waveform icon and reversible listening-volume cap."""

    def __init__(
        self,
        *,
        indicator: WaveformIndicator | None = None,
        volume_limit: int = DEFAULT_LISTENING_VOLUME,
        volume_getter: Callable[[], int] = get_system_volume,
        volume_setter: Callable[[int], None] = set_system_volume_exact,
        manage_volume: bool = True,
        error_handler: Callable[[Exception], None] | None = None,
    ) -> None:
        if not 0 <= volume_limit <= 100:
            raise ValueError("等待指令音量必須介於 0 到 100")
        self.indicator = indicator or WaveformIndicator()
        self.volume_limit = volume_limit
        self.volume_getter = volume_getter
        self.volume_setter = volume_setter
        self.manage_volume = manage_volume
        self.error_handler = error_handler
        self._active = False
        self._restore_volume: int | None = None
        self._lock = threading.RLock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def _report(self, exc: Exception) -> None:
        if self.error_handler is not None:
            self.error_handler(exc)

    def start(self) -> None:
        with self._lock:
            if self._active:
                return
            self._active = True
            self._restore_volume = None
            if self.manage_volume:
                try:
                    current_volume = self.volume_getter()
                    if current_volume > self.volume_limit:
                        self.volume_setter(self.volume_limit)
                        self._restore_volume = current_volume
                except Exception as exc:
                    self._restore_volume = None
                    self._report(exc)
            self.indicator.show()

    def stop(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.indicator.hide()
            restore_volume = self._restore_volume
            self._restore_volume = None
            if restore_volume is not None and self.manage_volume:
                try:
                    self.volume_setter(restore_volume)
                except Exception as exc:
                    self._report(exc)

    def close(self) -> None:
        self.stop()
        self.indicator.close()
