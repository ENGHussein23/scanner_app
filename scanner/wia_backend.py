"""USB scanners through Windows Image Acquisition (WIA) - needs pywin32.

WIA is built into Windows and works with almost every scanner driver, so it is
the first thing we try for USB scanners.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import threading
from typing import Iterator, Optional

from PIL import Image

from .base import Backend, Device, ScanCancelled, ScanOptions, ScanResult, ScannerError, StatusFn

FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"

SCANNER_DEVICE_TYPE = 1
INTENT = {"color": 1, "gray": 2, "bw": 4}
BITS = {"color": 24, "gray": 8, "bw": 1}
HANDLING_FEEDER = 1
HANDLING_FLATBED = 2
HANDLING_DUPLEX = 4
DUPLEX_ERROR = "هاد السكانر ما بيدعم مسح الوجهين (duplex)."
FEED_READY = 1

# WIA error codes -> (message, fatal)
PAPER_EMPTY = 0x80210003
WIA_ERRORS = {
    0x80210002: ("الورقة علقت بالسكانر (paper jam). طلّعها وجرّب كمان مرة.", True),
    PAPER_EMPTY: ("ما في ورق بالـ feeder. حط الأوراق وجرّب كمان مرة.", True),
    0x80210005: ("السكانر مطفي أو مو متصل. تأكد من الكابل والكهربا.", False),
    0x80210006: ("السكانر مشغول بعملية ثانية. استنى شوي وجرّب.", True),
    0x80210007: ("السكانر عم يسخّن. جرّب بعد ثواني.", True),
    0x80210016: ("غطاء السكانر مفتوح. سكّره وجرّب كمان مرة.", True),
}


@contextlib.contextmanager
def _com():
    """COM must be initialised in every thread that talks to WIA."""
    import pythoncom

    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _code(exc) -> int:
    info = getattr(exc, "excepinfo", None)
    if info and len(info) >= 6 and info[5]:
        return info[5] & 0xFFFFFFFF
    return (getattr(exc, "hresult", 0) or 0) & 0xFFFFFFFF


def _explain(exc, fallback: str) -> ScannerError:
    code = _code(exc)
    if code in WIA_ERRORS:
        msg, fatal = WIA_ERRORS[code]
        return ScannerError(msg, fatal=fatal)
    return ScannerError(f"{fallback} (WIA 0x{code:08X})")


def _get(props, name):
    try:
        return props(name).Value
    except Exception:
        return None


def _set(props, name, value) -> bool:
    try:
        props(name).Value = value
        return True
    except Exception:
        return False


def _set_clamped(props, name, value) -> None:
    try:
        prop = props(name)
    except Exception:
        return
    try:
        maximum = prop.SubTypeMax
        if maximum:
            value = min(value, maximum)
    except Exception:
        pass
    try:
        prop.Value = value
    except Exception:
        pass


class WiaBackend(Backend):
    name = "wia"
    kind = "usb"

    def available(self) -> tuple[bool, str]:
        try:
            import pythoncom  # noqa: F401
            import pywintypes  # noqa: F401
            import win32com.client  # noqa: F401
        except ImportError:
            return False, "مكتبة pywin32 مو مثبتة (pip install pywin32)"
        return True, ""

    # ---- discovery -----------------------------------------------------
    def _scanner_infos(self, manager):
        infos = manager.DeviceInfos
        for i in range(1, infos.Count + 1):
            info = infos.Item(i)
            if info.Type == SCANNER_DEVICE_TYPE:
                yield info

    def discover(self, cancel: threading.Event, host: Optional[str] = None) -> list[Device]:
        import win32com.client

        with _com():
            manager = win32com.client.Dispatch("WIA.DeviceManager")
            devices = []
            for info in self._scanner_infos(manager):
                name = _get(info.Properties, "Name") or "Scanner"
                devices.append(Device("wia", str(info.DeviceID), str(name), "usb"))
            return devices

    # ---- scanning ------------------------------------------------------
    def scan(
        self, device: Device, opts: ScanOptions, cancel: threading.Event, status: StatusFn
    ) -> Iterator[ScanResult]:
        import pywintypes
        import win32com.client

        com_error = pywintypes.com_error
        with _com():
            manager = win32com.client.Dispatch("WIA.DeviceManager")
            info = next(
                (i for i in self._scanner_infos(manager) if str(i.DeviceID) == device.id), None
            )
            if info is None:
                raise ScannerError("السكانر انفصل أو ما عاد ظاهر. تأكد من الكابل.")
            try:
                dev = info.Connect()
            except com_error as exc:
                raise _explain(exc, "ما قدرت اتصل بالسكانر")

            tmp = tempfile.mkdtemp(prefix="wia_")
            item = None
            try:
                item = dev.Items.Item(1)
                feeder = self._configure(dev, item, opts)
                fmt = FORMAT_PNG
                page_no = 1
                while True:
                    if cancel.is_set():
                        raise ScanCancelled()
                    status("scanning", f"جاري قراءة الصفحة {page_no}…")
                    try:
                        image_file = item.Transfer(fmt)
                    except com_error as exc:
                        code = _code(exc)
                        if code == PAPER_EMPTY:
                            if page_no == 1:
                                raise _explain(exc, "")
                            break  # feeder finished normally
                        if fmt == FORMAT_PNG and page_no == 1 and code not in WIA_ERRORS:
                            fmt = FORMAT_BMP  # driver can't do PNG, BMP always works
                            continue
                        raise _explain(exc, "فشلت قراءة الصفحة")
                    path = os.path.join(tmp, f"page{page_no}.img")
                    image_file.SaveFile(path)
                    with Image.open(path) as im:
                        pil = im.copy()
                    yield ScanResult(pil, opts.dpi)
                    page_no += 1
                    if not feeder:
                        break
            finally:
                del item
                shutil.rmtree(tmp, ignore_errors=True)

    def _configure(self, dev, item, opts: ScanOptions) -> bool:
        """Apply the settings. Returns True when scanning from the feeder."""
        feeder = opts.source == "feeder"
        duplex = feeder and opts.duplex
        select = HANDLING_FEEDER if feeder else HANDLING_FLATBED
        if duplex:
            caps = _get(dev.Properties, "Document Handling Capabilities")
            if caps is not None and not int(caps) & HANDLING_DUPLEX:
                raise ScannerError(DUPLEX_ERROR)
            select |= HANDLING_DUPLEX
        ok = _set(dev.Properties, "Document Handling Select", select)
        if feeder:
            if not ok:
                raise ScannerError(
                    DUPLEX_ERROR if duplex else "هاد السكانر ما عنده تغذية تلقائية (feeder).", fatal=not duplex
                )
            status = _get(dev.Properties, "Document Handling Status")
            if status is not None and not (int(status) & FEED_READY):
                raise ScannerError("ما في ورق بالـ feeder. حط الأوراق وجرّب كمان مرة.", fatal=True)
            # one sheet per Transfer, we loop. Duplex: 0 = "all pages", so the back side of each
            # sheet is not cut off; every Transfer then returns the next image (front, back, ...).
            _set(dev.Properties, "Pages", 0 if duplex else 1)

        props = item.Properties
        _set(props, "Current Intent", INTENT[opts.color])
        _set(props, "Bits Per Pixel", BITS[opts.color])
        _set(props, "Horizontal Resolution", opts.dpi)
        _set(props, "Vertical Resolution", opts.dpi)
        _set(props, "Horizontal Start Position", 0)
        _set(props, "Vertical Start Position", 0)

        names = (
            ("Horizontal Sheet Feed Size", "Vertical Sheet Feed Size")
            if feeder
            else ("Horizontal Bed Size", "Vertical Bed Size")
        )
        width = _get(dev.Properties, names[0])  # in 1/1000 inch
        height = _get(dev.Properties, names[1])
        if width and height:
            _set_clamped(props, "Horizontal Extent", int(width / 1000 * opts.dpi))
            _set_clamped(props, "Vertical Extent", int(height / 1000 * opts.dpi))
        elif not feeder:  # flatbed fallback: A4
            _set_clamped(props, "Horizontal Extent", int(8.27 * opts.dpi))
            _set_clamped(props, "Vertical Extent", int(11.69 * opts.dpi))
        return feeder
