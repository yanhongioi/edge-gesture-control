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
