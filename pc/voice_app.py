"""麥克風 → 喚醒詞 → 本機 LLM → 經驗證工具的電腦端入口。"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from pc.audio.board_wake import DEFAULT_WAKE_TOPIC, BoardWakeError, BoardWakeListener
from pc.audio.listening_feedback import DEFAULT_LISTENING_VOLUME, ListeningFeedback
from pc.audio.pipeline import VoiceCommandPipeline
from pc.audio.network_recorder import DEFAULT_AUDIO_PORT, NetworkAudioStream
from pc.audio.recorder import AudioInputError, MicrophoneStream, list_input_devices
from pc.audio.transcriber import TranscriptionError, WhisperTranscriber
from pc.audio.vad import SileroSpeechSegmenter, VadError
from pc.audio.wakeword import DEFAULT_WAKE_PHRASE, WakeWordGate
from pc.control import ControlExecutor
from pc.control.browser import BrowserControlError
from pc.control.executor import ControlExecutionError
from pc.control.media import MediaControlError
from pc.control.youtube import YouTubeError
from pc.control.timer import TimerError
from pc.control.windows_input import WindowsInputError
from pc.llm import AgentPlanner
from pc.speech.windows_tts import WindowsSpeaker, WindowsTtsError


DEFAULT_TTS_ECHO_COOLDOWN_SECONDS = 0.8


def _device_argument(value: str | None) -> int | str | None:
    if value is None:
        return None
    return int(value) if value.isdecimal() else value


def _discard_audio_for(
    audio_input: Any,
    seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """清掉 TTS 播放期間的積存音訊，並持續吃掉短暫的喇叭殘響。"""
    audio_input.flush()
    if seconds <= 0:
        return
    deadline = clock() + seconds
    while clock() < deadline:
        audio_input.read_chunk()
    audio_input.flush()


def _should_transcribe(
    mqtt_wake_enabled: bool,
    wake_gate: WakeWordGate,
    pipeline: VoiceCommandPipeline,
) -> bool:
    """MQTT 模式平常省略 Whisper，但助理追問期間必須接受直接回答。"""
    return (
        not mqtt_wake_enabled
        or wake_gate.is_armed()
        or pipeline.awaiting_followup
    )


def _print_devices() -> int:
    devices = list_input_devices()
    if not devices:
        print("找不到任何輸入裝置。", file=sys.stderr)
        return 1
    for device in devices:
        print(
            f"[{device.index}] {device.name} "
            f"({device.channels} ch, {device.default_sample_rate:.0f} Hz)"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="實際執行通過驗證的工具")
    mode.add_argument("--dry-run", action="store_true", help="只顯示計畫（預設）")
    parser.add_argument("--list-devices", action="store_true", help="列出麥克風後結束")
    audio_source = parser.add_mutually_exclusive_group()
    audio_source.add_argument(
        "--device", help="電腦麥克風索引或名稱；預設使用系統預設值"
    )
    audio_source.add_argument(
        "--board-audio",
        metavar="HOST",
        help="改從指定 IP 的板子接收 16 kHz PCM 音訊",
    )
    parser.add_argument("--board-audio-port", type=int, default=DEFAULT_AUDIO_PORT)
    parser.add_argument("--wake-phrase", default=DEFAULT_WAKE_PHRASE)
    parser.add_argument(
        "--mqtt-wake",
        metavar="BROKER",
        help="改用板子 VIT 的喚醒訊號 (MQTT edge/voice)：沒喚醒的句子不跑 Whisper",
    )
    parser.add_argument("--mqtt-wake-topic", default=DEFAULT_WAKE_TOPIC)
    parser.add_argument("--model", default="turbo", help="faster-whisper 模型名稱")
    parser.add_argument("--asr-device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument(
        "--beam-size",
        type=int,
        choices=range(1, 6),
        default=3,
        metavar="1-5",
        help="解碼候選數；越大通常越準但越慢（預設 3）",
    )
    parser.add_argument("--no-tts", action="store_true", help="執行模式不朗讀 reply")
    parser.add_argument("--tts-voice", help="指定 Windows 語音名稱")
    parser.add_argument(
        "--tts-rate",
        type=int,
        choices=range(-10, 11),
        default=0,
        metavar="-10..10",
    )
    parser.add_argument(
        "--tts-echo-cooldown",
        type=float,
        default=DEFAULT_TTS_ECHO_COOLDOWN_SECONDS,
        metavar="SECONDS",
        help="TTS 後丟棄麥克風殘響的秒數（預設 0.8）",
    )
    parser.add_argument(
        "--listening-volume",
        type=int,
        choices=range(0, 101),
        default=DEFAULT_LISTENING_VOLUME,
        metavar="0-100",
        help="等待語音指令時的系統音量上限（預設 35）",
    )
    args = parser.parse_args()
    if not 0 <= args.tts_echo_cooldown <= 3:
        parser.error("--tts-echo-cooldown 必須介於 0 到 3 秒")
    speaker: WindowsSpeaker | None = None
    listening_feedback: ListeningFeedback | None = None
    tts_output_active = threading.Event()
    pending_board_wake = threading.Event()
    try:
        if args.list_devices:
            return _print_devices()

        print(
            f"正在載入 Whisper {args.model} "
            f"({args.asr_device}/{args.compute_type})..."
        )
        transcriber = WhisperTranscriber(
            args.model,
            device=args.asr_device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
        )
        segmenter = SileroSpeechSegmenter()
        if args.execute and not args.no_tts:
            speaker = WindowsSpeaker(voice=args.tts_voice, rate=args.tts_rate)
        listening_feedback = ListeningFeedback(
            volume_limit=args.listening_volume,
            manage_volume=args.execute,
            error_handler=lambda exc: print(
                f"[等待提示警告] {exc}", file=sys.stderr
            ),
        )

        def speak_reply(text: str) -> None:
            if speaker is None:
                return
            tts_output_active.set()
            try:
                # 工具已先執行；這裡只等待 reply 播完以處理麥克風殘響。
                speaker.speak(text)
            except Exception:
                tts_output_active.clear()
                raise

        pipeline = VoiceCommandPipeline(
            AgentPlanner(),
            ControlExecutor(),
            wake_gate=WakeWordGate(args.wake_phrase),
            execute=args.execute,
            reply_speaker=speak_reply if speaker is not None else None,
        )

        def show_listening_feedback() -> None:
            if listening_feedback is not None:
                listening_feedback.start()

        mode_label = "執行模式" if args.execute else "預覽模式"
        if args.board_audio:
            audio_input = NetworkAudioStream(
                args.board_audio,
                port=args.board_audio_port,
            )
            source_label = f"板端音訊 {args.board_audio}:{args.board_audio_port}"
        else:
            audio_input = MicrophoneStream(device=_device_argument(args.device))
            source_label = "電腦麥克風"
        wake_gate = pipeline.wake_gate
        board_wake: contextlib.AbstractContextManager[object]
        if args.mqtt_wake:

            def on_board_wake(wakeword: str) -> None:
                if tts_output_active.is_set():
                    print(f"[忽略喚醒] TTS／殘響期間收到「{wakeword}」。")
                    return
                wake_gate.arm()
                pending_board_wake.set()
                print(f"[已喚醒] 板子偵測到「{wakeword}」，請在 10 秒內說出指令。")

            board_wake = BoardWakeListener(
                args.mqtt_wake, on_board_wake, topic=args.mqtt_wake_topic
            )
            print(
                f"[{mode_label}] 等待板子 VIT 喚醒 "
                f"(MQTT {args.mqtt_wake} {args.mqtt_wake_topic})；按 Ctrl+C 結束。"
            )
        else:
            board_wake = contextlib.nullcontext()
            print(f"[{mode_label}] 等待喚醒詞「{args.wake_phrase}」；按 Ctrl+C 結束。")
        print(f"[音訊來源] {source_label}")
        with board_wake, audio_input as microphone:
            while True:
                if pending_board_wake.is_set():
                    pending_board_wake.clear()
                    show_listening_feedback()
                    segmenter.reset()
                if (
                    listening_feedback is not None
                    and listening_feedback.active
                    and not wake_gate.is_armed()
                    and not pipeline.awaiting_followup
                ):
                    listening_feedback.stop()
                utterance = segmenter.accept(microphone.read_chunk())
                if utterance is None:
                    continue
                was_listening = (
                    listening_feedback is not None
                    and listening_feedback.active
                )
                if was_listening and listening_feedback is not None:
                    # VAD 已經收到一句完整語音；在 ASR/LLM 開始前立即還原音量。
                    listening_feedback.stop()
                if not _should_transcribe(bool(args.mqtt_wake), wake_gate, pipeline):
                    print("[略過] 板子沒有喚醒，這句不送 Whisper。")
                    continue

                transcript = transcriber.transcribe(utterance)
                print(
                    f"[辨識 {transcript.latency_seconds:.3f}s] "
                    f"{transcript.text or '(空白)'}"
                )
                if not transcript.reliable:
                    print("[忽略] 語音可信度不足。")
                    if (
                        was_listening
                        and listening_feedback is not None
                        and (wake_gate.is_armed() or pipeline.awaiting_followup)
                    ):
                        listening_feedback.start()
                    microphone.flush()
                    continue

                result = pipeline.handle_text(transcript.text)
                if result.decision.status == "ignored":
                    print("[待命] 未偵測到句首喚醒詞。")
                elif result.decision.status == "armed":
                    print("[已喚醒] 請在 10 秒內說出指令。")
                    show_listening_feedback()
                elif result.decision.status == "cancelled":
                    print("[取消] 已回到待命狀態。")
                elif result.plan is not None:
                    print(f"[指令] {result.decision.command}")
                    print(json.dumps(result.plan.to_dict(), ensure_ascii=False, indent=2))
                    if result.plan.error:
                        print(f"[攔截] {result.plan.error}")
                    elif result.plan.intent == "clarify" and pipeline.awaiting_followup:
                        print("[等待補充] 10 秒內可直接回答，不必重說喚醒詞。")
                    elif result.executions:
                        for execution in result.executions:
                            print(f"[完成] {execution.message}")
                    elif args.execute and result.plan.intent != "action":
                        print(f"[回答] {result.plan.reply}")
                    else:
                        print("[預覽] 沒有執行任何動作。")

                if speaker is not None:
                    reply_was_spoken = tts_output_active.is_set()
                    try:
                        speaker.wait()
                    except WindowsTtsError as exc:
                        print(f"[TTS 警告] {exc}", file=sys.stderr)
                    finally:
                        try:
                            if reply_was_spoken:
                                _discard_audio_for(
                                    microphone,
                                    args.tts_echo_cooldown,
                                )
                                if (
                                    result.plan is not None
                                    and result.plan.intent == "clarify"
                                ):
                                    pipeline.renew_followup_window()
                        finally:
                            tts_output_active.clear()

                if (
                    listening_feedback is not None
                    and result.plan is not None
                    and result.plan.intent == "clarify"
                    and pipeline.awaiting_followup
                ):
                    listening_feedback.start()

                segmenter.reset()
                if result.decision.status != "armed":
                    # 只喚醒、還沒有指令時不要丟掉緩衝：轉文字的這段時間使用者可能已經開始講指令
                    microphone.flush()
    except KeyboardInterrupt:
        print("\n語音助理已停止。")
        return 0
    except (
        AudioInputError,
        BoardWakeError,
        VadError,
        TranscriptionError,
        BrowserControlError,
        ControlExecutionError,
        MediaControlError,
        YouTubeError,
        TimerError,
        WindowsInputError,
        WindowsTtsError,
    ) as exc:
        print(f"語音助理錯誤：{exc}", file=sys.stderr)
        return 1
    finally:
        if listening_feedback is not None:
            listening_feedback.close()
        if speaker is not None:
            speaker.stop()


if __name__ == "__main__":
    raise SystemExit(main())
