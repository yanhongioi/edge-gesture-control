# --------------------------------------------------------------------------------------
# PC 端：收到板子確認過的手勢，就控制這台電腦 (Windows)
#
#   point         游標跟著食指 (預設第一節關節 pip = 第 6 點，--anchor 可改) 移動
#   point + 捏合   按住左鍵 (手槍姿勢：拇指立起來 = 瞄準，拇指壓下去碰食指 = 扣扳機)
#                 快速捏一下 = 點擊；捏住不放再移動 = 拖曳；拇指放開 = 放開左鍵
#                 預先鎖定：拇指一開始靠近食指 (pinch_ratio < --prefreeze)，游標就先鎖住，
#                 點擊發生在鎖住的位置 (拇指壓下時食指會被帶著晃，這樣晃了也不影響)
#   two           捲動：拇指捏下 (壓向食指根部) = 往上捲，拇指放開 = 往下捲
#                 速度：基本速度 + 往捲動方向推離起點 (換方向時重算) 越遠越快
#                 短暫掉幀 (--scroll-hold 秒以內) 用原本的速度繼續捲，起點不重算
#                 一比出 two 就會開始捲，沒有「停在原地」的狀態 —— 要停就別比 two
#   open + 捏合    播放/暫停 (張開手掌再捏一下；放開手掌就能再捏一次)
#   thumbs_up     切換視窗 (Alt+Tab)
#                 這兩個是「一次性動作」：比出來只送一次，要先回到中立姿勢才能再觸發
#                 (中立 = 手掌張開沒捏 / 握拳 / 手移出畫面)。--action-map 可以改對應
#   open / fist / 其他  不動作 (open 沒捏是起手式兼中立；fist 是拿刀具時的手，永遠不能觸發)
#
#   捏合判斷在板子上 (board/gesture.py 的 PinchDetector)，這裡用 MQTT 的 hands[].pinch
#   手不見、換成其他手勢、資料中斷、程式結束 → 游標模式結束，按住的左鍵一律放開
#
# 用法:
#   py -3.11 pc\gesture_control.py                         # broker 在這台電腦
#   py -3.11 pc\gesture_control.py --broker 10.66.97.205   # broker 在別台電腦
#   py -3.11 pc\gesture_control.py --dry-run               # 只印出動作，不真的控制
# 板子請加 --mqtt-hz 30 (預設 15 Hz，游標會一頓一頓)：
#   python3 hand_cam.py --mqtt 192.168.7.1 --mqtt-hz 30
# 結束: Ctrl+C
# --------------------------------------------------------------------------------------

import os
import sys
import json
import math
import time
import ctypes
import argparse
import threading
from collections import deque

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pc.control import hotkeys                                      # noqa: E402

TOPIC = "edge/hand"
INDEX_TIP = 8                  # MediaPipe 21 點：8 = 食指尖
ANCHORS = {"tip": 8, "pip": 6, "mcp": 5}    # 游標跟哪個點：食指尖 / 食指第一節關節 / 食指根部
PALM = (0, 5, 9, 13, 17)       # 手腕 + 四指根部，平均起來當作手掌中心
CURSOR_GESTURE = "point"
SCROLL_GESTURE = "two"

# 一次性快捷鍵 (見 GestureActionDispatcher)
# 觸發條件是「手勢 + 捏合狀態」的組合，寫成 token：手勢名稱，捏著的話加上 "+pinch"。
# 這樣 open (張開手掌) 和 open+pinch (張開手掌再捏一下) 才分得開 —— 前者是中立，後者觸發動作。
PINCH_SUFFIX = "+pinch"
# 中立 token：看到其中之一才會重新「上膛」，同一個姿勢維持著不會連發。
# open 沒捏 = 起手式；fist 按設計永遠不觸發任何動作 (拿刀具的手)；手不見 = None。
NEUTRAL_TOKENS = (None, "open", "fist")
DEFAULT_ACTION_MAP = {"open" + PINCH_SUFFIX: "play_pause", "thumbs_up": "alt_tab"}
# board/gesture.py classify_landmarks() 會回傳的全部手勢
KNOWN_GESTURES = ("point", "two", "three", "four", "six", "rock", "ok", "open", "fist",
                  "thumbs_up")
# 這些手勢已經有其他用途，捏不捏合都不可以再綁快捷鍵：
#   point 游標 (+pinch 是點擊 / 拖曳)、two 捲動、fist 安全考量永遠不觸發
RESERVED_GESTURES = (CURSOR_GESTURE, SCROLL_GESTURE, "fist")

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
    """滑鼠滾輪：delta > 0 往上、< 0 往下；120 = 一格，可以更小 (瀏覽器會捲得比較平順)"""
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_int(int(delta)), 0)


WINDOWS_OUTPUT = {"set_cursor": set_cursor, "left_button": left_button, "wheel": wheel}


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
    min_cutoff 越小越穩但越黏；beta 越大，快速移動時越跟手；d_cutoff 越小，速度估計越不受雜訊影響。"""

    def __init__(self, min_cutoff=0.5, beta=0.05, d_cutoff=0.3):
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
    """游標移動 + 捏合點擊 / 拖曳。MQTT 執行緒每收到一筆資料呼叫一次 update()。
    狀態：idle (待機)、move (游標跟著食指)、press (左鍵按住)"""

    BACK = 2                    # 捏下 / 放開時，用幾筆之前的位置 (拇指一動，食指也會跟著抖一下)

    def __init__(self, args, screen, out=None):
        self.args, (self.sw, self.sh) = args, screen
        self.out = out or WINDOWS_OUTPUT
        self.fx = OneEuro(args.min_cutoff, args.beta, args.d_cutoff)
        self.fy = OneEuro(args.min_cutoff, args.beta, args.d_cutoff)
        self.history = deque(maxlen=self.BACK + 1)    # 最近幾筆濾波後的位置
        self.state = "idle"
        self.pos = None             # 目前游標位置 (送出去的)
        self.hold_until = 0.0       # 這個時間之前游標不動 (捏下 / 放開後的短暫停頓)
        self.other_since = None     # 開始出現「不是 point」的時間
        self.armed = False          # 進入游標模式後，要先看到拇指立起來 (沒捏) 一次，捏合才有效
        self.lock_pos = None        # 預先鎖定的位置 (拇指正在靠近食指)
        self.lock_since = 0.0
        self.moves = 0
        self.last_print = 0.0

    # ---- 輸出 --------------------------------------------------------------------------
    def _set(self, x, y, t):
        self.pos = (x, y)
        if self.args.dry_run:
            if t - self.last_print > 0.5:
                print(f"    游標 → ({x:6.0f}, {y:6.0f})")
                self.last_print = t
        else:
            self.out["set_cursor"](x, y)

    def _button(self, down):
        where = f"  @ ({self.pos[0]:.0f}, {self.pos[1]:.0f})" if self.pos else ""
        print(f"  {'▼ 按下左鍵' if down else '▲ 放開左鍵'}{where}")
        if not self.args.dry_run:
            self.out["left_button"](down)

    def _back_pos(self):
        """幾筆之前的位置 (沒有歷史就用目前位置)"""
        return self.history[0] if self.history else self.pos

    def _restart_filter(self, t):
        """從目前位置重新開始濾波，避免停頓後跳動"""
        self.fx.reset(); self.fy.reset(); self.history.clear()
        if self.pos:
            self.fx(self.pos[0], t); self.fy(self.pos[1], t)

    # ---- 狀態 --------------------------------------------------------------------------
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
        g = hand.get("gesture")

        if self.state == "idle":
            if g != CURSOR_GESTURE:
                return
            self.state, self.moves, self.pos, self.other_since = "move", 0, None, None
            self.armed, self.lock_pos = False, None
            self.fx.reset(); self.fy.reset(); self.history.clear()
            print(f"  ● 游標模式開始（{CURSOR_GESTURE}）")

        # 換成其他手勢：先停住，超過 grace 秒才結束 (中間的誤判不會打斷拖曳)
        if g != CURSOR_GESTURE:
            self.other_since = self.other_since or t
            if t - self.other_since > a.grace:
                self.stop(f"（換成 {g}）" if g else "（沒有手勢）")
            return
        self.other_since = None

        # 捏合：按下 / 放開 (位置用鎖定的或幾筆之前的，然後短暫停住)
        pinched = bool(hand.get("pinch")) and not a.no_click
        ratio = hand.get("pinch_ratio")
        if not pinched:
            self.armed = True       # 一開始就捏著 (拇指自然貼著) 不算，避免一進游標模式就點下去

        # 預先鎖定：拇指正在靠近食指 → 游標先鎖住 (拇指壓下時會把食指帶著晃)
        if self.state == "move" and self.armed and not a.no_click and ratio is not None:
            if self.lock_pos is None and not pinched and ratio < a.prefreeze and t >= self.hold_until:
                self.lock_pos, self.lock_since = self._back_pos(), t
                if self.lock_pos:
                    self._set(*self.lock_pos, t)
                if a.dry_run:
                    print(f"  [鎖] 預先鎖定 (pinch_ratio {ratio:.2f})")
            elif self.lock_pos is not None and not pinched and (
                    ratio > a.prefreeze + 0.05 or t - self.lock_since > a.lock_timeout):
                self.lock_pos = None                    # 拇指抬回去 (或鎖太久)：解鎖，從目前位置接續
                self._restart_filter(t)
                if a.dry_run:
                    print(f"  [解鎖] 解鎖 (pinch_ratio {ratio:.2f})")

        if pinched and self.state == "move" and self.armed:
            target = self.lock_pos or self._back_pos()
            if target:
                self._set(*target, t)
            self._button(True)
            self.state, self.hold_until, self.lock_pos = "press", t + a.press_settle, None
            return
        if not pinched and self.state == "press":
            if t >= self.hold_until:                      # 拖曳中放開：也退回幾筆之前
                back = self._back_pos()
                if back:
                    self._set(*back, t)
            self._button(False)                           # 還在停頓中放開 = 原地點擊
            self.state, self.hold_until = "move", t + a.release_settle
            return
        if t < self.hold_until or self.lock_pos is not None:
            return

        # 跟著食指 (預設指尖) 移動 (One Euro 濾波 + 不動區)
        tip = hand["landmarks"][ANCHORS[a.anchor]]
        x, y = map_to_screen(tip[0], tip[1], self.sw, self.sh, a.region, (a.center_x, a.center_y),
                             not a.no_mirror)
        if self.hold_until:                               # 剛從停頓恢復
            self._restart_filter(t)
            self.hold_until = 0.0
        x, y = self.fx(x, t), self.fy(y, t)
        self.history.append((x, y))
        if self.pos is None or math.hypot(x - self.pos[0], y - self.pos[1]) >= a.deadband:
            self._set(x, y, t)
            self.moves += 1


class ScrollJoystick:
    """two 捲動：
      方向 = 拇指：捏合 (拇指壓向食指根部) = 往上捲，沒捏 (拇指放開) = 往下捲
             用拇指而不是手指指向，是因為上下捲要切換時手腕不用整個翻過來，
             而且兩個方向都維持同一個舒服的手勢
      速度 = 基本速度 + 往捲動方向推離起點越遠越快
             起點 = 開始往這個方向捲那一刻的手掌高度 (換方向時重算)
      短暫掉幀 (手不見 / 手勢跳掉) --scroll-hold 秒以內：用原本的速度繼續捲，起點和方向都保留

    注意：一比出 two 就會開始捲 (沒捏 = 往下)，沒有「停在原地」的狀態 —— 要停就別比 two。
    捏合判斷在板子上 (board/gesture.py 的 PinchDetector)，已經過遲滯 + 連續幀，不會逐幀閃。"""

    def __init__(self, args):
        self.args = args
        self.anchor = None          # 起點 (手掌中心 y，0~1)
        self.rate = 0.0             # 滾輪單位 / 秒 (正 = 往上)
        self.dir = 0
        self.lost_since = None

    @staticmethod
    def direction(hand):
        """捏合 = 往上 (+1)，沒捏 = 往下 (-1)"""
        return 1 if hand.get("pinch") else -1

    def update(self, hand, t):
        a = self.args
        if hand is None or hand.get("gesture") != SCROLL_GESTURE:
            if self.anchor is None:
                return
            self.lost_since = self.lost_since or t
            if t - self.lost_since > a.scroll_hold:
                print("  ■ 停止捲動")
                self.stop()
            return                  # 掉幀期間：rate 不變，繼續捲
        self.lost_since = None
        lm = hand["landmarks"]
        y = sum(lm[i][1] for i in PALM) / len(PALM)
        if self.anchor is None:
            self.anchor = y
            print(f"  ● 捲動模式（{SCROLL_GESTURE}）：拇指捏下 = 往上捲，放開 = 往下捲")
        d = self.direction(hand)
        if d != self.dir:
            print({1: "    ↑ 往上捲（捏合）", -1: "    ↓ 往下捲（放開）"}[d])
            self.dir = d
            self.anchor = y         # 換方向：起點重算，從基本速度重新開始加速
        push = (self.anchor - y) if d > 0 else (y - self.anchor)    # 往捲動方向推離起點的量
        speed = min(a.scroll_max, a.scroll_base + a.scroll_gain * max(0.0, push - a.scroll_dead))
        self.rate = -speed * d if a.scroll_invert else speed * d

    def stop(self):
        self.anchor, self.rate, self.dir, self.lost_since = None, 0.0, 0, None


def gesture_token(hand):
    """(手勢, 捏合) → 觸發用的 token。手不見時是 None。
    捏合狀態是板子算的 (board/gesture.py 的 PinchDetector)，已經過遲滯 + 連續幀。"""
    g = hand.get("gesture") if hand else None
    if g is None:
        return None
    return g + PINCH_SUFFIX if hand.get("pinch") else g


class GestureActionDispatcher:
    """一次性快捷鍵 (播放/暫停、切視窗)。和 point/two 的「連續模式」不同：
    只在『token 改變』的那一瞬間送一次，不是每幀送。

    防連發有兩道，缺一不可：
      armed  觸發後就「卸膛」，要先看到中立 token (open 沒捏 / fist / 手不見) 才會重新上膛。
             沒有這個的話，捏著不放 = 音樂瘋狂 play/pause。
      cooldown  擋住 open+pinch → open → open+pinch 這種一瞬間的抖動 (中間的 open 會重新上膛)。

    open / open+pinch 這組搭配得剛好：張開手掌是中立，捏一下觸發，放開就自動重新上膛。

    只有游標和捲動都沒在跑的時候才會被呼叫，所以拖曳 / 捲動中途不會誤觸。"""

    def __init__(self, args, sender=None):
        self.args = args
        self.action_map = args.action_map
        self.sender = sender or hotkeys.HotkeySender(dry_run=args.dry_run)
        self.last = None
        self.armed = True
        self.cooldown_until = 0.0

    def update(self, hand, t):
        token = gesture_token(hand)
        if token == self.last:
            return                       # 同一個姿勢持續中，不重複觸發
        self.last = token
        if token in NEUTRAL_TOKENS:
            self.armed = True
            return
        action = self.action_map.get(token)
        if action is None or not self.armed or t < self.cooldown_until:
            return
        self.armed = False
        self.cooldown_until = t + self.args.action_cooldown
        try:
            label = self.sender.fire(action)
        except hotkeys.HotkeyError as exc:
            print(f"  ※ {token} → {action} 失敗：{exc}")
            return
        print(f"  ★ {token} → {label}" + ("  [dry-run，沒有真的送出]" if self.args.dry_run else ""))

    def stop(self):
        """收尾：確保沒有修飾鍵卡在按住的狀態 (見 hotkeys.py 的說明)"""
        self.sender.release_modifiers()


def parse_action_map(text):
    """"open+pinch=play_pause,thumbs_up=alt_tab" → dict。
    token 是「手勢」或「手勢+pinch」(比出該手勢並且捏合)。
    名稱錯了就直接報錯，不要讓使用者以為綁好了、到現場才發現沒反應。"""
    mapping = {}
    for pair in text.split(","):
        pair = pair.strip()
        if not pair:
            continue
        token, sep, action = (s.strip() for s in pair.partition("="))
        if not sep or not token or not action:
            raise argparse.ArgumentTypeError(f"格式要是 手勢=動作，收到 {pair!r}")
        base = token[:-len(PINCH_SUFFIX)] if token.endswith(PINCH_SUFFIX) else token
        if base not in KNOWN_GESTURES:
            raise argparse.ArgumentTypeError(
                f"沒有 {base!r} 這個手勢（可用：{', '.join(KNOWN_GESTURES)}；"
                f"要加上捏合就寫成 手勢{PINCH_SUFFIX}）")
        if base in RESERVED_GESTURES:
            raise argparse.ArgumentTypeError(
                f"{base!r} 已經有其他用途（游標 / 點擊 / 捲動 / 安全考量），不能綁快捷鍵")
        if token in NEUTRAL_TOKENS:
            raise argparse.ArgumentTypeError(
                f"{token!r} 是中立姿勢（用來讓快捷鍵重新上膛），不能綁快捷鍵；"
                f"改綁 {token}{PINCH_SUFFIX}（比這個手勢並捏合）")
        if action not in hotkeys.ACTIONS:
            raise argparse.ArgumentTypeError(
                f"沒有 {action!r} 這個動作（可用：{', '.join(sorted(hotkeys.ACTIONS))}）")
        mapping[token] = action
    return mapping


def main():
    ap = argparse.ArgumentParser(description="gesture -> PC control")
    ap.add_argument("--broker", default="127.0.0.1", help="MQTT broker 的 IP")
    ap.add_argument("--port", type=int, default=1883)
    g = ap.add_argument_group("游標")
    g.add_argument("--region", type=float, default=0.4,
                   help="鏡頭畫面中央多大比例的範圍對應到整個螢幕 (越小，手移動越少)")
    g.add_argument("--center-x", type=float, default=0.5, help="對應範圍的中心 x (0~1，鏡頭畫面座標)")
    g.add_argument("--center-y", type=float, default=0.5, help="對應範圍的中心 y (0~1)")
    g.add_argument("--no-mirror", action="store_true", help="板子有加 --mirror 時請加這個")
    g.add_argument("--min-cutoff", type=float, default=0.5, help="濾波：越小越穩、越黏 (預設 0.5)")
    g.add_argument("--beta", type=float, default=0.05, help="濾波：越大，快速移動時越跟手 (預設 0.05)")
    g.add_argument("--d-cutoff", type=float, default=0.3, help="濾波：速度估計的平滑度 (預設 0.3)")
    g.add_argument("--deadband", type=float, default=6.0,
                   help="移動小於幾個像素就不動 (手想停住時游標不會閃)")
    g.add_argument("--grace", type=float, default=0.3, help="手勢短暫變成別的，容許幾秒才結束游標模式")
    g = ap.add_argument_group("點擊 (捏合)")
    g.add_argument("--anchor", choices=list(ANCHORS), default="pip",
                   help="游標跟哪個點：pip 食指第一節關節 (預設)、tip 食指尖、mcp 食指根部 (越往根部越穩)")
    g.add_argument("--no-click", action="store_true", help="關閉捏合 = 按住左鍵，只移動游標")
    g.add_argument("--prefreeze", type=float, default=0.40,
                   help="pinch_ratio 低於這個值 (拇指正在靠近食指) 就先鎖住游標；0 = 關閉")
    g.add_argument("--lock-timeout", type=float, default=1.0,
                   help="預先鎖定最多幾秒沒捏下就自動解鎖 (避免拇指放鬆時一直鎖著)")
    g.add_argument("--press-settle", type=float, default=0.2,
                   help="捏下後游標先停住幾秒 (這段時間內放開 = 原地點擊)")
    g.add_argument("--release-settle", type=float, default=0.1, help="放開後游標停住幾秒")
    g = ap.add_argument_group("捲動 (two)")
    g.add_argument("--scroll-base", type=float, default=240,
                   help="基本捲動速度 (每秒滾輪單位，120 = 一格；預設 240 = 每秒 2 格)")
    g.add_argument("--scroll-gain", type=float, default=6000,
                   help="加速：往捲動方向推離起點 (畫面高度比例) × 這個值 = 額外的每秒滾輪單位"
                        " (預設 6000 = 推離 10% 畫面高度多加每秒 5 格)")
    g.add_argument("--scroll-dead", type=float, default=0.05,
                   help="推離起點多少比例以內不加速 (越大越不容易手一抖就加速)")
    g.add_argument("--scroll-max", type=float, default=1800,
                   help="最快每秒幾個滾輪單位 (預設 1800 = 每秒 15 格)")
    g.add_argument("--scroll-hold", type=float, default=0.5,
                   help="手或手勢短暫不見時，繼續用原本速度捲幾秒 (起點不重算)")
    g.add_argument("--scroll-invert", action="store_true", help="上下反過來")
    g = ap.add_argument_group("快捷鍵 (一次性動作)")
    default_map = ",".join(f"{k}={v}" for k, v in DEFAULT_ACTION_MAP.items())
    g.add_argument("--action-map", type=parse_action_map, default=DEFAULT_ACTION_MAP,
                   metavar="手勢=動作,...",
                   help=f"手勢對應的快捷鍵（預設 {default_map}）。可用動作："
                        + "、".join(f"{n} {label}" for n, label in hotkeys.iter_actions()))
    g.add_argument("--action-cooldown", type=float, default=1.0,
                   help="送出一次快捷鍵後，幾秒內不再送 (預設 1.0)")
    g.add_argument("--no-actions", action="store_true", help="關閉所有快捷鍵")
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
    cursor = CursorController(args, screen)
    scroll = ScrollJoystick(args)
    actions = None if args.no_actions else GestureActionDispatcher(args)
    lock = threading.Lock()
    state = {"stamp": 0.0}

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
            if cursor.state == "idle":
                scroll.update(hand, now)
            elif scroll.anchor is not None:               # 換成 point (游標模式)：捲動立刻停
                print("  ■ 停止捲動（換成游標）")
                scroll.stop()
            # 快捷鍵只在游標和捲動都閒置時才看，拖曳 / 捲動中途不會誤觸
            if actions is not None and cursor.state == "idle" and scroll.anchor is None:
                actions.update(hand, now)
            state["stamp"] = now

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port)
    client.loop_start()

    click = "" if args.no_click else "，point + 捏合 = 按住左鍵（點擊 / 拖曳）"
    print(f"手勢對應: {CURSOR_GESTURE} = 游標 (跟著 {args.anchor}){click}，"
          f"{SCROLL_GESTURE} = 捲動 (捏合 = 往上 / 放開 = 往下)"
          f"{'  [dry-run，不會真的控制]' if args.dry_run else ''}")
    if actions is not None:
        pairs = "，".join(f"{t} = {hotkeys.describe(a)}" for t, a in sorted(args.action_map.items()))
        print(f"快捷鍵: {pairs}"
              f"（+pinch = 比該手勢並捏合；觸發後要先回到中立姿勢"
              f"「手掌張開沒捏 / 握拳 / 手收起來」才能再觸發一次）")
    print(f"螢幕 {screen[0]}x{screen[1]}，鏡頭畫面中央 {args.region:.0%} 對應整個螢幕"
          f"{'，左右翻轉' if not args.no_mirror else ''}。Ctrl+C 結束。")

    tick, pending, last = 0.02, 0.0, time.monotonic()
    try:
        while True:
            time.sleep(tick)
            now = time.monotonic()
            dt, last = now - last, now
            with lock:
                if now - state["stamp"] > args.stale:
                    cursor.stop("（沒有收到資料）")
                    if scroll.anchor is not None:
                        print("  ■ 停止捲動（沒有收到資料）")
                    scroll.stop()
                rate = scroll.rate
            pending += rate * dt                          # 累積到整數才送 (滾輪只收整數)
            step = int(pending)
            if step:
                pending -= step
                if not args.dry_run:
                    wheel(step)
            elif rate == 0:
                pending = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        with lock:
            cursor.stop("（程式結束）")                  # 左鍵一定要放開
            if actions is not None:
                actions.stop()                           # 修飾鍵 (Alt) 一定要放開
        client.loop_stop()
        client.disconnect()
        print("結束")


if __name__ == "__main__":
    main()
