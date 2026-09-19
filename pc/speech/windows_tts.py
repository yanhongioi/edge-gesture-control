"""使用 Windows System.Speech 在預設喇叭朗讀繁體中文。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Any


MAX_SPEECH_LENGTH = 300
DEFAULT_CULTURE = "zh-TW"

_SPEAK_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $preferredName = $env:EDGE_TTS_VOICE
    $preferredCulture = $env:EDGE_TTS_CULTURE
    $voices = @($speaker.GetInstalledVoices() | Where-Object { $_.Enabled })
    $selected = $null
    if ($preferredName) {
        $selected = $voices | Where-Object { $_.VoiceInfo.Name -eq $preferredName } |
            Select-Object -First 1
    }
    if (-not $selected) {
        $selected = $voices | Where-Object {
            $_.VoiceInfo.Culture.Name -eq $preferredCulture
        } | Select-Object -First 1
    }
    if (-not $selected) {
        throw "No enabled TTS voice matches $preferredCulture"
    }
    $speaker.SelectVoice($selected.VoiceInfo.Name)
    $speaker.Rate = [int]$env:EDGE_TTS_RATE
    $speaker.Volume = [int]$env:EDGE_TTS_VOLUME
    $speaker.Speak($env:EDGE_TTS_TEXT)
}
finally {
    $speaker.Dispose()
}
"""


class WindowsTtsError(RuntimeError):
    """Windows TTS 無法啟動、找不到語音或播放失敗。"""


class WindowsSpeaker:
    def __init__(
        self,
        *,
        voice: str | None = None,
        culture: str = DEFAULT_CULTURE,
        rate: int = 0,
        volume: int = 100,
    ) -> None:
        if sys.platform != "win32":
            raise WindowsTtsError("目前只支援 Windows System.Speech")
        if not -10 <= rate <= 10:
            raise WindowsTtsError("TTS rate 必須介於 -10 到 10")
        if not 0 <= volume <= 100:
            raise WindowsTtsError("TTS volume 必須介於 0 到 100")
        self.voice = voice
        self.culture = culture
        self.rate = rate
        self.volume = volume
        self._process: subprocess.Popen[Any] | None = None

    @staticmethod
    def _validate_text(text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise WindowsTtsError("TTS 文字不可為空")
        normalized = text.strip()
        if len(normalized) > MAX_SPEECH_LENGTH:
            raise WindowsTtsError(f"TTS 文字不可超過 {MAX_SPEECH_LENGTH} 字")
        return normalized

    def speak_async(self, text: str) -> None:
        normalized = self._validate_text(text)
        self.stop()
        environment = os.environ.copy()
        environment.update(
            {
                "EDGE_TTS_TEXT": normalized,
                "EDGE_TTS_VOICE": self.voice or "",
                "EDGE_TTS_CULTURE": self.culture,
                "EDGE_TTS_RATE": str(self.rate),
                "EDGE_TTS_VOLUME": str(self.volume),
            }
        )
        try:
            self._process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _SPEAK_SCRIPT,
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self._process = None
            raise WindowsTtsError(f"無法啟動 Windows TTS：{exc}") from exc

    def wait(self, *, timeout: float = 30.0) -> None:
        process = self._process
        if process is None:
            return
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.terminate()
            raise WindowsTtsError("Windows TTS 播放逾時") from exc
        finally:
            self._process = None
        if return_code != 0:
            raise WindowsTtsError("Windows TTS 播放失敗，請確認已安裝繁體中文語音")

    def speak(self, text: str) -> None:
        self.speak_async(text)
        self.wait()

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?", default="語音回覆測試成功。")
    parser.add_argument("--voice")
    parser.add_argument("--rate", type=int, default=0)
    parser.add_argument("--volume", type=int, default=100)
    args = parser.parse_args()
    try:
        WindowsSpeaker(
            voice=args.voice,
            rate=args.rate,
            volume=args.volume,
        ).speak(args.text)
    except WindowsTtsError as exc:
        print(f"TTS 測試失敗：{exc}", file=sys.stderr)
        return 1
    print("TTS 測試完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
