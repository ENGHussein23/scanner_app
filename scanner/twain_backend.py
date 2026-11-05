"""USB scanners through TWAIN (fallback when WIA doesn't work) - needs `pytwain`.

Notes:
  * 32-bit TWAIN drivers need 32-bit Python (or a 64-bit driver + 64-bit DSM).
  * pytwain changed its method names between versions (GetSourceList vs
    source_list ...), so every call goes through _call() which tries both.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from typing import Iterator, Optional

from PIL import Image

from .base import Backend, Device, ScanCancelled, ScanOptions, ScanResult, ScannerError, StatusFn


def _call(obj, names, *args):
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn(*args)
    raise AttributeError(f"{type(obj).__name__} has none of {names}")


def _open_manager():
    import twain

    try:
        return twain.SourceManager(0)
    except TypeError:
        return twain.SourceManager()


def _close(obj) -> None:
    for name in ("destroy", "close"):
        fn = getattr(obj, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
            return


class TwainBackend(Backend):
    name = "twain"
    kind = "usb"

    def available(self) -> tuple[bool, str]:
        try:
            import twain  # noqa: F401
        except ImportError:
            return False, "مكتبة pytwain مو مثبتة (اختيارية: pip install -r requirements-twain.txt)"
        except Exception as exc:  # DSM missing etc.
            return False, f"TWAIN ما اشتغل: {exc}"
        return True, ""

    def discover(self, cancel: threading.Event, host: Optional[str] = None) -> list[Device]:
        manager = _open_manager()
        try:
            names = _call(manager, ("GetSourceList", "get_source_list")) or []
        finally:
            _close(manager)
        return [Device("twain", str(n), str(n), "usb") for n in names]

    def scan(
        self, device: Device, opts: ScanOptions, cancel: threading.Event, status: StatusFn
    ) -> Iterator[ScanResult]:
        import twain

        feeder = opts.source == "feeder"
        manager = _open_manager()
        source = None
        tmp = tempfile.mkdtemp(prefix="twain_")
        try:
            try:
                source = _call(manager, ("OpenSource", "open_source"), device.id)
            except Exception as exc:
                raise ScannerError(f"ما قدرت افتح السكانر عبر TWAIN: {exc}")

            def cap(name, kind, value):
                try:
                    _call(source, ("SetCapability", "set_capability"), getattr(twain, name), getattr(twain, kind), value)
                except Exception:
                    pass  # the driver simply doesn't support this setting

            pixel = {"color": "TWPT_RGB", "gray": "TWPT_GRAY", "bw": "TWPT_BW"}[opts.color]
            cap("ICAP_PIXELTYPE", "TWTY_UINT16", getattr(twain, pixel))
            cap("ICAP_XRESOLUTION", "TWTY_FIX32", float(opts.dpi))
            cap("ICAP_YRESOLUTION", "TWTY_FIX32", float(opts.dpi))
            if feeder:
                cap("CAP_FEEDERENABLED", "TWTY_BOOL", True)
                cap("CAP_AUTOFEED", "TWTY_BOOL", True)

            if feeder and opts.duplex:
                try:
                    _call(source, ("SetCapability", "set_capability"), twain.CAP_DUPLEXENABLED, twain.TWTY_BOOL, True)
                except Exception:
                    raise ScannerError("هاد السكانر ما بيدعم مسح الوجهين (duplex).")

            try:
                _call(source, ("RequestAcquire", "request_acquire"), 0, 0)  # no vendor dialog
            except Exception as exc:
                raise ScannerError(f"السكانر رفض بدء المسح: {exc}")

            page_no = 1
            while True:
                if cancel.is_set():
                    raise ScanCancelled()
                status("scanning", f"جاري قراءة الصفحة {page_no}…")
                try:
                    result = _call(source, ("XferImageNatively", "xfer_image_natively"))
                except Exception as exc:
                    raise ScannerError(f"فشلت قراءة الصفحة: {exc}")
                if not result:
                    if page_no == 1 and feeder:
                        raise ScannerError("ما في ورق بالـ feeder.", fatal=True)
                    break
                handle, remaining = result
                path = os.path.join(tmp, f"page{page_no}.bmp")
                try:
                    _call(twain, ("DIBToBMFile", "dib_to_bm_file"), handle, path)
                finally:
                    try:
                        _call(twain, ("GlobalHandleFree", "global_handle_free"), handle)
                    except Exception:
                        pass
                with Image.open(path) as im:
                    pil = im.copy()
                yield ScanResult(pil, opts.dpi)
                page_no += 1
                if not feeder or remaining == 0:
                    break
        finally:
            if source is not None:
                _close(source)
            _close(manager)
            shutil.rmtree(tmp, ignore_errors=True)
