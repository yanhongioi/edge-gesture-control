# --------------------------------------------------------------------------------------
# PC 端：收到板子確認過的手勢，就控制這台電腦 (Windows)
#
# 目前的對應 (CONTINUOUS 表)：
#   point  -> 持續慢慢往下捲 (滑鼠滾輪)，手勢維持期間一直捲，換手勢 / 手不見就停
#
# 用法:
#   py -3.11 pc\gesture_control.py                         # broker 在這台電腦
#   py -3.11 pc\gesture_control.py --broker 10.52.95.205   # broker 在別台電腦
#   py -3.11 pc\gesture_control.py --dry-run               # 只印出動作，不真的捲
#   py -3.11 pc\gesture_control.py --scroll-step 40 --interval 0.03   # 捲快一點
#
# 捲動會作用在「滑鼠游標底下的視窗」，測試前先把游標移到要捲的網頁上。
# 結束: Ctrl+C
# --------------------------------------------------------------------------------------

import sys
import json
import time
import ctypes
import argparse
import threading

import paho.mqtt.client as mqtt

TOPIC = "edge/hand"
WHEEL_DELTA = 120              # Windows 滾輪一格 = 120
MOUSEEVENTF_WHEEL = 0x0800


def wheel(delta, dry_run=False):
    """滑鼠滾輪：delta > 0 往上、< 0 往下；可以小於 120 (比一格還細，瀏覽器會捲得比較平順)"""
    if dry_run:
        return
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_int(int(delta)), 0)


# 持續型動作：手勢維持期間，每 --interval 秒執行一次
#   key = gesture.py 確認過的手勢名稱；value = (說明, 動作)
CONTINUOUS = {
    "point": ("往下捲動", lambda a: wheel(-a.scroll_step, a.dry_run)),
}


class LatestGesture:
    """MQTT 執行緒寫入、主迴圈讀取：最近一次收到的確認手勢和時間"""

    def __init__(self):
        self.lock = threading.Lock()
        self.gesture = None
        self.stamp = 0.0
        self.fps = 0.0

    def set(self, gesture, fps):
        with self.lock:
            self.gesture, self.fps, self.stamp = gesture, fps, time.monotonic()

    def get(self, stale):
        with self.lock:
            if time.monotonic() - self.stamp > stale:     # 太久沒收到資料 (板子停了、斷線) 就當沒有手勢
                return None, self.fps
            return self.gesture, self.fps


def main():
    ap = argparse.ArgumentParser(description="gesture -> PC control")
    ap.add_argument("--broker", default="127.0.0.1", help="MQTT broker 的 IP")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--scroll-step", type=int, default=30,
                    help="每次捲動量，120 = 滾輪一格 (預設 30，約 1/4 格)")
    ap.add_argument("--interval", type=float, default=0.05, help="持續動作的間隔秒數")
    ap.add_argument("--stale", type=float, default=0.5, help="超過幾秒沒收到資料就停止動作")
    ap.add_argument("--dry-run", action="store_true", help="只印出動作，不真的控制電腦")
    args = ap.parse_args()
    if sys.platform != "win32" and not args.dry_run:
        sys.exit("目前只支援 Windows (滾輪用 user32.mouse_event)；其他系統請先加 --dry-run 測試")

    latest = LatestGesture()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print(f"MQTT connected to {args.broker}:{args.port} ({reason_code}), subscribe {TOPIC}")
        client.subscribe(TOPIC)

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        # 第一隻有確認手勢的手；都沒有就是 None
        g = next((h.get("gesture") for h in data.get("hands", []) if h.get("gesture")), None)
        latest.set(g, data.get("fps", 0.0))

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port)
    client.loop_start()

    mapping = ", ".join(f"{g} = {desc}" for g, (desc, _) in CONTINUOUS.items())
    print(f"手勢對應: {mapping}{'  [dry-run，不會真的控制]' if args.dry_run else ''}")
    print("把滑鼠游標移到要控制的視窗上。Ctrl+C 結束。")

    active, count, t_start = None, 0, 0.0
    try:
        while True:
            gesture, fps = latest.get(args.stale)
            action = gesture if gesture in CONTINUOUS else None
            if action != active:                               # 動作切換時印一行
                if active:
                    print(f"  ■ 停止 {CONTINUOUS[active][0]}（{count} 次，{time.monotonic() - t_start:.1f} 秒）")
                if action:
                    print(f"  ▶ {action}: {CONTINUOUS[action][0]}")
                active, count, t_start = action, 0, time.monotonic()
            if active:
                CONTINUOUS[active][1](args)
                count += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        print("結束")


if __name__ == "__main__":
    main()
