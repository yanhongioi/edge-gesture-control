#!/bin/sh
# 板子開機後，一次設定好網路：USB 直連 (usb_net.sh) + Wi-Fi (/etc/wpa_hotspot.conf，有的話)
# 用法: sh /root/edge-gesture-control/board/bringup.sh
# USB 直連已經在運作時會跳過，所以從 USB 的 SSH 執行也不會斷線。

DIR=$(cd "$(dirname "$0")" && pwd)
CONF=/etc/wpa_hotspot.conf

# --- USB 直連 ---
if ip -4 addr show usb0 2>/dev/null | grep -q "192.168.7.2/"; then
    echo "usb : already up (192.168.7.2)"
else
    sh "$DIR/usb_net.sh" || echo "usb : FAILED (see docs/setup.md 3-D)"
fi

# --- Wi-Fi (只讓板子上網) ---
if [ ! -f "$CONF" ]; then
    echo "wifi: no $CONF, skipped (see docs/setup.md 3-A)"
    exit 0
fi
killall wpa_supplicant udhcpc 2>/dev/null
ip link set mlan0 up
wpa_supplicant -B -i mlan0 -D nl80211 -c "$CONF" >/dev/null 2>&1
sleep 3
if udhcpc -i mlan0 -n -t 5 >/dev/null 2>&1; then
    echo "wifi: $(ip -4 -o addr show mlan0 | awk '{print $4}')"
    if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
        echo "net : internet OK"
    else
        echo "net : no internet"
    fi
else
    echo "wifi: could not connect (is the hotspot on? see docs/setup.md 3-A)"
fi
