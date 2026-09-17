# 上板教學：手部骨架（FRDM-i.MX93 + Logitech C270）

從零開始的完整步驟。我們這塊板子目前的實際狀態（IP、接線、進度）記錄在 [README 的「板子狀態」](../README.md#板子狀態)。

目標：C270 接在板子上，板子用 NPU 跑「手部偵測 + 21 點手骨架 + 人物定位」，筆電螢幕即時看到畫面與骨架。辨識流程和調校紀錄見 README。

```text
C270 ──USB──▶ FRDM-i.MX93 (NPU 推論) ──USB 直連 / 網路線──▶ 筆電
                   │                               ├─ 瀏覽器 http://<板子IP>:8080 看畫面+骨架
                   │                               └─ (選用) MQTT 收 21 點座標
                   └─HDMI──▶ 螢幕 (選用，直接顯示)
```

---

## 步驟 0：接線

| 線 | 板子這端 | 另一端 | 用途 |
| --- | --- | --- | --- |
| 電源 | 標示 **POWER** 的 USB-C | 5V/3A 以上 USB-C 充電器 | 供電 |
| Debug | 標示 **DEBUG** 的 USB-C | 筆電 USB | 序列埠主控台（看開機訊息、登入） |
| USB 網路（沒有網路線時，推薦） | **`USB1_C`** | 筆電 **USB-A**（用 A 對 C 線） | 板子模擬成 USB 網卡，SSH、傳檔、看串流（步驟 3-D） |
| 網路線（有的話） | 任一 RJ45 | 筆電網孔（或 USB 網卡） | SSH、傳檔、看串流、MQTT |
| C270 | USB Type-A | — | 鏡頭 |
| HDMI（選用） | HDMI | 螢幕 | 板子自己顯示畫面 |

> 建議先接 Debug 線、開好序列埠軟體（步驟 1），**最後才插電源**，這樣可以看到完整開機訊息。

**網路線插哪個孔？** 板子上的兩個 RJ45 都是 Gigabit 乙太網路，插哪一個都可以。插好之後，在板子上執行 `ip link`，顯示 `state UP` 的那個介面（`eth0` 或 `eth1`）就是你插的孔。筆電如果沒有網孔，需要 USB 轉 RJ45 網卡。

**USB 網路為什麼要用 A 對 C 線接筆電的 USB-A？** 用 C 對 C 線時，板子和筆電的 USB-C 都可能搶著當主機，板子就不會以網卡身分出現（我們實測時，USB 裝置狀態一直沒有變成 `configured`）。筆電的 USB-A 一定是主機，這樣最穩。POWER 則改用 C 對 C 線接筆電的 USB-C，電流比較大。

**可以用筆電供電嗎？** 開發初期可以，但不建議長期這樣用。筆電 USB 埠的輸出電流通常比充電器小，板子在跑 NPU、接著 C270 時，可能因為電流不夠而自動重開機，或鏡頭斷線。遇到這類狀況，第一個先懷疑電源，改接 5V/3A 以上的 USB-C 充電器（實際規格以板子或官方 Quick Start 標示為準）。demo 當天一定要用充電器。

---

## 步驟 1：用序列埠進入板子（第一次一定要用這個）

1. 筆電打開「裝置管理員 → 連接埠 (COM 和 LPT)」，插上 Debug 線後會多出 **兩個 COM port**（一個是 Linux 的 Cortex-A55，一個是 Cortex-M33）。
   - 如果出現「未知的裝置」，請到 NXP FRDM-IMX93 的 Getting Started 頁面安裝 USB 轉 UART 驅動。
2. 開啟序列埠（二選一）：
   - **VS Code**：安裝 Microsoft 的 **Serial Monitor** 擴充套件 → 下方面板切到「SERIAL MONITOR」→ Port 選 COM、Baud rate 選 **115200** → 按 **Start Monitoring** → 再按 **Toggle Terminal Mode**（終端機模式才能直接打指令）。
   - **PuTTY / Tera Term / MobaXterm**：Speed **115200**、8 data bits、no parity、1 stop bit、**flow control: None**。
   - 兩個 COM 都試試看，按 Enter 會出現 `login:` 的那個就是 Linux（我們這塊板子是 **COM11 / A**）。
   - **Serial Monitor 貼上多行指令會亂掉，要一次貼一行。**
   - 網路設定好之後（步驟 3），改用 VS Code 的 **Remote - SSH** 擴充套件會更好用：可以直接開啟板子上的資料夾、編輯檔案，也能開終端機。
3. 帳號輸入 `root`，預設沒有密碼。

---

## 步驟 2：看板子內的資訊

登入後可以用這些指令查看狀態：

| 想知道什麼 | 指令 |
| --- | --- |
| 系統 / kernel 版本 | `cat /etc/os-release`、`uname -a` |
| CPU / 記憶體 / 儲存空間 | `cat /proc/cpuinfo`、`free -h`、`df -h` |
| 即時 CPU 使用率 | `top`（按 `q` 離開） |
| 網路介面與 IP | `ip addr` |
| 接了哪些鏡頭 | `v4l2-ctl --list-devices` |
| 鏡頭支援的解析度 | `v4l2-ctl -d /dev/video2 --list-formats-ext`（節點換成你的 C270，見步驟 5） |
| USB 裝置 | `lsusb` |
| NPU 驅動有沒有起來 | `ls /dev/ethosu*`、`dmesg \| grep -i ethos` |
| NPU delegate 在不在 | `ls -l /usr/lib/libethosu_delegate.so` |
| Python TFLite 能不能用 | `python3 -c "import tflite_runtime.interpreter as t; print(t.__file__)"` |
| OpenCV 版本 | `python3 -c "import cv2; print(cv2.__version__)"` |
| MQTT 套件（MQTT image 才有） | `python3 -c "import paho.mqtt; print('ok')"`、`which mosquitto_pub` |
| **別人放進去的模型在哪** | `find / -name "*.tflite" -not -path "/proc/*" 2>/dev/null \| grep -i hand` |
| 有沒有其他程式在用鏡頭 | `fuser /dev/video2` |
| 開機後的核心訊息 | `dmesg \| tail -50` |

### 圖形化看檔案（選用）

網路設定好（步驟 3）後，可以用 **WinSCP**（SFTP，host 填板子 IP，例如 `192.168.7.2`，user `root`）或 **VS Code Remote-SSH** 直接瀏覽／編輯板子上的檔案，比在序列埠用 `vi` 方便很多。

---

## 步驟 3：網路（筆電 ↔ 板子）

| 方式 | 板子 IP | 延遲 | 用途 |
| --- | --- | --- | --- |
| **3-D USB 直連（推薦，已實測）** | `192.168.7.2`（固定） | 幾 ms | SSH、傳檔、看串流 |
| 3-A 手機熱點（已實測） | 由熱點分配（會變） | 61～446 ms，串流會卡 | **讓板子上網**（安裝套件） |
| 3-B 網路線 | `192.168.10.2`（固定） | 幾 ms | 同 USB 直連 |

我們目前的用法是 **3-D + 3-A 同時開**：USB 直連負責筆電和板子之間的傳輸，熱點只負責讓板子上網。後面指令裡的 `<板子IP>` 在 USB 直連時就是 `192.168.7.2`。

### 3-A Wi-Fi（讓板子上網；以手機熱點為例）

**Wi-Fi 只是讓板子能上網**，例如安裝套件、下載檔案。筆電和板子之間的傳輸建議走 **3-D USB 直連**，這樣：

- 板子連**哪個 Wi-Fi 都可以**，誰的手機熱點都行，之後換網路也不影響 USB 直連。
- 筆電**不需要**和板子連同一個 Wi-Fi。
- 只有在「不用 USB 直連、改用 Wi-Fi 連板子」時，筆電才要連同一個網路，而且這個網路不能有裝置隔離。學校和比賽場地的 Wi-Fi 常常有裝置隔離，這種情況請用手機熱點。

支援的網路：**只要輸入密碼就能連的 Wi-Fi**（WPA2 / WPA3-Personal）。需要網頁登入，或需要帳號加密碼的網路（例如 eduroam），這份教學沒有涵蓋，請改用手機熱點。

1. 準備網路：手機熱點的名稱和密碼請用英數字；iPhone 要打開「最大化相容性」。
2. 在板子上執行 `ip link`，確認有 `mlan0`。如果沒有，執行 `modprobe moal mod_para=nxp/wifi_mod_para.conf`。
3. 寫入設定檔。Serial Monitor 貼上多行會亂掉，所以用單行指令：
   ```bash
   printf '%s\n' 'ctrl_interface=/var/run/wpa_supplicant' 'network={' 'ssid="熱點名稱"' 'psk="熱點密碼"' '}' > /etc/wpa_hotspot.conf
   cat /etc/wpa_hotspot.conf
   ```
4. 連線並取得 IP（一次貼一行）：

   | 指令 | 作用 |
   | --- | --- |
   | `ip link set mlan0 up` | 啟用 Wi-Fi 網卡 |
   | `killall wpa_supplicant` | 關掉已經在跑的認證程式，避免衝突（`no process found` 是正常的） |
   | `wpa_supplicant -B -i mlan0 -D nl80211 -c /etc/wpa_hotspot.conf` | 在背景用設定檔連線；`rfkill` 警告可以忽略。執行後等 5 秒 |
   | `wpa_cli -i mlan0 status` | 查詢狀態，要看到 `wpa_state=COMPLETED` |
   | `udhcpc -i mlan0` | 向熱點要 IP，成功會顯示 `lease of x.x.x.x obtained` |
   | `ip addr show mlan0` | `inet` 後面就是板子的 IP |
   | `ping -c 3 8.8.8.8` | 測試能不能上網 |

5. （只有要用 Wi-Fi 連板子時才需要）筆電連上同一個網路，執行 `ipconfig` 查看 Wi-Fi 的 IPv4，確認和板子在同一個網段，再 `ping <板子IP>`。
6. **板子重開機後，要上網時再重做第 4 步**（設定檔不用重寫）。

#### 換成別的 Wi-Fi

板子會一直使用 `/etc/wpa_hotspot.conf` 裡的網路。要換網路，就用新的名稱和密碼重寫這個檔案，再重新連線。

> ⚠ 請透過 **USB 直連的 SSH**（`ssh root@192.168.7.2`）或**序列埠**操作。如果你是透過 Wi-Fi 的 SSH 連進板子，執行第一行後連線就會中斷。

```bash
killall wpa_supplicant udhcpc
printf '%s\n' 'ctrl_interface=/var/run/wpa_supplicant' 'network={' 'ssid="新的名稱"' 'psk="新的密碼"' '}' > /etc/wpa_hotspot.conf
ip link set mlan0 up
wpa_supplicant -B -i mlan0 -D nl80211 -c /etc/wpa_hotspot.conf
udhcpc -i mlan0
```

- `killall ... udhcpc`：`udhcpc` 拿到 IP 後會留在背景繼續續約，換網路前要一起關掉。
- 換網路後，板子的 Wi-Fi IP 會改變，用 `ip addr show mlan0` 查詢。USB 直連的 `192.168.7.2` 不受影響。

#### 讓板子記住多個網路（選用）

一個設定檔裡可以放好幾個網路，板子會自動連上目前找得到的那個。`priority` 數字越大，越優先連線：

```bash
printf '%s\n' 'ctrl_interface=/var/run/wpa_supplicant' 'network={' 'ssid="網路A"' 'psk="密碼A"' 'priority=2' '}' 'network={' 'ssid="網路B"' 'psk="密碼B"' 'priority=1' '}' > /etc/wpa_hotspot.conf
```

#### 板子交給別人之前

Wi-Fi 密碼是以**明文**存在板子上的 `/etc/wpa_hotspot.conf`。交給別人或歸還之前，請刪掉這個檔案，或改寫成別的網路：

```bash
rm /etc/wpa_hotspot.conf
```

### 3-B 網路線

#### 3-B-1 筆電設定固定 IP

設定 → 網路和網際網路 → 乙太網路 → IP 指派「編輯」→ 手動 → IPv4：

- IP 位址 `192.168.10.1`
- 子網路遮罩 `255.255.255.0`（或首碼長度 `24`）
- 閘道、DNS 留空

#### 3-B-2 板子設定 IP（序列埠裡輸入）

```bash
ip link                                   # 看哪個 eth 是 state UP（網路線插的那個）
ip addr add 192.168.10.2/24 dev eth0      # 如果插的是另一個孔就改成 eth1
ip link set eth0 up
ping -c 3 192.168.10.1
```

> Windows 防火牆預設會擋 ping，所以板子 ping 筆電不通**不一定代表有問題**。改從筆電 `ping 192.168.10.2` 測試比較準。

這樣設定在重開機後會消失。想要永久生效可以寫成 systemd-networkd 設定：

```bash
cat > /etc/systemd/network/10-eth0-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.2/24
EOF
systemctl restart systemd-networkd
```

### 3-D USB 直連（沒有網路線時推薦）

板子透過 `USB1_C` 模擬成一張 USB 網卡（NCM），Windows 11 內建驅動，會認成「**UsbNcm Host Device**」。

1. 接線：筆電 **USB-A** → A 對 C 線 → 板子 **`USB1_C`**。
2. USB 直連本身**不需要 Wi-Fi**，但第一次要先把 `usb_net.sh` 放到板子上（步驟 4），這需要一種傳檔方式：Wi-Fi（3-A）、網路線（3-B）或 USB 隨身碟（官方 PDF 的方法）。
   - 放上去之後就會一直留在板子上。之後就算沒有 Wi-Fi，也可以直接在序列埠執行下一步。
   - 我們這塊板子已經放好了：`/root/edge-gesture-control/board/usb_net.sh`。
3. 在板子上（序列埠或 SSH）執行：
   ```bash
   cd /root/edge-gesture-control/board
   sh usb_net.sh
   ```
   最後一行應該顯示 `mode=ncm iface=usb0 ip=192.168.7.2/24 ... state=configured`。如果顯示 `powered`，代表 Windows 還在辨識，等幾秒就好。
4. 筆電：裝置管理員的「網路介面卡」會多出 **UsbNcm Host Device**。查出它在 Windows 裡的名稱：
   ```powershell
   Get-NetAdapter | Where-Object InterfaceDescription -like "*Ncm*" | Format-Table Name, InterfaceDescription, Status
   ```
5. 設定筆電 IP（**系統管理員** PowerShell，名稱換成第 4 步查到的；只需要設定一次，Windows 會記住）：
   ```powershell
   New-NetIPAddress -InterfaceAlias "乙太網路 7" -IPAddress 192.168.7.1 -PrefixLength 24
   ```
   不用設定閘道。輸出裡的 `Tentative` / `Invalid` 是正常的。
   - **每台筆電都要設定一次**。網卡名稱（「乙太網路 N」）每台筆電都不一樣，請用第 4 步查到的名稱。
   - 換筆電時，板子這端不用改。新的筆電一樣設成 `192.168.7.1`，一次只接一台筆電即可。
6. 測試：`ping 192.168.7.2`、`ssh root@192.168.7.2`。
7. **板子重開機後，要重新執行 `sh usb_net.sh`**，筆電這端不用再設定。

> 不要用 `modprobe g_ether`：它是舊式的 RNDIS 網卡，Windows 會把它認成「USB 序列裝置 (COMx)」，也沒辦法手動改成網卡驅動。如果 NCM 不行，可以改用 `sh usb_net.sh rndis`（附 Microsoft 描述資訊，Windows 會自動安裝 RNDIS 網卡驅動）。`sh usb_net.sh stop` 可以關閉 USB 網路。

### 3-C SSH 登入（每種方式都要做）

筆電 PowerShell（Windows 11 內建 ssh）：

```powershell
ssh root@192.168.7.2       # USB 直連；網路線是 192.168.10.2；熱點則換成板子的實際 IP
```

第一次連線會詢問 `yes/no`，輸入 `yes`。我們這塊板子用 root 可以免密碼登入（已實測）。

如果 SSH 不接受空密碼，就先在序列埠執行 `passwd` 設一組密碼。


---

## 步驟 4：把程式放上板子

模型已經包含在 `board/models/`，整個 `board/` 資料夾複製過去就好（約 15 MB）。在筆電 repo 根目錄執行：

```powershell
.\scripts\deploy_board.ps1                      # 預設板子 IP 是 192.168.7.2（USB 直連）
.\scripts\deploy_board.ps1 -BoardIp 10.52.95.226  # 其他 IP
# 等同於：
# ssh root@192.168.7.2 "mkdir -p /root/edge-gesture-control"
# scp -r board root@192.168.7.2:/root/edge-gesture-control/
```

只改了一個檔案的話，單獨傳那個檔案比較快：`scp board/hand_cam.py root@192.168.7.2:/root/edge-gesture-control/board/`

如果 PowerShell 不允許執行腳本，先執行：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

> 別人已經放進板子的模型也可以直接用：`python3 hand_cam.py --model <路徑>/hand_detect_20000_quant.tflite --model-landmark <路徑>/hand_landmark_new_256x256_integer_quant.tflite`。
> 使用 NPU 時程式會自動改成讀取同一個資料夾裡的 `*_vela.tflite`。

---

## 步驟 5：確認鏡頭

```bash
v4l2-ctl --list-devices
```

找到 `C270 HD WEBCAM` 下面列出的**第一個** `/dev/videoX`（第二個是 metadata，不能用）。我們這塊板子是 `/dev/video2`；`/dev/video0`、`/dev/video1` 是板子自己的 MIPI 鏡頭介面。

有接 HDMI 的話，可以先直接看鏡頭畫面（`Ctrl+C` 結束）：

```bash
gst-launch-1.0 v4l2src device=/dev/video2 ! videoconvert ! waylandsink
```

---

## 步驟 6：先量模型速度（NPU vs CPU）

```bash
cd /root/edge-gesture-control/board/models
BM=$(ls /usr/bin/tensorflow-lite-*/examples/benchmark_model | head -1)

# NPU（Vela 版 + ethosu delegate）
$BM --graph=hand_detect_20000_quant_vela.tflite --external_delegate_path=/usr/lib/libethosu_delegate.so
$BM --graph=hand_landmark_new_256x256_integer_quant_vela.tflite --external_delegate_path=/usr/lib/libethosu_delegate.so

# CPU（原始版）
$BM --graph=hand_detect_20000_quant.tflite --num_threads=2
$BM --graph=hand_landmark_new_256x256_integer_quant.tflite --num_threads=2
```

看輸出最後的 `Inference (avg)`，單位是微秒。兩個模型的平均時間加起來，就是每一幀的推論時間。**把數字記下來**，這就是 plan 裡「手部模型上板，量幀率」這一步的答案，簡報的 NPU 對照也用得到。

> NPU 第一次推論（warm-up）比較慢，是正常現象。

---

## 步驟 7：單張圖片測試

```bash
cd /root/edge-gesture-control/board
python3 hand_cam.py --image test_images/hand-1.jpg
```

- 啟動時會印出兩個模型的輸入輸出資訊（`in`/`out0..`）。
- 會印出偵測到幾隻手和 21 點座標，結果圖存在 `output/hand-1_hand_cam.jpg`，可以用 WinSCP 拉回筆電看。
- 圖上沒有骨架的話，先用 `--delegate cpu` 再跑一次。CPU 有、NPU 沒有，表示 Vela 版的輸出順序跟原始版不同（見疑難排解）。

---

## 步驟 8：即時鏡頭＋在筆電看畫面

```bash
python3 hand_cam.py
```

筆電瀏覽器打開 **http://192.168.7.2:8080**（USB 直連；網路線是 `http://192.168.10.2:8080`），就能看到鏡頭畫面、手部框（黃色 = 這一幀偵測到的，青色 `track` = 追蹤中，加 `--debug` 時還會畫出：細紅框 = 被骨架模型否決的偵測框、細紫框 = 這一幀在人物附近找手的範圍）、骨架（綠線、藍點，指尖是紅點），以及人物框（藍色，灰色代表最近沒更新到）、人物中心點、畫面中央十字和水平偏移 `dx`。左上角會顯示 NPU/CPU、FPS，以及各模型的推論時間。終端機每 2 秒印一行統計（`ok`、`lmk-best`、`tries`、`det-run`，欄位意思見 README 的「辨識流程」）。

**站遠一點時**：先把手舉到臉旁邊（起手式），讓系統找到手、出現青色 `track` 框，之後手移到別處也會繼續追蹤。

常用選項：

```bash
python3 hand_cam.py --display          # 同時顯示在板子 HDMI 螢幕（按 q 結束）
python3 hand_cam.py --delegate cpu     # 改用 CPU 跑，跟 NPU 比較 FPS（demo 用）
python3 hand_cam.py --mirror           # 左右翻轉，像照鏡子
python3 hand_cam.py --device /dev/video2   # 自動找不到 C270 時手動指定
python3 hand_cam.py --max-hands 2      # 同時追兩隻手（FPS 會下降）
python3 hand_cam.py --person-every 10  # 人物偵測改成每 10 幀跑一次（預設 5；0 = 關閉）
python3 hand_cam.py --no-track         # 關閉手部追蹤（每幀都跑手部偵測，用來比較）
python3 hand_cam.py --track-thresh 0.5 # 追蹤中的手，骨架分數低於這個值才算跟丟（預設 0.55；新偵測到的手用 --lmk-thresh 0.7）
python3 hand_cam.py --no-person-search # 找手時只看全畫面，不看人物附近（用來比較）
python3 hand_cam.py --debug            # 畫出除錯用的紅框、紫框
python3 hand_cam.py --port 0           # 不開串流（省下 JPEG 編碼的 CPU）
python3 hand_cam.py --stream-scale 0.5 --stream-fps 10   # 網路慢（熱點）時減輕串流，不影響推論
python3 hand_cam.py --stream-fps 30 --stream-quality 80  # USB 直連 / 網路線時，畫面更順、更清楚
python3 hand_cam.py -h                 # 所有參數
```

---

## 步驟 9：把資料送到筆電（MQTT）

板子會把手部 21 點、手勢、人物資訊送到筆電，之後做手勢觸發、游標控制都要用到。訊息格式見 README 的「MQTT topics」。

> 筆電上的 Python 請一律用 `py -3.11`。`python3` 可能會跑到 MSYS2 的 Python，那裡沒有這些套件。

**第一次設定（每台筆電做一次）**

1. 安裝 Mosquitto（MQTT broker）和 Python 套件：
   ```powershell
   winget install --id EclipseFoundation.Mosquitto -e
   py -3.11 -m pip install -r pc\requirements.txt
   ```
2. 防火牆只對 USB 直連網卡開放 1883 埠（**系統管理員** PowerShell；`乙太網路 7` 換成你的網卡名稱，見步驟 3-D）。`pc/mosquitto.conf` 允許不用帳密就能連線，所以不要對所有網路開放：
   ```powershell
   New-NetFirewallRule -DisplayName "MQTT 1883 (USB board link)" -Direction Inbound -Protocol TCP -LocalPort 1883 -InterfaceAlias "乙太網路 7" -Action Allow
   ```
3. 安裝程式可能會建立一個自動啟動的 Mosquitto 服務，它只接受本機連線，而且會佔住 1883 埠。如果 `Get-Service mosquitto` 顯示 `Running`，用系統管理員 PowerShell 停掉它：
   ```powershell
   Stop-Service mosquitto; Set-Service mosquitto -StartupType Manual
   ```
4. 板子安裝 Python 套件（板子要能上網；如果出現 `Temporary failure in name resolution`，是 DNS 的問題，先執行 `bringup.sh`）：
   ```bash
   python3 -m pip install paho-mqtt
   ```

**每次使用**：照 README「指令區 → 每次開工」的順序：broker → 接收端 → 板子加 `--mqtt 192.168.7.1`。接收端會即時顯示 FPS、人物 `dx`、手的來源、確認過的手勢，以及手腕和食指的座標。

---

## 疑難排解

| 狀況 | 原因 / 解法 |
| --- | --- |
| `No module named 'paho'`（筆電） | 用了 `python3`，改用 `py -3.11` |
| `ConnectionRefusedError`（筆電接收端） | broker 沒開，先啟動 `mosquitto.exe -v -c .\pc\mosquitto.conf` |
| `ModuleNotFoundError: No module named 'gesture'`（板子） | `board/gesture.py` 沒有傳到板子，用 `deploy_board.ps1` 傳整個 `board/` |
| `Tensor N is invalidly specified in schema`（`required_bytes <= bytes`） | 舊版 Vela 編出來的模型，新版 TFLite (2.16+) 不接受。在 PC 執行 `python scripts/fix_vela_scratch.py <模型>_vela.tflite` 修正後，再重新傳到板子。repo 裡的模型已經修過；**板子上 `/root/hand-demo/model/` 的原版模型沒有修**。 |
| `Failed to load delegate` | 官方 `app.py` 預設的 `vx` 是 i.MX8MP 的 delegate，i.MX93 要用 `ethosu`。`hand_cam.py` 預設已經是 NPU (ethosu)。 |
| `找不到 USB 鏡頭` | `v4l2-ctl --list-devices` 查到節點後，用 `--device /dev/videoX` 指定。 |
| `無法開啟鏡頭` / Device busy | 有其他程式在用鏡頭（例如 GoPoint demo）。用 `fuser /dev/video2`（換成你的鏡頭節點）找出來再 `kill`。 |
| 有手部框但沒有骨架 | 加 `--debug` 看紅框：紅框代表偵測框被骨架模型否決（通常框到的不是手）。骨架分數多半不是接近 0 就是接近 1，調門檻幫助不大。 |
| NPU 沒骨架但 `--delegate cpu` 有 | Vela 版輸出順序不同。比對啟動時印出的 `out0..out2` shape：21×3 座標是 63 個值、信心分數是 1 個值。然後修改 `HandLandmark.__init__` 裡的 `outs[2]` / `outs[0]`。 |
| 手部框完全偵測不到 | 手在畫面中太小（640×480 裡寬度小於約 50～70 px，大約是 1.5 公尺以外）。靠近一點，或先把手舉到臉旁邊，找到後再退回原位。**不要把 `--det-thresh` 調到 0.5 以下**：沒有手時偵測模型會給出一堆剛好 0.50 的假框。 |
| `--display` 報錯 cannot open display | 板子 HDMI 桌面 (Weston) 沒有啟動，或 `/run/user/0/wayland-*` 不存在。改用瀏覽器串流即可。 |
| 瀏覽器串流打不開 | 確認筆電 ping 得到板子；程式有印「串流已開啟」；網址是 `http://`，不是 https。 |
| 串流很卡但終端機 FPS 正常 | 瓶頸在網路（手機熱點延遲大）。**改用 USB 直連（3-D）或網路線**，實測後非常順。暫時的解法是 `--stream-scale 0.5 --stream-fps 10` 減少傳輸量。 |
| `g_ether` 載入後，Windows 出現「USB 序列裝置 (COMx)」 | Windows 把舊式 RNDIS 認成序列埠，也無法手動改成網卡驅動。先執行 `rmmod g_ether`（`usb_net.sh` 會自動處理），再改用 `sh usb_net.sh`（NCM）。 |
| `usb_net.sh` 執行了，但 `state` 是 `not attached`，Windows 也沒反應 | 改用 A 對 C 線接筆電的 **USB-A**（C 對 C 線可能被協商成板子當主機）；也確認這條線能傳資料，不是只能充電的線。 |
| 終端機 FPS 本身就很低 | 瓶頸在板子。先用 `--port 0` 關掉串流，看 FPS 有沒有回升；再試 `--width 320 --height 240`。 |
| 重開機後 SSH 連不上 | 網路設定不會保留。在序列埠執行 `sh /root/edge-gesture-control/board/bringup.sh`（USB 直連 + Wi-Fi）；網路線要重做 3-B-2。 |
| 按 Ctrl+C 出現 `Failed to invoke ethos_u op` | 舊版程式在 NPU 推論途中被中斷，新版已經修正，會等這一幀跑完再結束。 |

---

## 需要現場驗證的地方

以下是寫程式時無法在板子上實測的部分，第一次上板請留意：

1. ~~Vela 版模型的輸出順序是否與原始版相同~~ → 已確認相同（2026-09-16）。
2. 板子的 OpenCV 是否支援 GStreamer（不支援的話程式會自動改用 V4L2）→ 還沒確認，看 `hand_cam.py` 啟動時印的「鏡頭開啟 (…)」。
3. ~~1～1.5 公尺距離下，C270 畫面中的手夠不夠大~~ → 1.5 公尺已經是偵測模型的極限，靠手部追蹤、在人物附近找手、起手式來補強，詳見 README 的調校紀錄（2026-09-17）。
