#!/bin/sh
# 把板子的 USB1_C 設成 USB 網卡，讓筆電用一條 USB 線直連板子（低延遲，看串流用）
#
# 用法 (在板子上):
#   sh usb_net.sh          # NCM 模式 (Windows 11 內建驅動)
#   sh usb_net.sh rndis    # RNDIS 模式 (附 Microsoft OS 描述，讓 Windows 自動裝網卡驅動)
#   sh usb_net.sh stop     # 關閉
#
# 板子 IP: 192.168.7.2/24，筆電那張 USB 網卡請設 192.168.7.1/24
# 接線: 筆電 USB-A --(A 對 C 線)--> 板子 USB1_C  (用 C 對 C 線時板子可能被協商成 host)

set -e
MODE=${1:-ncm}
G=/sys/kernel/config/usb_gadget/imx93net
BOARD_IP=192.168.7.2/24
HOST_MAC=02:00:00:00:93:01
DEV_MAC=02:00:00:00:93:02

stop_gadget() {
    [ -d "$G" ] || return 0
    echo "" > "$G/UDC" 2>/dev/null || true
    rm -f "$G"/configs/c.1/*.usb0 "$G/os_desc/c.1" 2>/dev/null || true
    rmdir "$G/configs/c.1/strings/0x409" "$G/configs/c.1" 2>/dev/null || true
    for f in "$G"/functions/*; do rmdir "$f" 2>/dev/null || true; done
    rmdir "$G/strings/0x409" "$G" 2>/dev/null || true
}

# 舊的 g_ether 會占用 USB 控制器，先卸載
if lsmod | grep -q '^g_ether'; then rmmod g_ether; fi
stop_gadget
if [ "$MODE" = "stop" ]; then
    echo "USB network stopped"
    exit 0
fi

modprobe libcomposite 2>/dev/null || true
[ -d /sys/kernel/config/usb_gadget ] || mount -t configfs none /sys/kernel/config
UDC=$(ls /sys/class/udc | head -n 1)

mkdir -p "$G/strings/0x409" "$G/configs/c.1/strings/0x409"
echo 0x1d6b > "$G/idVendor"                 # Linux Foundation
echo 0x0200 > "$G/bcdUSB"
echo 0x0100 > "$G/bcdDevice"
echo "imx93frdm0001"      > "$G/strings/0x409/serialnumber"
echo "NXP FRDM-i.MX93"    > "$G/strings/0x409/manufacturer"
echo "i.MX93 USB Network" > "$G/strings/0x409/product"
echo "USB network"        > "$G/configs/c.1/strings/0x409/configuration"
echo 250 > "$G/configs/c.1/MaxPower"

if [ "$MODE" = "rndis" ]; then
    # PID 與 NCM 不同，Windows 才會重新比對驅動
    echo 0x0105 > "$G/idProduct"
    echo 0xEF > "$G/bDeviceClass"
    echo 0x02 > "$G/bDeviceSubClass"
    echo 0x01 > "$G/bDeviceProtocol"
    echo 1       > "$G/os_desc/use"
    echo 0xcd    > "$G/os_desc/b_vendor_code"
    echo MSFT100 > "$G/os_desc/qw_sign"
    F=rndis.usb0
    mkdir "$G/functions/$F"
    echo RNDIS   > "$G/functions/$F/os_desc/interface.rndis/compatible_id"
    echo 5162001 > "$G/functions/$F/os_desc/interface.rndis/sub_compatible_id"
elif [ "$MODE" = "ncm" ]; then
    echo 0x0104 > "$G/idProduct"
    F=ncm.usb0
    mkdir "$G/functions/$F"
else
    echo "unknown mode: $MODE (use ncm / rndis / stop)"
    exit 1
fi

echo "$HOST_MAC" > "$G/functions/$F/host_addr"
echo "$DEV_MAC"  > "$G/functions/$F/dev_addr"
ln -s "$G/functions/$F" "$G/configs/c.1/"
if [ "$MODE" = "rndis" ]; then ln -s "$G/configs/c.1" "$G/os_desc/"; fi
echo "$UDC" > "$G/UDC"

IFACE=$(cat "$G/functions/$F/ifname")
ip addr flush dev "$IFACE"
ip addr add "$BOARD_IP" dev "$IFACE"
ip link set "$IFACE" up
sleep 1
echo "mode=$MODE iface=$IFACE ip=$BOARD_IP udc=$UDC state=$(cat /sys/class/udc/$UDC/state)"
