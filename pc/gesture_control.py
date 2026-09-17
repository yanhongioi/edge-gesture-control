# --------------------------------------------------------------------------------------
# PC 端：收到板子確認過的手勢，就控制這台電腦 (Windows)
#
# 目前的對應：
#   point  -> 游標模式：滑鼠游標跟著食指尖 (第 8 點) 移動
#             開始：手勢確認為 point；結束：這一幀不是 point 或手不見 → 立刻停，
#             游標退回幾筆資料之前的位置 (收手時食指在彎，最後幾筆位置不準)
#   CONTINUOUS 表：手勢維持期間重複執行的動作 (目前空的；scroll_down / scroll_up 可以拿來用)
#
# 用法:
#   py -3.11 pc\gesture_control.py                         # broker 在這台電腦
#   py -3.11 pc\gesture_control.py --broker 10.66.97.205   # broker 在別台電腦
#   py -3.11 pc\gesture_control.py --dry-run               # 只印出動作，不真的動游標
#   py -3.11 pc\gesture_control.py --region 0.3            # 手移動較小範圍就能走遍整個螢幕
#
# 板子請加 --mqtt-hz 30 (預設 15 Hz，游標會一頓一頓)：
#   python3 hand_cam.py --mqtt 192.168.7.1 --mqtt-hz 30
# 結束: Ctrl+C
# --------------------------------------------------------------------------------------

import sys
import json
import math
import time
import ctypes
import argparse
import threading
from collections import deque

import paho.mqtt.client as mqtt

TOPIC = "edge/hand"
INDEX_TIP = 8                  # MediaPipe 21 點：8 = 食指尖
CURSOR_GESTURE = "point"
WHEEL_DELTA = 120              # Windows 滾輪一格 = 120
MOUSEEVENTF_WHEEL = 0x0800


# --------------------------------------------------------------------------------------
# Windows 輸入
# --------------------------------------------------------------------------------------
def setup_dpi():
    """讓游標座標用實際像素 (不然 125%/150% 縮放時位置會對不準)"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_size():
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)     # 主螢幕寬、高


def set_cursor(x, y):
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def wheel(delta):
    """滑鼠滾輪：delta > 0 往上、< 0 往下；可以小於 120 (比一格還細)"""
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_int(int(delta)), 0)


# --------------------------------------------------------------------------------------
# 游標：座標對應 + One Euro 濾波
# --------------------------------------------------------------------------------------
def map_to_screen(x, y, sw, sh, region=0.4, center=(0.5, 0.5), mirror=True):
    """鏡頭畫面 (0~1) 中央 region 比例的範圍 → 整個螢幕；超出範圍就貼在螢幕邊緣。
    mirror=True：鏡頭面對使用者，手往右移在畫面上是往左，所以左右翻轉。"""
    if mirror:
        x = 1.0 - x
    u = (x - (center[0] - region / 2)) / region
    v = (y - (center[1] - region / 2)) / region
    u = min(1.0, max(0.0, u))
    v = min(1.0, max(0.0, v))
    return u * (sw - 1), v * (sh - 1)


class OneEuro:
    """One Euro filter (Casiez et al. 2012)：慢速時強力平滑 (不抖)，快速時少平滑 (不延遲)。
    min_cutoff 越小越穩但越黏；beta 越大，快速移動時越跟手。"""

    def __init__(self, min_cutoff=1.0, beta=0.005, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.t is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = max(t - self.t, 1e-3)
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x


class CursorMode:
    """point 期間讓游標跟著食指尖；由 MQTT 執行緒每收到一筆資料呼叫一次 update()"""

    UNDO_SAMPLES = 3            # 結束時退回幾筆之前的位置

    def __init__(self, args, screen):
        self.args, (self.sw, self.sh) = args, screen
        self.fx = OneEuro(args.min_cutoff, args.beta)
        self.fy = OneEuro(args.min_cutoff, args.beta)
        self.history = deque(maxlen=self.UNDO_SAMPLES + 1)
        self.active = False
        self.moves = 0
        self.last_print = 0.0

    def update(self, hand, t):
        a = self.args
        want = (hand is not None and hand.get("gesture") == CURSOR_GESTURE
                and hand.get("gesture_raw") == CURSOR_GESTURE)
        if want and not self.active:                   # 開始
            self.active, self.moves = True, 0
            self.fx.reset(); self.fy.reset(); self.history.clear()
            print(f"  ▶ 游標模式開始（{CURSOR_GESTURE}）")
        elif not want and self.active:                 # 結束：立刻停，退回幾筆之前的位置
            self.stop()
            return
        if not self.active:
            return
        tip = hand["landmarks"][INDEX_TIP]
        x, y = map_to_screen(tip[0], tip[1], self.sw, self.sh, a.region, (a.center_x, a.center_y),
                             not a.no_mirror)
        x, y = self.fx(x, t), self.fy(y, t)
        self.history.append((x, y))
        self.moves += 1
        if a.dry_run:
            if t - self.last_print > 0.5:
                print(f"    游標 → ({x:6.0f}, {y:6.0f})  指尖 ({tip[0]:.3f}, {tip[1]:.3f})")
                self.last_print = t
        else:
            set_cursor(x, y)

    def stop(self, reason=""):
        if not self.active:
            return
        self.active = False
        if len(self.history) > 1:
            x, y = self.history[0]                     # 最舊的那筆 = 幾筆之前
            if not self.args.dry_run:
                set_cursor(x, y)
        print(f"  ■ 游標模式結束{reason}（更新 {self.moves} 次）")


# 持續型動作：手勢維持期間，每 --interval 秒執行一次
#   key = gesture.py 確認過的手勢名稱；value = (說明, 動作)
#   之後的新手勢可以對應到這些，例如 "xxx": ("往下捲動", scroll_down)
def scroll_down(a):
    if not a.dry_run:
        wheel(-a.scroll_step)


def scroll_up(a):
    if not a.dry_run:
        wheel(a.scroll_step)


CONTINUOUS = {}


def main():
    ap = argparse.ArgumentParser(description="gesture -> PC control")
    ap.add_argument("--broker", default="127.0.0.1", help="MQTT broker 的 IP")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--region", type=float, default=0.4,
                    help="鏡頭畫面中央多大比例的範圍對應到整個螢幕 (越小，手移動越少)")
    ap.add_argument("--center-x", type=float, default=0.5, help="對應範圍的中心 x (0~1，鏡頭畫面座標)")
    ap.add_argument("--center-y", type=float, default=0.5, help="對應範圍的中心 y (0~1)")
    ap.add_argument("--no-mirror", action="store_true", help="板子有加 --mirror 時請加這個")
    ap.add_argument("--min-cutoff", type=float, default=1.0, help="濾波：越小越穩、越黏")
    ap.add_argument("--beta", type=float, default=0.005, help="濾波：越大，快速移動時越跟手")
    ap.add_argument("--scroll-step", type=int, default=30, help="捲動量，120 = 滾輪一格")
    ap.add_argument("--interval", type=float, default=0.05, help="持續動作的間隔秒數")
    ap.add_argument("--stale", type=float, default=0.5, help="超過幾秒沒收到資料就停止動作")
    ap.add_argument("--dry-run", action="store_true", help="只印出動作，不真的控制電腦")
    args = ap.parse_args()
    if sys.platform != "win32" and not args.dry_run:
        sys.exit("目前只支援 Windows；其他系統請先加 --dry-run 測試")

    if sys.platform == "win32":
        setup_dpi()
        screen = screen_size()
    else:
        screen = (1920, 1080)
    cursor = CursorMode(args, screen)
    lock = threading.Lock()
    state = {"gesture": None, "stamp": 0.0}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print(f"MQTT connected to {args.broker}:{args.port} ({reason_code}), subscribe {TOPIC}")
        client.subscribe(TOPIC)

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        hands = data.get("hands", [])
        hand = hands[0] if hands else None
        now = time.monotonic()
        with lock:
            cursor.update(hand, now)
            state["gesture"] = hand.get("gesture") if hand else None
            state["stamp"] = now

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port)
    client.loop_start()

    extra = "".join(f", {g} = {desc}" for g, (desc, _) in CONTINUOUS.items())
    print(f"手勢對應: {CURSOR_GESTURE} = 游標跟著食指尖{extra}"
          f"{'  [dry-run，不會真的控制]' if args.dry_run else ''}")
    print(f"螢幕 {screen[0]}x{screen[1]}，鏡頭畫面中央 {args.region:.0%} 對應整個螢幕"
          f"{'，左右翻轉' if not args.no_mirror else ''}。Ctrl+C 結束。")

    active, count, t_start = None, 0, 0.0
    try:
        while True:
            now = time.monotonic()
            with lock:
                stale = now - state["stamp"] > args.stale
                gesture = None if stale else state["gesture"]
                if stale:
                    cursor.stop("（沒有收到資料）")
            action = gesture if gesture in CONTINUOUS else None
            if action != active:                               # 動作切換時印一行
                if active:
                    print(f"  ■ 停止 {CONTINUOUS[active][0]}（{count} 次，{now - t_start:.1f} 秒）")
                if action:
                    print(f"  ▶ {action}: {CONTINUOUS[action][0]}")
                active, count, t_start = action, 0, now
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
