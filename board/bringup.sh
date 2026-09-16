#!/bin/sh
# 板子開機後，一次設定好網路：USB 直連 (usb_net.sh) + Wi-Fi (/etc/wpa_hotspot.conf，有的話)
# 用法: sh /root/edge-gesture-control/board/bringup.sh
# 已經連好的部分會跳過，所以從 USB 的 SSH 重複執行也不會斷線。

DIR=$(cd "$(dirname "$0")" && pwd)
CONF=/etc/wpa_hotspot.conf

# ------------------------------------------------------------------ USB 直連
UDC_STATE=/sys/class/udc/$(ls /sys/class/udc | head -n 1)/state
if ip -4 addr show usb0 2>/dev/null | grep -q "192.168.7.2/" && [ "$(cat "$UDC_STATE")" = "configured" ]; then
    echo "usb : already up (192.168.7.2, configured)"
else
    sh "$DIR/usb_net.sh" || echo "usb : FAILED (see docs/setup.md 3-D)"
    # 給筆電幾秒辨識
    i=0
    while [ $i -lt 8 ] && [ "$(cat "$UDC_STATE")" != "configured" ]; do sleep 1; i=$((i + 1)); done
    st=$(cat "$UDC_STATE")
    if [ "$st" = "configured" ]; then
        echo "usb : OK, laptop connected (ssh root@192.168.7.2)"
    else
        echo "usb : laptop NOT connected (state=$st)."
        echo "      Check the A-to-C cable: laptop USB-A <-> board USB1_C. Unplug/replug it, then run:"
        echo "      cat $UDC_STATE    (should become 'configured')"
    fi
fi

# ------------------------------------------------------------------ Wi-Fi (只讓板子上網)
if [ ! -f "$CONF" ]; then
    echo "wifi: no $CONF, skipped (see docs/setup.md 3-A)"
    exit 0
fi

wifi_state() { wpa_cli -i mlan0 status 2>/dev/null | sed -n 's/^wpa_state=//p'; }
wifi_ip()    { ip -4 -o addr show mlan0 2>/dev/null | awk '{print $4}'; }
wifi_ssid()  { wpa_cli -i mlan0 status 2>/dev/null | sed -n 's/^ssid=//p'; }

# 已經連上且有 IP：不要動它 (斷線會讓手機熱點以為沒人用而自動關閉)
if [ "$(wifi_state)" = "COMPLETED" ] && [ -n "$(wifi_ip)" ]; then
    echo "wifi: already up $(wifi_ip) ($(wifi_ssid))"
    exit 0
fi

if ! ip link show mlan0 >/dev/null 2>&1; then
    echo "wifi: mlan0 missing, loading driver..."
    modprobe moal mod_para=nxp/wifi_mod_para.conf
    sleep 3
fi
ip link set mlan0 up || { echo "wifi: cannot bring up mlan0"; exit 1; }

if wpa_cli -i mlan0 status >/dev/null 2>&1; then
    # wpa_supplicant 已經在跑：不要殺掉重開 (會撞到還沒釋放的 ctrl_iface)，請它重讀設定、重連
    wpa_cli -i mlan0 reconfigure >/dev/null
    wpa_cli -i mlan0 reassociate >/dev/null
else
    # 沒在跑：清掉殘留的行程和 socket 再啟動
    if pidof wpa_supplicant >/dev/null; then
        killall wpa_supplicant
        i=0
        while pidof wpa_supplicant >/dev/null && [ $i -lt 5 ]; do sleep 1; i=$((i + 1)); done
        pidof wpa_supplicant >/dev/null && killall -9 wpa_supplicant
    fi
    rm -f /var/run/wpa_supplicant/mlan0
    if ! wpa_supplicant -B -i mlan0 -D nl80211 -c "$CONF" > /tmp/wpa_start.log 2>&1; then
        echo "wifi: wpa_supplicant failed to start:"
        grep -v "Match already configured" /tmp/wpa_start.log
        exit 1
    fi
fi

# 等到真的連上熱點 (最多 20 秒) 才要 IP
state=""
i=0
while [ $i -lt 20 ]; do
    state=$(wifi_state)
    [ "$state" = "COMPLETED" ] && break
    sleep 1
    i=$((i + 1))
done
if [ "$state" != "COMPLETED" ]; then
    echo "wifi: not associated (wpa_state=${state:-unknown}). Hotspot on (2.4 GHz)? Name/password right?"
    echo "      networks seen: $(wpa_cli -i mlan0 scan_results 2>/dev/null | awk 'NR>1{print $5}' | sort -u | tr '\n' ' ')"
    echo "      wpa_supplicant keeps trying in the background; re-run this script once the hotspot is on."
    exit 1
fi

killall udhcpc 2>/dev/null
if udhcpc -i mlan0 -n -t 5 >/dev/null 2>&1; then
    echo "wifi: $(wifi_ip) ($(wifi_ssid))"
    if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
        echo "net : internet OK"
    else
        echo "net : no internet"
    fi
else
    echo "wifi: associated but no IP from DHCP"
fi
