# PyInstaller spec - build with:  pyinstaller --noconfirm --clean packaging\HomsScanner.spec
# One-folder build (fast start, fewer antivirus false alarms); the installer wraps it in ONE Setup file.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
icon = root / "packaging" / "icon.ico"

hidden = (
    ["win32com.client", "pythoncom", "pywintypes", "twain", "pystray._win32", "PIL._tkinter_finder"]
    + collect_submodules("zeroconf")
    + collect_submodules("scanner")
)

a = Analysis(
    [str(root / "service_main.py")],
    pathex=[str(root)],
    datas=[(str(root / "static"), "static")],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "pypdf", "unittest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="HomsScanner",
    console=False,               # no black window: logs go to %APPDATA%\HomsScanner\logs
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="HomsScanner")
