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

# 每根手指用來判斷「伸直/彎曲」的三個點 (第一個關節, 中間關節, 指尖)
# 中指/無名指/小指/食指用 MCP-PIP-TIP 的夾角；拇指用 MCP-IP-TIP (拇指沒有 PIP)
FINGER_JOINTS = {
    "thumb": (THUMB[1], THUMB[2], THUMB[3]),
    "index": (INDEX[0], INDEX[1], INDEX[3]),
    "middle": (MIDDLE[0], MIDDLE[1], MIDDLE[3]),
    "ring": (RING[0], RING[1], RING[3]),
    "pinky": (PINKY[0], PINKY[1], PINKY[3]),
}

# 伸直時關節夾角接近 180 度，彎曲時明顯變小；拇指的活動方式不同，門檻放寬一點
EXTENDED_ANGLE_DEG = {
    "thumb": 140.0,
    "index": 155.0,
    "middle": 155.0,
    "ring": 155.0,
    "pinky": 155.0,
}


def _angle_deg(a, b, c):
    """b 點的夾角 (a-b-c)，單位度。三點共線 (伸直) 時接近 180。"""
    ba, bc = a - b, c - b
    na, nc = np.linalg.norm(ba), np.linalg.norm(bc)
    if na < 1e-6 or nc < 1e-6:
        return 180.0
    cos = np.clip(np.dot(ba, bc) / (na * nc), -1.0, 1.0)
    return math.degrees(math.acos(cos))


def finger_states(pts):
    """pts: (21, 2) 陣列。回傳 {"thumb": bool, "index": bool, ...} 每根手指是否伸直。"""
    pts = np.asarray(pts, dtype=np.float32)
    states = {}
    for name, (i0, i1, i2) in FINGER_JOINTS.items():
        angle = _angle_deg(pts[i0], pts[i1], pts[i2])
        states[name] = angle >= EXTENDED_ANGLE_DEG[name]
    return states


# 每種手勢 = 一組「伸直的手指」，跟角度門檻、方向都無關，純粹查表。
# ok：拇指和食指互相彎過去捏在一起 (兩者都判定為彎曲)，中指/無名指/小指伸直。
GESTURE_TABLE = {
    frozenset(): "fist",
    frozenset({"thumb"}): "thumbs_up",
    frozenset({"index"}): "point",
    frozenset({"index", "middle"}): "two",
    frozenset({"index", "middle", "ring"}): "three",
    frozenset({"index", "middle", "ring", "pinky"}): "four",
    frozenset({"thumb", "pinky"}): "six",
    frozenset({"index", "pinky"}): "rock",
    frozenset({"middle", "ring", "pinky"}): "ok",
    frozenset({"thumb", "index", "middle", "ring", "pinky"}): "open",
}


def classify_landmarks(pts):
    """回傳 GESTURE_TABLE 裡的手勢名稱，或 None (沒有定義成任何手勢的手指組合)。"""
    states = finger_states(pts)
    extended = frozenset(name for name, is_extended in states.items() if is_extended)
    return GESTURE_TABLE.get(extended)


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
