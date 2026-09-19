#!/bin/sh
# --------------------------------------------------------------------------------------
# 啟動 NXP 語音辨識：AFE (VoiceSeekerLight 降噪) + voice_ui_app (VIT 喚醒詞 + 指令)
#   喚醒詞：HEY NXP / HEY TV；指令：MUTE、NEXT、SKIP、PAUSE、STOP、PLAY MUSIC ... (共 12 個)
#   偵測結果 -> vit_notify.py -> MQTT edge/voice (筆電 192.168.7.1)
#
# 用法 (在板子上):
#   sh run_voice.sh              # 啟動 (結果印在畫面上，也送 MQTT)；Ctrl+C 結束
#   sh run_voice.sh --no-notify  # 只印在畫面上，不送 MQTT
#   sh run_voice.sh stop         # 停止
#   sh run_voice.sh restore      # 停止，並把 /etc/asound.conf 和 AFE 的 Config.ini 還原
# 環境變數：MQTT_HOST (預設 192.168.7.1；多台用逗號分開)、MIC_GAIN (C270 麥克風增益 0~16，預設 8；16 會爆音)
# --------------------------------------------------------------------------------------

DIR=$(cd "$(dirname "$0")" && pwd)
AFE_DIR=/unit_tests/nxp-afe

stop_all() {
    killall voice_ui_app afe 2>/dev/null
}

case "$1" in
    stop)
        stop_all; echo "voice: stopped"; exit 0 ;;
    restore)
        stop_all
        [ -f /etc/asound.conf.gopoint-original ] && cp /etc/asound.conf.gopoint-original /etc/asound.conf
        [ -f "$AFE_DIR/Config.ini.original" ] && cp "$AFE_DIR/Config.ini.original" "$AFE_DIR/Config.ini"
        echo "voice: stopped and restored /etc/asound.conf, $AFE_DIR/Config.ini"; exit 0 ;;
esac

# 第一次執行時備份原本的設定 (已經有備份就不覆蓋)
[ -f /etc/asound.conf.gopoint-original ] || cp /etc/asound.conf /etc/asound.conf.gopoint-original
[ -f "$AFE_DIR/Config.ini.original" ] || cp "$AFE_DIR/Config.ini" "$AFE_DIR/Config.ini.original"

cp "$DIR/asound.conf" /etc/asound.conf
cp "$DIR/Config.ini" "$AFE_DIR/Config.ini"
cp "$DIR/vit_notify.py" /usr/bin/WakeWordNotify
cp "$DIR/vit_notify.py" /usr/bin/WWCommandNotify
chmod +x /usr/bin/WakeWordNotify /usr/bin/WWCommandNotify

stop_all
sleep 0.3
rm -f /dev/mqueue/voicespot_vslout /dev/mqueue/voiceseeker_iterations \
      /dev/mqueue/voiceseeker_trigger /dev/mqueue/voicespot_offset
amixer -q -c WEBCAM sset Mic "${MIC_GAIN:-8}" || echo "voice: C270 mic not found (arecord -l)"
modprobe snd-aloop || { echo "voice: cannot load snd-aloop"; exit 1; }
export MQTT_HOST="${MQTT_HOST:-192.168.7.1}"

NOTIFY="-notify"
[ "$1" = "--no-notify" ] && NOTIFY=""

trap 'stop_all; echo; echo "voice: stopped"' INT TERM
cd "$AFE_DIR" || exit 1
echo "voice: starting voice_ui_app $NOTIFY (MQTT -> $MQTT_HOST), then afe. Say 'Hey NXP' ... Ctrl+C to stop."
./voice_ui_app $NOTIFY &
sleep 0.5
./afe libvoiceseekerlight
stop_all
