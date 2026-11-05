"""Finds scanners (USB first, then the local network) and picks what to try."""
from __future__ import annotations

import logging
import sys
import threading
from typing import Iterator, Optional

from .base import Backend, Device, ScanCancelled, StatusFn

log = logging.getLogger(__name__)


def load_backends(demo: bool = False) -> list[Backend]:
    if demo:
        from .demo_backend import DemoBackend

        return [DemoBackend()]
    from .escl_backend import EsclBackend

    backends: list[Backend] = []
    if sys.platform == "win32":
        from .twain_backend import TwainBackend
        from .wia_backend import WiaBackend

        backends += [WiaBackend(), TwainBackend()]  # WIA first, TWAIN as fallback
    backends.append(EsclBackend())
    return backends


class ScannerManager:
    def __init__(self, backends: list[Backend]):
        self.backends = {b.name: b for b in backends}
        self._known: dict[str, Device] = {}  # last search result: uid -> Device
        self._lock = threading.Lock()

    def list_devices(self, host: Optional[str], cancel: threading.Event) -> tuple[list[Device], list[str]]:
        """Every scanner we can see (USB first, then network). Remembers them so a scan can
        be started on one of them by its uid."""
        errors: list[str] = []
        found = self._discover("usb", cancel, None, errors) + self._discover("network", cancel, host, errors)
        unique: dict[str, Device] = {}
        for dev in found:
            unique.setdefault(dev.uid, dev)
        with self._lock:
            self._known = dict(unique)
        return list(unique.values()), errors

    def known(self, uid: str) -> Optional[Device]:
        with self._lock:
            return self._known.get(uid)

    def backend(self, name: str) -> Backend:
        return self.backends[name]

    def _discover(self, kind: str, cancel: threading.Event, host: Optional[str], errors: list) -> list[Device]:
        found: list[Device] = []
        for backend in self.backends.values():
            if backend.kind != kind:
                continue
            ok, reason = backend.available()
            if not ok:
                errors.append(f"{backend.name}: {reason}")
                continue
            try:
                found.extend(backend.discover(cancel, host))
            except ScanCancelled:
                raise
            except Exception as exc:
                log.exception("discovery failed in %s", backend.name)
                errors.append(f"{backend.name}: {exc}")
        return found

    def candidates(
        self, mode: str, host: Optional[str], cancel: threading.Event, status: StatusFn
    ) -> Iterator[Device]:
        """USB scanners first; if none (or none works) the network ones.

        Lazy on purpose: the (slower) network search only happens when needed.
        """
        found_usb = False
        if mode in ("auto", "usb"):
            status("searching", "عم دوّر على سكانر موصول بالـ USB…")
            usb = self._discover("usb", cancel, None, [])
            found_usb = bool(usb)
            yield from usb
        if mode in ("auto", "network"):
            if mode == "auto" and not found_usb:
                status("searching", "ما لقيت سكانر عالـ USB — عم دوّر عالشبكة المحلية…")
            elif mode == "auto":
                status("searching", "تعذّر الاتصال عالـ USB — عم جرّب الشبكة المحلية…")
            else:
                status("searching", "عم دوّر على سكانر بالشبكة المحلية…")
            yield from self._discover("network", cancel, host, [])

    def diagnose(self, host: Optional[str] = None) -> dict:
        """Used by the 'check devices' button in the UI."""
        devices, errors = self.list_devices(host, threading.Event())
        backends = []
        for b in self.backends.values():
            ok, reason = b.available()
            backends.append({"name": b.name, "kind": b.kind, "available": ok, "reason": reason})
        return {"devices": [d.to_json() for d in devices], "backends": backends, "errors": errors}
