#!/usr/bin/env python3
# --------------------------------------------------------------------------------------
# i.MX93 FRDM : 手部偵測 + 21 點手骨架 + 人物定位 (Logitech C270)
# 改寫自 MobileNetSSD_HandAndSKeletonDetect/app.py (WPI, Weilly Li, Apache-2.0)，修正/新增：
#   * 預設用 i.MX93 的 NPU (Ethos-U, libethosu_delegate.so)，app.py 預設的 vx 是 i.MX8MP 用的
#   * 自動尋找 C270 的 /dev/videoX，不再寫死 /dev/video3
#   * 畫面預設左右翻轉 (像照鏡子)，--no-mirror 關掉；MQTT 會送出 mirror 讓 PC 端知道
#   * 鏡頭畫面轉 RGB 再送進模型 (OpenCV 讀進來是 BGR)
#   * 內建 HTTP MJPEG 串流 -> 筆電瀏覽器開 http://<板子IP>:8080 就能看到畫面+骨架
#   * 可選：板子 HDMI 螢幕顯示 (--display)、MQTT 送出 21 點座標 (--mqtt)
#   * 手部追蹤：找到手之後用上一幀的骨架範圍當裁切框，只跑骨架模型；跟丟才跑手部偵測
#   * 找手時，一幀看全畫面、一幀只看人物上半身附近 (遠距離時手在偵測模型裡會變大)
#   * 人物定位：每 N 幀跑一次人物偵測 (MobileNetSSD_VehicleHumanDetector)，標出人的中心點
#     和偏離畫面中央的量 dx
#   * 雲台追人 (預設啟用)：dx 偏離中央超過死區就用固定轉速轉馬達，把人拉回畫面中央
#     比一次 ok 手勢 = 開 / 關「跟著人轉」(--servo-toggle 可換手勢)
#
# 用法 (在板子上):
#   python3 hand_cam.py                          # NPU + 串流到 :8080
#   python3 hand_cam.py --display                # 另外顯示在板子 HDMI 螢幕 / 投影機 (全螢幕；--windowed 小視窗)
#   python3 hand_cam.py --stream-scale 0.5 --stream-fps 10   # 網路慢時減輕串流
#   python3 hand_cam.py --delegate cpu           # 用 CPU 跑 (跟 NPU 對照)
#   python3 hand_cam.py --mqtt 192.168.7.1       # 21 點座標送到 PC, topic: edge/hand
#   python3 hand_cam.py --person-every 5         # 人物偵測更頻繁 (預設 10；0 = 關閉，但雲台會不能用)
#   python3 hand_cam.py                          # 雲台預設鎖定，比 ok 才開始追人
#   python3 hand_cam.py --servo-dir 1            # 轉錯邊時換方向 (方向看馬達怎麼裝)
#   python3 hand_cam.py --servo-speed 4          # 還是會左右晃的話再轉慢一點
#   python3 hand_cam.py --no-servo               # 完全停用雲台，只跑鏡頭與辨識
#   python3 hand_cam.py --servo-on               # 如需啟動後立刻追人，可明確指定
#   python3 hand_cam.py --image test_images/hand-1.jpg  # 單張圖片測試，結果存到 output/
# --------------------------------------------------------------------------------------

import os
import sys
import glob
import json
import time
import signal
import argparse
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

import gesture
import servo

try:
    import tflite_runtime.interpreter as tflite          # 板子 (NXP BSP)
except ImportError:
    try:
        import ai_edge_litert.interpreter as tflite       # PC 測試用
    except ImportError:
        import tensorflow.lite as tflite                  # PC 測試用

HERE = os.path.dirname(os.path.abspath(__file__))
ETHOSU_DELEGATE = "/usr/lib/libethosu_delegate.so"
WINDOW_NAME = "i.MX93 Hand Skeleton"     # --display 的視窗

# MediaPipe 21 點手部骨架連線
# 0 手腕 | 1-4 拇指 | 5-8 食指 | 9-12 中指 | 13-16 無名指 | 17-20 小指
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (17, 0),
]
FINGER_TIPS = (4, 8, 12, 16, 20)


# --------------------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------------------
def to_vela_path(path):
    return path if path.endswith("_vela.tflite") else path[:-len(".tflite")] + "_vela.tflite"


def make_interpreter(path, delegate):
    if delegate == "npu":
        return tflite.Interpreter(model_path=path,
                                  experimental_delegates=[tflite.load_delegate(ETHOSU_DELEGATE)])
    return tflite.Interpreter(model_path=path, num_threads=2)


def describe(name, interp):
    print(f"[{name}]")
    for d in interp.get_input_details():
        print(f"  in  : {d['name']:<44} shape={[int(x) for x in d['shape']]} dtype={d['dtype'].__name__}")
    for i, d in enumerate(interp.get_output_details()):
        print(f"  out{i}: {d['name']:<44} shape={[int(x) for x in d['shape']]} dtype={d['dtype'].__name__}")


class SsdDetector:
    """MobileNet-SSD 偵測 (輸出為 TFLite_Detection_PostProcess：boxes/classes/scores/num)
    手部偵測和人物偵測共用"""

    def __init__(self, path, delegate, name):
        self.interp = make_interpreter(path, delegate)
        self.interp.allocate_tensors()
        inp = self.interp.get_input_details()[0]
        self.in_index = inp["index"]
        self.in_dtype = inp["dtype"]
        self.h, self.w = int(inp["shape"][1]), int(inp["shape"][2])
        outs = self.interp.get_output_details()
        self.out_boxes, self.out_classes, self.out_scores, self.out_num = (
            outs[0]["index"], outs[1]["index"], outs[2]["index"], outs[3]["index"])
        describe(name + ": " + os.path.basename(path), self.interp)

    def __call__(self, rgb):
        img = cv2.resize(rgb, (self.w, self.h)).astype(self.in_dtype)
        self.interp.set_tensor(self.in_index, img[None])
        t0 = time.perf_counter()
        self.interp.invoke()
        ms = (time.perf_counter() - t0) * 1000
        boxes = self.interp.get_tensor(self.out_boxes)[0]     # [N,4] = ymin,xmin,ymax,xmax (0~1)
        classes = self.interp.get_tensor(self.out_classes)[0]
        scores = self.interp.get_tensor(self.out_scores)[0]
        num = int(self.interp.get_tensor(self.out_num).reshape(-1)[0])
        return boxes[:num], classes[:num], scores[:num], ms


class HandLandmark:
    """手部 21 點骨架 (MediaPipe 架構，輸入 float32 RGB 0~1，輸出 21x3 為輸入影像像素座標)"""

    def __init__(self, path, delegate):
        self.interp = make_interpreter(path, delegate)
        self.interp.allocate_tensors()
        inp = self.interp.get_input_details()[0]
        self.in_index = inp["index"]
        self.h, self.w = int(inp["shape"][1]), int(inp["shape"][2])
        outs = self.interp.get_output_details()
        # 與 app.py 相同：out2 = 21x3 座標、out0 = 有手的信心分數
        self.out_points = outs[2]["index"]
        self.out_presence = outs[0]["index"]
        describe("hand landmark: " + os.path.basename(path), self.interp)

    def __call__(self, rgb_crop):
        img = cv2.resize(rgb_crop, (self.w, self.h)).astype(np.float32) / 255.0
        self.interp.set_tensor(self.in_index, img[None])
        t0 = time.perf_counter()
        self.interp.invoke()
        ms = (time.perf_counter() - t0) * 1000
        pts = self.interp.get_tensor(self.out_points).reshape(21, 3)
        presence = float(self.interp.get_tensor(self.out_presence).reshape(-1)[0])
        return pts, presence, ms


def expand_box(box, fw, fh):
    """把偵測框放大成手骨模型要的範圍 (比例沿用 app.py)，回傳整數像素座標"""
    y0, x0, y1, x1 = box[0] * fh, box[1] * fw, box[2] * fh, box[3] * fw
    w, h = x1 - x0, y1 - y0
    x0, x1 = x0 - w * 0.4, x1 + w * 0.2
    y0, y1 = y0 - h * 0.2, y1 - h * 0.1
    x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
    x1, y1 = min(fw, int(round(x1))), min(fh, int(round(y1)))
    return x0, y0, x1, y1


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class HandTracker:
    """手部追蹤 (MediaPipe 的做法)：
    找到手之後，下一幀的裁切框 = 上一幀 21 點的範圍 (正方形、放大 scale 倍) + 移動速度預測，
    只跑骨架模型；骨架信心不夠 (跟丟) 才回頭跑手部偵測。
    手部偵測在遠一點或手動來動去時常常找不到手，追蹤可以把手「接住」。"""

    MIN_SIDE = 24

    def __init__(self, scale):
        self.scale = scale
        self.tracks = []      # [{"pts": (21,2) 像素, "vel": (2,), "score": 偵測分數}]

    def roi(self, track, fw, fh):
        pts = track["pts"]
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        side = min(max(x1 - x0, y1 - y0) * self.scale, max(fw, fh))
        cx, cy = (x0 + x1) / 2 + track["vel"][0], (y0 + y1) / 2 + track["vel"][1]
        r = (max(0, int(cx - side / 2)), max(0, int(cy - side / 2)),
             min(fw, int(cx + side / 2)), min(fh, int(cy + side / 2)))
        if r[2] - r[0] < self.MIN_SIDE or r[3] - r[1] < self.MIN_SIDE:
            return None
        return r

    @staticmethod
    def follow(track, pts):
        """用這一幀的結果更新 track (速度 = 中心點位移，限制在手的大小以內)；
        沿用同一個 smoother / 捏合偵測器，手勢和捏合的連續幀數才會累積"""
        old_c, new_c = track["pts"].mean(0), pts.mean(0)
        size = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]))
        vel = np.clip(new_c - old_c, -size / 2, size / 2)
        return {"pts": pts, "vel": vel, "score": track["score"], "smoother": track["smoother"],
                "pinch": track["pinch"]}

    @staticmethod
    def new(pts, score, smoother=None, pinch=None):
        return {"pts": pts, "vel": np.zeros(2), "score": score,
                "smoother": smoother or gesture.GestureSmoother(),
                "pinch": pinch or gesture.PinchDetector()}


PERSON_CLASS = 0      # COCO label: 0 = person


class PersonLocator:
    """每 N 幀跑一次人物偵測，記住畫面中最大的人 (通常就是使用者)。
    dx / dy = 人物中心偏離畫面中央的量，-1~+1，正值 = 人在畫面右 / 下方。
    之後的雲台就是要把 dx 拉回 0。注意：畫面翻轉時 dx 的左右跟真實世界相反 (PersonPanner 會補償)。"""

    def __init__(self, detector, every, thresh):
        self.detector, self.every, self.thresh = detector, max(1, every), thresh
        self.frame = 0
        self.box = None           # (x0, y0, x1, y1) 像素
        self.score = 0.0
        self.age = 0              # 距離上次偵測到人過了幾幀
        self.ms = 0.0             # 最近一次推論時間
        self.lost_after = self.every * 3

    def update(self, rgb):
        """需要時跑一次偵測；回傳這一幀有沒有跑"""
        ran = self.frame % self.every == 0
        self.frame += 1
        self.age += 1
        if ran:
            fh, fw = rgb.shape[:2]
            boxes, classes, scores, self.ms = self.detector(rgb)
            best = None
            for b, c, sc in zip(boxes, classes, scores):
                if int(c) != PERSON_CLASS or sc < self.thresh:
                    continue
                x0, y0 = max(0, int(b[1] * fw)), max(0, int(b[0] * fh))
                x1, y1 = min(fw, int(b[3] * fw)), min(fh, int(b[2] * fh))
                area = (x1 - x0) * (y1 - y0)
                if best is None or area > best[0]:
                    best = (area, (x0, y0, x1, y1), float(sc))
            if best:
                _, self.box, self.score = best
                self.age = 0
        if self.box is not None and self.age > self.lost_after:
            self.box = None
        return ran

    def center(self):
        x0, y0, x1, y1 = self.box
        return (x0 + x1) / 2, (y0 + y1) / 2

    def offset(self, fw, fh):
        cx, cy = self.center()
        return (cx - fw / 2) / (fw / 2), (cy - fh / 2) / (fh / 2)

    def search_roi(self, fw, fh):
        """找手用的正方形區域：以人物為中心、涵蓋頭頂上方到腰部、左右留手臂伸展的空間。
        遠距離時人很小，這塊區域交給手部偵測，手在模型輸入裡會比看全畫面大很多。"""
        if self.box is None or self.age >= self.every * 2:
            return None
        x0, y0, x1, y1 = self.box
        pw, ph = x1 - x0, y1 - y0
        side = int(min(max(pw * 2.2, ph * 0.9, 160), fw, fh))
        cx = (x0 + x1) / 2
        top = y0 - 0.15 * side                       # 手可能舉過頭
        sx0 = int(min(max(0, cx - side / 2), fw - side))
        sy0 = int(min(max(0, top), fh - side))
        return sx0, sy0, sx0 + side, sy0 + side

    def draw(self, frame):
        fh, fw = frame.shape[:2]
        mx, my = fw // 2, fh // 2
        cv2.line(frame, (mx - 15, my), (mx + 15, my), (220, 220, 220), 1)     # 畫面中央十字
        cv2.line(frame, (mx, my - 15), (mx, my + 15), (220, 220, 220), 1)
        if self.box is None:
            cv2.putText(frame, "no person", (8, fh - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            return
        x0, y0, x1, y1 = self.box
        cx, cy = (int(v) for v in self.center())
        dx, dy = self.offset(fw, fh)
        color = (255, 140, 0) if self.age < self.every else (160, 160, 160)  # 灰色 = 最近沒更新到
        cv2.rectangle(frame, (x0, y0), (x1, y1), color, 2)
        cv2.circle(frame, (cx, cy), 7, color, -1)
        cv2.line(frame, (mx, cy), (cx, cy), color, 2)                           # 水平偏移
        cv2.putText(frame, f"person {self.score:.2f}  dx {dx:+.2f}", (x0 + 4, y0 + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

    def as_dict(self, fw, fh):
        if self.box is None:
            return None
        x0, y0, x1, y1 = self.box
        cx, cy = self.center()
        dx, dy = self.offset(fw, fh)
        return {"score": round(self.score, 3),
                "box": [round(x0 / fw, 4), round(y0 / fh, 4), round(x1 / fw, 4), round(y1 / fh, 4)],
                "center": [round(cx / fw, 4), round(cy / fh, 4)],
                "dx": round(dx, 4), "dy": round(dy, 4), "age": self.age}


class GestureToggle:
    """用手勢切換開關：手勢「第一次出現」才切一次 (rising edge)，一直比著不會連續切。
    cooldown 是防止辨識閃一下 (ok → 別的 → ok) 就連切兩次；板子上的 GestureSmoother 已經要求
    連續 4 幀一致才確認，這裡再加一層時間保險。"""

    def __init__(self, name, state=True, cooldown=1.5):
        self.name, self.state, self.cooldown = name, state, cooldown
        self.seen = False           # 上一幀是不是這個手勢
        self.last = float("-inf")   # 上次切換的時間；-inf 讓第一次切換不會被 cooldown 擋掉

    def update(self, gesture, t):
        """回傳 (目前狀態, 這一幀有沒有切換)"""
        seen, toggled = gesture == self.name, False
        if seen and not self.seen and t - self.last > self.cooldown:
            self.state, self.last, toggled = not self.state, t, True
        self.seen = seen
        return self.state, toggled


class PersonPanner:
    """雲台追人：讓人的 bounding box 一直留在畫面中央。
    人偏離中央超過 deadband 就往那邊「定速」轉 (不是比例控制，就是固定轉速一直轉)，
    轉到偏移小於 hold 才停。兩個門檻是遲滯：只用一個門檻的話，停下來那一刻偏移剛好在門檻上，
    下一幀又會再轉，鏡頭會一直左右抖。

    為什麼 hold 不能設太小、速度不能設太快：人物偵測每 --person-every 幀才跑一次 (預設 10，
    30 fps 下約 3 Hz)，中間那 10 幀馬達是「盲轉」的 —— 看到的 dx 是 300 多毫秒前的。
    盲轉一次的距離 (速度 x 更新間隔) 必須明顯小於 hold 對應的角度，不然馬達會直接跨過停止範圍，
    下次更新才發現過頭了要回頭修，看起來就是左右來回晃。預設 8 度/秒 x 0.33 秒 = 一次約 2.7 度。
    人不見了 (或 box 太舊) 就地停住，不要亂轉去找。"""

    def __init__(self, panner, deadband=0.15, hold=0.05, dir_sign=-1, mirror=False,
                 toggle="ok", enabled=True, wiggle_seconds=0.2):
        self.panner, self.deadband, self.hold = panner, deadband, max(0.0, hold)
        self.toggle = GestureToggle(toggle, enabled)      # 比 ok 開 / 關「跟著人轉」
        # dx > 0 = 人在畫面右邊 → 鏡頭要往右轉。角度要加還是要減，看馬達怎麼裝 → --servo-dir。
        # 畫面翻轉時 dx 的左右跟真實世界相反，先抵銷掉；抵銷後剩下的就只有馬達安裝方向，
        # 所以開不開鏡像不會改變馬達該往哪轉 (兩件事互相獨立)。
        self.sign = dir_sign * (-1 if mirror else 1)
        self.moving = 0             # 目前正往哪邊轉 (遲滯要用)
        self.wiggle = servo.WiggleMotion(wiggle_seconds)

    def update(self, person_info, max_age, gesture, t):
        """person_info = PersonLocator.as_dict() 的結果 (沒看到人時是 None)；
        gesture = 這一幀確認過的手勢 (拿來當開關)。關掉時馬達就地停住，不是回中。"""
        on, toggled = self.toggle.update(gesture, t)
        if toggled:
            print(f"  {'[O] 雲台追人：開' if on else '[X] 雲台追人：關'}（手勢 {self.toggle.name}）")
            if on:
                # 先往離最近極限較遠的一側開始，避免剛好卡在邊界完全看不出輕晃。
                midpoint = (self.panner.min_angle + self.panner.max_angle) / 2
                initial_direction = 1 if self.panner.target <= midpoint else -1
                self.wiggle.start(t, initial_direction)
                if self.wiggle.segment_seconds > 0:
                    print("  ↔ 鏡頭輕晃確認，完成後開始跟隨人物")
            else:
                self.wiggle.cancel()
        wiggle_direction = self.wiggle.direction(t) if on else None
        if wiggle_direction is not None:
            self.moving = wiggle_direction
            self.panner.set_direction(wiggle_direction)
            return self.moving
        if not on or person_info is None or person_info["age"] > max_age:
            self.moving = 0                                  # 關掉 / 沒人 / 資料太舊：停住
        else:
            dx = person_info["dx"]
            threshold = self.hold if self.moving else self.deadband   # 轉動中用較小的門檻
            self.moving = 0 if abs(dx) < threshold else (1 if dx > 0 else -1)
        self.panner.set_direction(self.moving * self.sign)
        return self.moving

    def close(self):
        """停止轉動；馬達保持出力撐住鏡頭 (要放鬆請自己呼叫 servo 的 off())"""
        self.panner.set_direction(0)
        self.panner.close()

    def status(self):
        if not self.toggle.state:
            return f"pan {self.panner.target:3.0f} OFF"
        arrow = {1: ">>", -1: "<<", 0: "--"}[self.moving]
        limit = " LIMIT" if self.panner.at_limit else ""
        return f"pan {self.panner.target:3.0f} {arrow}{limit}"


# --------------------------------------------------------------------------------------
# 鏡頭
# --------------------------------------------------------------------------------------
def find_camera(keyword="C270"):
    """從 /sys/class/video4linux 找名稱含 keyword 的影像擷取節點 (index 0 才是影像，另一個是 metadata)"""
    fallback = None
    for node in sorted(glob.glob("/sys/class/video4linux/video*"),
                       key=lambda p: int(p.rsplit("video", 1)[1])):
        try:
            name = open(os.path.join(node, "name")).read().strip()
            index = open(os.path.join(node, "index")).read().strip()
        except OSError:
            continue
        if index != "0":
            continue
        dev = "/dev/" + os.path.basename(node)
        if keyword.lower() in name.lower():
            print(f"找到鏡頭: {dev} ({name})")
            return dev
        if fallback is None and ("usb" in name.lower() or "webcam" in name.lower()):
            fallback = dev
    if fallback:
        print(f"沒找到 {keyword}，改用 {fallback}")
    return fallback


def open_camera(dev, width, height, fps):
    gst = (f"v4l2src device={dev} ! video/x-raw,format=YUY2,width={width},height={height},"
           f"framerate={fps}/1 ! videoconvert ! video/x-raw,format=BGR ! "
           f"appsink drop=true max-buffers=1 sync=false")
    cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
    if cap.isOpened():
        print("鏡頭開啟 (GStreamer):", gst)
        return cap
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        print("鏡頭開啟 (V4L2):", dev)
        return cap
    return None


# --------------------------------------------------------------------------------------
# 輸出：HTTP MJPEG 串流 / HDMI 顯示 / MQTT
# --------------------------------------------------------------------------------------
class MjpegStreamer:
    PAGE = (b"<html><head><title>i.MX93 Hand</title></head>"
            b"<body style='margin:0;background:#111;display:flex;justify-content:center'>"
            b"<img src='/stream' style='max-width:100%;max-height:100vh'></body></html>")

    def __init__(self, port, scale=1.0, quality=60, fps=15):
        self.jpeg = None
        self.cond = threading.Condition()
        self.clients = 0
        self.latest = None
        self.new_frame = threading.Event()
        self.scale, self.quality, self.period = scale, quality, 1.0 / fps
        streamer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(MjpegStreamer.PAGE)
                elif self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    streamer.clients += 1
                    try:
                        while True:
                            with streamer.cond:
                                streamer.cond.wait()
                                jpg = streamer.jpeg
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                             b"Content-Length: " + str(len(jpg)).encode() +
                                             b"\r\n\r\n" + jpg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        streamer.clients -= 1
                else:
                    self.send_error(404)

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._encode_loop, daemon=True).start()
        print(f"串流已開啟：在筆電瀏覽器打開 http://<板子IP>:{port} "
              f"(scale {scale}, quality {quality}, max {fps} fps)")

    def push(self, frame):
        # 只交出最新一幀，JPEG 壓縮在另一個執行緒做，不拖慢推論；沒人看就不壓縮
        if self.clients:
            self.latest = frame
            self.new_frame.set()

    def _encode_loop(self):
        while True:
            self.new_frame.wait()
            self.new_frame.clear()
            t0 = time.perf_counter()
            frame = self.latest
            if self.scale != 1.0:
                frame = cv2.resize(frame, None, fx=self.scale, fy=self.scale,
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if ok:
                with self.cond:
                    self.jpeg = buf.tobytes()
                    self.cond.notify_all()
            time.sleep(max(0.0, self.period - (time.perf_counter() - t0)))


def setup_wayland():
    """從 SSH / 序列埠執行時，讓 cv2.imshow 能畫到板子 HDMI 上的 Weston 桌面。
    Weston 可能用別的使用者在跑 (socket 在 /run/user/<uid>/)，所以各處都找一遍。
    找不到就回傳 False (不開 --display)，不然 Qt 會直接讓整個程式 abort。"""
    if os.environ.get("DISPLAY"):
        return True
    if os.environ.get("WAYLAND_DISPLAY"):
        sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), os.environ["WAYLAND_DISPLAY"])
        if os.path.exists(sock):
            return True
    sockets = sorted(s for s in glob.glob("/run/user/*/wayland-*") + glob.glob("/run/wayland-*")
                     if not s.endswith(".lock"))
    if not sockets:
        print("找不到 HDMI 桌面 (Weston 的 wayland socket)，這次不開 --display。"
              "請確認開機前 HDMI 就接好、螢幕上有桌面，再用 systemctl status weston 看 Weston 有沒有在跑")
        return False
    os.environ["XDG_RUNTIME_DIR"] = os.path.dirname(sockets[0])
    os.environ["WAYLAND_DISPLAY"] = os.path.basename(sockets[0])
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
    print(f"使用 HDMI 桌面: {sockets[0]}")
    return True


class MqttPublisher:
    def __init__(self, host, topic, hz):
        import paho.mqtt.client as mqtt
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            self.client = mqtt.Client()
        self.client.connect_async(host, 1883)
        self.client.loop_start()
        self.topic, self.period, self.last = topic, 1.0 / hz, 0.0
        print(f"MQTT -> {host}:1883 topic={topic} ({hz} Hz)")

    def publish(self, payload):
        now = time.time()
        if now - self.last >= self.period:
            self.last = now
            self.client.publish(self.topic, json.dumps(payload))


def draw_hand(frame, px, py):
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, (px[a], py[a]), (px[b], py[b]), (0, 255, 0), 2)
    for i in range(21):
        color = (0, 0, 255) if i in FINGER_TIPS else (255, 0, 0)
        cv2.circle(frame, (px[i], py[i]), 4, color, -1)


# process() 每幀留下的資訊 (給主迴圈統計、決定這一幀偵測要看哪裡)
STATE = types.SimpleNamespace(det_count=0, last_presences=[], last_det_ran=False, last_gesture_debug=0.0)


def debug_print_gesture(xy, raw, confirmed):
    """--debug 時每 0.5 秒印一次手指判斷細節，方便對規則用的實際數字，不要猜"""
    now = time.perf_counter()
    if now - STATE.last_gesture_debug < 0.5:
        return
    STATE.last_gesture_debug = now
    info = gesture.debug_features(xy)
    print(f"[gesture] raw={raw} confirmed={confirmed} states={info['states']}")
    print(f"          angles={info['angles']}")
    print(f"          thumb_tip={info['thumb_tip']}  thumb_reach_ratio={info['thumb_reach_ratio']}"
          f"  thumb_across_palm={info['thumb_across_palm']}")
    print(f"          landmarks={info['landmarks']}")


def process(frame, detector, landmark, person, tracker, args):
    """在 frame 上畫出結果，回傳 (hands, person_info, (det_ms, lmk_ms, person_ms))；
    座標皆為 0~1 正規化；沒跑的模型時間為 0"""
    fh, fw = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    person_ms = 0.0
    if person is not None:
        if person.update(rgb):
            person_ms = person.ms
        person.draw(frame)

    hands, rois, new_tracks, presences = [], [], [], []
    lmk_ms = det_ms = 0.0

    def run_landmark(roi):
        nonlocal lmk_ms
        x0, y0, x1, y1 = roi
        pts, presence, ms = landmark(rgb[y0:y1, x0:x1])
        lmk_ms += ms
        presences.append(presence)
        sx, sy = (x1 - x0) / landmark.w, (y1 - y0) / landmark.h
        xy = np.stack([x0 + pts[:, 0] * sx, y0 + pts[:, 1] * sy], 1)
        return xy, pts[:, 2], presence

    def accept(roi, xy, z, presence, score, source, color, gesture_raw, gesture_confirmed,
               pinched, pinch_r):
        x0, y0, x1, y1 = roi
        cv2.rectangle(frame, (x0, y0), (x1, y1), color, 2)
        tag = f" {gesture_confirmed}" if gesture_confirmed else (f" ({gesture_raw})" if gesture_raw else "")
        if pinched:
            tag += " PINCH"
        label = (f"track  lmk {presence:.2f}{tag}" if source == "track"
                  else f"hand {score:.2f}  lmk {presence:.2f}{tag}")
        cv2.putText(frame, label, (x0, max(14, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        px, py = xy[:, 0].astype(int).tolist(), xy[:, 1].astype(int).tolist()
        draw_hand(frame, px, py)
        rois.append(roi)
        hands.append({
            "source": source,
            "score": round(float(score), 3),
            "presence": round(presence, 3),
            "gesture": gesture_confirmed,      # 連續數幀一致才會有值 (見 GestureSmoother)
            "gesture_raw": gesture_raw,        # 這一幀單獨判斷的結果，未經時間平滑
            "pinch": pinched,                  # 拇指扣扳機 (見 gesture.PinchDetector)，連續幀 + 遲滯
            "pinch_ratio": None if pinch_r is None else round(pinch_r, 3),   # 校準門檻用
            "box": [round(x0 / fw, 4), round(y0 / fh, 4), round(x1 / fw, 4), round(y1 / fh, 4)],
            "landmarks": [[round(px[i] / fw, 4), round(py[i] / fh, 4), round(float(z[i]), 2)]
                          for i in range(21)],
        })

    # 1) 追蹤：沿用上一幀的手，只跑骨架模型
    if tracker is not None:
        for track in tracker.tracks:
            if len(hands) >= args.max_hands:
                break
            roi = tracker.roi(track, fw, fh)
            if roi is None or any(iou(roi, r) > 0.3 for r in rois):
                continue
            xy, z, presence = run_landmark(roi)
            # 已經在追的手用較低的門檻 (--track-thresh)，分數偶爾下滑一兩幀不會斷掉；
            # 新偵測到的手仍用 --lmk-thresh
            if presence >= args.track_thresh:
                raw = gesture.classify_landmarks(xy)
                confirmed = track["smoother"].update(raw)
                if args.debug:
                    debug_print_gesture(xy, raw, confirmed)
                pinch_r = gesture.pinch_ratio(xy)
                pinched = track["pinch"].update(pinch_r)
                accept(roi, xy, z, presence, track["score"], "track", (255, 255, 0), raw, confirmed,
                       pinched, pinch_r)
                new_tracks.append(tracker.follow(track, xy))

    # 2) 偵測：手還不夠 (還沒找到或跟丟了) 才跑手部偵測。
    #    偵測模型常給出「不是手」的高分框，所以依分數由高到低最多試 --hand-candidates 個
    det_ran = len(hands) < args.max_hands
    if det_ran:
        # 有人物時，一幀看人物附近、一幀看全畫面，輪流
        search = None
        if person is not None and not args.no_person_search and STATE.det_count % 2 == 0:
            search = person.search_roi(fw, fh)
        STATE.det_count += 1
        if search is not None:
            sx0, sy0, sx1, sy1 = search
            boxes, _, scores, det_ms = detector(rgb[sy0:sy1, sx0:sx1])
            sw, sh = sx1 - sx0, sy1 - sy0
            boxes = np.stack([(sy0 + boxes[:, 0] * sh) / fh, (sx0 + boxes[:, 1] * sw) / fw,
                              (sy0 + boxes[:, 2] * sh) / fh, (sx0 + boxes[:, 3] * sw) / fw], 1) \
                if len(boxes) else boxes
            if args.debug:
                cv2.rectangle(frame, (sx0, sy0), (sx1, sy1), (255, 0, 200), 1)
        else:
            boxes, _, scores, det_ms = detector(rgb)
        tried = 0
        for box, score in sorted(zip(boxes, scores), key=lambda t: -t[1]):
            if score < args.det_thresh or len(hands) >= args.max_hands or tried >= args.hand_candidates:
                break
            roi = expand_box(box, fw, fh)
            if roi[2] - roi[0] < 8 or roi[3] - roi[1] < 8 or any(iou(roi, r) > 0.3 for r in rois):
                continue
            tried += 1
            xy, z, presence = run_landmark(roi)
            if presence >= args.lmk_thresh:
                # 沒開追蹤 (--no-track) 時每幀都是新的 smoother，手勢永遠不會累積到確認門檻
                smoother = gesture.GestureSmoother()
                raw = gesture.classify_landmarks(xy)
                confirmed = smoother.update(raw)
                if args.debug:
                    debug_print_gesture(xy, raw, confirmed)
                pinch_det = gesture.PinchDetector()
                pinch_r = gesture.pinch_ratio(xy)
                pinched = pinch_det.update(pinch_r)
                accept(roi, xy, z, presence, score, "detect", (0, 200, 255), raw, confirmed,
                       pinched, pinch_r)
                if tracker is not None:
                    new_tracks.append(tracker.new(xy, float(score), smoother, pinch_det))
            elif args.debug:   # 被骨架模型否決的候選框：細紅框
                cv2.rectangle(frame, roi[:2], roi[2:], (0, 0, 255), 1)
                cv2.putText(frame, f"{score:.2f}/{presence:.2f}", (roi[0] + 2, roi[3] - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)

    if tracker is not None:
        tracker.tracks = new_tracks
    person_info = person.as_dict(fw, fh) if person is not None else None
    STATE.last_presences = presences        # 給主迴圈做統計
    STATE.last_det_ran = det_ran
    return hands, person_info, (det_ms, lmk_ms, person_ms)



# --------------------------------------------------------------------------------------
# 主程式
# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="i.MX93 hand skeleton demo")
    ap.add_argument("--delegate", choices=["npu", "cpu"], default="npu")
    ap.add_argument("--model", default=os.path.join(HERE, "models/hand_detect_20000_quant.tflite"))
    ap.add_argument("--model-landmark",
                    default=os.path.join(HERE, "models/hand_landmark_new_256x256_integer_quant.tflite"))
    ap.add_argument("--model-person", default=os.path.join(HERE, "models/detect_ssdmobilenetv3_quant.tflite"))
    ap.add_argument("--person-every", type=int, default=10,
                    help="人物偵測每幾幀跑一次，0 = 關閉。調小會更即時，但 NPU 要分更多時間給它，手勢那邊的 FPS 會掉")
    ap.add_argument("--person-thresh", type=float, default=0.5, help="人物偵測分數門檻")
    ap.add_argument("--device", default="", help="例如 /dev/video2；不給就自動找 C270")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--no-mirror", action="store_true",
                    help="不要左右翻轉畫面 (預設是翻轉的，看起來像照鏡子；手勢判斷不受影響)")
    ap.add_argument("--det-thresh", type=float, default=0.55,
                    help="hand detection score threshold (the model outputs junk boxes at exactly 0.50 when there is no hand)")
    ap.add_argument("--lmk-thresh", type=float, default=0.7, help="手骨信心分數門檻")
    ap.add_argument("--max-hands", type=int, default=1, help="每幀最多處理幾隻手 (每多一隻多一次推論)")
    ap.add_argument("--no-track", action="store_true", help="關閉手部追蹤 (每幀都跑手部偵測)")
    ap.add_argument("--no-person-search", action="store_true",
                    help="找手時不要看人物附近的區域 (只看全畫面)")
    ap.add_argument("--track-scale", type=float, default=1.8, help="追蹤時裁切框 = 骨架範圍 x 這個倍數")
    ap.add_argument("--track-thresh", type=float, default=0.55,
                    help="追蹤中的手，骨架信心低於這個值才算跟丟 (新偵測到的手用 --lmk-thresh)")
    ap.add_argument("--hand-candidates", type=int, default=2,
                    help="每幀最多拿幾個手部偵測框給骨架模型確認 (前面的框被否決才會試下一個)")
    ap.add_argument("--port", type=int, default=8080, help="HTTP 串流埠，0 = 不開")
    ap.add_argument("--stream-scale", type=float, default=1.0, help="串流畫面縮放，例如 0.5 = 320x240")
    ap.add_argument("--stream-quality", type=int, default=60, help="串流 JPEG 畫質 1~100")
    ap.add_argument("--stream-fps", type=float, default=15, help="串流最高幀率 (不影響推論)")
    ap.add_argument("--display", action="store_true", help="在板子 HDMI 螢幕 / 投影機上顯示 (預設全螢幕)")
    ap.add_argument("--windowed", action="store_true", help="搭配 --display：用原始大小的小視窗，不要全螢幕")
    ap.add_argument("--mqtt", default="", help="MQTT broker IP，例如 192.168.10.1")
    ap.add_argument("--mqtt-topic", default="edge/hand")
    ap.add_argument("--mqtt-hz", type=float, default=15)
    g = ap.add_argument_group("雲台 (servo，把人維持在畫面中央)")
    servo_mode = g.add_mutually_exclusive_group()
    servo_mode.add_argument("--servo", dest="servo", action="store_true",
                            help="開啟雲台功能（已是預設；保留此參數以相容舊指令）")
    servo_mode.add_argument("--no-servo", dest="servo", action="store_false",
                            help="完全停用雲台，只執行鏡頭與辨識")
    g.add_argument("--servo-speed", type=float, default=8.0,
                   help="轉速 (度/秒)。兩次人物偵測之間馬達是盲轉的，轉太快會直接衝過停止門檻，"
                        "然後要回頭修正，看起來就是左右晃")
    g.add_argument("--servo-deadband", type=float, default=0.22,
                   help="人偏離畫面中央超過這個量 (0~1) 才開始轉")
    g.add_argument("--servo-hold", type=float, default=0.12,
                   help="轉到偏移小於這個量才停 (要比 --servo-deadband 小，這是遲滯)。"
                        "要比「一次盲轉的距離」大，不然停不進這個範圍裡，會一直來回過頭")
    g.add_argument("--servo-dir", type=int, choices=(1, -1), default=-1,
                   help="轉動方向：馬達怎麼裝決定的。轉錯邊就換成另一個值 (1 / -1)")
    g.add_argument("--servo-toggle", default="ok",
                   help="用哪個手勢開 / 關「跟著人轉」(比一次切換一次；空字串 = 不用手勢控制)")
    g.add_argument("--servo-toggle-cooldown", type=float, default=1.5,
                   help="切換後幾秒內不理會同一個手勢 (避免辨識閃爍時連切兩次)")
    g.add_argument("--servo-wiggle-seconds", type=float, default=0.5,
                   help="從鎖定切到跟隨時，鏡頭輕晃單段秒數；完整動作為四倍，0 = 關閉")
    servo_start_mode = g.add_mutually_exclusive_group()
    servo_start_mode.add_argument("--servo-off", dest="servo_off", action="store_true",
                                  help="啟動時鎖定鏡頭（已是預設；保留以相容舊指令）")
    servo_start_mode.add_argument("--servo-on", dest="servo_off", action="store_false",
                                  help="啟動後立刻跟隨人物，不等待 OK 手勢")
    g.set_defaults(servo=True, servo_off=True)
    g.add_argument("--servo-start", type=float, default=90.0, help="開機時的起始角度")
    g.add_argument("--servo-min", type=float, default=10.0, help="可轉範圍下限 (度)")
    g.add_argument("--servo-max", type=float, default=170.0, help="可轉範圍上限 (度)")
    g.add_argument("--servo-chip", type=int, default=1)
    g.add_argument("--servo-channel", type=int, default=2)
    g.add_argument("--servo-min-us", type=int, default=500, help="0 度的脈衝寬度 (校準用，見 servo.py)")
    g.add_argument("--servo-max-us", type=int, default=2500, help="180 度的脈衝寬度 (校準用)")
    ap.add_argument("--image", default="", help="單張圖片測試模式")
    ap.add_argument("--verbose", action="store_true", help="每幀印出推論時間")
    ap.add_argument("--debug", action="store_true",
                    help="畫出除錯用的框：被骨架模型否決的偵測框 (紅)、在人物附近找手的範圍 (紫)")
    args = ap.parse_args()
    # 預設翻轉：人看著自己的畫面，手往右移畫面裡也要往右，不然很難操作。
    # 翻轉在讀進影格後、所有處理之前，所以骨架座標和 dx 都是翻過的；PersonPanner 會補償，
    # MQTT 也會送出 mirror 讓 PC 端知道不要再翻一次。
    args.mirror = not args.no_mirror

    det_path, lmk_path, per_path = args.model, args.model_landmark, args.model_person
    if args.delegate == "npu":
        det_path, lmk_path, per_path = (to_vela_path(det_path), to_vela_path(lmk_path),
                                        to_vela_path(per_path))
        if not os.path.exists(ETHOSU_DELEGATE):
            sys.exit(f"找不到 {ETHOSU_DELEGATE}，這台不是 i.MX93？請改用 --delegate cpu")
    for p in (det_path, lmk_path) + ((per_path,) if args.person_every > 0 else ()):
        if not os.path.exists(p):
            sys.exit(f"找不到模型檔: {p}")
    print(f"delegate = {args.delegate}")
    detector = SsdDetector(det_path, args.delegate, "hand detect")
    landmark = HandLandmark(lmk_path, args.delegate)
    tracker = None if args.no_track else HandTracker(args.track_scale)
    person = None
    if args.person_every > 0:
        person = PersonLocator(SsdDetector(per_path, args.delegate, "person detect"),
                               args.person_every, args.person_thresh)

    # 單張圖片測試
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            sys.exit(f"讀不到圖片: {args.image}")
        hands, person_info, (det_ms, lmk_ms, per_ms) = process(frame, detector, landmark, person,
                                                                None, args)
        os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
        out = os.path.join(HERE, "output",
                           os.path.splitext(os.path.basename(args.image))[0] + "_hand_cam.jpg")
        cv2.imwrite(out, frame)
        print(f"det {det_ms:.1f} ms, lmk {lmk_ms:.1f} ms, person {per_ms:.1f} ms, "
              f"偵測到 {len(hands)} 隻手，結果存到 {out}")
        print("person:", json.dumps(person_info))
        for h in hands:
            print(json.dumps(h))
        return

    panner = None
    if args.servo:
        if person is None:
            sys.exit("雲台功能需要人物偵測，--person-every 不能是 0；"
                     "若不使用雲台請加 --no-servo")
        try:
            motor = servo.Servo(args.servo_chip, args.servo_channel,
                                args.servo_min_us, args.servo_max_us)
            panner = PersonPanner(
                servo.Panner(motor, args.servo_speed, start=args.servo_start,
                             min_angle=args.servo_min, max_angle=args.servo_max).start(),
                args.servo_deadband, args.servo_hold, args.servo_dir, args.mirror,
                args.servo_toggle, not args.servo_off, args.servo_wiggle_seconds)
            panner.toggle.cooldown = args.servo_toggle_cooldown
            blind = args.servo_speed * args.person_every / max(1, args.fps)
            print(f"雲台啟動：{args.servo_speed:.0f} 度/秒，範圍 {args.servo_min:.0f}~{args.servo_max:.0f} 度，"
                  f"人物偵測每 {args.person_every} 幀 (約 {args.person_every / max(1, args.fps) * 1000:.0f} ms) "
                  f"更新一次 → 每次盲轉約 {blind:.1f} 度，"
                  f"死區 {args.servo_deadband:.2f} / 停止 {args.servo_hold:.2f}"
                  f"，方向 {args.servo_dir:+d}"
                  f"{f'；手勢 {args.servo_toggle} = 開 / 關' if args.servo_toggle else ''}"
                  f"{'，目前鎖定（等待 OK 手勢）' if args.servo_off else '，目前正在跟隨'}")
        except OSError as e:          # 不在板子上 / 沒權限：其他功能照跑，不要整個掛掉
            print(f"雲台開不起來 (繼續跑，不控制馬達): {e}")
            panner = None


    dev = args.device or find_camera()
    if not dev:
        sys.exit("找不到 USB 鏡頭，請用 v4l2-ctl --list-devices 查詢後以 --device 指定")
    cap = open_camera(dev, args.width, args.height, args.fps)
    if cap is None:
        sys.exit(f"無法開啟鏡頭 {dev}")

    streamer = (MjpegStreamer(args.port, args.stream_scale, args.stream_quality, args.stream_fps)
                if args.port else None)
    mqtt_pub = MqttPublisher(args.mqtt, args.mqtt_topic, args.mqtt_hz) if args.mqtt else None
    if args.display and not setup_wayland():
        args.display = False
    if args.display:
        # WINDOW_NORMAL 才能縮放；全螢幕時 640x480 的畫面會等比例放大填滿 (兩側可能有黑邊)
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
        if not args.windowed:
            cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    if not streamer and not args.display and not mqtt_pub:
        print("注意：沒開串流/顯示/MQTT，只會在終端機印 FPS")

    fps, last_print = 0.0, time.time()
    n_frames = n_cand = n_ok = n_tries = n_det = 0
    pres_sum = 0.0

    # Ctrl+C 只設旗標，等這一幀跑完再結束；直接中斷會打斷 NPU 推論 (Failed to invoke ethos_u op)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        while not stop.is_set():
            t0 = time.perf_counter()
            ok, frame = cap.read()
            if not ok:
                print("讀取鏡頭失敗，重試…")
                time.sleep(0.1)
                continue
            if args.mirror:
                frame = cv2.flip(frame, 1)

            hands, person_info, (det_ms, lmk_ms, per_ms) = process(frame, detector, landmark,
                                                                    person, tracker, args)
            if panner is not None:
                g = hands[0].get("gesture") if hands else None
                panner.update(person_info, person.every * 2, g, time.monotonic())

            n_frames += 1
            n_tries += len(STATE.last_presences)
            n_det += STATE.last_det_ran
            if STATE.last_presences:
                n_cand += 1
                pres_sum += max(STATE.last_presences)
            if hands:
                n_ok += 1

            dt = time.perf_counter() - t0
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
            info = f"{args.delegate.upper()}  FPS {fps:4.1f}  det {det_ms:4.1f}  lmk {lmk_ms:4.1f}"
            if person is not None:
                info += f"  per {person.ms:4.1f}/{person.every}f"
            if panner is not None:
                info += "  " + panner.status()
            cv2.putText(frame, info, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
            cv2.putText(frame, info, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

            if streamer:
                streamer.push(frame)
            if mqtt_pub:
                mqtt_pub.publish({"ts": time.time(), "fps": round(fps, 1),
                                  "width": frame.shape[1], "height": frame.shape[0],
                                  "mirror": args.mirror,     # 座標是不是已經左右翻轉過 (PC 端別再翻一次)
                                  "hands": hands, "person": person_info,
                                  # None 表示沒有啟用雲台；布林值讓 PC 端顯示真實切換狀態，
                                  # 不必靠收到 ok 手勢後自行猜測目前是開還是關。
                                  "camera_follow": (None if panner is None
                                                    else panner.toggle.state)})
            if args.display:
                cv2.imshow(WINDOW_NAME, frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if args.verbose or time.time() - last_print > 2:
                ptxt = f"  person dx={person_info['dx']:+.2f}" if person_info else "  person -"
                stat = (f"  | ok {100 * n_ok / n_frames:3.0f}%  lmk-best {pres_sum / n_cand if n_cand else 0:.2f}"
                        f"  tries {n_tries / n_frames:.1f}/frame  det-run {100 * n_det / n_frames:3.0f}%")
                print(info + f"  hands={len(hands)}" + (ptxt if person is not None else "") + stat)
                last_print = time.time()
                n_frames = n_cand = n_ok = n_tries = n_det = 0
                pres_sum = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        print("結束")
        if panner is not None:
            panner.close()
        cap.release()
        if args.display:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
