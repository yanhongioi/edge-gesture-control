# 板子資訊與進度紀錄

我們這塊 FRDM-i.MX93 的實際狀態。有變動就更新這份文件。操作步驟請見 [01_手部骨架上板教學.md](01_手部骨架上板教學.md)。

最後更新：2026-09-16

---

## 系統

| 項目 | 值 |
| --- | --- |
| hostname | `imx93-11x11-lpddr4x-frdm` |
| Kernel | `6.18.2-1.0.0-gf49f45233f7b`（aarch64，2026-02-11 build） |
| 發行版 | NXP i.MX Release Distro `6.18-whinlatter`（`ID=fsl-imx-xwayland`） |
| 登入 | `root`，無密碼（序列埠和 SSH 都可以） |
| 記憶體 | 1.9 GiB（開機後可用約 1.7 GiB），無 swap |
| 儲存空間 | `/` 共 8.2 G，已用 4.9 G，剩餘 2.9 G |

## AI / 軟體環境

| 項目 | 狀態 |
| --- | --- |
| NPU 裝置 | `/dev/ethosu0` ✅ |
| NPU delegate | `/usr/lib/libethosu_delegate.so` ✅ |
| `tflite_runtime` | ✅ |
| `ai_edge_litert` | ✅ |
| OpenCV (python3) | 4.12.0 ✅ |
| `paho-mqtt` | ❌ 未安裝（不是官方的 MQTT image）→ 要用 MQTT 時需要另外安裝 |
| TFLite 版本 | 2.19.0（python 3.13），會顯示 `tf.lite.Interpreter is deprecated` 警告，不影響執行 |
| 官方手部範例 | `/root/hand-demo/`（`app.py`、`model/`（8 個模型）、`img/`、`output/`），與 WPI 原版 MobileNetSSD_HandAndSKeletonDetect 相同。**其中的 `_vela` 模型在 TFLite 2.19 無法載入**（見下方已知問題），請改用 repo 裡修正過的版本 |
| 我們的程式 | `/root/edge-gesture-control/board/`（從筆電 scp 過去） |

## 周邊與介面

| 介面 | 名稱 / 節點 | 備註 |
| --- | --- | --- |
| C270 鏡頭 | **`/dev/video2`**（影像）、`/dev/video3`（metadata，不能讀影像）、`/dev/media0` | 插在板子的 USB-A 孔，`usb-ci_hdrc.1-1` |
| 板上 MIPI 鏡頭介面 | `/dev/video0`、`/dev/video1`（`mxc-isi-cap`） | 沒有使用 |
| 有線網路 | `eth0`、`eth1` | 目前沒接網路線 |
| Wi-Fi | `mlan0`（MAC `80:a1:97:50:49:91`） | 開機就有，不需要 `modprobe` |
| Wi-Fi AP / Direct | `uap0`、`wfd0` | 沒有使用 |
| CAN | `can0` | 沒有使用 |

## 接線（目前）

| 板子的孔 | 接到 | 用途 |
| --- | --- | --- |
| DEBUG（USB-C） | 筆電 | 序列埠：**COM11 (A) = Linux 主控台**，COM12 (B) 沒有使用 |
| POWER（USB-C） | 筆電 **USB-C**（C 對 C 線） | 供電，到目前為止沒有重開機；demo 前要改用充電器 |
| USB-A | C270 | 鏡頭 |
| **`USB1_C`** | 筆電 **USB-A**（A 對 C 線） | **USB 直連網路**（NCM，`usb_net.sh`）。用 C 對 C 線接時沒辦法被辨識 |
| RJ45 × 2、HDMI | （空） | 目前沒有網路線和 HDMI 線 |

序列埠設定：VS Code Serial Monitor、115200、8N1，並切換到 Terminal Mode。
**Serial Monitor 貼上多行指令會亂掉，要一次貼一行。**

## 網路

網路分成兩條，**彼此獨立**：

- **USB 直連**：負責筆電和板子之間的所有傳輸（SSH、傳檔、串流、之後的 MQTT）。IP 固定，**跟用哪個 Wi-Fi 無關**。
- **Wi-Fi**：只讓板子上網（安裝套件、下載）。**連哪個網路都可以，之後也會換**，目前暫時借用個人手機熱點。

| 要做的事 | 走哪條網路 | 換 Wi-Fi 有影響嗎 |
| --- | --- | --- |
| SSH、scp、看串流 | USB 直連 `192.168.7.2` | 沒有 |
| 板子 → 筆電 MQTT | USB 直連，筆電是 `192.168.7.1` | 沒有 |
| 板子上網（pip、下載） | Wi-Fi | 要重新設定 Wi-Fi |
| 筆電上網（LLM、查資料） | 筆電自己的網路 | 沒有 |

### USB 直連（主要使用）

| 項目 | 值 |
| --- | --- |
| 板子 | `usb0` = **`192.168.7.2/24`**（NCM gadget，由 `board/usb_net.sh` 設定；MAC `02:00:00:00:93:02`） |
| 筆電 | **`192.168.7.1/24`**，手動設定，Windows 會記住。**每台筆電都要設定一次**（教學 3-D 第 5 步）；目前開發用的筆電上，這張網卡叫「乙太網路 7」，其他筆電的名稱會不一樣 |
| SSH | `ssh root@192.168.7.2` ✅ |
| 串流 | `http://192.168.7.2:8080`，640×480 很順 ✅ |

### Wi-Fi（讓板子上網，**暫時性設定**）

| 項目 | 值 |
| --- | --- |
| 目前連的網路 | **暫時借用的個人手機熱點**（WPA2-PSK，2.4 GHz）。名稱和密碼不記錄在 repo |
| 板子設定檔 | `/etc/wpa_hotspot.conf`（⚠ 內含目前網路的**明文密碼**，交出板子前要刪除或改寫） |
| 板子 Wi-Fi IP | 由網路分配，**換網路或重新連線就會變**。查詢：`ip addr show mlan0`（在目前熱點下曾經是 `10.52.95.226`） |
| 換網路 | 見教學 3-A「換成別的 Wi-Fi」，請透過 USB 直連的 SSH 操作 |

### 板子重開機後要重做的事（在序列埠一次貼一行）

**USB 直連（每次都要做）**，做完就能從筆電 `ssh root@192.168.7.2`：

```bash
sh /root/edge-gesture-control/board/usb_net.sh
```

**Wi-Fi（板子需要上網時才做）**，連的是 `/etc/wpa_hotspot.conf` 裡的網路，也可以在 USB SSH 裡執行：

```bash
ip link set mlan0 up
wpa_supplicant -B -i mlan0 -D nl80211 -c /etc/wpa_hotspot.conf
udhcpc -i mlan0
```

- 出現 `rfkill: Cannot open RFKILL control device` 可以忽略。
- 待辦：開機自動執行 `usb_net.sh`。Wi-Fi 會換網路，是否也要開機自動連線，之後再決定。

---

## 進度

- [x] 序列埠登入（COM11）
- [x] 檢查系統、NPU、Python 環境、既有模型
- [x] C270 辨識為 `/dev/video2`
- [x] 板子透過手機熱點上網、取得 IP
- [x] 筆電 ping 得到板子、SSH 登入成功
- [x] USB 直連網路（`USB1_C` NCM，`192.168.7.2` ↔ `192.168.7.1`），串流很順
- [ ] VS Code Remote-SSH（主機填 `root@192.168.7.2`）
- [x] 把 `board/` 部署到板子（scp）
- [ ] 用 `benchmark_model` 量 NPU / CPU 推論時間 → 填到下表
- [x] 單張圖片測試（`hand_cam.py --image`，NPU）：結果正確，與 PC CPU 結果一致
- [x] 即時鏡頭 + 瀏覽器看骨架：板子約 30 FPS（等於 C270 上限），瀏覽器畫面會卡（熱點）
- [ ] MQTT（需要先安裝 paho-mqtt）
- [ ] 在板子上安裝 `paho-mqtt`（透過熱點上網）
- [ ] 改用充電器供電；Wi-Fi 和 USB 網路改成開機自動啟動
- [ ] demo 距離測試：1～1.5 公尺外偵測不偵測得到手
- [ ] 確認 `hand_cam.py` 開鏡頭時走的是 GStreamer 還是 V4L2（看啟動訊息）

## 已知問題

- **官方 Vela 模型載入失敗**：錯誤為 `Tensor 8 is invalidly specified in schema`。原因是舊版 Vela 讓 `*_scratch_fast` tensor 指向一個空的 buffer，TFLite 2.19 會拒絕載入。已用 `scripts/fix_vela_scratch.py` 修正 repo 裡的兩個 `_vela.tflite`（2026-09-16）。

- **透過手機熱點看串流會卡**：板子本身有 30 FPS，瓶頸在網路（筆電和板子都連熱點時，要經過手機轉送，ping 61～446 ms）。→ **已改用 USB 直連解決**，640×480 很順。demo 時也可以接 HDMI 螢幕。
- **`g_ether` 在 Windows 上被認成「USB 序列裝置 (COM14)」**，也無法手動改成網卡驅動 → 改用 configfs 的 NCM（`usb_net.sh`），Windows 11 會自動認成 UsbNcm Host Device。
- **C 對 C 線接 `USB1_C` 時沒有被辨識**，改用 A 對 C 線接筆電的 USB-A 就正常了。
- `dmesg` 裡的 `ethosu: can't change firmware or remote processor is running`：每次啟動 NPU 程式都會出現，可以忽略。
  - i.MX93 沒有硬體影像編碼器，JPEG 壓縮只能靠 CPU，所以無法用 H.264 串流來省頻寬。

## 效能紀錄

NPU 分工（delegate 訊息）：偵測模型 `1 nodes delegated out of 2`，後處理 `TFLite_Detection_PostProcess` 在 CPU；骨架模型 `1 of 5`，輸入輸出的量化轉換在 CPU。


| 模型 | NPU (vela) | CPU |
| --- | --- | --- |
| hand_detect_20000_quant | 9.8 ms（單張圖片、第一次推論，含暖機） | |
| hand_landmark_new_256x256_integer_quant | 12.2 ms（同上） | |
| `hand_cam.py` 即時 FPS（640x480，1 隻手） | 約 30（`--port 0` 和輕量串流都一樣，受限於鏡頭 30 fps） | |
