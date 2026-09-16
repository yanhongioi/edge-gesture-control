#!/usr/bin/env python3
# --------------------------------------------------------------------------------------
# i.MX93 FRDM : 手部偵測 + 21 點手骨架 + 人物定位 (Logitech C270)
# 改寫自 MobileNetSSD_HandAndSKeletonDetect/app.py (WPI, Weilly Li, Apache-2.0)，修正/新增：
#   * 預設用 i.MX93 的 NPU (Ethos-U, libethosu_delegate.so)，app.py 預設的 vx 是 i.MX8MP 用的
#   * 自動尋找 C270 的 /dev/videoX，不再寫死 /dev/video3
#   * 鏡頭畫面轉 RGB 再送進模型 (OpenCV 讀進來是 BGR)
#   * 內建 HTTP MJPEG 串流 -> 筆電瀏覽器開 http://<板子IP>:8080 就能看到畫面+骨架
#   * 可選：板子 HDMI 螢幕顯示 (--display)、MQTT 送出 21 點座標 (--mqtt)
#   * 人物定位：每 N 幀跑一次人物偵測 (MobileNetSSD_VehicleHumanDetector)，標出人的中心點
#     和偏離畫面中央的量 dx，給之後的雲台用
#
# 用法 (在板子上):
#   python3 hand_cam.py                          # NPU + 串流到 :8080
#   python3 hand_cam.py --display                # 另外顯示在板子 HDMI 螢幕
#   python3 hand_cam.py --stream-scale 0.5 --stream-fps 10   # 網路慢時減輕串流
#   python3 hand_cam.py --delegate cpu           # 用 CPU 跑 (跟 NPU 對照)
#   python3 hand_cam.py --mqtt 192.168.7.1       # 21 點座標送到 PC, topic: edge/hand
#   python3 hand_cam.py --person-every 10        # 人物偵測每 10 幀跑一次 (0 = 關閉)
#   python3 hand_cam.py --image test_images/hand-1.jpg  # 單張圖片測試，結果存到 output/
# --------------------------------------------------------------------------------------

import os
import sys
import glob
import json
import time
import argparse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

try:
    import tflite_runtime.interpreter as tflite          # 板子 (NXP BSP)
except ImportError:
    try:
        import ai_edge_litert.interpreter as tflite       # PC 測試用
    except ImportError:
        import tensorflow.lite as tflite                  # PC 測試用

HERE = os.path.dirname(os.path.abspath(__file__))
ETHOSU_DELEGATE = "/usr/lib/libethosu_delegate.so"

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


PERSON_CLASS = 0      # COCO label: 0 = person


class PersonLocator:
    """每 N 幀跑一次人物偵測，記住畫面中最大的人 (通常就是使用者)。
    dx / dy = 人物中心偏離畫面中央的量，-1~+1，正值 = 人在畫面右 / 下方。
    之後的雲台就是要把 dx 拉回 0。注意：用 --mirror 時畫面已左右翻轉，雲台方向要跟著反過來。"""

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
    """從 SSH / 序列埠執行時，讓 cv2.imshow 能畫到板子 HDMI 上的 Weston 桌面"""
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        return
    runtime = os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/0")
    sockets = [s for s in glob.glob(os.path.join(runtime, "wayland-*")) if not s.endswith(".lock")]
    if sockets:
        os.environ["WAYLAND_DISPLAY"] = os.path.basename(sorted(sockets)[0])
        print("使用 Wayland 顯示:", os.environ["WAYLAND_DISPLAY"])


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


def process(frame, detector, landmark, person, args):
    """在 frame 上畫出結果，回傳 (hands, person_info, (det_ms, lmk_ms, person_ms))；
    座標皆為 0~1 正規化；person_ms 在這一幀沒跑人物偵測時為 0"""
    fh, fw = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    person_ms = 0.0
    if person is not None:
        if person.update(rgb):
            person_ms = person.ms
        person.draw(frame)
    boxes, _, scores, det_ms = detector(rgb)

    # 手部偵測常常給出「不是手」的高分框，所以依分數由高到低，最多試 --hand-candidates 個框，
    # 讓骨架模型判斷哪個才是手；找到 --max-hands 隻就停，所以第一個框就是手時不會多花時間
    hands, lmk_ms, tried, presences = [], 0.0, 0, []
    for box, score in sorted(zip(boxes, scores), key=lambda t: -t[1]):
        if score < args.det_thresh or len(hands) >= args.max_hands or tried >= args.hand_candidates:
            break
        x0, y0, x1, y1 = expand_box(box, fw, fh)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        tried += 1

        pts, presence, ms = landmark(rgb[y0:y1, x0:x1])
        lmk_ms += ms
        presences.append(presence)
        ok = presence >= args.lmk_thresh
        if ok:
            cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 200, 255), 2)
            cv2.putText(frame, f"hand {score:.2f}  lmk {presence:.2f}", (x0, max(14, y0 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 0), 2)
        else:   # 被骨架模型否決的候選框：細紅框
            cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 0, 255), 1)
            cv2.putText(frame, f"{score:.2f}/{presence:.2f}", (x0 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            continue
        sx, sy = (x1 - x0) / landmark.w, (y1 - y0) / landmark.h
        px = [x0 + int(p[0] * sx) for p in pts]
        py = [y0 + int(p[1] * sy) for p in pts]
        draw_hand(frame, px, py)
        hands.append({
            "score": round(float(score), 3),
            "presence": round(presence, 3),
            "box": [round(x0 / fw, 4), round(y0 / fh, 4), round(x1 / fw, 4), round(y1 / fh, 4)],
            "landmarks": [[round(px[i] / fw, 4), round(py[i] / fh, 4), round(float(pts[i][2]), 2)]
                          for i in range(21)],
        })
    person_info = person.as_dict(fw, fh) if person is not None else None
    process.last_presences = presences      # 給主迴圈做統計
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
    ap.add_argument("--person-every", type=int, default=5, help="人物偵測每幾幀跑一次，0 = 關閉")
    ap.add_argument("--person-thresh", type=float, default=0.5, help="人物偵測分數門檻")
    ap.add_argument("--device", default="", help="例如 /dev/video0；不給就自動找 C270")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--mirror", action="store_true", help="左右翻轉畫面 (像照鏡子)")
    ap.add_argument("--det-thresh", type=float, default=0.55,
                    help="hand detection score threshold (the model outputs junk boxes at exactly 0.50 when there is no hand)")
    ap.add_argument("--lmk-thresh", type=float, default=0.7, help="手骨信心分數門檻")
    ap.add_argument("--max-hands", type=int, default=1, help="每幀最多處理幾隻手 (每多一隻多一次推論)")
    ap.add_argument("--hand-candidates", type=int, default=2,
                    help="每幀最多拿幾個手部偵測框給骨架模型確認 (前面的框被否決才會試下一個)")
    ap.add_argument("--port", type=int, default=8080, help="HTTP 串流埠，0 = 不開")
    ap.add_argument("--stream-scale", type=float, default=1.0, help="串流畫面縮放，例如 0.5 = 320x240")
    ap.add_argument("--stream-quality", type=int, default=60, help="串流 JPEG 畫質 1~100")
    ap.add_argument("--stream-fps", type=float, default=15, help="串流最高幀率 (不影響推論)")
    ap.add_argument("--display", action="store_true", help="在板子 HDMI 螢幕上顯示")
    ap.add_argument("--mqtt", default="", help="MQTT broker IP，例如 192.168.10.1")
    ap.add_argument("--mqtt-topic", default="edge/hand")
    ap.add_argument("--mqtt-hz", type=float, default=15)
    ap.add_argument("--image", default="", help="單張圖片測試模式")
    ap.add_argument("--verbose", action="store_true", help="每幀印出推論時間")
    args = ap.parse_args()

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
    person = None
    if args.person_every > 0:
        person = PersonLocator(SsdDetector(per_path, args.delegate, "person detect"),
                               args.person_every, args.person_thresh)

    # 單張圖片測試
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            sys.exit(f"讀不到圖片: {args.image}")
        hands, person_info, (det_ms, lmk_ms, per_ms) = process(frame, detector, landmark, person, args)
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

    dev = args.device or find_camera()
    if not dev:
        sys.exit("找不到 USB 鏡頭，請用 v4l2-ctl --list-devices 查詢後以 --device 指定")
    cap = open_camera(dev, args.width, args.height, args.fps)
    if cap is None:
        sys.exit(f"無法開啟鏡頭 {dev}")

    streamer = (MjpegStreamer(args.port, args.stream_scale, args.stream_quality, args.stream_fps)
                if args.port else None)
    mqtt_pub = MqttPublisher(args.mqtt, args.mqtt_topic, args.mqtt_hz) if args.mqtt else None
    if args.display:
        setup_wayland()
    if not streamer and not args.display and not mqtt_pub:
        print("注意：沒開串流/顯示/MQTT，只會在終端機印 FPS")

    fps, last_print = 0.0, time.time()
    n_frames = n_cand = n_ok = n_tries = 0
    pres_sum = 0.0
    try:
        while True:
            t0 = time.perf_counter()
            ok, frame = cap.read()
            if not ok:
                print("讀取鏡頭失敗，重試…")
                time.sleep(0.1)
                continue
            if args.mirror:
                frame = cv2.flip(frame, 1)

            hands, person_info, (det_ms, lmk_ms, per_ms) = process(frame, detector, landmark,
                                                                    person, args)

            n_frames += 1
            n_tries += len(process.last_presences)
            if process.last_presences:
                n_cand += 1
                pres_sum += max(process.last_presences)
            if hands:
                n_ok += 1

            dt = time.perf_counter() - t0
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
            info = f"{args.delegate.upper()}  FPS {fps:4.1f}  det {det_ms:4.1f}  lmk {lmk_ms:4.1f}"
            if person is not None:
                info += f"  per {person.ms:4.1f}/{person.every}f"
            cv2.putText(frame, info, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
            cv2.putText(frame, info, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

            if streamer:
                streamer.push(frame)
            if mqtt_pub:
                mqtt_pub.publish({"ts": time.time(), "fps": round(fps, 1),
                                  "width": frame.shape[1], "height": frame.shape[0],
                                  "hands": hands, "person": person_info})
            if args.display:
                cv2.imshow("i.MX93 Hand Skeleton", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if args.verbose or time.time() - last_print > 2:
                ptxt = f"  person dx={person_info['dx']:+.2f}" if person_info else "  person -"
                stat = (f"  | ok {100 * n_ok / n_frames:3.0f}%  lmk-best {pres_sum / n_cand if n_cand else 0:.2f}"
                        f"  tries {n_tries / n_frames:.1f}/frame")
                print(info + f"  hands={len(hands)}" + (ptxt if person is not None else "") + stat)
                last_print = time.time()
                n_frames = n_cand = n_ok = n_tries = 0
                pres_sum = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if args.display:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
