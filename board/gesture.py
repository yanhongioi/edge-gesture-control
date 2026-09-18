# --------------------------------------------------------------------------------------
# 從 21 點手部骨架判斷手勢："open"（手掌張開）、"point"（食指指）、其他為 None
# 純幾何規則，不需要額外模型；輸入是 hand_cam.py 的骨架模型輸出 (21, 2) 的 (x, y) 像素座標
# --------------------------------------------------------------------------------------

import math

import numpy as np

# MediaPipe 21 點索引 (見 hand_cam.py 的 HAND_CONNECTIONS 註解)
THUMB = (1, 2, 3, 4)       # CMC, MCP, IP, TIP
INDEX = (5, 6, 7, 8)       # MCP, PIP, DIP, TIP
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)

# 每根手指 (拇指除外) 用來判斷「伸直/彎曲」的三個點 (第一個關節, 中間關節, 指尖)：MCP-PIP-TIP 的夾角
FINGER_JOINTS = {
    "index": (INDEX[0], INDEX[1], INDEX[3]),
    "middle": (MIDDLE[0], MIDDLE[1], MIDDLE[3]),
    "ring": (RING[0], RING[1], RING[3]),
    "pinky": (PINKY[0], PINKY[1], PINKY[3]),
}

# 伸直時關節夾角接近 180 度，彎曲時明顯變小
EXTENDED_ANGLE_DEG = {
    "index": 155.0,
    "middle": 155.0,
    "ring": 155.0,
    "pinky": 155.0,
}

WRIST = 0
THUMB_TIP = THUMB[3]
INDEX_PIP = INDEX[1]
MIDDLE_MCP = MIDDLE[0]

# 拇指指尖到食指第一指節 (PIP) 的距離，相對手掌大小 (手腕到中指根部) 的比例。
# 用兩次真實握拳 + 兩張真實張開手掌校準：握拳量到 0.17/0.28，張開量到 0.41/0.90，
# 門檻設在兩群數字的中間 (0.35) 兩邊都有餘裕。這是給 fist/thumbs_up 用的：握拳時拇指
# 是往指節「內收」過去貼著彎起來的手指，跟手腕的距離拉不開。
THUMB_EXTENDED_RATIO = 0.35

# 手掌底部多邊形：手腕 + 四指根部 (MCP)，用在 four/open：比「四」時拇指通常還是打直的，
# 只是收攏貼著手掌 (跟握拳時拇指往指節壓過去的動作不同)，指尖會落在這個範圍裡面；
# 伸直張開時指尖會伸出這個範圍外。
PALM_MCP_POLYGON = (WRIST, INDEX[0], MIDDLE[0], RING[0], PINKY[0])


def _angle_deg(a, b, c):
    """b 點的夾角 (a-b-c)，單位度。三點共線 (伸直) 時接近 180。"""
    ba, bc = a - b, c - b
    na, nc = np.linalg.norm(ba), np.linalg.norm(bc)
    if na < 1e-6 or nc < 1e-6:
        return 180.0
    cos = np.clip(np.dot(ba, bc) / (na * nc), -1.0, 1.0)
    return math.degrees(math.acos(cos))


def _dist(a, b):
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def _point_in_polygon(pt, poly):
    """Ray casting：pt 是否落在 poly (頂點座標 list) 圍成的範圍內。"""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_at_y = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_at_y:
                inside = not inside
    return inside


def _thumb_across_palm(pts):
    """拇指指尖是否貼在手掌範圍內 (打直但收攏，用在 four vs open)。"""
    poly = [pts[i] for i in PALM_MCP_POLYGON]
    return _point_in_polygon(pts[THUMB_TIP], poly)


def finger_states(pts):
    """pts: (21, 2) 陣列。回傳 {"thumb": bool, "index": bool, ...} 每根手指是否伸直。"""
    pts = np.asarray(pts, dtype=np.float32)
    states = {}
    for name, (i0, i1, i2) in FINGER_JOINTS.items():
        angle = _angle_deg(pts[i0], pts[i1], pts[i2])
        states[name] = angle >= EXTENDED_ANGLE_DEG[name]
    # 拇指不用角度：握拳/內收時拇指自己的關節幾乎不會像其他手指一樣彎折，
    # 角度判斷幾乎永遠讀成「伸直」。改用距離：指尖離食指第一指節夠遠才算伸直；
    # 握拳/內收時指尖會貼在食指第一指節附近。
    palm_size = _dist(pts[WRIST], pts[MIDDLE_MCP])
    thumb_reach = _dist(pts[THUMB_TIP], pts[INDEX_PIP])
    states["thumb"] = palm_size > 1e-6 and (thumb_reach / palm_size) >= THUMB_EXTENDED_RATIO
    return states


# 食指/中指/無名指/小指這 4 指的組合就能唯一決定手勢，拇指不用管：
# 比 two/three 這種手勢時，拇指常常自然往外撐開 (不是刻意收攏)，量出來的伸直/彎曲很不穩定，
# 但只要看這 4 指，point/two/three/six/rock/ok 各自的組合本來就不會互相撞名。
FOUR_FINGER_TABLE = {
    frozenset({"index"}): "point",
    frozenset({"index", "middle"}): "two",
    frozenset({"index", "middle", "ring"}): "three",
    frozenset({"pinky"}): "six",
    frozenset({"index", "pinky"}): "rock",
    frozenset({"middle", "ring", "pinky"}): "ok",
}
FOUR_FINGER_ALL = frozenset({"index", "middle", "ring", "pinky"})
FOUR_FINGERS = ("index", "middle", "ring", "pinky")


def classify_landmarks(pts):
    """回傳手勢名稱，或 None (沒有定義成任何手勢的手指組合)。
    拇指只在「4 指全彎 (fist/thumbs_up)」和「4 指全伸直 (four/open)」這兩種情況才用來判斷，
    其他手勢只看 4 指的組合，不管拇指 (原因見上面的註解)。
    fist/thumbs_up 用距離比例 (握拳時拇指往指節壓)；four/open 用手掌多邊形 (比四時拇指打直但收攏，
    是不同的動作，不能用同一套判斷)。"""
    pts = np.asarray(pts, dtype=np.float32)
    states = finger_states(pts)
    four_finger = frozenset(name for name in FOUR_FINGERS if states[name])
    if not four_finger:
        return "thumbs_up" if states["thumb"] else "fist"
    if four_finger == FOUR_FINGER_ALL:
        return "four" if _thumb_across_palm(pts) else "open"
    return FOUR_FINGER_TABLE.get(four_finger)


def debug_features(pts):
    """除錯用：每根手指的判斷細節，加上完整 21 點座標。--debug 時印出來，用真實數字對規則，不要猜。"""
    pts = np.asarray(pts, dtype=np.float32)
    angles = {name: round(_angle_deg(pts[i0], pts[i1], pts[i2]), 1)
              for name, (i0, i1, i2) in FINGER_JOINTS.items()}
    palm_size = _dist(pts[WRIST], pts[MIDDLE_MCP])
    thumb_reach = _dist(pts[THUMB_TIP], pts[INDEX_PIP])
    return {
        "angles": angles,
        "thumb_tip": [round(float(v), 1) for v in pts[THUMB_TIP]],
        "thumb_reach_ratio": round(thumb_reach / palm_size, 3) if palm_size > 1e-6 else None,
        "thumb_across_palm": _thumb_across_palm(pts),
        "states": finger_states(pts),
        "landmarks": [[round(float(x), 1), round(float(y), 1)] for x, y in pts],
    }


# --------------------------------------------------------------------------------------
# 捏合 (pinch)：比 point 時用拇指「扣扳機」= 按下滑鼠
#   手槍姿勢：食指指出去、拇指立起來 = 沒按 (瞄準)；拇指往下壓，碰到食指側邊 = 捏下 (按住)
#   point 的分類本來就不看拇指，所以捏下時手勢仍然是 point，游標可以繼續跟著食指尖走
#   判斷：拇指指尖到「食指根部 (MCP) 到第一指節 (PIP)」這段骨頭的距離 / 手掌大小 (手腕到中指根部)
# --------------------------------------------------------------------------------------
# 門檻是初始值，要用真實鏡頭校準 (MQTT 的 hands[].pinch_ratio 會送出即時比例)：
#   比例 < PINCH_ON_RATIO 連續幾幀 → 捏下；> PINCH_OFF_RATIO 連續幾幀 → 放開；中間是遲滯區，避免在門檻附近閃爍
PINCH_ON_RATIO = 0.25
PINCH_OFF_RATIO = 0.35
PINCH_FRAMES = 2


def _point_segment_dist(p, a, b):
    """點 p 到線段 ab 的最短距離"""
    p, a, b = (np.asarray(v, dtype=np.float32) for v in (p, a, b))
    ab = b - a
    denom = float(np.dot(ab, ab))
    t = 0.0 if denom < 1e-9 else float(np.clip(np.dot(p - a, ab) / denom, 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def pinch_ratio(pts):
    """拇指指尖到食指 MCP-PIP 線段的距離 / 手掌大小；手掌大小算不出來時回傳 None"""
    pts = np.asarray(pts, dtype=np.float32)
    palm_size = _dist(pts[WRIST], pts[MIDDLE_MCP])
    if palm_size < 1e-6:
        return None
    return _point_segment_dist(pts[THUMB_TIP], pts[INDEX[0]], pts[INDEX[1]]) / palm_size


class PinchDetector:
    """捏合狀態 (遲滯 + 連續幀)：比例 < on 連續 frames 幀才算捏下，> off 連續 frames 幀才算放開"""

    def __init__(self, on=PINCH_ON_RATIO, off=PINCH_OFF_RATIO, frames=PINCH_FRAMES):
        self.on, self.off, self.frames = on, off, frames
        self.pinched = False
        self._streak = 0

    def update(self, ratio):
        if ratio is None:
            return self.pinched
        crossing = ratio > self.off if self.pinched else ratio < self.on
        self._streak = self._streak + 1 if crossing else 0
        if self._streak >= self.frames:
            self.pinched = not self.pinched
            self._streak = 0
        return self.pinched


class GestureSmoother:
    """避免手勢逐幀閃爍：同一個手勢要連續看到 confirm_frames 次才算確認過，
    確認後才會換成新手勢；中間出現的雜訊 (None 或別的手勢) 不會馬上蓋掉目前的結果。"""

    def __init__(self, confirm_frames=4):
        self.confirm_frames = confirm_frames
        self.confirmed = None
        self._candidate = None
        self._streak = 0

    def update(self, raw_gesture):
        if raw_gesture == self._candidate:
            self._streak += 1
        else:
            self._candidate = raw_gesture
            self._streak = 1
        if self._streak >= self.confirm_frames:
            self.confirmed = self._candidate
        return self.confirmed
