# edge-gesture-control

梅竹黑客松 × NXP FRDM-i.MX93：不用碰電腦就能控制電腦。
- **反射層**：板子用 NPU 即時辨識手勢，反應在毫秒級。
- **認知層**：PC 用本地 LLM 處理語音指令，反應在秒級。

| 文件 | 內容 |
| --- | --- |
| `README.md`（本頁） | 專案概覽、**指令區（每天用的指令）**、板子目前狀態與進度 |
| [docs/setup.md](docs/setup.md) | 從零開始的上板教學：接線、登入、網路、跑模型、疑難排解 |
| [docs/plan.md](docs/plan.md) | 專案構想、分工、demo 腳本 |

## 目錄結構

```text
edge-gesture-control/
├── board/                    # 跑在 FRDM-i.MX93 上（整個資料夾 scp 到板子）
│   ├── hand_cam.py           # C270 → 手部偵測 + 21 點骨架 + 手勢 + 人物定位 → HTTP 串流 / HDMI / MQTT
│   ├── gesture.py            # 從 21 點判斷手勢（10 種）和捏合，附連續幀確認（隊友的分支 gesture-classification）
│   ├── bringup.sh            # 開機後一行設定好網路（USB 直連 + Wi-Fi）
│   ├── servo.py              # MG996R 伺服馬達（雲台）：pin 33 硬體 PWM，用角度控制
│   ├── voice/                # 語音：NXP AFE + VIT 喚醒詞 / 指令（C270 麥克風），結果送 MQTT edge/voice
│   ├── usb_net.sh            # 把 USB1_C 設成 USB 網卡，讓筆電直連 (192.168.7.2)
│   ├── models/               # 手部、人物模型（原始 + Vela 編譯版 + Vela 報告）
│   └── test_images/          # 單張圖片測試用（張開手掌、握拳、指東西）
├── pc/                       # 跑在 Windows 筆電
│   ├── hand_listener.py      # 接收板子送來的資料並顯示 (MQTT edge/hand)，除錯用
│   ├── gesture_control.py    # 收到手勢就控制這台電腦（游標、捲動、播放/暫停、切視窗、音量）
│   ├── control/hotkeys.py    # Windows 按鍵注入（SendInput）：媒體鍵、Alt+Tab、音量
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

**分支規則**：`board/gesture.py`（手勢辨識）主要由隊友在 `gesture-classification` 分支開發。要改辨識邏輯時，先在那個分支上改、推上去，再合進 `main`，這樣兩邊的 `gesture.py` 一致，之後不容易衝突：
```powershell
git switch gesture-classification; git pull
# 修改、commit
git push
git switch main; git merge gesture-classification
```

## 指令區

第一次設定（接線、網路、安裝）請看 [docs/setup.md](docs/setup.md)。以下是設定完成之後，每天會用到的指令。

> 筆電上的 Python 請一律用 **`py -3.11`**。`python3` 或 `python` 可能會跑到 MSYS2 的 Python，那裡沒有我們裝的套件。
> 板子上的 Python 用 `python3` 沒問題。

### 每次開工（依序執行）

需要 4 個視窗：序列埠（VS Code Serial Monitor）、筆電 PowerShell × 3。

**① 板子開機 → 序列埠（COM11，115200）登入 `root`，設定網路**
```bash
sh /root/edge-gesture-control/board/bringup.sh
```
- 會看到 `usb : OK ...`（或 `already up`）；需要上網時，Wi-Fi 那一行要有 IP。
- 做完之後，DEBUG 線可以拔掉，SSH 走的是 USB 直連。

**② 筆電 PowerShell #1：啟動 MQTT broker（保持開著）**
```powershell
cd C:\Users\user\Desktop\NXP\edge-gesture-control
& "C:\Program Files\mosquitto\mosquitto.exe" -v -c .\pc\mosquitto.conf
```

**③ 筆電 PowerShell #2：啟動接收端**
```powershell
cd C:\Users\user\Desktop\NXP\edge-gesture-control
py -3.11 .\pc\hand_listener.py
```

　（要用手勢控制電腦時，把 ③ 換成下面這行，或再開一個視窗同時跑）
```powershell
py -3.11 .\pc\gesture_control.py              # 加 --dry-run 只印出動作、不真的控制
```

**④ 筆電 PowerShell #3：SSH 進板子，開始辨識**
```powershell
ssh root@192.168.7.2
```
```bash
cd /root/edge-gesture-control/board
python3 hand_cam.py --mqtt 192.168.7.1 --mqtt-hz 30
```
`--mqtt-hz 30`：每秒送 30 次（預設 15 次），游標才會順。

**⑤ 瀏覽器看畫面**：http://192.168.7.2:8080

站遠一點時，先把**手掌張開、舉到臉旁邊**，讓系統找到手（出現青色 `track` 框），再比其他手勢。

結束時：在 ④ 按 `Ctrl+C`；要關機的話在板子上執行 `poweroff`，等 10 秒再拔電。

### 手勢控制（`pc/gesture_control.py`）

| 手勢 | 動作 |
| --- | --- |
| `point`（手槍姿勢：食指指出去，**拇指立起來**） | 游標跟著食指移動（預設跟第一節關節 `pip`，比指尖穩） |
| `point` + **拇指壓下去碰食指側邊**（扣扳機 = 捏合） | 按住左鍵。快速捏一下 = 點擊；捏住不放再移動 = 拖曳；拇指立起來 = 放開 |
| `two`（食指 + 中指） | 捲動：**拇指捏下 = 往上捲，拇指放開 = 往下捲**。基本速度每秒 1 格；手再往捲動的方向推離起點（換方向時重算）越遠，捲得越快，最快每秒 10 格。手或手勢短暫不見 0.5 秒以內，會用原本的速度繼續捲，起點不重算。**一比出 `two` 就會開始捲，沒有「停在原地」的狀態** —— 要停就別比 `two` |
| `open` + **捏合** | 播放/暫停（送媒體鍵 `VK_MEDIA_PLAY_PAUSE`）。張開手掌 → 捏一下 → 放開就能再捏一次 |
| `four`（四指伸直，**拇指收攏貼手掌**） | 切換視窗（Alt+Tab，切回上一個視窗） |
| `rock` + **捏合** / `rock`（食指 + 小指） | 音量加大 / 減小。**穩定比著 1 秒才開始**，之後每 0.25 秒一階（約每秒 8%） |
| `open`（不捏） | 不動作（起手式：站遠時先張開手掌舉到臉旁邊，讓系統找到手；同時是快捷鍵的「中立姿勢」） |
| `fist` | **永遠不動作**（拿刀、拿鍋鏟時的手） |
| 其他（`three`、`six`、`ok`、`thumbs_up`） | 還沒對應，`--action-map` 可以自己綁 |

```powershell
py -3.11 .\pc\gesture_control.py --anchor tip         # 游標改跟食指尖（預設 pip 第一節關節；mcp = 食指根部，更穩）
py -3.11 .\pc\gesture_control.py --prefreeze 0.45     # 拇指離食指還比較遠時就先鎖住游標（預設 0.40；0 = 關閉）
py -3.11 .\pc\gesture_control.py --region 0.3         # 手移動更小的範圍就能走遍整個螢幕（預設 0.4）
py -3.11 .\pc\gesture_control.py --center-y 0.4       # 對應範圍往上移（手習慣舉高一點時）
py -3.11 .\pc\gesture_control.py --deadband 10        # 手想停住時游標更不會動（預設 6 px）
py -3.11 .\pc\gesture_control.py --min-cutoff 0.3     # 游標更穩但更黏（預設 0.5）
py -3.11 .\pc\gesture_control.py --beta 0.1           # 快速移動時更跟手（預設 0.05）
py -3.11 .\pc\gesture_control.py --press-settle 0.3   # 捏下後停久一點，點擊比較不會變成拖曳（預設 0.2 秒）
py -3.11 .\pc\gesture_control.py --no-click           # 關掉捏合點擊，只移動游標
py -3.11 .\pc\gesture_control.py --scroll-base 240    # 基本捲動速度快一點（預設 120 = 每秒 1 格）
py -3.11 .\pc\gesture_control.py --scroll-gain 6000   # 推離起點時加速更多（預設 3000）；--scroll-invert 上下反過來
py -3.11 .\pc\gesture_control.py --scroll-hold 1.0    # 掉幀時繼續捲久一點（預設 0.5 秒）
py -3.11 .\pc\gesture_control.py --action-hold 1.5    # 音量要比更久才開始調（預設 1.0 秒）
py -3.11 .\pc\gesture_control.py --action-repeat 0.4  # 音量調慢一點（預設 0.25 秒一階）
py -3.11 .\pc\gesture_control.py --action-map "open+pinch=play_pause,four=alt_tab,rock+pinch=volume_up,rock=volume_down"
                                                      # 自己綁快捷鍵（整組覆寫；動作清單見 --help）
py -3.11 .\pc\gesture_control.py --no-actions         # 只留游標和捲動，關掉所有快捷鍵
py -3.11 .\pc\gesture_control.py --no-mirror          # 板子有加 --mirror 時要加
py -3.11 .\pc\gesture_control.py --broker <IP>        # broker 在別台電腦
```

設計重點：
- **為什麼用捏合點擊**：舊的做法要「張開整隻手再收回」，手的形狀大改兩次，指尖一定會偏，很難點準。捏合只動拇指，食指不動，所以點得準；`point` 的分類本來就不看拇指，捏合時手勢仍然是 `point`，游標不會中斷。
- **捏合判斷在板子上**：`board/gesture.py` 的 `PinchDetector` 看拇指指尖到食指第一節的距離相對於手掌大小的比例（`pinch_ratio`），小於 0.25 連續 2 幀算捏下，大於 0.35 連續 2 幀算放開。**這兩個門檻是初始值，要用真實鏡頭校準**：`pc/hand_listener.py` 每一行的 `pinch no (0.58)`，括號裡就是比例。比出「拇指立起來」「拇指放輕鬆」「拇指壓下」各看一下數字，再調整 `gesture.py` 裡的 `PINCH_ON_RATIO` / `PINCH_OFF_RATIO`（要改 `gesture.py` 請照上面的分支規則），以及 `gesture_control.py` 的 `--prefreeze`。
- **防誤點**：
  - 一進游標模式就已經捏著（拇指自然貼著手指）不算，要先看到拇指立起來一次，捏合才有效。
  - 待機時捏合不動作。
- **預先鎖定（解決點擊時晃掉）**：拇指壓下的過程中，食指會被帶著晃。所以只要 `pinch_ratio` 一低於 `--prefreeze`（0.40，也就是拇指正在靠近食指、還沒捏到），游標就**先鎖住**，點擊發生在鎖住的位置。
  - 拇指抬回去（比例超過 0.45），或鎖住超過 1 秒還沒捏下，就會解鎖。
  - 模擬測試：壓下過程中食指偏移 3% / 6% 畫面寬時，沒有預先鎖定的點擊位置偏了 34 / 75 px（以 1000 px 寬的螢幕計），有預先鎖定則是 0 px。
  - **`--prefreeze` 要配合校準**：如果拇指放輕鬆時的比例本來就低於 0.40，游標會一直被鎖住，這時要把它調低。
- **按下後**：游標停 0.2 秒，這段時間內放開就是原地點擊。
- **游標要跟哪個點**（`--anchor`）：指尖在手指最末端，最會晃；`pip`（第一節關節）、`mcp`（根部）比較穩，拇指壓下時也比較不會被帶動。實測 `pip` 點擊比較準，所以是預設值。
- **為什麼捲動方向看拇指**：早期版本用「手指指向上 / 指向下」決定方向，要換方向得把手腕整個翻過來，而且手指朝下時偵測本來就比較難找到手。改看捏合之後，兩個方向都維持同一個舒服的手勢，換方向只動拇指。代價是沒有「停在原地」的狀態（拇指只有兩種狀態），要停就放下 `two`。
- **快捷鍵分兩類**（`pc/control/hotkeys.py`）：
  - **一次性**（播放/暫停、切視窗）：比出來只送一次，要先回到中立姿勢（手掌張開沒捏 / 握拳 / 手移出畫面）才能再觸發。沒有這道的話，捏著不放 = 音樂瘋狂 play/pause。
  - **連發**（音量）：Windows 音量一次按鍵只動 2%，不連發根本調不動。所以改成「穩定比著 1 秒才開始，之後每 0.25 秒一階」—— 前 1 秒完全不送按鍵，手在換姿勢過程中被判成 `rock` 幾幀不會誤調音量。音量上下可以直接互切（不用回中立），但切到一次性動作仍然要回中立。
- **播放/暫停不看前景視窗**：媒體鍵由 Windows 轉成 `WM_APPCOMMAND`，交給目前的媒體 session（SMTC），所以比手勢時焦點在哪都沒關係。副作用是同時開 YouTube 和 Spotify 時會送到「最後播放的那個」。
- **Alt+Tab 的四個按鍵事件放在同一次 `SendInput`**：Alt 按下去之後如果 Tab 沒送成，系統會停在「Alt 一直按著」的狀態，之後每個按鍵都變成選單快速鍵。一次送整個陣列是原子的，中間插不進別的輸入；程式結束時還會再補送一次「放開所有修飾鍵」。
  - 前景視窗如果是以**系統管理員**身分執行的，按鍵注入會被 UIPI 整個丟掉，而且沒有任何錯誤提示。這時 `gesture_control.py` 也要用系統管理員身分跑。
- **游標不再閃爍**：
  - 舊版只要某一幀手勢判斷閃一下，游標就會退回再跳回來，現在只看確認過的手勢。
  - 濾波參數重新挑過，另外加了 6 px 的不動區。
  - 模擬測試（骨架點抖動約 ±11 px）：靜止時每秒閃動從約 21 次降到約 4 次；快速移動（1500 px/s）的落後從約 21 px 降到約 3 px。實際效果要上板確認。
- **左鍵不會卡住**：手不見、換成其他手勢超過 0.3 秒、資料中斷、按 Ctrl+C，都會先放開。
- **對應方式**：鏡頭畫面**中央 40%** 對應整個主螢幕，預設左右翻轉；程式會使用實際像素座標，Windows 縮放 125% / 150% 時也對得準。
- **還沒做**：右鍵；裝上雲台後改用「手相對於人物框」的位置。

### 用另一台電腦接收 / 被控制

控制程式可以跑在任何連得到 broker 的電腦上。

| 做法 | 怎麼接 | 備註 |
| --- | --- | --- |
| **A. USB 線改插到那台電腦**（demo 推薦） | 那台電腦照 setup.md 3-D 設好 `192.168.7.1`，再照步驟 9 裝好 Mosquitto，指令都跟上面一樣 | 延遲最低 |
| B. 板子透過 Wi-Fi 送到那台電腦 | 那台電腦連同一個熱點、啟動 broker；板子改用 `--mqtt <那台電腦的 Wi-Fi IP>` | 會經過熱點，延遲大（實測 60～400 ms），不適合游標模式 |
| C. broker 留在原本的電腦，另一台去訂閱 | 另一台電腦跑 `gesture_control.py --broker <原本那台電腦的 Wi-Fi IP>` | 兩台可以同時收到資料；延遲跟 B 一樣 |

B、C 都要讓跑 broker 的電腦對 Wi-Fi 開放 1883 埠。我們的 broker 不需要帳密就能連線，所以**只在自己的手機熱點上這樣做**，不要在學校或比賽場地的公共 Wi-Fi 上開。系統管理員 PowerShell，`Wi-Fi` 換成你的無線網卡名稱：
```powershell
New-NetFirewallRule -DisplayName "MQTT 1883 (Wi-Fi, trusted hotspot only)" -Direction Inbound -Protocol TCP -LocalPort 1883 -InterfaceAlias "Wi-Fi" -Action Allow
Remove-NetFirewallRule -DisplayName "MQTT 1883 (Wi-Fi, trusted hotspot only)"    # 用完要記得關
```

### 改了程式之後

在筆電執行，位置在 repo 根目錄：

```powershell
# 整個 board/ 傳到板子（有新檔案、模型時用這個）
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_board.ps1

# 只改了一個檔案時
scp board/hand_cam.py root@192.168.7.2:/root/edge-gesture-control/board/

# 跟隊友同步
git pull                    # 先拿最新的
git add -A
git commit -m "說明這次改了什麼"
git push
```

### `hand_cam.py` 常用參數

| 指令 | 用途 |
| --- | --- |
| `python3 hand_cam.py` | 預設：NPU + 串流 :8080 |
| `python3 hand_cam.py --mqtt 192.168.7.1` | 同時把手部、手勢、人物資料送到筆電 |
| `python3 hand_cam.py --debug` | 畫出除錯用的框（被否決的偵測框、在人物附近找手的範圍） |
| `python3 hand_cam.py --display` | 在板子的 HDMI 螢幕 / 投影機上全螢幕顯示（`--windowed` = 小視窗；按 `q` 結束）。投影機要在**板子開機前**就打開並切到這個 HDMI 輸入，見下方已知問題 |
| `python3 hand_cam.py --image test_images/hand-1.jpg` | 單張圖片測試，結果存到 `output/` |
| `python3 hand_cam.py --delegate cpu` | 改用 CPU 跑（跟 NPU 比較，demo 用） |
| `python3 hand_cam.py --mirror` | 畫面左右翻轉 |
| `python3 hand_cam.py --no-track` / `--no-person-search` / `--person-every 0` | 關掉手部追蹤 / 人物附近找手 / 人物偵測（比較用） |
| `python3 hand_cam.py -h` | 所有參數 |

終端機每 2 秒印一行統計，欄位意思見下方「辨識流程」。

### 板子上常用

```bash
ip -4 addr show usb0 mlan0            # 看 USB 直連和 Wi-Fi 的 IP
cat /sys/class/udc/ci_hdrc.0/state     # USB 直連狀態，正常是 configured
ping -c 2 8.8.8.8                      # 能不能上網
ping -c 1 pypi.org                     # 網址解析 (DNS) 正不正常
v4l2-ctl --list-devices                # 鏡頭節點（C270 = /dev/video2）
ls /dev/ethosu0                        # NPU 在不在
python3 /root/edge-gesture-control/board/servo.py 90     # 雲台轉到 90 度（sweep = 掃一次；off = 放鬆）
arecord -l                             # 錄音裝置（C270 麥克風 = card WEBCAM）
arecord -D plughw:CARD=WEBCAM,DEV=0 -f S16_LE -r 16000 -c 1 -d 5 -V mono /tmp/mic_test.wav   # 錄 5 秒測試
python3 -m pip install <套件>          # 安裝 Python 套件（板子要能上網）
reboot / poweroff                      # 重開機 / 關機
```

Wi-Fi 卡住（`bringup.sh` 顯示 `not associated`，但熱點明明開著）時，依序執行：
```bash
killall wpa_supplicant
sleep 2; rm -f /var/run/wpa_supplicant/mlan0
ip link set mlan0 down; ip link set mlan0 up
sh /root/edge-gesture-control/board/bringup.sh
```

### 筆電上常用

```powershell
ping 192.168.7.2                                         # 連不連得到板子
Get-NetAdapter | Where-Object InterfaceDescription -like "*Ncm*"   # USB 直連網卡在不在
Get-NetTCPConnection -LocalPort 1883 -State Listen       # broker 有沒有在跑（沒有輸出 = 沒在跑）
Get-Service mosquitto                                    # 要是 Stopped；Running 會佔住 1883 埠
py -3.11 -m pip install <套件>                            # 安裝 Python 套件
```

| 狀況 | 處理 |
| --- | --- |
| `hand_listener.py` 出現 `No module named 'paho'` | 用了 `python3`，改用 `py -3.11` |
| `hand_listener.py` 出現 `ConnectionRefusedError` | broker 沒開，先做「每次開工」的 ② |
| `ssh` 連不上 | 板子重開過？到序列埠執行 `bringup.sh`；看 A 對 C 線是否接在筆電 USB-A ↔ 板子 `USB1_C` |
| `deploy_board.ps1` 無法執行 | 用上面的 `powershell -ExecutionPolicy Bypass -File ...` |

---

## 板子狀態

我們這塊 FRDM-i.MX93 目前的實際狀態。**有變動就更新這一節**。最後更新：2026-09-17

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
| `paho-mqtt` | 2.1.0 ✅（2026-09-17 用 pip 安裝；系統原本沒有，因為這不是官方的 MQTT image）|
| DNS | `/etc/resolv.conf` 原本連到 ConnMan 的本機 DNS 轉發器，但我們手動連的 Wi-Fi 不歸 ConnMan 管，所以網址解析失敗。已改成固定的 `8.8.8.8`、`1.1.1.1`（原連結備份在 `/etc/resolv.conf.connman-link`），`bringup.sh` 也會自動檢查 |
| ConnMan | 正在執行（`systemctl is-active connman` = active）。目前沒有用它管網路；如果 Wi-Fi 或 USB 網路又出現莫名的問題，考慮讓它忽略 `mlan0`、`usb0` |
| 我們的程式 | `/root/edge-gesture-control/board/` |
| 官方手部範例 | `/root/hand-demo/`（隊友放的 WPI 原版）。**裡面的 `_vela` 模型在 TFLite 2.19 無法載入**，請用 repo 裡修正過的版本 |

### 周邊與介面

| 介面 | 名稱 / 節點 | 備註 |
| --- | --- | --- |
| C270 鏡頭 | **`/dev/video2`**（影像）、`/dev/video3`（metadata，不能用） | 插在 USB-A 孔 |
| C270 麥克風 | ALSA 第 1 張卡 `WEBCAM`（`plughw:CARD=WEBCAM,DEV=0`） | 2026-09-18 測試錄音清楚。增益用 8（0～16；16 會爆音）。第 0 張 `mqsaudio` 是板子的音訊輸出 |
| 伺服馬達 MG996R（雲台） | 訊號 = 排針 **pin 33 = GPIO_IO13** = 硬體 PWM **`pwmchip1` channel 2**（2026-09-18 實測） | 50 Hz；500 µs = 0°、2500 µs = 180°（可用 `servo.py --min-us/--max-us` 校準）。GPIO_IO04、GPIO_IO12 在 `pwmchip0`（`424e0000.pwm`），也是 PWM 腳 |
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
| 排針 pin 33（GPIO_IO13） | MG996R 訊號線（橘） | 馬達電源（紅）接**獨立的 4 顆 3 號電池**（約 6 V），電池負極、馬達負極（棕）和板子 GND 在麵包板上共地。**電池正極絕對不能碰到板子**；MG996R 瞬間電流可到 1～2.5 A，不能從板子取電 |
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

1. 先打開手機熱點（要讓板子上網時才需要），USB 線保持接著，筆電這邊不用做任何設定。
2. 在序列埠（COM11）登入 `root`，輸入**這一行**：
   ```bash
   sh /root/edge-gesture-control/board/bringup.sh
   ```
   正常的輸出如下：
   ```text
   mode=ncm iface=usb0 ip=192.168.7.2/24 udc=ci_hdrc.0 state=configured
   wifi: 10.x.x.x/xx
   net : internet OK
   ```
3. 在筆電執行 `ssh root@192.168.7.2`，就可以開始工作了。

- 腳本會跳過已經連好的部分，重複執行也沒關係。
- 顯示 `usb : laptop NOT connected`：檢查 A 對 C 線是否接在筆電 USB-A ↔ 板子 `USB1_C`，拔掉重插一次。
- 顯示 `wifi: not associated` 但熱點明明有開（筆電也搜尋得到）：Wi-Fi 可能卡住了，照下面四行重設（在 USB SSH 裡執行，不會斷線）：
  ```bash
  killall wpa_supplicant
  sleep 2; rm -f /var/run/wpa_supplicant/mlan0
  ip link set mlan0 down; ip link set mlan0 up
  sh /root/edge-gesture-control/board/bringup.sh
  ```
  還是不行的話，就 `reboot` 重開板子。

`bringup.sh` 做的事情等同於以下指令，腳本有問題時可以手動一行一行輸入：
```bash
sh /root/edge-gesture-control/board/usb_net.sh
ip link set mlan0 up
wpa_supplicant -B -i mlan0 -D nl80211 -c /etc/wpa_hotspot.conf
udhcpc -i mlan0
```

### 辨識流程（`board/hand_cam.py`，每一幀）

```text
C270 640×480
 ├─ 人物偵測（每 5 幀一次）→ 挑最大的人 → 人物框、中心點、dx（之後給雲台用）
 ├─ 手部追蹤（上一幀有手時）
 │    裁切框 = 上一幀骨架範圍 ×1.8 + 移動速度預測 → 骨架模型
 │    骨架分數 ≥ 0.55 → 繼續追；低於 0.55 → 算跟丟
 └─ 手部偵測（只在沒追到手時）
      一幀看全畫面、一幀看人物上半身附近，兩者輪流
      分數 ≥ 0.55 的框，最多試 2 個 → 骨架模型，分數 ≥ 0.7 才採用 → 開始追蹤
 └─ 手勢（每隻手、每幀）：gesture.py 用手指關節角度判斷 open / point
      每隻被追蹤的手有自己的計數器，同一個手勢連續 4 幀才確認
→ 畫面串流 http://192.168.7.2:8080、MQTT edge/hand
```

終端機每 2 秒印一行，欄位的意思：

| 欄位 | 意思 |
| --- | --- |
| `det` / `lmk` / `per` | 手部偵測 / 骨架 / 人物偵測的推論時間（ms），0 代表這一幀沒跑 |
| `ok` | 這 2 秒內有畫出骨架的幀比例 |
| `lmk-best` | 每幀最好的骨架分數，取平均 |
| `tries` | 平均每幀跑了幾次骨架模型 |
| `det-run` | 有跑手部偵測的幀比例，追蹤穩定時會接近 0% |

### 進度

**環境**

- [x] 序列埠登入；檢查系統、NPU、Python、既有模型；C270 = `/dev/video2`
- [x] USB 直連網路和 Wi-Fi 上網，`bringup.sh` 開機後一行設定好
- [ ] 用 `benchmark_model` 量 NPU / CPU 推論時間（setup.md 步驟 6），填進效能紀錄
- [ ] 開機自動執行 `usb_net.sh`（Wi-Fi 會換網路，是否也要自動連線之後再決定）
- [ ] VS Code Remote-SSH（主機填 `root@192.168.7.2`）
- [x] `hand_cam.py` 開鏡頭走的是 GStreamer（2026-09-18 上板確認）
- [x] HDMI 顯示（`--display`）接投影機成功（2026-09-18）：投影機要在開機前就開好；預設全螢幕，**全螢幕後的 FPS 待確認**
- [ ] 改用充電器供電

**手部與人物辨識**（都已上板驗證，細節見下方調校紀錄）

- [x] 修正 Vela 模型，三個模型都跑在 NPU 上
- [x] 人物定位：每 5 幀一次，標出中心點和 `dx`
- [x] 手部偵測：門檻 0.55（擋掉假框），一次最多試 2 個候選框
- [x] 手部追蹤：找到手之後只跑骨架模型，追蹤中的門檻是 0.55
- [x] 沒追到手時，偵測也會看人物附近，遠距離比較容易找到手
- [x] `--debug` 才畫除錯用的框；Ctrl+C 可以正常結束
- [x] 手勢擴充到 10 種（隊友，分支 `gesture-classification`）：`fist`、`thumbs_up`、`two`、`three`、`four`、`six`、`rock`、`ok`，拇指用實際鏡頭畫面校準；部分判斷還不太穩
- [x] 捏合偵測 `PinchDetector`（加在隊友的分支上，再合進 `main`）：拇指壓下碰食指 = 捏下，門檻**待鏡頭校準**
- [x] 手勢分類 `board/gesture.py`（隊友，PR #1）：用手指關節夾角判斷每根手指伸直或彎曲，歸類成 `open`（手掌張開）、`point`（只伸食指）；同一個手勢連續 4 幀才確認，確認次數會跟著追蹤延續

**主線（下一步）**：MQTT 打通 → 手勢分類 → PC 控制 → 防誤觸 / 回饋音

- [x] 在板子上安裝 `paho-mqtt`
- [x] 筆電安裝 Mosquitto，打通 MQTT（板子 → 筆電），`pc/hand_listener.py` 收得到手部、手勢、人物資料
- [x] PC 控制：`pc/gesture_control.py`：`point` 移動游標（預設跟 `pip`）、`point` + 捏合 = 點擊 / 拖曳（拇指靠近時預先鎖定）、`two` = 手指朝上 / 朝下捲動（加速、掉幀時繼續捲）。實機測試：點擊明顯變準（2026-09-18）；新的 `two` 捲動**待實機驗證**
- [ ] 上板驗證手勢：先張開手掌讓系統找到手，追蹤中再改比 `point`，看能不能正確判斷
- [ ] 更多手勢（握拳、左右滑等）；「起手式」＝張開手掌舉到臉旁邊
- [ ] 更多 PC 控制（捲動、快捷鍵、暫停 / 播放），對應到新手勢
- [ ] 防誤觸：手勢維持一段時間才觸發、冷卻時間、提示音

**之後再做**

- [x] 伺服馬達 MG996R 可以用硬體 PWM 控制（pin 33 = `pwmchip1` channel 2），`board/servo.py`
- [ ] 語音：`board/voice/run_voice.sh`（NXP AFE + VIT，C270 複製成 4 聲道），**待上板測試**
- [ ] 雲台持續追人（人物定位、馬達控制都有了，剩下把 `dx` 接到馬達：把 `dx` 拉回 0）。設計（2026-09-17 決定）：
  - **人物偵測**：`detect_ssdmobilenetv3_quant`（來自 MobileNetSSD_VehicleHumanDetector）。正面、側面、背面都偵測得到，用人物框中心的 x 算雲台要轉的角度，讓人保持在畫面中央。
  - **手勢**：在這個穩定的畫面裡持續跑，不是兩種模式輪流切換。三個模型輪流使用 NPU，各自的 SRAM 都在 384 KB 以內，不會衝突。
  - **游標座標**：改用「手相對於人物框」的位置，不受鏡頭轉動影響。
  - 舵機使用獨立電源。

### 手部辨識調校紀錄（2026-09-17）

| 問題 | 原因 | 處理 | 結果 |
| --- | --- | --- | --- |
| 骨架時有時無 | 跟人物偵測、骨架門檻都無關：關掉人物偵測結果一樣；骨架分數不是接近 0 就是接近 1。真正原因是手部偵測沒有框到手 | 見下面幾列 | — |
| 沒有手時 FPS 掉到約 20 | 手部偵測在沒有手時，所有框的分數都剛好是 **0.50**（假框），舊門檻 0.5 會讓它們通過，多跑骨架模型 | 門檻改成 0.55 | FPS 回到約 33 |
| 分數最高的框常常不是手 | 舊版只檢查分數最高的那一個框 | 依分數最多試 2 個框 | 略有改善，但主因是下一列 |
| 遠一點就找不到手 | **手太小時偵測模型找不到**：640×480 畫面中，手寬約 80 px 以上才穩定，50～70 px 時有時找不到，50 px 以下幾乎找不到。只要有框到手，骨架分數都接近 1，跟裁切方式、左右手無關 | 手部追蹤、看人物附近 | 見下兩列 |
| 手部偵測斷斷續續 | 近距離時也只有約一半的幀找得到手 | **手部追蹤**：找到手之後改用骨架範圍當裁切框 | 50 公分：`ok` 約 88～100%，`det-run` 0～20%，FPS 約 33 |
| 遠距離「第一次」很難找到手 | 同上，手太小 | **看人物附近**：偵測輪流看全畫面和人物上半身附近。模擬：手寬 50 / 35 / 30 / 25 px 時，只看全畫面是 0/41 幀，加上人物附近是 21 / 21 / 15 / 6 幀 | 1.5 公尺：有機會重新找到手 |
| 追到之後偶爾斷掉 | 骨架分數偶爾掉到 0.7 以下一兩幀 | 追蹤中的門檻改成 **0.55**（模擬中降到 0.3 也不會殘留骨架） | 上板後使用者回饋「挺不錯」 |

換算成距離（C270 水平視角約 60°、手張開約 18 cm，估算）：1 m ≈ 100 px，1.5 m ≈ 67 px，2 m ≈ 50 px，**廚房情境剛好落在偵測模型的極限附近**。

**觀察：手舉到臉前比較容易被找到**。站遠時，手在臉前比較容易被偵測到，找到之後手移到別處也能繼續追蹤。可能的原因（都是推測，沒有逐一驗證）：臉附近一定在「人物附近」的搜尋範圍內；以臉當背景時手的輪廓比較清楚；模型的訓練資料多半是手在臉或胸前的畫面。

**設計想法：「起手式」**。先把手舉到臉旁邊（像打招呼），讓系統找到手，之後再比其他手勢。
- 也能當防誤觸機制：切菜時手不會舉到臉前。
- demo 給評審的教學卡可以寫這一步。

另外也試過 `HandSkeletonDetect_Mediapipe`：它直接把整個畫面交給骨架模型（跟我們用的是同一個模型），手只要小於畫面的 1/3，骨架就會錯，所以不適用。它的另一個模型 `handskeleton_new_quant`，NPU 比例比較低、信心分數也不可靠，沒有採用。

### 效能紀錄

| 項目 | 板子 NPU 實測 | 備註 |
| --- | --- | --- |
| 手部偵測 `hand_detect_20000_quant` | 約 10 ms | 只在沒追到手時跑 |
| 手部骨架 `hand_landmark_new_256x256_integer_quant` | 約 9.6 ms | 每隻手每幀一次 |
| 人物偵測 `detect_ssdmobilenetv3_quant` | 約 9.2 ms | 每 5 幀一次，平均每幀約 2 ms |
| `hand_cam.py` 即時（640×480，1 隻手，含串流） | 約 32～35 FPS | 舊版每幀都跑手部偵測時約 28 FPS；C270 本身最高 30 fps |

- CPU 對照：尚未量測，請用 setup.md 步驟 6 的 `benchmark_model` 量。
- NPU 分工：偵測模型的後處理（`TFLite_Detection_PostProcess`）在 CPU 上跑，骨架模型的輸入輸出量化轉換在 CPU 上跑，其餘都在 NPU。

### 已知問題

- **官方 Vela 模型載入失敗**（`Tensor 8 is invalidly specified in schema`）：舊版 Vela 讓 `*_scratch_fast` tensor 指向一個空的 buffer，TFLite 2.19 會拒絕載入。repo 裡的三個 `_vela.tflite` 都已經用 `scripts/fix_vela_scratch.py` 修正。板子上 `/root/hand-demo/` 的原版沒有修。
- **非張開手掌的姿勢（握拳、指東西）不容易被找到**（2026-09-17，隊友的測試圖）：
  - `fist.jpg`、`point_2.jpg`：手部偵測直接找不到手。
  - `point_1.JPG`：有找到手，但偵測框只框住拳頭，`expand_box()` 往上只多留 20%，伸直的食指被切在框外，骨架整個畫錯，手勢判斷成 `null`。分類規則本身沒有問題。
  - 以前沒發現，是因為之前測的都是張開的手掌。
  - 追蹤中的裁切框是依骨架範圍放大 1.8 倍，手指伸出去時會跟著變大，所以「先張開手掌讓系統找到手，再改成其他手勢」應該可行，**待上板確認**。
- **筆電和板子都連手機熱點時，串流會卡**：畫面要經過手機轉送，ping 61～446 ms，已改用 USB 直連。i.MX93 沒有硬體影像編碼器，只能用 CPU 壓 JPEG。
- **`g_ether` 在 Windows 上被認成「USB 序列裝置 (COMx)」**，也無法手動改成網卡驅動 → 改用 `usb_net.sh`（NCM）。
- **C 對 C 線接 `USB1_C` 時辨識不到** → 改用 A 對 C 線接筆電的 USB-A。
- **Wi-Fi 卡在 SCANNING，找不到熱點**：舊版 `bringup.sh` 在同一張網卡上啟動了第二個 `wpa_supplicant`。新版腳本已經不會重複啟動；遇到時請照「板子重開機後要重做的事」裡的四行重設。
- **HDMI 投影機沒畫面、Weston 啟動失敗**（2026-09-18）：`weston.log` 顯示 `no available modes for HDMI-A-1`，也就是板子讀不到螢幕的解析度資訊（`/sys/class/drm/card0-HDMI-A-1/edid` 是 0 bytes）。原因是開機時投影機還沒開，而板子只在開機時讀一次。**先打開投影機、切到這個 HDMI 輸入，再開機（或 `reboot`）** 就正常了。
  - `kmsro: driver missing` 只是警告（i.MX93 沒有 GPU），Weston 用的是 G2D 繪圖，不影響。
  - 檢查：`wc -c /sys/class/drm/card0-HDMI-A-1/edid`（要是 128 或 256）、`systemctl status weston`（要是 active）。
  - 如果某台螢幕怎樣都讀不到，備案是在 `/etc/xdg/weston/weston.ini` 的 `[output]` 手動指定解析度（例如 1280×720 的 modeline）。
- **Android 熱點在沒有裝置連線時會自動關閉**，重新開啟後 BSSID 和頻道都會改變。建議在手機上關掉「自動關閉熱點」。
- `dmesg` 裡的 `ethosu: can't change firmware ...`：每次啟動 NPU 程式都會出現，可以忽略。
- **pip 安裝失敗：`Temporary failure in name resolution`**（ping 8.8.8.8 卻是通的）：DNS 的問題，見「系統與軟體」的 DNS 那一列。
- 板子的時鐘不準（檔案日期顯示 9/5），開機後沒有自動校時。如果之後遇到 SSL 或憑證錯誤，先檢查 `date`。

---

## MQTT topics

| topic | 方向 | 內容 |
| --- | --- | --- |
| `edge/hand` | 板子 → PC | `{"ts", "fps", "width", "height", "hands": [...], "person": {...} 或 null}` |

- `hands[]`：`{"source", "score", "presence", "gesture", "gesture_raw", "pinch", "pinch_ratio", "box": [x0, y0, x1, y1], "landmarks": [[x, y, z] × 21]}`
  - `source`：`"detect"`（這一幀由手部偵測找到）或 `"track"`（沿用上一幀追蹤）。
  - `gesture`：確認過的手勢（`"open"`、`"point"`、`"fist"`、`"thumbs_up"`、`"two"`、`"three"`、`"four"`、`"six"`、`"rock"`、`"ok"` 或 `null`），同一個手勢要連續 4 幀才會出現，之後做觸發請用這個。
  - `gesture_raw`：這一幀單獨判斷的結果，還沒經過連續幀確認，會跳動，主要給除錯和單張圖片測試用。
  - `pinch`：拇指有沒有壓在食指側邊（扣扳機），已經過遲滯和連續 2 幀處理；`pinch_ratio`：原始比例，校準門檻用。
- `person`：`{"score", "box", "center": [x, y], "dx", "dy", "age"}`
  - `dx`、`dy`：人物中心偏離畫面中央的量，範圍 -1～+1，正值代表人在右方或下方。
  - `age`：距離上次偵測到這個人過了幾幀。
- 座標都是 0～1 的正規化值。

## 授權與來源

**人物偵測**：`board/models/detect_ssdmobilenetv3_quant*.tflite` 來自 WPI 的 MobileNetSSD_VehicleHumanDetector（SSD MobileNetV3，COCO，Apache License 2.0，授權全文見 [third_party/MobileNetSSD_VehicleHumanDetector/LICENSE](third_party/MobileNetSSD_VehicleHumanDetector/LICENSE)）。它的 Vela 版一樣已經用 `scripts/fix_vela_scratch.py` 修正。

**手部**：`board/models/` 的手部模型，以及 `board/hand_cam.py` 的前後處理流程，改寫自 WPI（Weilly Li）的 i.MX 手部偵測範例 MobileNetSSD_HandAndSKeletonDetect（Code Ver 4.0，2023/04/26，Apache License 2.0，授權全文見 [third_party/MobileNetSSD_HandAndSKeletonDetect/LICENSE](third_party/MobileNetSSD_HandAndSKeletonDetect/LICENSE)）。

- 使用的模型：`hand_detect_20000_quant`（MobileNet-SSD 手部偵測）、`hand_landmark_new_256x256_integer_quant`（21 點手骨架），兩者都包含原始版和 Vela 版。Vela 版已修正，見已知問題。
- 沒有收錄 `hand_landmark_quant`、`hand_landmark_mediapipe_quant`：根據 Vela 報告，這兩個模型沒有任何運算能放到 NPU 上。
- ⚠ 原始 `app.py` 的檔頭標示「WPI Confidential Proprietary」，跟 LICENSE 寫的授權不一致，所以 repo 裡沒有放 `app.py`。**repo 公開前請先向提供者確認授權。**
