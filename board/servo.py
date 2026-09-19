#!/usr/bin/env python3
# --------------------------------------------------------------------------------------
# MG996R 伺服馬達 (雲台用)
#   接線：訊號 (橘) = 排針 pin 33 = GPIO_IO13；電源 (紅) = 獨立電池 (4 顆 3 號，約 6 V)；
#         電池負極、馬達負極 (棕)、板子 GND 共地。電池正極絕對不能碰到板子。
#   控制：pin 33 是硬體 PWM = /sys/class/pwm/pwmchip1 的 channel 2 (實測)，50 Hz，
#         脈衝寬度決定角度 (預設 500 us = 0 度、2500 us = 180 度，每顆馬達略有不同，可校準)
#
# 用法 (在板子上):
#   python3 servo.py 90                  # 轉到 90 度 (平滑轉過去)
#   python3 servo.py sweep               # 30 → 150 → 90 度掃一次
#   python3 servo.py off                 # 關掉訊號，馬達放鬆不出力
#   python3 servo.py 0 --min-us 600      # 校準：0 度時如果卡住有嗡嗡聲，把 --min-us 調大
#   python3 servo.py 180 --max-us 2400   # 校準：180 度同理，把 --max-us 調小
# 其他程式：from servo import Servo; s = Servo(); s.move_to(120); s.off()
# --------------------------------------------------------------------------------------

import os
import sys
import time
import argparse
import threading

PWM_ROOT = "/sys/class/pwm"


class Servo:
    def __init__(self, chip=1, channel=2, min_us=500, max_us=2500, max_angle=180.0,
                 period_us=20000, root=PWM_ROOT):
        self.base = os.path.join(root, f"pwmchip{chip}")
        self.path = os.path.join(self.base, f"pwm{channel}")
        self.channel = channel
        self.min_us, self.max_us, self.max_angle = min_us, max_us, max_angle
        self.angle = None
        if not os.path.isdir(self.path):
            self._write(os.path.join(self.base, "export"), channel)
            for _ in range(50):                            # 等 sysfs 建好資料夾
                if os.path.isdir(self.path):
                    break
                time.sleep(0.01)
        self._disable_other_channels()
        self.angle = self._read_current_angle()     # 上次執行留下的位置，這樣才能平滑轉過去
        self._write("period", period_us * 1000)

    def _read_current_angle(self):
        try:
            with open(os.path.join(self.path, "enable")) as f:
                if f.read().strip() != "1":
                    return None
            with open(os.path.join(self.path, "duty_cycle")) as f:
                us = int(f.read().strip()) / 1000
        except (OSError, ValueError):
            return None
        if not self.min_us <= us <= self.max_us:
            return None
        return (us - self.min_us) / (self.max_us - self.min_us) * self.max_angle

    def _write(self, name, value):
        path = name if os.path.isabs(name) else os.path.join(self.path, name)
        with open(path, "w") as f:
            f.write(str(value))

    def _disable_other_channels(self):
        """同一個 PWM 控制器上，其他被開著的通道先關掉 (測試時可能開過)"""
        try:
            names = os.listdir(self.base)
        except OSError:
            return
        for n in names:
            if n.startswith("pwm") and n != f"pwm{self.channel}" and n[3:].isdigit():
                try:
                    self._write(os.path.join(self.base, n, "enable"), 0)
                except OSError:
                    pass

    def angle_to_us(self, angle):
        angle = min(self.max_angle, max(0.0, float(angle)))
        return self.min_us + (self.max_us - self.min_us) * angle / self.max_angle

    def set_angle(self, angle):
        """立刻轉到 angle 度 (0 ~ max_angle)"""
        us = self.angle_to_us(angle)
        self._write("duty_cycle", int(us * 1000))
        self._write("enable", 1)
        self.angle = min(self.max_angle, max(0.0, float(angle)))

    def move_to(self, angle, speed=120.0, step_s=0.02):
        """平滑轉到 angle 度，速度 speed 度/秒 (避免 MG996R 一下子猛轉、電流太大)"""
        target = min(self.max_angle, max(0.0, float(angle)))
        if self.angle is None or speed <= 0:
            self.set_angle(target)
            return
        step = speed * step_s
        while abs(target - self.angle) > step:
            self.set_angle(self.angle + (step if target > self.angle else -step))
            time.sleep(step_s)
        self.set_angle(target)

    def off(self):
        """關掉訊號：馬達不再出力 (可以用手轉動)"""
        self._write("enable", 0)


# --------------------------------------------------------------------------------------
# 定速轉動 (雲台追人用)
# --------------------------------------------------------------------------------------
class Panner:
    """背景執行緒定速轉動：主程式只用 set_direction(+1 / 0 / -1) 說「往這邊一直轉」。
    不能在主迴圈直接呼叫 move_to()，它裡面會 sleep，鏡頭那邊的影格處理會整個卡住。
    角度是用時間累積的 (速度 x 經過秒數)，所以實際轉速跟執行緒被排到的頻率無關。"""

    def __init__(self, servo, speed=25.0, step_s=0.02, start=90.0,
                 min_angle=0.0, max_angle=None):
        self.servo = servo
        self.speed, self.step_s = speed, step_s
        self.min_angle = max(0.0, min_angle)
        self.max_angle = servo.max_angle if max_angle is None else min(servo.max_angle, max_angle)
        self.direction = 0
        self._stop = threading.Event()
        self._thread = None
        if servo.angle is None:                 # 剛上電、不知道目前在哪：先平滑回到起始角度
            servo.move_to(min(self.max_angle, max(self.min_angle, start)))
        self.target = servo.angle

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def set_direction(self, d):
        """+1 / -1 = 往那個方向持續轉，0 = 停在原地 (不是回中，是就地停住)"""
        self.direction = 0 if not d else (1 if d > 0 else -1)

    @property
    def at_limit(self):
        return self.target <= self.min_angle + 1e-6 or self.target >= self.max_angle - 1e-6

    def _run(self):
        last = time.monotonic()
        while not self._stop.is_set():
            time.sleep(self.step_s)
            now = time.monotonic()
            dt, last = now - last, now
            d = self.direction
            if d == 0:
                continue
            target = min(self.max_angle, max(self.min_angle, self.target + d * self.speed * dt))
            if abs(target - self.target) < 1e-6:     # 頂到極限了，不用一直重寫 sysfs
                continue
            self.target = target
            try:
                self.servo.set_angle(target)
            except OSError as e:                     # 寫不進去就別再試了，不然每 20 ms 噴一次
                print(f"servo 寫入失敗，停止轉動: {e}")
                self._stop.set()

    def close(self):
        """停下來；不關訊號，讓馬達繼續撐住鏡頭 (要放鬆請自己呼叫 servo.off())"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def main():
    ap = argparse.ArgumentParser(description="MG996R servo on pin 33 (pwmchip1 channel 2)")
    ap.add_argument("command", help="角度 (0~180)、sweep 或 off")
    ap.add_argument("--chip", type=int, default=1)
    ap.add_argument("--channel", type=int, default=2)
    ap.add_argument("--min-us", type=int, default=500, help="0 度的脈衝寬度 (us)")
    ap.add_argument("--max-us", type=int, default=2500, help="180 度的脈衝寬度 (us)")
    ap.add_argument("--speed", type=float, default=120.0, help="轉動速度 (度/秒)，0 = 直接跳過去")
    ap.add_argument("--root", default=PWM_ROOT, help=argparse.SUPPRESS)      # 測試用
    args = ap.parse_args()

    s = Servo(args.chip, args.channel, args.min_us, args.max_us, root=args.root)
    if args.command == "off":
        s.off()
        print("servo off")
        return
    if args.command == "sweep":
        s.move_to(90, args.speed)
        time.sleep(0.5)
        for a in (30, 150, 90):
            print(f"-> {a} deg ({s.angle_to_us(a):.0f} us)")
            s.move_to(a, args.speed)
            time.sleep(0.5)
        return
    try:
        angle = float(args.command)
    except ValueError:
        sys.exit("command 要是角度 (0~180)、sweep 或 off")
    start = "unknown" if s.angle is None else f"{s.angle:.0f}"
    print(f"{start} -> {angle:.0f} deg ({s.angle_to_us(angle):.0f} us)")
    s.move_to(angle, args.speed)    # 知道目前角度時平滑轉過去；不知道時直接跳到目標


if __name__ == "__main__":
    main()
