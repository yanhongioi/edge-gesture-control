"""麥克風 → 喚醒詞 → 本機 LLM → 經驗證工具的電腦端入口。"""

from __future__ import annotations

import argparse
import json
import sys

from pc.audio.pipeline import VoiceCommandPipeline
from pc.audio.network_recorder import DEFAULT_AUDIO_PORT, NetworkAudioStream
from pc.audio.recorder import AudioInputError, MicrophoneStream, list_input_devices
from pc.audio.transcriber import TranscriptionError, WhisperTranscriber
from pc.audio.vad import SileroSpeechSegmenter, VadError
from pc.audio.wakeword import DEFAULT_WAKE_PHRASE, WakeWordGate
from pc.control import ControlExecutor
from pc.control.browser import BrowserControlError
from pc.control.executor import ControlExecutionError
from pc.control.youtube import YouTubeError
from pc.llm import AgentPlanner
from pc.speech.windows_tts import WindowsSpeaker, WindowsTtsError


def _device_argument(value: str | None) -> int | str | None:
    if value is None:
        return None
    return int(value) if value.isdecimal() else value


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
    args = parser.parse_args()

    speaker: WindowsSpeaker | None = None
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
        pipeline = VoiceCommandPipeline(
            AgentPlanner(),
            ControlExecutor(),
            wake_gate=WakeWordGate(args.wake_phrase),
            execute=args.execute,
            reply_speaker=speaker.speak_async if speaker is not None else None,
        )

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
        print(f"[{mode_label}] 等待喚醒詞「{args.wake_phrase}」；按 Ctrl+C 結束。")
        print(f"[音訊來源] {source_label}")
        with audio_input as microphone:
            while True:
                utterance = segmenter.accept(microphone.read_chunk())
                if utterance is None:
                    continue

                transcript = transcriber.transcribe(utterance)
                print(
                    f"[辨識 {transcript.latency_seconds:.3f}s] "
                    f"{transcript.text or '(空白)'}"
                )
                if not transcript.reliable:
                    print("[忽略] 語音可信度不足。")
                    microphone.flush()
                    continue

                result = pipeline.handle_text(transcript.text)
                if result.decision.status == "ignored":
                    print("[待命] 未偵測到句首喚醒詞。")
                elif result.decision.status == "armed":
                    print("[已喚醒] 請在 8 秒內說出指令。")
                elif result.decision.status == "cancelled":
                    print("[取消] 已回到待命狀態。")
                elif result.plan is not None:
                    print(f"[指令] {result.decision.command}")
                    print(json.dumps(result.plan.to_dict(), ensure_ascii=False, indent=2))
                    if result.plan.error:
                        print(f"[攔截] {result.plan.error}")
                    elif result.executions:
                        for execution in result.executions:
                            print(f"[完成] {execution.message}")
                    elif args.execute and result.plan.intent != "action":
                        print(f"[回答] {result.plan.reply}")
                    else:
                        print("[預覽] 沒有執行任何動作。")

                if speaker is not None:
                    try:
                        speaker.wait()
                    except WindowsTtsError as exc:
                        print(f"[TTS 警告] {exc}", file=sys.stderr)

                segmenter.reset()
                microphone.flush()
    except KeyboardInterrupt:
        print("\n語音助理已停止。")
        return 0
    except (
        AudioInputError,
        VadError,
        TranscriptionError,
        BrowserControlError,
        ControlExecutionError,
        YouTubeError,
        WindowsTtsError,
    ) as exc:
        print(f"語音助理錯誤：{exc}", file=sys.stderr)
        return 1
    finally:
        if speaker is not None:
            speaker.stop()


if __name__ == "__main__":
    raise SystemExit(main())
