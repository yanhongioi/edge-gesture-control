"""使用 faster-whisper 在本機將語音轉成中文文字。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import sysconfig
import time
from typing import Any


class TranscriptionError(RuntimeError):
    """Whisper 模型無法載入或轉錄。"""


_DLL_DIRECTORY_HANDLES: list[Any] = []


def _configure_windows_cuda_dlls() -> None:
    """讓目前 Python 行程找到 venv 內的 NVIDIA CUDA DLL，不修改系統 PATH。"""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    package_root = Path(sysconfig.get_paths()["purelib"])
    candidates = (
        package_root / "nvidia" / "cublas" / "bin",
        package_root / "nvidia" / "cuda_nvrtc" / "bin",
        package_root / "ctranslate2",
    )
    known = {str(getattr(handle, "path", "")) for handle in _DLL_DIRECTORY_HANDLES}
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    for directory in candidates:
        if directory.is_dir() and str(directory) not in known:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
        if directory.is_dir() and str(directory) not in path_entries:
            path_entries.insert(0, str(directory))
    os.environ["PATH"] = os.pathsep.join(path_entries)


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    duration_seconds: float
    latency_seconds: float
    average_log_probability: float | None
    no_speech_probability: float | None

    @property
    def reliable(self) -> bool:
        if self.no_speech_probability is not None and self.no_speech_probability > 0.80:
            return False
        if self.average_log_probability is not None and self.average_log_probability < -1.20:
            return False
        return bool(self.text.strip())


class WhisperTranscriber:
    def __init__(
        self,
        model_size: str = "turbo",
        *,
        device: str = "cuda",
        compute_type: str = "int8_float16",
        beam_size: int = 3,
    ) -> None:
        if not 1 <= beam_size <= 5:
            raise TranscriptionError("beam_size 必須介於 1 到 5")
        try:
            if device == "cuda":
                _configure_windows_cuda_dlls()
            from faster_whisper import WhisperModel

            kwargs: dict[str, Any] = {
                "device": device,
                "compute_type": compute_type,
            }
            download_root = os.getenv("WHISPER_MODEL_DIR")
            if download_root:
                kwargs["download_root"] = download_root
            self._model = WhisperModel(model_size, **kwargs)
        except Exception as exc:
            raise TranscriptionError(
                f"無法載入 Whisper {model_size}（{device}/{compute_type}）：{exc}"
            ) from exc
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size

    def transcribe(self, audio: Any, *, sample_rate: int = 16_000) -> Transcript:
        try:
            import numpy as np

            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            started = time.perf_counter()
            segments, info = self._model.transcribe(
                samples,
                language="zh",
                beam_size=self.beam_size,
                condition_on_previous_text=False,
                # 外層 Silero 負責即時切句；這一層再攔截 turbo 的靜音幻覺。
                vad_filter=True,
                initial_prompt="以下內容使用臺灣繁體中文。",
            )
            collected = list(segments)
            latency = time.perf_counter() - started
        except Exception as exc:
            raise TranscriptionError(f"Whisper 轉錄失敗：{exc}") from exc

        text = "".join(segment.text for segment in collected).strip()
        weighted_logs: list[tuple[float, float]] = []
        no_speech_values: list[float] = []
        for segment in collected:
            duration = max(0.0, float(segment.end) - float(segment.start))
            weighted_logs.append((float(segment.avg_logprob), duration or 1.0))
            no_speech_values.append(float(segment.no_speech_prob))
        total_weight = sum(weight for _, weight in weighted_logs)
        average_log_probability = (
            sum(value * weight for value, weight in weighted_logs) / total_weight
            if total_weight
            else None
        )
        no_speech_probability = max(no_speech_values) if no_speech_values else None
        return Transcript(
            text=text,
            language=str(getattr(info, "language", "zh")),
            duration_seconds=len(samples) / sample_rate,
            latency_seconds=latency,
            average_log_probability=average_log_probability,
            no_speech_probability=no_speech_probability,
        )
