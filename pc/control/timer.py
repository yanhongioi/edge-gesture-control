"""Launch a non-blocking local Windows timer worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


MAX_TIMER_SECONDS = 24 * 60 * 60
MAX_TIMER_LABEL_LENGTH = 80


class TimerError(RuntimeError):
    """A local timer could not be started safely."""


@dataclass(frozen=True)
class TimerResult:
    seconds: int
    label: str
    process_id: int
    process: Any = field(default=None, repr=False, compare=False)


def start_timer(
    seconds: int,
    label: str = "計時器",
    *,
    process_factory: Callable[..., Any] = subprocess.Popen,
) -> TimerResult:
    if sys.platform != "win32":
        raise TimerError("本機計時器目前只支援 Windows")
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise TimerError("計時秒數必須是整數")
    if not 1 <= seconds <= MAX_TIMER_SECONDS:
        raise TimerError("計時秒數必須介於 1 秒到 24 小時")
    if not isinstance(label, str) or not label.strip():
        raise TimerError("計時器名稱不可為空")
    normalized_label = label.strip()
    if len(normalized_label) > MAX_TIMER_LABEL_LENGTH:
        raise TimerError(f"計時器名稱不可超過 {MAX_TIMER_LABEL_LENGTH} 字")

    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise TimerError("找不到 Windows PowerShell，無法顯示計時器")
    worker = Path(__file__).with_name("timer_window.ps1")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = process_factory(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(worker),
                "-Seconds",
                str(seconds),
                "-Label",
                normalized_label,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise TimerError(f"無法啟動本機計時器：{exc}") from exc
    return TimerResult(
        seconds=seconds,
        label=normalized_label,
        process_id=int(process.pid),
        process=process,
    )


def is_timer_running(timer: TimerResult) -> bool:
    """Return whether the timer worker is still alive."""
    if timer.process is None:
        # Custom launchers may only expose a PID. Treat it as active until a
        # matching custom canceller reports otherwise.
        return True
    try:
        return timer.process.poll() is None
    except (AttributeError, OSError):
        return False


def cancel_timer(timer: TimerResult) -> bool:
    """Stop the exact timer worker returned by start_timer."""
    process = timer.process
    if process is None:
        return False
    try:
        if process.poll() is not None:
            return False
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class TimerController:
    """Track and cancel the most recently started local timer."""

    def __init__(
        self,
        launcher: Callable[..., TimerResult] = start_timer,
        canceller: Callable[[TimerResult], bool] = cancel_timer,
        active_checker: Callable[[TimerResult], bool] = is_timer_running,
    ) -> None:
        self.launcher = launcher
        self.canceller = canceller
        self.active_checker = active_checker
        self._active: TimerResult | None = None

    @property
    def has_timer(self) -> bool:
        if self._active is None:
            return False
        if self.active_checker(self._active):
            return True
        self._active = None
        return False

    def start(self, seconds: int, label: str) -> TimerResult:
        result = self.launcher(seconds, label)
        self._active = result
        return result

    def cancel(self) -> bool:
        if not self.has_timer or self._active is None:
            return False
        timer = self._active
        self._active = None
        return self.canceller(timer)
