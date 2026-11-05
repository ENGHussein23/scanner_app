"""Shared types for all scanner backends."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from PIL import Image


class ScannerError(Exception):
    """A scanner problem we can explain to the user.

    fatal=True means "don't try other scanners/backends, the user has to fix
    something first" (no paper in the feeder, paper jam, scanner busy...).
    """

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class ScanCancelled(Exception):
    """Raised when the user pressed Cancel."""


@dataclass
class ScanOptions:
    dpi: int = 300
    color: str = "color"  # color | gray | bw
    source: str = "flatbed"  # flatbed | feeder
    duplex: bool = False  # scan both sides of every sheet (feeder only)


@dataclass
class Device:
    backend: str  # wia | twain | escl | demo
    id: str
    name: str
    kind: str  # usb | network
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        """Short stable id the web page sends back to pick this scanner."""
        return f"{self.backend}-{hashlib.sha1(self.id.encode('utf-8')).hexdigest()[:12]}"

    def to_json(self) -> dict:
        return {"id": self.uid, "backend": self.backend, "name": self.name, "kind": self.kind}


@dataclass
class ScanResult:
    image: Image.Image
    dpi: int


# status callback: (stage, message). stage: searching | connecting | scanning
StatusFn = Callable[[str, str], None]


class Backend:
    name = ""
    kind = "usb"  # usb | network

    def available(self) -> tuple[bool, str]:
        """(usable?, reason if not). Used for diagnostics."""
        return True, ""

    def discover(self, cancel: threading.Event, host: Optional[str] = None) -> list[Device]:
        raise NotImplementedError

    def scan(
        self,
        device: Device,
        opts: ScanOptions,
        cancel: threading.Event,
        status: StatusFn,
    ) -> Iterator[ScanResult]:
        """Yield one ScanResult per page as soon as it has been read."""
        raise NotImplementedError


def arabic_pages(n: int) -> str:
    """'صفحة واحدة' / 'صفحتين' / '3 صفحات' / '12 صفحة'."""
    if n == 1:
        return "صفحة واحدة"
    if n == 2:
        return "صفحتين"
    if 3 <= n <= 10:
        return f"{n} صفحات"
    return f"{n} صفحة"
