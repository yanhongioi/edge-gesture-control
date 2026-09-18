# PC 端：接收 i.MX93 hand_cam.py 送來的手部 21 點座標 (MQTT topic: edge/hand)
# 需求: pip install paho-mqtt ，並先啟動 mosquitto (見 Edge AI Example 的 Readme)
# 用法: python pc_hand_listener.py [broker_ip]
import sys
import json
import paho.mqtt.client as mqtt

BROKER = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
TOPIC = "edge/hand"
NAMES = {0: "wrist", 4: "thumb", 8: "index", 12: "middle", 16: "ring", 20: "pinky"}


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"MQTT connected ({reason_code}), subscribe {TOPIC}")
    client.subscribe(TOPIC)


def on_message(client, userdata, msg):
    data = json.loads(msg.payload)
    person = data.get("person")
    ptxt = f"person dx {person['dx']:+.2f}" if person else "no person"
    if not data["hands"]:
        print(f"\rfps {data['fps']:5.1f}  {ptxt}  no hand" + " " * 70, end="")
        return
    hand = data["hands"][0]
    # gesture = 連續數幀一致才確認的手勢；gesture_raw = 這一幀單獨判斷的結果 (board/gesture.py)
    gtxt = f"gesture {hand.get('gesture') or '-':9s} (raw {hand.get('gesture_raw') or '-':9s})"
    ratio = hand.get("pinch_ratio")
    gtxt += f"  pinch {'YES' if hand.get('pinch') else 'no ':3s} ({ratio:.2f})" if ratio is not None else ""
    lm = hand["landmarks"]                        # 21 個 [x, y, z]，x/y 為 0~1
    text = "  ".join(f"{NAMES[i]}({lm[i][0]:.2f},{lm[i][1]:.2f})" for i in (0, 8))
    print(f"\rfps {data['fps']:5.1f}  {ptxt}  [{hand.get('source', '?'):6s}] {gtxt}  {text}   ", end="")


try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
except AttributeError:
    client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, 1883)
client.loop_forever()
