# 板子效能實測（FRDM-i.MX93，NPU vs CPU）

所有指令都在**板子上**執行（`ssh root@192.168.7.2`）。一次貼一行最保險。

## 0. 準備

```bash
cd /root/edge-gesture-control/board/models
B=/usr/bin/tensorflow-lite-2.19.0/examples/benchmark_model
ls $B /usr/lib/libethosu_delegate.so /dev/ethosu0     # 三個都要在
uptime                                                # load average 接近 0 再量，數字才準
```

- 量之前先停掉會搶 CPU / NPU 的程式（`hand_cam.py`、語音 `run_voice.sh stop`），不然數字會偏大。
- `B`、`cd` 只在這個 SSH 視窗有效；開新視窗要重新設定。
- `_vela.tflite` = Vela 編譯過的 NPU 版；沒有 `_vela` 的是原始 int8 模型，給 CPU 跑。

## 1. 單一模型：NPU vs CPU

以手骨模型為例（其他兩個換檔名即可）：

```bash
# NPU (Ethos-U65)
$B --graph=hand_landmark_new_256x256_integer_quant_vela.tflite --external_delegate_path=/usr/lib/libethosu_delegate.so --num_runs=50 --warmup_runs=5

# CPU (Cortex-A55，2 執行緒 = 兩顆核心全用)
$B --graph=hand_landmark_new_256x256_integer_quant.tflite --num_threads=2 --num_runs=50 --warmup_runs=5

# CPU 單執行緒 (對照用)
$B --graph=hand_landmark_new_256x256_integer_quant.tflite --num_threads=1 --num_runs=50 --warmup_runs=5
```

**怎麼看輸出**（最後幾行）：

```text
INFO: count=50 first=... curr=... min=51536 max=56574 avg=54074.1 std=1042 ...
INFO: Inference timings in us: Init: ..., First inference: ..., Warmup (avg): ..., Inference (avg): 54074.1
INFO: Memory footprint delta from the start of the tool (MB): init=... overall=...
```

| 欄位 | 意思 |
| --- | --- |
| `Inference (avg)` | **每次推論平均時間（微秒）**，54074 us = 54.1 ms。報告用這個 |
| `min` / `max` / `std` | 最快、最慢、標準差，看穩不穩定 |
| `First inference` | 第一次推論（含初始化），通常比較慢，不算在平均裡 |
| `Memory footprint` | 大約用了多少記憶體（MB） |

推論時間換算成每秒幾次：`1000 / 毫秒`，例如 9.5 ms ≈ 105 次/秒。

## 2. 三個模型一次量完（NPU + CPU）

整段複製貼上即可（一行很長，是同一行）：

```bash
for m in hand_detect_20000_quant hand_landmark_new_256x256_integer_quant detect_ssdmobilenetv3_quant; do npu=$($B --graph=${m}_vela.tflite --external_delegate_path=/usr/lib/libethosu_delegate.so --num_runs=50 --warmup_runs=5 2>&1 | sed -n 's/.*Inference (avg): \([0-9.]*\).*/\1/p'); cpu=$($B --graph=${m}.tflite --num_threads=2 --num_runs=50 --warmup_runs=5 2>&1 | sed -n 's/.*Inference (avg): \([0-9.]*\).*/\1/p'); echo "$m  NPU $npu us  CPU $cpu us  speedup $(python3 -c "print(round($cpu/$npu,1))")x"; done
```

約 30 秒跑完（CPU 比較久），輸出例如：

```text
hand_detect_20000_quant  NPU 9852.21 us  CPU 70656.1 us  speedup 7.2x
```

## 3. 有多少運算在 NPU 上跑

Vela 編譯時的報告已經放在 `models/*_vela_report.txt`：

```bash
grep -E "NPU operators|CPU operators|Total SRAM used|Batch Inference time" *_vela_report.txt
```

- `NPU operators = 171 (97.7%)`：97.7% 的運算在 NPU。留在 CPU 的通常是偵測模型的後處理（`TFLite_Detection_PostProcess`）和輸入輸出的量化轉換。
- `Total SRAM used`：NPU 用的 SRAM（i.MX93 有 384 KB 可用，三個模型都在範圍內，所以可以輪流跑）。
- `Batch Inference time`：Vela 在編譯時**預估**的時間，可以跟第 1 節的實測對照。

也可以在板子上重新編譯看報告（會在目前資料夾產生 `output/`）：

```bash
vela hand_landmark_new_256x256_integer_quant.tflite
```

## 4. 整個程式實際跑起來的速度（FPS）

模型 benchmark 只量推論；實際 app 還有讀鏡頭、畫圖、串流。直接看 `hand_cam.py` 的 FPS：

```bash
cd /root/edge-gesture-control/board
python3 hand_cam.py --port 0                    # NPU；終端機每 2 秒印一行 FPS / det / lmk / per (ms)
python3 hand_cam.py --port 0 --delegate cpu     # 同一支程式改用 CPU
```

- `--port 0` = 不開串流，只量辨識本身；要看畫面就拿掉，開 `http://192.168.7.2:8080`（畫面左上角也有 FPS）。
- 手放在鏡頭前，量 20 秒以上看穩定值；按 Ctrl+C 結束。
- 終端機欄位：`FPS` 整體幀率、`det` 手部偵測 ms、`lmk` 手骨 ms、`per` 人物偵測 ms。

## 5. 系統資源

另開一個 SSH 視窗，在第 4 節跑的同時觀察：

```bash
top -d 1                                          # 按 1 看每顆核心；CPU 模式時 python3 會吃滿
cat /sys/class/thermal/thermal_zone*/temp         # 晶片溫度 (除以 1000 = °C)
free -m                                           # 記憶體
```

## 6. 目前的結果（2026-09-19，TFLite 2.19，50 次平均）

| 模型 | NPU | CPU（2 執行緒） | 加速 | NPU 運算比例 |
| --- | --- | --- | --- | --- |
| 手部偵測 `hand_detect_20000_quant` | 9.9 ms | 70.7 ms | 7.2× | 98.4% |
| 手骨 21 點 `hand_landmark_new_256x256_integer_quant` | 9.5 ms | 54.1 ms | 5.7× | 97.7% |
| 人物偵測 `detect_ssdmobilenetv3_quant` | 8.6 ms | 54.4 ms | 6.3× | 98.4% |
| `hand_cam.py` 整體（640×480，1 隻手，含串流） | 約 32～35 FPS | 尚未量 | | |

量到新數字請更新這張表和 README 的「效能紀錄」。

## 7. demo 全開時的效能分析（`board/perf_monitor.py`）

手勢、人物偵測、語音全部開著，照 demo 的方式操作，同時每秒記錄整台板子的狀態，結束時印出摘要。

**① 照平常開好 demo**（順序見 README 指令區），唯一不同：`hand_cam.py` 要加 `-u` 並用 `tee` 存一份 log：

```bash
# 板子 SSH #1：手勢 (log 給 perf_monitor 讀 FPS 和推論時間)
cd /root/edge-gesture-control/board
python3 -u hand_cam.py --mqtt 192.168.7.1 --mqtt-hz 30 | tee /tmp/hand_cam.log
# 板子 SSH #2：語音喚醒詞
MIC_GAIN=13 MQTT_HOST=192.168.7.1 sh /root/edge-gesture-control/board/voice/run_voice.sh
# 板子 SSH #3：聲音送 PC
python3 /root/edge-gesture-control/board/audio_stream.py --device c270
# PC：broker、gesture_control.py、voice_app.py --mqtt-wake (見 README)
```

**② 板子 SSH #4：開始量**（量測期間照 demo 腳本操作：移動游標、點擊、捲動、喊 Hey NXP 下指令）

```bash
cd /root/edge-gesture-control/board
python3 perf_monitor.py -d 120 --csv /tmp/perf.csv      # 量 120 秒；Ctrl+C 可提早結束
```

**③ 看摘要**（例：2026-09-19，hand_cam `--port 0` + 語音 + audio_stream，沒有手在畫面上）

```text
項目                               平均       最低       最高
CPU 整體 %                       64.9     59.3     97.7
晶片溫度 °C                        50.7     49.9     51.4
hand_cam FPS                   31.7     26.5     33.5
NPU 每幀推論 ms (推算)           12.7     11.1     21.5
NPU 忙碌 % (推算)                40.3     31.4     66.6

程式                     CPU% 平均   CPU% 最高    記憶體 MB   (CPU% 以一顆核心 = 100%)
hand_cam                  96.1     103.0     131.8
VIT voice_ui_app           7.2       8.7       5.0
AFE afe                    4.2       4.8       5.2
audio_stream               0.4       1.9      12.9
```

| 欄位 | 怎麼解讀 |
| --- | --- |
| CPU 整體 % | 兩顆 Cortex-A55 的平均；長時間 > 90% 代表 CPU 是瓶頸 |
| 程式 CPU% | 以一顆核心 = 100%，所以最高可到 200%。hand_cam 約一整顆：讀鏡頭、轉色彩、縮放、畫圖、JPEG 串流都在 Python / CPU |
| NPU 忙碌 %（推算） | FPS × 每幀推論 ms / 1000。i.MX93 的 NPU 由 Cortex-M33 透過 rpmsg 驅動，系統沒有 NPU 使用率計數，只能用 `hand_cam.py` 印出的推論時間推算 |
| usb0 送 KB/s | 板子 → PC：MQTT (手勢資料) + 聲音 (約 32 KB/s) + MJPEG 串流 |
| 晶片溫度 | 這塊板子的保護門檻：115°C 開始降頻（passive）、125°C 強制關機（critical）；平常 50°C 左右 |

- `/tmp/perf.csv` 是每秒的原始數據，可以 `scp root@192.168.7.2:/tmp/perf.csv .` 拿回 PC 用 Excel 畫圖。
- PC 端（Whisper / LLM）不在這裡量：看 `voice_app.py` 印的 `[辨識 x.xxxs]`（語音轉文字時間），GPU / CPU 用 Windows 工作管理員的「效能」分頁。

## 8. eIQ AI Hub 對照（雲端）

<https://eiq.nxp.com/ai-hub/dashboard> 的 **Benchmark → Latency**：選模型 → 裝置 **i.MX 93** → BSP 版本 → 後端 CPU 或 NPU → 送出，到 **Task List** 看結果。可以跟第 6 節的實測對照。
