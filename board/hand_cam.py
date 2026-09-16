#!/usr/bin/env python3
# --------------------------------------------------------------------------------------
# i.MX93 FRDM : 手部偵測 + 21 點手骨架 (Logitech C270)
# 改寫自 MobileNetSSD_HandAndSKeletonDetect/app.py (WPI, Weilly Li, Apache-2.0)，修正/新增：
#   * 預設用 i.MX93 的 NPU (Ethos-U, libethosu_delegate.so)，app.py 預設的 vx 是 i.MX8MP 用的
#   * 自動尋找 C270 的 /dev/videoX，不再寫死 /dev/video3
#   * 鏡頭畫面轉 RGB 再送進模型 (OpenCV 讀進來是 BGR)
#   * 內建 HTTP MJPEG 串流 -> 筆電瀏覽器開 http://<板子IP>:8080 就能看到畫面+骨架
#   * 可選：板子 HDMI 螢幕顯示 (--display)、MQTT 送出 21 點座標 (--mqtt)
#
# 用法 (在板子上):
#   python3 hand_cam.py                          # NPU + 串流到 :8080
#   python3 hand_cam.py --display                # 另外顯示在板子 HDMI 螢幕
#   python3 hand_cam.py --stream-scale 0.5 --stream-fps 10   # 網路慢時減輕串流
#   python3 hand_cam.py --delegate cpu           # 用 CPU 跑 (跟 NPU 對照)
#   python3 hand_cam.py --mqtt 192.168.10.1      # 21 點座標送到 PC, topic: edge/hand
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


class HandDetector:
    """MobileNet-SSD 手部偵測 (輸出為 TFLite_Detection_PostProcess：boxes/classes/scores/num)"""

    def __init__(self, path, delegate):
        self.interp = make_interpreter(path, delegate)
        self.interp.allocate_tensors()
        inp = self.interp.get_input_details()[0]
        self.in_index = inp["index"]
        self.in_dtype = inp["dtype"]
        self.h, self.w = int(inp["shape"][1]), int(inp["shape"][2])
        outs = self.interp.get_output_details()
        self.out_boxes, self.out_scores, self.out_num = (outs[0]["index"], outs[2]["index"],
                                                         outs[3]["index"])
        describe("hand detect: " + os.path.basename(path), self.interp)

    def __call__(self, rgb):
        img = cv2.resize(rgb, (self.w, self.h)).astype(self.in_dtype)
        self.interp.set_tensor(self.in_index, img[None])
        t0 = time.perf_counter()
        self.interp.invoke()
        ms = (time.perf_counter() - t0) * 1000
        boxes = self.interp.get_tensor(self.out_boxes)[0]     # [N,4] = ymin,xmin,ymax,xmax (0~1)
        scores = self.interp.get_tensor(self.out_scores)[0]
        num = int(self.interp.get_tensor(self.out_num).reshape(-1)[0])
        return boxes[:num], scores[:num], ms


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


def process(frame, detector, landmark, args):
    """在 frame 上畫出結果，回傳 (hands, (det_ms, lmk_ms))；座標皆為 0~1 正規化"""
    fh, fw = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    boxes, scores, det_ms = detector(rgb)

    hands, lmk_ms, tried = [], 0.0, 0
    for box, score in sorted(zip(boxes, scores), key=lambda t: -t[1]):
        if score < args.det_thresh or tried >= args.max_hands:
            break
        x0, y0, x1, y1 = expand_box(box, fw, fh)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        tried += 1
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 200, 255), 2)
        cv2.putText(frame, f"hand {score:.2f}", (x0, max(12, y0 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

        pts, presence, ms = landmark(rgb[y0:y1, x0:x1])
        lmk_ms += ms
        if presence < args.lmk_thresh:
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
    return hands, (det_ms, lmk_ms)


# --------------------------------------------------------------------------------------
# 主程式
# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="i.MX93 hand skeleton demo")
    ap.add_argument("--delegate", choices=["npu", "cpu"], default="npu")
    ap.add_argument("--model", default=os.path.join(HERE, "models/hand_detect_20000_quant.tflite"))
    ap.add_argument("--model-landmark",
                    default=os.path.join(HERE, "models/hand_landmark_new_256x256_integer_quant.tflite"))
    ap.add_argument("--device", default="", help="例如 /dev/video0；不給就自動找 C270")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--mirror", action="store_true", help="左右翻轉畫面 (像照鏡子)")
    ap.add_argument("--det-thresh", type=float, default=0.5, help="手部偵測分數門檻")
    ap.add_argument("--lmk-thresh", type=float, default=0.7, help="手骨信心分數門檻")
    ap.add_argument("--max-hands", type=int, default=1, help="每幀最多處理幾隻手 (每多一隻多一次推論)")
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

    det_path, lmk_path = args.model, args.model_landmark
    if args.delegate == "npu":
        det_path, lmk_path = to_vela_path(det_path), to_vela_path(lmk_path)
        if not os.path.exists(ETHOSU_DELEGATE):
            sys.exit(f"找不到 {ETHOSU_DELEGATE}，這台不是 i.MX93？請改用 --delegate cpu")
    for p in (det_path, lmk_path):
        if not os.path.exists(p):
            sys.exit(f"找不到模型檔: {p}")
    print(f"delegate = {args.delegate}")
    detector = HandDetector(det_path, args.delegate)
    landmark = HandLandmark(lmk_path, args.delegate)

    # 單張圖片測試
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            sys.exit(f"讀不到圖片: {args.image}")
        hands, (det_ms, lmk_ms) = process(frame, detector, landmark, args)
        os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
        out = os.path.join(HERE, "output",
                           os.path.splitext(os.path.basename(args.image))[0] + "_hand_cam.jpg")
        cv2.imwrite(out, frame)
        print(f"det {det_ms:.1f} ms, lmk {lmk_ms:.1f} ms, 偵測到 {len(hands)} 隻手，結果存到 {out}")
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

            hands, (det_ms, lmk_ms) = process(frame, detector, landmark, args)

            dt = time.perf_counter() - t0
            fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt
            info = f"{args.delegate.upper()}  FPS {fps:4.1f}  det {det_ms:5.1f}ms  lmk {lmk_ms:5.1f}ms"
            cv2.putText(frame, info, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
            cv2.putText(frame, info, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

            if streamer:
                streamer.push(frame)
            if mqtt_pub:
                mqtt_pub.publish({"ts": time.time(), "fps": round(fps, 1),
                                  "width": frame.shape[1], "height": frame.shape[0],
                                  "hands": hands})
            if args.display:
                cv2.imshow("i.MX93 Hand Skeleton", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if args.verbose or time.time() - last_print > 2:
                print(info + f"  hands={len(hands)}")
                last_print = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
