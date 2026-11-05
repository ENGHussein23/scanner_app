"""Creates packaging/icon.ico (run by build_windows.bat)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scanner.icon import make_icon  # noqa: E402

out = Path(__file__).with_name("icon.ico")
make_icon(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
