"""HomsScanner background service: web scanner server + tray icon (+ start with Windows).

    HomsScanner.exe                 # start (opens the settings page if it was started by hand)
    HomsScanner.exe --background    # start quietly (used at Windows start and by homsscan://start)
    HomsScanner.exe --settings      # open the settings page
    python service_main.py --demo   # fake scanner for testing
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import threading
import time
import urllib.request
import webbrowser

from werkzeug.serving import make_server

from app import create_app
from scanner import autostart
from scanner.config import VERSION, Config, app_home, data_home

log = logging.getLogger("homsscan")


def setup_logging() -> None:
    logs = app_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        logs / "service.log", maxBytes=500_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:  # None when built without a console
        root.addHandler(logging.StreamHandler())
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return bool(json.load(r).get("app") == "HomsScanner")
    except Exception:
        return False


class Service:
    """Runs the Flask app in a background thread so the tray can control it."""

    def __init__(self, config: Config, demo: bool = False):
        self.config = config
        self.app = create_app(demo=demo, data_dir=data_home(), config=config)
        self.app.config["on_settings_changed"] = self._settings_changed
        self.server = make_server("127.0.0.1", config.port, self.app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="http")
        self.stopped = threading.Event()
        self.tray = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.config.port}"

    def start(self) -> None:
        self.thread.start()
        log.info("HomsScanner %s listening on %s", VERSION, self.url)

    def stop(self) -> None:
        log.info("stopping")
        self.server.shutdown()
        self.stopped.set()
        if self.tray:
            self.tray.stop()

    def open_settings(self) -> None:
        webbrowser.open(f"{self.url}/settings")

    def _settings_changed(self, config: Config) -> None:
        try:
            autostart.set_enabled(config.autostart)
        except Exception:
            log.exception("could not update the Windows startup entry")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HomsScanner service")
    parser.add_argument("--background", action="store_true", help="start quietly")
    parser.add_argument("--settings", action="store_true", help="open the settings page")
    parser.add_argument("--demo", action="store_true", help="fake scanner (no hardware)")
    parser.add_argument("--no-tray", action="store_true")
    args, _unknown = parser.parse_known_args(argv)  # homsscan://start may be passed too

    setup_logging()
    config = Config()

    if already_running(config.port):
        log.info("already running on port %s", config.port)
        if args.settings or not args.background:
            webbrowser.open(f"http://127.0.0.1:{config.port}/settings")
        return 0

    try:
        service = Service(config, demo=args.demo)
    except OSError as exc:
        log.error("cannot listen on port %s: %s", config.port, exc)
        return 2
    service.start()

    try:
        if getattr(sys, "frozen", False) or autostart.is_enabled() != config.autostart:
            autostart.set_enabled(config.autostart)
    except Exception:
        log.exception("could not update the Windows startup entry")

    if args.settings or not args.background:
        service.open_settings()

    if not args.no_tray:
        try:
            from scanner.tray import run_tray

            run_tray(service)  # blocks until "Exit"
            return 0
        except ImportError:
            log.warning("pystray is not installed: running without a tray icon")
        except Exception:
            log.exception("tray failed: running without a tray icon")

    try:
        while not service.stopped.wait(1):
            pass
    except KeyboardInterrupt:
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
