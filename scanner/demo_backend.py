"""Fake scanner for trying the web page without any hardware (python app.py --demo)."""
from __future__ import annotations

import threading
from typing import Iterator, Optional

from PIL import Image, ImageDraw, ImageFont

from .base import Backend, Device, ScanCancelled, ScanOptions, ScanResult, StatusFn


class DemoBackend(Backend):
    name = "demo"
    kind = "usb"

    def discover(self, cancel: threading.Event, host: Optional[str] = None) -> list[Device]:
        return [Device("demo", "demo-1", "سكانر تجريبي (Demo)", "usb")]

    def scan(
        self, device: Device, opts: ScanOptions, cancel: threading.Event, status: StatusFn
    ) -> Iterator[ScanResult]:
        dpi = min(opts.dpi, 150)  # keep the demo quick
        width, height = int(8.27 * dpi), int(11.69 * dpi)
        sheets = 3 if opts.source == "feeder" else 1
        count = sheets * 2 if opts.duplex and opts.source == "feeder" else sheets  # 2 pages per sheet
        for n in range(1, count + 1):
            status("scanning", f"جاري قراءة الصفحة {n}…")
            if cancel.wait(1.2):
                raise ScanCancelled()
            yield ScanResult(self._page(n, width, height, opts.color), dpi)

    @staticmethod
    def _page(n: int, w: int, h: int, color: str) -> Image.Image:
        img = Image.new("RGB", (w, h), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=max(24, w // 8))
        except TypeError:  # very old Pillow
            font = ImageFont.load_default()
        accent = (40, 110, 200)
        draw.rectangle([w // 10, h // 12, w - w // 10, h // 12 + h // 40], fill=accent)
        draw.text((w // 2, h // 3), str(n), fill=accent, font=font, anchor="mm")
        for i in range(14):
            y = h // 2 + i * (h // 40)
            draw.rectangle([w // 10, y, w - w // 10 - (i % 4) * w // 12, y + h // 120], fill=(170, 170, 170))
        if color == "gray":
            return img.convert("L")
        if color == "bw":
            return img.convert("L").point(lambda v: 255 if v > 200 else 0).convert("1")
        return img
