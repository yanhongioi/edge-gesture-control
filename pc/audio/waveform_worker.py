"""Render the listening waveform icon in an isolated Tk process."""

from __future__ import annotations

import math
import time
import tkinter as tk


def main() -> int:
    background = "#010203"
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", background)
    root.configure(background=background)

    width, height = 440, 200
    screen_x = root.winfo_screenwidth() - width - 24
    screen_y = root.winfo_screenheight() - height - 72
    root.geometry(f"{width}x{height}+{screen_x}+{screen_y}")
    canvas = tk.Canvas(
        root,
        width=width,
        height=height,
        background=background,
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack()

    bar_width = 7
    bar_count = 12
    gap = 20
    total_width = bar_count * bar_width + (bar_count - 1) * gap
    left = (width - total_width) // 2
    bars = []
    for index in range(bar_count):
        x1 = left + index * (bar_width + gap)
        bars.append(
            canvas.create_rectangle(
                x1,
                height // 2 - 4,
                x1 + bar_width,
                height // 2 + 4,
                fill="#62E6FF",
                outline="",
            )
        )

    started_at = time.monotonic()

    def animate() -> None:
        phase = (time.monotonic() - started_at) * 7.0
        for index, item in enumerate(bars):
            wave = abs(math.sin(phase + index * 0.75))
            bar_height = 28 + int(100 * wave)
            x1 = left + index * (bar_width + gap)
            canvas.coords(
                item,
                x1,
                (height - bar_height) // 2,
                x1 + bar_width,
                (height + bar_height) // 2,
            )
        root.after(45, animate)

    root.after(0, animate)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
