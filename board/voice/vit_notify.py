#!/usr/bin/env python3
# --------------------------------------------------------------------------------------
# voice_ui_app -notify 偵測到東西時會執行 (run_voice.sh 會把這支複製成兩個名字放到 /usr/bin)：
#   WakeWordNotify <喚醒詞編號>
#   WWCommandNotify <喚醒詞編號> <指令編號>
# 這裡把結果送到 MQTT topic edge/voice (broker = 環境變數 MQTT_HOST，預設 192.168.7.1)
#
# 編號對應 /unit_tests/nxp-afe/voice_ui_app 內建的 VIT 模型 (啟動時會印出清單)。
# 編號從 1 開始、依清單順序 (2026-09-19 上板實測：HEY NXP = 1、NEXT = 2)；0 = 沒聽懂的指令 (UNKNOWN)。
# --------------------------------------------------------------------------------------
import os
import sys
import json
import time

WAKEWORDS = ["HEY NXP", "HEY TV"]
COMMANDS = ["MUTE", "NEXT", "SKIP", "PAIR DEVICE", "PAUSE", "STOP", "POWER OFF", "POWER ON",
            "PLAY MUSIC", "PLAY GAME", "WATCH CARTOON", "WATCH MOVIE"]
TOPIC = "edge/voice"


def name(table, idx):
    if idx == 0:
        return "UNKNOWN"
    return table[idx - 1] if 1 <= idx <= len(table) else f"#{idx}"


def main():
    prog = os.path.basename(sys.argv[0])
    args = [int(a) for a in sys.argv[1:] if a.lstrip("-").isdigit()]
    if prog == "WWCommandNotify" and len(args) >= 2:
        msg = {"type": "command", "wakeword_id": args[0], "wakeword": name(WAKEWORDS, args[0]),
               "id": args[1], "command": name(COMMANDS, args[1])}
    elif args:
        msg = {"type": "wakeword", "id": args[0], "wakeword": name(WAKEWORDS, args[0])}
    else:
        print(f"[voice] {prog}: unexpected arguments {sys.argv[1:]}")
        return
    msg["ts"] = time.time()
    print(f"[voice] {json.dumps(msg, ensure_ascii=False)}", flush=True)
    try:
        import paho.mqtt.publish as publish
        publish.single(TOPIC, json.dumps(msg), hostname=os.environ.get("MQTT_HOST", "192.168.7.1"))
    except Exception as e:                        # broker 沒開也不要讓語音辨識停下來
        print(f"[voice] MQTT publish failed: {e}", flush=True)


if __name__ == "__main__":
    main()
