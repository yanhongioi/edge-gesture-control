# edge-gesture-control

梅竹黑客松 × NXP FRDM-i.MX93 —— 不用碰電腦就能控制電腦：
**反射層**（板子 NPU 即時手勢，毫秒級）+ **認知層**（PC 本地 LLM 語音指令，秒級）。
完整構想見 [docs/plan_v3.md](docs/plan_v3.md)。

## 目錄結構

```text
edge-gesture-control/
├── board/                    # 跑在 FRDM-i.MX93 上（整個資料夾 scp 到板子）
│   ├── hand_cam.py           # C270 → 手部偵測 + 21 點骨架 → HTTP 串流 / HDMI / MQTT
│   ├── usb_net.sh            # 把 USB1_C 設成 USB 網卡，讓筆電直連 (192.168.7.2)
│   ├── models/               # 手部模型（原始 + Vela 編譯版 + Vela 報告）
│   └── test_images/          # 單張圖片測試用
├── pc/                       # 跑在 Windows 筆電
│   ├── hand_listener.py      # 接收板子送來的 21 點座標 (MQTT edge/hand)
│   ├── mosquitto.conf        # MQTT broker 設定（允許外部連線）
│   └── requirements.txt
├── scripts/
│   └── deploy_board.ps1      # 一鍵把 board/ 複製到板子
├── docs/
│   ├── 01_手部骨架上板教學.md  # 接線、看板子資訊、跑模型、看畫面（從這裡開始）
│   ├── board_info.md         # 我們這塊板子的實際資訊、IP、進度、效能紀錄
│   └── plan_v3.md            # 專案構想
└── third_party/              # 第三方授權
```

之後各分工預計放的位置（建立時再開資料夾）：

| 分工 | 位置 |
| --- | --- |
| A 手部模型、手勢分類 | `board/`（例如 `board/gesture.py`） |
| B 手勢狀態機、防誤觸、游標濾波 | `pc/gesture/` 或 `board/` |
| C LLM 與工具定義 | `pc/llm/` |
| D 語音、MQTT、PC 控制 | `pc/voice/`、`pc/control/` |
| E 介面、簡報 | `pc/ui/`、`docs/` |

## 快速開始

```powershell
# 0. 網路：USB 直連（板子 192.168.7.2 ↔ 筆電 192.168.7.1），設定方式見教學 3-D
# 1. 筆電：把 board/ 複製到板子（預設 192.168.7.2；其他 IP 用 -BoardIp）
.\scripts\deploy_board.ps1

# 2. 板子（ssh root@<板子IP>）
cd /root/edge-gesture-control/board
python3 hand_cam.py            # NPU 推論 + 串流

# 3. 筆電瀏覽器打開 http://<板子IP>:8080
```

詳細步驟與疑難排解：[docs/01_手部骨架上板教學.md](docs/01_手部骨架上板教學.md)

## MQTT topics

| topic | 方向 | 內容 |
| --- | --- | --- |
| `edge/hand` | 板子 → PC | `{"ts", "fps", "width", "height", "hands": [{"score", "presence", "box", "landmarks": [[x, y, z] × 21]}]}`，x/y 為 0~1 |

## 授權

`board/models/` 的模型與 `board/hand_cam.py` 的推論流程來自 WPI 的 MobileNetSSD_HandAndSKeletonDetect（Apache-2.0），見 [third_party/](third_party/)。
