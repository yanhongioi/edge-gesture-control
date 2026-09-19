# Edge Gesture Control 操作手冊（Lin）

這份手冊以目前程式碼為準，整理 Windows PC、FRDM-i.MX93 板子、手勢控制、板端音訊、語音助理、MQTT 與常用監聽方式。

## 1. 固定環境與位址

| 項目 | 預設值 |
| --- | --- |
| PC 專案路徑 | `C:\Users\user\Desktop\chenlin\NTHU\hackthon\edge-gesture-control` |
| 板端專案路徑 | `/root/edge-gesture-control` |
| PC USB 網卡 IP | `192.168.7.1` |
| 板子 USB IP | `192.168.7.2` |
| MQTT broker | PC 的 `1883` 埠 |
| 手勢 topic | `edge/hand` |
| 板端 VIT topic | `edge/voice` |
| 攝影機串流 | `http://192.168.7.2:8080` |
| 板端音訊 TCP | `192.168.7.2:8765` |
| Ollama API | `127.0.0.1:11434` |

如果改走手機熱點，板子的 `mlan0` IP 會由 DHCP 重新分配，不能假設每次都相同。USB 直連則固定使用 `192.168.7.1/192.168.7.2`。

PC 指令預設都在專案根目錄執行：

```powershell
cd "C:\Users\user\Desktop\chenlin\NTHU\hackthon\edge-gesture-control"
```

板端指令預設使用 `root`，並在下列目錄執行：

```bash
cd /root/edge-gesture-control
```

## 2. 第一次使用或環境檢查

### 2.1 PC Python 套件

本專案在 Windows 使用 Python 3.11：

```powershell
py -3.11 -m pip install -r .\pc\requirements.txt
py -3.11 --version
```

### 2.2 Ollama 與模型

先確認 Ollama 已啟動並有可用模型：

```powershell
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/tags | ConvertTo-Json -Depth 5
```

程式在 `LLM_MODEL=auto` 時優先使用 `qwen3:4b-instruct`，沒有才使用 `qwen3:4b`。需要下載時：

```powershell
ollama pull qwen3:4b-instruct
```

臨時指定模型只影響目前 PowerShell 視窗：

```powershell
$env:LLM_MODEL = "qwen3:4b-instruct"
```

### 2.3 Mosquitto

確認執行檔存在：

```powershell
Test-Path "C:\Program Files\mosquitto\mosquitto.exe"
Test-Path ".\pc\mosquitto.conf"
```

如果 Windows 的 Mosquitto 服務占用 `1883`，請用系統管理員 PowerShell 停止服務：

```powershell
Get-Service mosquitto
Stop-Service mosquitto
Set-Service mosquitto -StartupType Manual
```

## 3. 每次完整啟動流程

建議開啟數個 PowerShell／SSH 視窗，每個長時間執行的程式各占一個視窗。

### 步驟 1：板子開機與網路

從序列埠登入板子後執行：

```bash
sh /root/edge-gesture-control/board/bringup.sh
ip -4 -br addr
```

預期至少看到：

```text
usb0    UP    192.168.7.2/24
```

`mlan0` 是 Wi-Fi／手機熱點 IP；`usb0` 的 `192.168.7.2` 是 USB 直連 SSH IP。

PC 端確認連線：

```powershell
ping 192.168.7.2
ssh root@192.168.7.2
```

### 步驟 2：啟動 MQTT broker（PC 視窗 1）

```powershell
cd "C:\Users\user\Desktop\chenlin\NTHU\hackthon\edge-gesture-control"
& "C:\Program Files\mosquitto\mosquitto.exe" -v -c ".\pc\mosquitto.conf"
```

這個視窗保持開啟。`-v` 顯示連線、訂閱及發布紀錄，本身也是 broker 監聽視窗。

### 步驟 3：啟動板端手勢辨識（板端 SSH 視窗 1）

```bash
cd /root/edge-gesture-control/board
python3 hand_cam.py --mqtt 192.168.7.1 --mqtt-hz 30
```

瀏覽器查看攝影機與辨識畫面：

```text
http://192.168.7.2:8080
```

只測 NPU、不需要網頁畫面時，可關閉 HTTP 串流降低負載：

```bash
python3 hand_cam.py --mqtt 192.168.7.1 --mqtt-hz 30 --port 0
```

### 步驟 4：先監看手勢資料（PC 視窗 2，可選）

```powershell
py -3.11 .\pc\hand_listener.py
```

它只顯示 `edge/hand` 的 FPS、手勢、捏合比例與座標，不會控制滑鼠。

### 步驟 5：啟動手勢控制（PC 視窗 3）

第一次先用預覽模式：

```powershell
py -3.11 .\pc\gesture_control.py --dry-run
```

確認手勢穩定後，按 `Ctrl+C` 停止，再真正控制：

```powershell
py -3.11 .\pc\gesture_control.py
```

常用手勢：

| 手勢 | 動作 |
| --- | --- |
| 張開手掌 `open` | 待命／協助重新找到手 |
| 食指指出 `point` | 移動游標 |
| `point` 時拇指碰食指 | 點擊；持續捏住並移動可拖曳 |
| 食指與中指 `two` | 捲動 |
| 握拳 `fist` | 不執行動作 |

### 步驟 6：啟動板端 VIT（板端 SSH 視窗 2）

這一步會安裝目前板端的 ALSA 設定，並將 VIT 的喚醒結果發布到 `edge/voice`：

```bash
MQTT_HOST=192.168.7.1 sh /root/edge-gesture-control/board/voice/run_voice.sh
```

看到提示後可說 `Hey NXP`。此視窗保持開啟。

### 步驟 7：啟動板端音訊串流（板端 SSH 視窗 3）

先啟動上一步，讓 `/etc/asound.conf` 中的 `c270` 裝置準備完成，再執行：

```bash
python3 /root/edge-gesture-control/board/audio_stream.py --device c270
```

預期輸出：

```text
audio: c270 -> 0.0.0.0:8765 (16000 Hz, mono, S16_LE)
```

### 步驟 8：啟動語音助理（PC 視窗 4）

先使用預覽模式，不執行搜尋、音樂或計時器：

```powershell
py -3.11 -m pc.voice_app --board-audio 192.168.7.2 --mqtt-wake 127.0.0.1
```

確認辨識正常後，改成執行模式：

```powershell
py -3.11 -m pc.voice_app --board-audio 192.168.7.2 --mqtt-wake 127.0.0.1 --execute
```

如果沒有 NVIDIA CUDA 環境，使用 CPU：

```powershell
py -3.11 -m pc.voice_app --board-audio 192.168.7.2 --mqtt-wake 127.0.0.1 --execute --asr-device cpu --compute-type int8
```

使用 `--mqtt-wake` 時，由板端 VIT 判斷 `Hey NXP`。建議先說喚醒詞，看到 PC 顯示「已喚醒」後，在十秒內說指令：

偵測到喚醒詞後，畫面右下角會立即顯示無邊框的動態波形 icon，並直接開始接收指令，不播放喚醒回應。等待指令期間，如果系統音量高於 30%，會暫時降到 30%；收到完整指令或等待逾時後，立即還原原本音量。可用 `--listening-volume 35` 修改等待時的音量上限。

動作型指令會先執行工具，再朗讀 reply，以免瀏覽器、音樂、捲動或計時器等待 TTS 播放完才開始。一般回答與追問沒有工具動作，仍只會朗讀 reply。

```text
你：Hey NXP
PC：已喚醒
你：幫我播放韓文歌
```

沒有加 `--mqtt-wake` 時，PC 會把每一句送進 Whisper，並以轉錄文字句首的 `NXP` 作為軟體喚醒詞，例如「NXP，幫我找雞胸肉食譜」。

助理詢問必要資訊時，10 秒內可以直接補充，不必再說一次喚醒詞。例如：

```text
你：NXP，幫我設定計時器
助理：請告訴我要計時多久
你：十分鐘
```

播放控制會以本次 `voice_app` 工作階段最近成功開啟的 YouTube 音樂或影片為主：

```text
Hey NXP → 暫停
Hey NXP → 播放
Hey NXP → 繼續播放
```

如果本次工作階段還沒有成功播放任何媒體，助理會回答「目前沒有正在播放的音樂喔主人」，不會送出系統媒體鍵。

頁面捲動支援三段距離，以及直接前往頁面頂端／底端：

| 說法 | 動作 |
| --- | --- |
| `往上`／`往下` | 約四格 |
| `往上一點`／`往下一點` | 約兩格 |
| `往上一點點`／`往下一點點` | 約一格 |
| `最上面`／`最下面` | `Ctrl+Home`／`Ctrl+End` |

音量控制：

| 說法 | 動作 |
| --- | --- |
| `大聲點`／`小聲點` | 較大幅度調整 |
| `大聲一點`／`小聲一點` | 中等幅度調整 |
| `大聲一點點`／`小聲一點點` | 最小幅度調整 |
| `閉嘴` | 不回話，直接把音量降到 0 |
| `太小聲囉`／`太小聲喽` | 音量升到 100，回答「是的船長!」 |
| `幫我把音量調到 50` | 設定成指定的 0–100 音量 |

指定 TTS 聲音與語速：

```powershell
py -3.11 -m pc.voice_app --board-audio 192.168.7.2 --mqtt-wake 127.0.0.1 --execute --tts-voice "Microsoft Hanhan Desktop" --tts-rate 1
```

不要語音 reply：

```powershell
py -3.11 -m pc.voice_app --board-audio 192.168.7.2 --mqtt-wake 127.0.0.1 --execute --no-tts
```

## 4. 各種監聽與診斷指令

### 4.1 監聽全部 MQTT topic

```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h 127.0.0.1 -p 1883 -t "edge/#" -v
```

只監聽手勢：

```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h 127.0.0.1 -p 1883 -t "edge/hand" -v
```

只監聽板端喚醒詞與 VIT 指令：

```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h 127.0.0.1 -p 1883 -t "edge/voice" -v
```

發布測試訊息並確認 broker 工作正常：

```powershell
& "C:\Program Files\mosquitto\mosquitto_pub.exe" -h 127.0.0.1 -p 1883 -t "edge/test" -m '{"ok":true}'
```

### 4.2 檢查 PC 監聽埠

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 1883,11434
Get-NetTCPConnection -LocalPort 1883 -State Listen
Test-NetConnection 127.0.0.1 -Port 1883
```

檢查板端 HTTP 與音訊 TCP：

```powershell
Test-NetConnection 192.168.7.2 -Port 8080
Test-NetConnection 192.168.7.2 -Port 8765
```

注意：`8765` 同一時間只服務一個音訊 client。用 `Test-NetConnection` 檢查後會立即斷線，不要長時間占用。

### 4.3 檢查 PC 行程與 GPU

```powershell
Get-Process mosquitto,ollama,python -ErrorAction SilentlyContinue
nvidia-smi
```

列出錄音裝置：

```powershell
py -3.11 -m pc.voice_app --list-devices
```

### 4.4 檢查板端網路與監聽埠

```bash
ip -4 -br addr
wpa_cli -i mlan0 status
ss -lntp | grep -E ':8080|:8765'
ping -c 2 192.168.7.1
ping -c 2 8.8.8.8
```

檢查板端相關行程：

```bash
ps | grep -E 'hand_cam|audio_stream|voice_ui_app|afe' | grep -v grep
```

檢查 NPU、攝影機與麥克風：

```bash
ls -l /dev/ethosu0
v4l2-ctl --list-devices
arecord -l
```

直接錄五秒鐘測試 C270 麥克風：

```bash
arecord -D plughw:CARD=WEBCAM,DEV=0 -f S16_LE -r 16000 -c 1 -d 5 -V mono /tmp/mic_test.wav
ls -lh /tmp/mic_test.wav
```

### 4.5 獨立測試 LLM 與工具

只看 LLM 計畫，不執行：

```powershell
py -3.11 -m pc.llm.test_ollama "幫我找雞胸肉食譜"
py -3.11 -m pc.control.app "幫我播放韓文歌"
```

文字指令實際執行：

```powershell
py -3.11 -m pc.control.app "幫我找雞胸肉食譜" --execute
py -3.11 -m pc.control.app "幫我播放韓文歌" --execute
py -3.11 -m pc.control.app "幫我計時五秒" --execute
```

### 4.6 獨立測試 TTS 與計時器音效

```powershell
py -3.11 -m pc.speech.windows_tts --voice "Microsoft Hanhan Desktop" --rate 1 "語音回覆測試成功。"
py -3.11 -c "from pc.control.timer import start_timer; start_timer(5, '音效測試')"
```

計時結束會循環播放 `pc/control/assets/timer_sound.mp3`，按「關閉」後停止。

## 5. 建議的完整 Demo 測試順序

1. Broker 視窗看到板子連線。
2. `hand_listener.py` 能持續看到 `edge/hand` 資料。
3. 瀏覽器能開啟 `http://192.168.7.2:8080`。
4. `gesture_control.py --dry-run` 正確顯示 point、pinch、two。
5. 取消 `--dry-run`，測試移動、點擊、拖曳和捲動。
6. `edge/voice` 監聽視窗能在說 `Hey NXP` 後收到 wakeword JSON。
7. `audio_stream.py` 顯示 PC client 已連線。
8. `voice_app` 能顯示 Whisper 文字與 JSON 計畫。
9. 使用 `--execute` 測試搜尋、YouTube 播放與五秒計時器。
10. 測試助理追問：「設定計時器」→ 直接回答「十分鐘」。

## 6. 正常停止與關機

依序停止可以避免滑鼠按鍵卡住或板端行程殘留：

1. PC 的 `voice_app` 按 `Ctrl+C`。
2. PC 的 `gesture_control.py` 按 `Ctrl+C`；程式會釋放滑鼠左鍵。
3. 板端 `audio_stream.py` 按 `Ctrl+C`。
4. 板端 `hand_cam.py` 按 `Ctrl+C`。
5. 板端停止 VIT：

```bash
sh /root/edge-gesture-control/board/voice/run_voice.sh stop
```

6. PC 的 MQTT broker 按 `Ctrl+C`。
7. 要關閉板子時執行：

```bash
poweroff
```

看到系統停止後等待約十秒，再拔除電源。

## 7. 常見問題

### PowerShell 說 `-v` 或 `-c` 是未預期的語彙基元

含空白的執行檔路徑前要加 PowerShell 呼叫運算子 `&`：

```powershell
& "C:\Program Files\mosquitto\mosquitto.exe" -v -c ".\pc\mosquitto.conf"
```

### MQTT 顯示 Connection refused

先確認 broker 視窗仍開著：

```powershell
Get-NetTCPConnection -LocalPort 1883 -State Listen
```

### Broker 無法綁定 1883

通常是 Mosquitto Windows 服務已占用：

```powershell
Get-Service mosquitto
Get-NetTCPConnection -LocalPort 1883 -State Listen | Select-Object OwningProcess
```

### SSH 連不到板子

在序列埠重新執行：

```bash
sh /root/edge-gesture-control/board/bringup.sh
ip -4 -br addr
```

並確認 A-to-C 線連到板子的 `USB1_C`。

### 語音助理連不到 8765

先在板端啟動：

```bash
python3 /root/edge-gesture-control/board/audio_stream.py --device c270
```

再從 PC 檢查：

```powershell
Test-NetConnection 192.168.7.2 -Port 8765
```

### CUDA 或 Whisper 載入失敗

先改用 CPU 驗證整條流程：

```powershell
py -3.11 -m pc.voice_app --board-audio 192.168.7.2 --asr-device cpu --compute-type int8
```

### 指令有辨識到但沒有真的執行

確認啟動命令包含 `--execute`。沒有它時是安全預覽模式。

### 語音取消計時器

計時器作用中時，可以在喚醒助理後說「取消計時器」、「停止計時器」或「關掉計時器」。程式會關閉最近啟動且仍在執行的計時器視窗。

### 板端 Wi-Fi 失敗

```bash
killall wpa_supplicant
sleep 2
rm -f /var/run/wpa_supplicant/mlan0
ip link set mlan0 down
ip link set mlan0 up
sh /root/edge-gesture-control/board/bringup.sh
```

## 8. 更新板端檔案

只傳一個檔案：

```powershell
scp .\board\hand_cam.py root@192.168.7.2:/root/edge-gesture-control/board/
```

傳送整個 `board` 資料夾時，依 repo 現有部署方式操作；傳完後務必重新啟動對應的板端程式，舊行程不會自動載入新程式碼。
