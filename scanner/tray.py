"""System-tray icon (pystray): status, settings, 'start with Windows', exit."""
from __future__ import annotations

import logging

from . import autostart
from .config import VERSION
from .icon import make_icon

log = logging.getLogger(__name__)


def run_tray(service) -> None:
    import pystray  # ImportError is handled by the caller

    def toggle_autostart(icon, item):
        service.config.update({"autostart": not service.config.autostart})
        try:
            autostart.set_enabled(service.config.autostart)
        except Exception:
            log.exception("autostart toggle failed")

    def quit_(icon, item):
        service.stop()

    menu = pystray.Menu(
        pystray.MenuItem(f"HomsScanner {VERSION} — يعمل على المنفذ {service.config.port}", None, enabled=False),
        pystray.MenuItem("الإعدادات", lambda icon, item: service.open_settings(), default=True),
        pystray.MenuItem(
            "تشغيل مع ويندوز",
            toggle_autostart,
            checked=lambda item: service.config.autostart,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("خروج", quit_),
    )
    icon = pystray.Icon("HomsScanner", make_icon(64), "ماسح المستندات", menu)
    service.tray = icon
    icon.run()  # blocks until icon.stop()
