# edge-gesture-control

梅竹黑客松 × NXP FRDM-i.MX93：不用碰電腦就能控制電腦。
- **反射層**：板子用 NPU 即時辨識手勢，反應在毫秒級。
- **認知層**：PC 用本地 LLM 處理語音指令，反應在秒級。

| 文件 | 內容 |
| --- | --- |
| `README.md`（本頁） | 專案概覽、快速開始、**板子目前狀態與進度** |
| [docs/setup.md](docs/setup.md) | 從零開始的上板教學：接線、登入、網路、跑模型、疑難排解 |
| [docs/plan.md](docs/plan.md) | 專案構想、分工、demo 腳本 |

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
│   ├── deploy_board.ps1      # 一鍵把 board/ 複製到板子
│   └── fix_vela_scratch.py   # 修正舊版 Vela 模型在 TFLite 2.16+ 無法載入的問題
├── docs/
│   ├── setup.md              # 上板教學
│   └── plan.md               # 專案構想
└── third_party/              # 第三方授權 (LICENSE)
```

之後各分工預計放的位置（用到時再建立資料夾）：

| 分工 | 位置 |
| --- | --- |
| A 手部模型、手勢分類 | `board/`（例如 `board/gesture.py`） |
| B 手勢狀態機、防誤觸、游標濾波 | `pc/gesture/` 或 `board/` |
| C LLM 與工具定義 | `pc/llm/` |
| D 語音、MQTT、PC 控制 | `pc/voice/`、`pc/control/` |
| E 介面、簡報 | `pc/ui/`、`docs/` |

**文件規則**：新的說明優先寫進現有的三份文件，不要另外開新的 md。

## 快速開始

前提：筆電和板子已經用 USB 直連（板子 `192.168.7.2`、筆電 `192.168.7.1`）。第一次設定請看 [docs/setup.md](docs/setup.md) 的步驟 3-D。

```powershell
# 1. 筆電：把 board/ 複製到板子（預設 192.168.7.2；其他 IP 用 -BoardIp）
.\scripts\deploy_board.ps1

# 2. 板子（ssh root@192.168.7.2）
cd /root/edge-gesture-control/board
python3 hand_cam.py            # NPU 推論 + 串流

# 3. 筆電瀏覽器打開 http://192.168.7.2:8080
```

---

## 板子狀態

我們這塊 FRDM-i.MX93 目前的實際狀態。**有變動就更新這一節**。最後更新：2026-09-16

### 系統與軟體

| 項目 | 值 |
| --- | --- |
| hostname | `imx93-11x11-lpddr4x-frdm` |
| Kernel | `6.18.2-1.0.0-gf49f45233f7b`（aarch64，2026-02-11 build） |
| 發行版 | NXP i.MX Release Distro `6.18-whinlatter`（`ID=fsl-imx-xwayland`） |
| 登入 | `root`，無密碼（序列埠和 SSH 都可以） |
| 記憶體 / 儲存空間 | 1.9 GiB（可用約 1.7 GiB，無 swap）/ `/` 8.2 G，剩餘 2.9 G |
| NPU | `/dev/ethosu0` ✅，delegate `/usr/lib/libethosu_delegate.so` ✅ |
| Python / TFLite | python 3.13；`tflite_runtime` 2.19.0 ✅（會出現 deprecated 警告，不影響執行）、`ai_edge_litert` ✅ |
| OpenCV | 4.12.0 ✅ |
| `paho-mqtt` | ❌ 沒有安裝（這不是官方的 MQTT image）|
| 我們的程式 | `/root/edge-gesture-control/board/` |
| 官方手部範例 | `/root/hand-demo/`（隊友放的 WPI 原版）。**裡面的 `_vela` 模型在 TFLite 2.19 無法載入**，請用 repo 裡修正過的版本 |

### 周邊與介面

| 介面 | 名稱 / 節點 | 備註 |
| --- | --- | --- |
| C270 鏡頭 | **`/dev/video2`**（影像）、`/dev/video3`（metadata，不能用） | 插在 USB-A 孔 |
| 板上 MIPI 鏡頭介面 | `/dev/video0`、`/dev/video1`（`mxc-isi-cap`） | 沒有使用 |
| 有線網路 | `eth0`、`eth1` | 沒有接 |
| Wi-Fi | `mlan0` | 開機就有，不需要 `modprobe` |
| USB 網路 | `usb0`（執行 `usb_net.sh` 後才會出現） | 見下方網路段落 |
| 其他 | `uap0`、`wfd0`、`can0` | 沒有使用 |

### 接線

| 板子的孔 | 接到 | 用途 |
| --- | --- | --- |
| DEBUG（USB-C） | 筆電 | 序列埠：**COM11 (A) = Linux 主控台**，COM12 (B) 沒有使用 |
| POWER（USB-C） | 筆電 **USB-C**（C 對 C 線） | 供電，目前沒有重開機的情況；demo 前要改用充電器 |
| USB-A | C270 | 鏡頭 |
| **`USB1_C`** | 筆電 **USB-A**（A 對 C 線） | USB 直連網路。用 C 對 C 線接時辨識不到 |
| RJ45 × 2、HDMI | （空） | 目前沒有網路線和 HDMI 線 |

序列埠：用 VS Code Serial Monitor，115200 8N1，切換到 Terminal Mode。**貼上多行指令會亂掉，要一次貼一行。**

### 網路

網路分成兩條，**彼此獨立**：

| 要做的事 | 走哪條網路 | 換 Wi-Fi 有影響嗎 |
| --- | --- | --- |
| SSH、scp、看串流 | **USB 直連**，板子是 `192.168.7.2` | 沒有 |
| 板子 → 筆電 MQTT | **USB 直連**，筆電是 `192.168.7.1` | 沒有 |
| 板子上網（pip、下載） | Wi-Fi | 要重新設定 Wi-Fi |
| 筆電上網（LLM、查資料） | 筆電自己的網路 | 沒有 |

**USB 直連（主要使用，IP 固定）**
- 板子：`usb0` = `192.168.7.2/24`，由 `board/usb_net.sh` 設定（NCM 模式）。
- 筆電：`192.168.7.1/24`，手動設定一次後 Windows 會記住。**每台筆電都要各自設定一次**，做法見 setup.md 的 3-D。目前開發用的筆電上，這張網卡叫「乙太網路 7」。
- `ssh root@192.168.7.2` ✅；`http://192.168.7.2:8080` 640×480 很順 ✅

**Wi-Fi（只讓板子上網，暫時性設定）**
- 目前**暫時借用個人手機熱點**，之後會換。熱點名稱和密碼不寫進 repo。
- 設定檔是 `/etc/wpa_hotspot.conf`。⚠ **裡面是明文密碼**，交出板子前要刪除或改寫。
- 板子的 Wi-Fi IP 由網路分配，**換網路就會變**，用 `ip addr show mlan0` 查詢。
- 換網路的做法見 setup.md 3-A 的「換成別的 Wi-Fi」，請透過 USB 直連的 SSH 操作。

### 板子重開機後要重做的事

在序列埠輸入，一次貼一行。

```bash
# USB 直連（每次都要做），做完就能 ssh root@192.168.7.2
sh /root/edge-gesture-control/board/usb_net.sh

# Wi-Fi（板子需要上網時才做，也可以在 USB SSH 裡執行）
ip link set mlan0 up
wpa_supplicant -B -i mlan0 -D nl80211 -c /etc/wpa_hotspot.conf
udhcpc -i mlan0
```

### 進度

- [x] 序列埠登入、檢查系統 / NPU / Python / 既有模型
- [x] C270 辨識為 `/dev/video2`
- [x] Wi-Fi 上網（手機熱點）、SSH 登入
- [x] 修正 Vela 模型，NPU 單張圖片測試正確（跟 PC 上 CPU 的結果一致）
- [x] 即時鏡頭 + 瀏覽器看骨架：板子約 30 FPS（等於 C270 的上限）
- [x] USB 直連網路，串流很順
- [ ] 用 `benchmark_model` 量 NPU / CPU 推論時間，填進下方的效能紀錄
- [ ] 在板子上安裝 `paho-mqtt`，打通 MQTT（板子 → 筆電）
- [ ] 開機自動執行 `usb_net.sh`（Wi-Fi 會換網路，是否也要自動連線之後再決定）
- [ ] VS Code Remote-SSH（主機填 `root@192.168.7.2`）
- [ ] demo 距離測試：1～1.5 公尺外偵測不偵測得到手
- [ ] 確認 `hand_cam.py` 開鏡頭時走的是 GStreamer 還是 V4L2（看啟動訊息）
- [ ] 改用充電器供電

### 效能紀錄

| 項目 | NPU (vela) | CPU |
| --- | --- | --- |
| hand_detect_20000_quant | 9.8 ms（單張圖片、第一次推論，含暖機） | |
| hand_landmark_new_256x256_integer_quant | 12.2 ms（同上） | |
| `hand_cam.py` 即時（640×480，1 隻手） | 約 30 FPS（受限於鏡頭的 30 fps） | |

NPU 分工：偵測模型的後處理（`TFLite_Detection_PostProcess`）在 CPU 上跑；骨架模型的輸入輸出量化轉換在 CPU 上跑；其餘都在 NPU。

### 已知問題

- **官方 Vela 模型載入失敗**（`Tensor 8 is invalidly specified in schema`）：舊版 Vela 讓 `*_scratch_fast` tensor 指向一個空的 buffer，TFLite 2.19 會拒絕載入。repo 裡的兩個 `_vela.tflite` 已經用 `scripts/fix_vela_scratch.py` 修正。
- **筆電和板子都連手機熱點時，串流會卡**：要經過手機轉送，ping 61～446 ms → 已改用 USB 直連。i.MX93 沒有硬體影像編碼器，只能用 CPU 壓 JPEG。
- **`g_ether` 在 Windows 上被認成「USB 序列裝置 (COMx)」**，也無法手動改成網卡驅動 → 改用 `usb_net.sh`（NCM）。
- **C 對 C 線接 `USB1_C` 時辨識不到** → 改用 A 對 C 線接筆電的 USB-A。
- `dmesg` 裡的 `ethosu: can't change firmware ...`：每次啟動 NPU 程式都會出現，可以忽略。

---

## MQTT topics

| topic | 方向 | 內容 |
| --- | --- | --- |
| `edge/hand` | 板子 → PC | `{"ts", "fps", "width", "height", "hands": [{"score", "presence", "box", "landmarks": [[x, y, z] × 21]}]}`，x/y 為 0~1 |

## 授權與來源

`board/models/` 的模型，以及 `board/hand_cam.py` 的前後處理流程，改寫自 WPI（Weilly Li）的 i.MX 手部偵測範例 MobileNetSSD_HandAndSKeletonDetect（Code Ver 4.0，2023/04/26，Apache License 2.0，授權全文見 [third_party/MobileNetSSD_HandAndSKeletonDetect/LICENSE](third_party/MobileNetSSD_HandAndSKeletonDetect/LICENSE)）。

- 使用的模型：`hand_detect_20000_quant`（MobileNet-SSD 手部偵測）、`hand_landmark_new_256x256_integer_quant`（21 點手骨架），兩者都包含原始版和 Vela 版。Vela 版已修正，見已知問題。
- 沒有收錄 `hand_landmark_quant`、`hand_landmark_mediapipe_quant`：根據 Vela 報告，這兩個模型沒有任何運算能放到 NPU 上。
- ⚠ 原始 `app.py` 的檔頭標示「WPI Confidential Proprietary」，跟 LICENSE 寫的授權不一致，所以 repo 裡沒有放 `app.py`。**repo 公開前請先向提供者確認授權。**
