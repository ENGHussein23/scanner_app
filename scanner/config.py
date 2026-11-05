"""User settings of the background service (stored as JSON, editable from /settings).

Location: %APPDATA%\\HomsScanner\\config.json on Windows, ~/.config/HomsScanner elsewhere.
Set HOMSSCAN_HOME to use another folder (used by the tests).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "HomsScanner"
VERSION = "1.0.0"
DEFAULT_PORT = 17650

# scheme://host[:port|:*]   (no path, no wildcard host)
ORIGIN_RE = re.compile(r"^https?://[a-z0-9]([a-z0-9.\-]*[a-z0-9])?(:(\d{1,5}|\*))?$")

DEFAULTS: dict = {
    "port": DEFAULT_PORT,
    "autostart": True,
    # Websites allowed to embed the scanner page in an iframe.
    # The first two are for development (flutter run -d chrome). Add your production site in /settings.
    "allowed_origins": ["http://localhost:*", "http://127.0.0.1:*"],
    "defaults": {"dpi": 300, "color": "color", "source": "flatbed", "duplex": False, "mode": "auto", "host": ""},
}
DPI_CHOICES = (100, 150, 200, 300, 400, 600)
COLORS = ("color", "gray", "bw")
SOURCES = ("flatbed", "feeder")
MODES = ("auto", "usb", "network")


def app_home() -> Path:
    override = os.environ.get("HOMSSCAN_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_home() -> Path:
    """Scanned pages live here (cleared on every start). Not inside the install folder."""
    override = os.environ.get("HOMSSCAN_HOME")
    if override:
        base = Path(override) / "data"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME / "data"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def normalize_origin(value: str) -> str:
    return str(value).strip().lower().rstrip("/")


def validate_origins(values) -> list[str]:
    """Return the cleaned list or raise ValueError with an Arabic message."""
    if not isinstance(values, list):
        raise ValueError("قائمة المواقع غير صحيحة.")
    cleaned: list[str] = []
    for raw in values:
        origin = normalize_origin(raw)
        if not origin:
            continue
        if not ORIGIN_RE.match(origin):
            raise ValueError(f"العنوان غير صحيح: {raw} — الشكل المطلوب مثل https://example.com أو http://localhost:*")
        if origin not in cleaned:
            cleaned.append(origin)
    return cleaned


def origin_matches(pattern: str, origin: str) -> bool:
    try:
        p, o = urlparse(pattern), urlparse(origin)
        if p.scheme != o.scheme or p.hostname != o.hostname or not o.hostname:
            return False
        if o.path not in ("", "/") or o.query or o.fragment:
            return False
        if pattern.endswith(":*"):
            return True
        default = 443 if p.scheme == "https" else 80
        return (p.port or default) == (o.port or default)
    except ValueError:
        return False


class Config:
    """Thread-safe settings object shared by the web app and the tray."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else app_home() / "config.json"
        self._lock = threading.RLock()
        self._data = json.loads(json.dumps(DEFAULTS))
        self.load()

    # ---- persistence ----------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # first run or damaged file: keep the defaults
        with self._lock:
            try:
                self._apply(raw)
            except ValueError:
                pass

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    # ---- access ----------------------------------------------------------
    @property
    def port(self) -> int:
        return int(self._data["port"])

    @property
    def autostart(self) -> bool:
        return bool(self._data["autostart"])

    @property
    def allowed_origins(self) -> list[str]:
        with self._lock:
            return list(self._data["allowed_origins"])

    def origin_allowed(self, origin: str | None) -> bool:
        if not origin:
            return False
        origin = normalize_origin(origin)
        return any(origin_matches(p, origin) for p in self.allowed_origins)

    def to_json(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    # ---- update ----------------------------------------------------------
    def _apply(self, raw: dict) -> None:
        """Validate `raw` and merge it into the settings (raises ValueError)."""
        if not isinstance(raw, dict):
            raise ValueError("إعدادات غير صحيحة.")
        new = json.loads(json.dumps(self._data))
        if "port" in raw:
            try:
                port = int(raw["port"])
            except (TypeError, ValueError):
                raise ValueError("رقم المنفذ غير صحيح.")
            if not 1024 <= port <= 65535:
                raise ValueError("المنفذ لازم يكون بين 1024 و 65535.")
            new["port"] = port
        if "autostart" in raw:
            new["autostart"] = bool(raw["autostart"])
        if "allowed_origins" in raw:
            new["allowed_origins"] = validate_origins(raw["allowed_origins"])
        d = raw.get("defaults")
        if isinstance(d, dict):
            if "dpi" in d:
                try:
                    dpi = int(d["dpi"])
                except (TypeError, ValueError):
                    dpi = 0
                if dpi not in DPI_CHOICES:
                    raise ValueError("الدقة غير صحيحة.")
                new["defaults"]["dpi"] = dpi
            for key, choices in (("color", COLORS), ("source", SOURCES), ("mode", MODES)):
                if key in d:
                    if d[key] not in choices:
                        raise ValueError("خيار غير صحيح.")
                    new["defaults"][key] = d[key]
            if "duplex" in d:
                new["defaults"]["duplex"] = bool(d["duplex"])
            if "host" in d:
                new["defaults"]["host"] = str(d["host"] or "").strip()[:100]
        self._data = new

    def update(self, raw: dict) -> None:
        with self._lock:
            self._apply(raw)
            self.save()
