#!/usr/bin/env python3
# --------------------------------------------------------------------------------------
# demo 全開時的效能監測 (板子上跑)：每秒記錄一次，結束時印出摘要
#   CPU (每顆核心)、各程式的 CPU / 記憶體、可用記憶體、晶片溫度、USB / Wi-Fi 流量
#   hand_cam.py 的 FPS 和各模型推論時間 → 推算 NPU 忙碌比例
#   (i.MX93 的 NPU 由 Cortex-M33 透過 rpmsg 驅動，系統沒有 NPU 使用率計數，只能用推論時間推算)
#
# 用法 (在板子上):
#   1. hand_cam.py 的輸出要存一份 log 給這支讀 (-u = 不要緩衝，log 才會即時更新)：
#        python3 -u hand_cam.py --mqtt 192.168.7.1 --mqtt-hz 30 | tee /tmp/hand_cam.log
#   2. 其他 demo 程式照常開 (語音、audio_stream…)
#   3. 另一個 SSH 視窗：
#        python3 perf_monitor.py                     # 量 60 秒
#        python3 perf_monitor.py -d 120 --csv /tmp/perf.csv
#      Ctrl+C 可以提早結束 (一樣會印摘要)
# --------------------------------------------------------------------------------------

import os
import re
import sys
import time
import signal
import argparse

CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024

# 要追蹤的程式：顯示名稱 → 指令列裡的關鍵字
WATCH = [
    ("hand_cam", "hand_cam.py"),
    ("VIT voice_ui_app", "voice_ui_app"),
    ("AFE afe", "afe libvoiceseekerlight"),
    ("audio_stream", "audio_stream.py"),
    ("weston", "weston"),
    ("mosquitto", "mosquitto"),
]
NETS = ("usb0", "mlan0")

# hand_cam.py 每 2 秒印一行，例如：
# NPU  FPS 31.2  det  0.0  lmk  9.6  per  9.1/5f  hands=1  person dx=+0.12  | ok  98%  lmk-best 0.93  tries 1.0/frame  det-run   2%
HAND_RE = re.compile(
    r"(?P<dev>NPU|CPU)\s+FPS\s+(?P<fps>[\d.]+)\s+det\s+(?P<det>[\d.]+)\s+lmk\s+(?P<lmk>[\d.]+)"
    r"(?:\s+per\s+(?P<per>[\d.]+)/(?P<every>\d+)f)?.*?"
    r"(?:tries\s+(?P<tries>[\d.]+)/frame\s+det-run\s+(?P<detrun>[\d.]+)%)?\s*$")
# 推論時間在那一幀沒跑時會印 0，所以用最近看到的非零值；一開始沒有值就用 docs/benchmark.md 的實測
DEFAULT_MS = {"det": 9.9, "lmk": 9.5, "per": 8.6}


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def cpu_times():
    """每顆核心 (busy, total) jiffies"""
    out = {}
    for line in read("/proc/stat").splitlines():
        if line.startswith("cpu") and line[3:4].isdigit():
            parts = line.split()
            vals = list(map(int, parts[1:]))
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            out[parts[0]] = (sum(vals) - idle, sum(vals))
    return out


def find_pids():
    found = {}
    me = os.getpid()
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == me:
            continue
        cmd = read(f"/proc/{d}/cmdline").replace("\0", " ").strip()
        if not cmd or cmd.startswith(("sh -c", "/bin/sh -c", "bash -c")):
            continue
        for name, key in WATCH:
            if key in cmd:
                found.setdefault(name, []).append(int(d))
    return found


def proc_jiffies_rss(pid):
    stat = read(f"/proc/{pid}/stat")
    if not stat:
        return None
    fields = stat.rsplit(")", 1)[1].split()
    jiffies = int(fields[11]) + int(fields[12])            # utime + stime
    rss_mb = int(fields[21]) * PAGE_KB / 1024
    return jiffies, rss_mb


def mem_available_mb():
    for line in read("/proc/meminfo").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    return 0.0


def temp_c():
    v = read("/sys/class/thermal/thermal_zone0/temp").strip()
    return int(v) / 1000 if v else float("nan")


def net_bytes():
    out = {}
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, data = line.split(":", 1)
        name = name.strip()
        if name in NETS:
            v = data.split()
            out[name] = (int(v[0]), int(v[8]))
    return out


class HandLog:
    """跟著 hand_cam.py 的 log 讀新的統計行"""

    def __init__(self, path):
        self.path, self.pos = path, None
        self.ms = dict(DEFAULT_MS)
        self.samples = []            # (fps, npu_ms_per_frame, npu_busy_pct, dev)

    def poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if self.pos is None or size < self.pos:
            self.pos = size                     # 從現在開始讀 (舊的內容不算)
            return
        with open(self.path, errors="replace") as f:
            f.seek(self.pos)
            chunk = f.read()
            self.pos = f.tell()
        for line in chunk.splitlines():
            m = HAND_RE.search(line)
            if not m:
                continue
            for k in ("det", "lmk", "per"):
                v = float(m.group(k) or 0)
                if v > 0:
                    self.ms[k] = v
            fps = float(m.group("fps"))
            tries = float(m.group("tries") or 1)
            detrun = float(m.group("detrun") or 0) / 100
            every = int(m.group("every") or 0)
            per_frame = tries * self.ms["lmk"] + detrun * self.ms["det"]
            if every:
                per_frame += self.ms["per"] / every
            self.samples.append((fps, per_frame, fps * per_frame / 10, m.group("dev")))


def stats(values):
    values = [v for v in values if v == v]          # 去掉 NaN
    if not values:
        return "-", "-", "-"
    return f"{sum(values) / len(values):.1f}", f"{min(values):.1f}", f"{max(values):.1f}"


def main():
    ap = argparse.ArgumentParser(description="demo 全開時的板子效能監測")
    ap.add_argument("-d", "--duration", type=float, default=60, help="量幾秒 (預設 60)")
    ap.add_argument("-i", "--interval", type=float, default=1.0, help="取樣間隔秒數")
    ap.add_argument("--hand-log", default="/tmp/hand_cam.log", help="hand_cam.py 輸出的 log")
    ap.add_argument("--csv", default="", help="每秒的原始數據存成 CSV")
    ap.add_argument("-q", "--quiet", action="store_true", help="量測中不要每秒印一行")
    args = ap.parse_args()

    stop = []
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    hand = HandLog(args.hand_log)
    hand.poll()
    rows = []
    prev_cpu, prev_net, prev_proc = cpu_times(), net_bytes(), {}
    pids = find_pids()
    for name, plist in pids.items():
        for p in plist:
            r = proc_jiffies_rss(p)
            if r:
                prev_proc[p] = r[0]
    cores = sorted(prev_cpu)
    t_prev = t0 = time.monotonic()
    print(f"量測 {args.duration:.0f} 秒 (Ctrl+C 提早結束)。追蹤中的程式：" +
          (", ".join(f"{n}({len(p)})" for n, p in pids.items()) or "無"))
    if not os.path.exists(args.hand_log):
        print(f"注意：找不到 {args.hand_log}，不會有 FPS / NPU 數據 (hand_cam.py 要用 | tee {args.hand_log} 啟動)")

    while not stop and time.monotonic() - t0 < args.duration:
        time.sleep(args.interval)
        now = time.monotonic()
        dt = now - t_prev
        t_prev = now
        cur_cpu, cur_net = cpu_times(), net_bytes()
        row = {"t": round(now - t0, 1)}
        for c in cores:
            db = cur_cpu[c][0] - prev_cpu[c][0]
            dtot = cur_cpu[c][1] - prev_cpu[c][1]
            row[f"{c}%"] = 100.0 * db / dtot if dtot else 0.0
        row["cpu_avg%"] = sum(row[f"{c}%"] for c in cores) / len(cores)
        pids = find_pids()                         # 程式可能中途啟動 / 結束
        for name, _ in WATCH:
            cpu = rss = 0.0
            for p in pids.get(name, []):
                r = proc_jiffies_rss(p)
                if not r:
                    continue
                if p in prev_proc:
                    cpu += 100.0 * (r[0] - prev_proc[p]) / CLK_TCK / dt
                prev_proc[p] = r[0]
                rss += r[1]
            row[f"{name} cpu%"] = cpu if name in pids else float("nan")
            row[f"{name} MB"] = rss if name in pids else float("nan")
        row["mem_avail_MB"] = mem_available_mb()
        row["temp_C"] = temp_c()
        row["load1"] = float(read("/proc/loadavg").split()[0] or 0)
        for n in NETS:
            if n in cur_net and n in prev_net:
                row[f"{n} rx KB/s"] = (cur_net[n][0] - prev_net[n][0]) / 1024 / dt
                row[f"{n} tx KB/s"] = (cur_net[n][1] - prev_net[n][1]) / 1024 / dt
        hand.poll()
        if hand.samples:
            fps, per_frame, busy, _ = hand.samples[-1]
            row["fps"], row["npu_ms/frame"], row["npu_busy%"] = fps, per_frame, busy
        rows.append(row)
        prev_cpu, prev_net = cur_cpu, cur_net
        if not args.quiet:
            fps_txt = f"  FPS {row['fps']:4.1f}  NPU ~{row['npu_busy%']:3.0f}%" if "fps" in row else ""
            print(f"{row['t']:5.0f}s  CPU {row['cpu_avg%']:5.1f}% (" +
                  " ".join(f"{row[f'{c}%']:3.0f}" for c in cores) +
                  f")  mem {row['mem_avail_MB']:5.0f} MB free  {row['temp_C']:4.1f}°C" + fps_txt)

    if not rows:
        return
    if args.csv:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(args.csv, "w") as f:
            f.write(",".join(keys) + "\n")
            for r in rows:
                f.write(",".join("" if r.get(k) is None or r.get(k) != r.get(k) else f"{r[k]:.2f}"
                                 for k in keys) + "\n")

    col = lambda k: [r[k] for r in rows if k in r]
    print(f"\n==================== 摘要 ({rows[-1]['t']:.0f} 秒，{len(rows)} 筆) ====================")
    print(f"{'項目':<26}{'平均':>9}{'最低':>9}{'最高':>9}")
    for label, key in ([("CPU 整體 %", "cpu_avg%")] + [(f"CPU {c} %", f"{c}%") for c in cores] +
                       [("負載 load (1 分鐘)", "load1"), ("可用記憶體 MB", "mem_avail_MB"),
                        ("晶片溫度 °C", "temp_C")]):
        print(f"{label:<26}" + "".join(f"{v:>9}" for v in stats(col(key))))
    for n in NETS:
        if col(f"{n} rx KB/s"):
            print(f"{n + ' 收 KB/s':<26}" + "".join(f"{v:>9}" for v in stats(col(f'{n} rx KB/s'))))
            print(f"{n + ' 送 KB/s':<26}" + "".join(f"{v:>9}" for v in stats(col(f'{n} tx KB/s'))))
    if hand.samples:
        devs = sorted({s[3] for s in hand.samples})
        print(f"{'hand_cam FPS':<26}" + "".join(f"{v:>9}" for v in stats([s[0] for s in hand.samples])))
        print(f"{'NPU 每幀推論 ms (推算)':<22}" + "".join(f"{v:>9}" for v in stats([s[1] for s in hand.samples])))
        print(f"{'NPU 忙碌 % (推算)':<24}" + "".join(f"{v:>9}" for v in stats([s[2] for s in hand.samples])))
        print(f"  (推論在 {'/'.join(devs)} 上；每次推論 ms：det {hand.ms['det']:.1f}  lmk {hand.ms['lmk']:.1f}"
              f"  per {hand.ms['per']:.1f}；NPU 忙碌 = FPS × 每幀推論 ms / 1000)")
    print(f"\n{'程式':<20}{'CPU% 平均':>10}{'CPU% 最高':>10}{'記憶體 MB':>10}   (CPU% 以一顆核心 = 100%)")
    for name, _ in WATCH:
        c = [v for v in col(f"{name} cpu%") if v == v]
        m = [v for v in col(f"{name} MB") if v == v]
        if c:
            print(f"{name:<20}{sum(c) / len(c):>10.1f}{max(c):>10.1f}{max(m):>10.1f}")
    if args.csv:
        print(f"\n原始數據：{args.csv}")


if __name__ == "__main__":
    sys.exit(main())
