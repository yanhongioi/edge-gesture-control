# --------------------------------------------------------------------------------------
# PC 端：收到板子確認過的手勢，就控制這台電腦 (Windows)
#
# 游標模式 (CursorController)：
#   待機 --point--> 游標移動 --open--> 按住左鍵 (可拖曳) --point--> 放開，回到游標移動
#   * 游標跟著食指尖 (第 8 點)；point、open 時食指都是伸直的，換手勢時位置不太會跳
#   * 待機時單獨比 open 不會有動作 (open 也是「起手式」，不能一舉手就點下去)
#   * 換手勢的過程中游標凍結在換之前的位置，按下 / 放開都在那個位置發生；
#     按下後再停 --press-settle 秒才開始拖，所以快速 point→open→point = 一次乾淨的點擊
#   * 手不見、其他手勢超過 --grace 秒、資料中斷、程式結束 → 結束游標模式，按住的左鍵一律放開
#
# 用法:
#   py -3.11 pc\gesture_control.py                         # broker 在這台電腦
#   py -3.11 pc\gesture_control.py --broker 10.66.97.205   # broker 在別台電腦
#   py -3.11 pc\gesture_control.py --dry-run               # 只印出動作，不真的控制
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
MOVE_GESTURE = "point"         # 游標移動
PRESS_GESTURE = "open"         # 按住左鍵 (只在游標模式中有效)
WHEEL_DELTA = 120              # Windows 滾輪一格 = 120
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
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


def left_button(down):
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


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


class CursorController:
    """游標移動 + 點擊 / 拖曳的狀態機。MQTT 執行緒每收到一筆資料呼叫 update()。
    狀態：idle (待機)、move (游標跟著食指)、press (左鍵按住)"""

    FREEZE_BACK = 3             # 換手勢時，凍結在幾筆之前的位置 (換手勢時手指在動，最近幾筆不準)

    def __init__(self, args, screen, out=None):
        self.args, (self.sw, self.sh) = args, screen
        self.out = out or {"set_cursor": set_cursor, "left_button": left_button}
        self.fx = OneEuro(args.min_cutoff, args.beta)
        self.fy = OneEuro(args.min_cutoff, args.beta)
        self.history = deque(maxlen=self.FREEZE_BACK + 1)
        self.state = "idle"
        self.frozen = None          # 換手勢過程中凍結的位置
        self.odd_since = None       # 開始出現「不是 point / open」的時間
        self.settle_until = 0.0     # 按下後，這個時間之前不移動
        self.pos = None
        self.moves = 0
        self.last_print = 0.0

    # ---- 輸出 --------------------------------------------------------------------------
    def _move(self, x, y, t):
        self.pos = (x, y)
        if self.args.dry_run:
            if t - self.last_print > 0.5:
                print(f"    游標 → ({x:6.0f}, {y:6.0f})")
                self.last_print = t
        else:
            self.out["set_cursor"](x, y)

    def _button(self, down):
        print(f"  {'▼ 按下左鍵' if down else '▲ 放開左鍵'}  @ ({self.pos[0]:.0f}, {self.pos[1]:.0f})"
              if self.pos else f"  {'▼ 按下左鍵' if down else '▲ 放開左鍵'}")
        if not self.args.dry_run:
            self.out["left_button"](down)

    def _freeze(self, t):
        """換手勢開始：游標退回幾筆之前的位置並停住"""
        if self.frozen is None:
            self.frozen = self.history[0] if self.history else self.pos
            if self.frozen:
                self._move(*self.frozen, t)

    # ---- 狀態轉換 ----------------------------------------------------------------------
    def _start(self):
        self.state, self.moves = "move", 0
        self.fx.reset(); self.fy.reset(); self.history.clear()
        self.frozen, self.odd_since = None, None
        print(f"  ▶ 游標模式開始（{MOVE_GESTURE}）")

    def stop(self, reason=""):
        """結束游標模式；按住中一律放開"""
        if self.state == "idle":
            return
        if self.state == "press":
            self._button(False)
        self.state = "idle"
        print(f"  ■ 游標模式結束{reason}（更新 {self.moves} 次）")

    def update(self, hand, t):
        a = self.args
        if hand is None:
            self.stop("（手不見了）")
            return
        g, raw = hand.get("gesture"), hand.get("gesture_raw")

        if self.state == "idle":
            if g == MOVE_GESTURE and raw == MOVE_GESTURE:
                self._start()
            else:
                return                                   # 待機時單獨比 open 不動作

        # 不是 point / open 的狀態太久 → 結束
        if raw not in (MOVE_GESTURE, PRESS_GESTURE) and g not in (MOVE_GESTURE, PRESS_GESTURE):
            self.odd_since = self.odd_since or t
            self._freeze(t)
            if t - self.odd_since > a.grace:
                self.stop("（換成其他手勢）")
            return
        self.odd_since = None

        # 確認過的手勢改變 → 按下 / 放開 (位置用凍結的那個)
        if self.state == "move" and g == PRESS_GESTURE:
            self._freeze(t)
            self._button(True)
            self.state, self.settle_until = "press", t + a.press_settle
        elif self.state == "press" and g == MOVE_GESTURE:
            self._freeze(t)
            self._button(False)
            self.state = "move"

        want = MOVE_GESTURE if self.state == "move" else PRESS_GESTURE
        if raw != want or g != want or t < self.settle_until:
            self._freeze(t)                              # 換手勢中 / 剛按下：游標不動
            return

        # 正常跟著食指尖移動
        if self.frozen is not None:                      # 從凍結恢復：濾波從凍結位置接續，避免跳動
            self.fx.reset(); self.fy.reset()
            self.fx(self.frozen[0], t - 1e-3); self.fy(self.frozen[1], t - 1e-3)
            self.history.clear()
            self.frozen = None
        tip = hand["landmarks"][INDEX_TIP]
        x, y = map_to_screen(tip[0], tip[1], self.sw, self.sh, a.region, (a.center_x, a.center_y),
                             not a.no_mirror)
        x, y = self.fx(x, t), self.fy(y, t)
        self.history.append((x, y))
        self.moves += 1
        self._move(x, y, t)


# 持續型動作：手勢維持期間，每 --interval 秒執行一次 (游標模式以外的手勢)
#   key = gesture.py 確認過的手勢名稱；value = (說明, 動作)
#   例如之後有新手勢時： "xxx": ("往下捲動", scroll_down)
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
    ap.add_argument("--press-settle", type=float, default=0.25,
                    help="按下左鍵後，游標先停住幾秒才開始拖曳 (快速放開 = 點擊)")
    ap.add_argument("--grace", type=float, default=0.3,
                    help="換手勢時，容許幾秒「不是 point / open」才結束游標模式")
    ap.add_argument("--no-click", action="store_true", help="關閉 open = 按住左鍵，只移動游標")
    ap.add_argument("--scroll-step", type=int, default=30, help="捲動量，120 = 滾輪一格")
    ap.add_argument("--interval", type=float, default=0.05, help="持續動作的間隔秒數")
    ap.add_argument("--stale", type=float, default=0.5, help="超過幾秒沒收到資料就停止動作")
    ap.add_argument("--dry-run", action="store_true", help="只印出動作，不真的控制電腦")
    args = ap.parse_args()
    if sys.platform != "win32" and not args.dry_run:
        sys.exit("目前只支援 Windows；其他系統請先加 --dry-run 測試")

    global PRESS_GESTURE
    if args.no_click:
        PRESS_GESTURE = "__disabled__"

    if sys.platform == "win32":
        setup_dpi()
        screen = screen_size()
    else:
        screen = (1920, 1080)
    cursor = CursorController(args, screen)
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

    click = "" if args.no_click else f"，游標模式中 {PRESS_GESTURE} = 按住左鍵（換回 {MOVE_GESTURE} 放開）"
    extra = "".join(f", {g} = {desc}" for g, (desc, _) in CONTINUOUS.items())
    print(f"手勢對應: {MOVE_GESTURE} = 游標跟著食指尖{click}{extra}"
          f"{'  [dry-run，不會真的控制]' if args.dry_run else ''}")
    print(f"螢幕 {screen[0]}x{screen[1]}，鏡頭畫面中央 {args.region:.0%} 對應整個螢幕"
          f"{'，左右翻轉' if not args.no_mirror else ''}。Ctrl+C 結束。")

    active, count, t_start = None, 0, 0.0
    try:
        while True:
            now = time.monotonic()
            with lock:
                stale = now - state["stamp"] > args.stale
                gesture = None if stale or cursor.state != "idle" else state["gesture"]
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
        with lock:
            cursor.stop("（程式結束）")                  # 左鍵一定要放開
        client.loop_stop()
        client.disconnect()
        print("結束")


if __name__ == "__main__":
    main()
