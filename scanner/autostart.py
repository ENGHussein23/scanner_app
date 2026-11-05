"""Start the service when the user logs in to Windows (HKCU Run key - no admin rights needed).

A real Windows *Service* is NOT used on purpose: services run in session 0 and cannot talk to
scanner drivers (WIA / TWAIN). A per-user startup entry runs in the user's own session.
"""
from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "HomsScanner"


def command_line() -> str:
    """Command stored in the Run key (quoted so paths with spaces work)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    script = Path(__file__).resolve().parent.parent / "service_main.py"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" "{script}" --background'


def supported() -> bool:
    return sys.platform == "win32"


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if not supported():
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command_line())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
