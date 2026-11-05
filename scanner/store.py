"""Scanned pages on disk, PDF building and saving to the Desktop."""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import img2pdf
from PIL import Image

THUMB_SIZE = (420, 560)
JPEG_QUALITY = 90
PDF_NAME_RE = re.compile(r"^scan_\d{8}_\d{6}(_\d+)?\.pdf$")
TRASH_SECONDS = 60  # a deleted page can be restored (Undo) for this long
MAX_NAME_LEN = 100
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def sanitize_filename(name, default: str | None = None) -> str:
    """A safe Windows file name ending in .pdf (Arabic is fine).

    Removes path separators and characters Windows forbids, control characters,
    trailing dots/spaces and reserved device names (CON, NUL, COM1...), and limits the length.
    """
    text = unicodedata.normalize("NFC", str(name or ""))
    text = _ILLEGAL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    text = text.strip(" .")[:MAX_NAME_LEN].strip(" .")
    if not text:
        text = str(default or datetime.now().strftime("scan_%Y%m%d_%H%M%S"))
        if text.lower().endswith(".pdf"):
            text = text[:-4]
    if text.split(".")[0].lower() in _RESERVED:
        text = "_" + text
    return text + ".pdf"


@dataclass
class Page:
    id: str
    width: int
    height: int
    dpi: int
    rev: int = 0  # bumped on rotate so the browser reloads the image

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "rev": self.rev,
        }


def _normalize(img: Image.Image) -> Image.Image:
    """JPEG can only hold RGB or L, so convert everything else."""
    if img.mode in ("RGB", "L"):
        return img
    if img.mode == "1":
        return img.convert("L")
    has_alpha = "A" in img.getbands() or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def _unique(directory: Path, stem: str, ext: str, style: str) -> str:
    name = f"{stem}{ext}"
    i = 0
    while (directory / name).exists():
        i += 1
        name = f"{stem}_{i}{ext}" if style == "underscore" else f"{stem} ({i}){ext}"
    return name


def desktop_dir() -> Path:
    """The user's real Desktop folder (handles OneDrive-redirected Desktops)."""
    override = os.environ.get("SCANNER_DESKTOP")
    if override:
        return Path(override)
    if sys.platform == "win32":
        try:
            import ctypes

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            # FOLDERID_Desktop
            guid = GUID.from_buffer_copy(
                uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le
            )
            buf = ctypes.c_wchar_p()
            res = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(buf)
            )
            if res == 0 and buf.value:
                path = buf.value
                ctypes.windll.ole32.CoTaskMemFree(buf)
                return Path(path)
        except Exception:  # fall back to the plain path below
            pass
    return Path.home() / "Desktop"


class PageStore:
    def __init__(self, root):
        self.root = Path(root)
        self.pages_dir = self.root / "pages"
        self.pdf_dir = self.root / "pdf"
        self._lock = threading.RLock()
        self.trash_dir = self.root / "trash"
        self._pages: list[Page] = []
        self._trash: dict[str, tuple[Page, int, float]] = {}  # id -> (page, old index, time)
        # Scans can be private documents: start every run with an empty folder.
        for d in (self.pages_dir, self.pdf_dir, self.trash_dir):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

    # ---- helpers -------------------------------------------------------
    def _path(self, pid: str, thumb: bool = False) -> Path:
        return self.pages_dir / f"{pid}{'_t' if thumb else ''}.jpg"

    def _write_thumb(self, image: Image.Image, pid: str) -> None:
        thumb = image.copy()
        thumb.thumbnail(THUMB_SIZE)
        thumb.save(self._path(pid, thumb=True), "JPEG", quality=80)

    # ---- pages ---------------------------------------------------------
    def add(self, image: Image.Image, dpi: int) -> Page:
        image = _normalize(image)
        dpi = int(dpi) if dpi else 300
        pid = uuid.uuid4().hex[:12]
        image.save(self._path(pid), "JPEG", quality=JPEG_QUALITY, dpi=(dpi, dpi))
        self._write_thumb(image, pid)
        page = Page(pid, image.width, image.height, dpi)
        with self._lock:
            self._pages.append(page)
        return page

    def list(self) -> list[Page]:
        with self._lock:
            return list(self._pages)

    def image_path(self, pid: str, thumb: bool = False):
        with self._lock:
            if not any(p.id == pid for p in self._pages):
                return None
        path = self._path(pid, thumb)
        return path if path.exists() else None

    def rotate(self, ids, degrees: int) -> None:
        """Rotate clockwise by 90 / -90 / 180 degrees."""
        if degrees not in (90, -90, 180):
            raise ValueError("degrees must be 90, -90 or 180")
        with self._lock:
            by_id = {p.id: p for p in self._pages}
            for pid in ids:
                page = by_id.get(pid)
                if page is None:
                    continue
                with Image.open(self._path(pid)) as im:
                    rotated = im.rotate(-degrees, expand=True)
                rotated.save(
                    self._path(pid), "JPEG", quality=JPEG_QUALITY, dpi=(page.dpi, page.dpi)
                )
                self._write_thumb(rotated, pid)
                page.width, page.height = rotated.size
                page.rev += 1

    def delete(self, ids) -> None:
        with self._lock:
            wanted = set(ids)
            for page in [p for p in self._pages if p.id in wanted]:
                self._path(page.id).unlink(missing_ok=True)
                self._path(page.id, thumb=True).unlink(missing_ok=True)
                self._pages.remove(page)

    def clear(self) -> None:
        with self._lock:
            self.delete([p.id for p in self._pages])
            self._purge_trash(force=True)

    # ---- order ---------------------------------------------------------
    def reorder(self, ids) -> None:
        """Put the pages in the order given. Pages not listed (e.g. scanned meanwhile) keep
        their relative order after the listed ones; unknown ids are ignored."""
        with self._lock:
            by_id = {p.id: p for p in self._pages}
            seen: set[str] = set()
            ordered = []
            for pid in ids:
                if pid in by_id and pid not in seen:
                    seen.add(pid)
                    ordered.append(by_id[pid])
            ordered += [p for p in self._pages if p.id not in seen]
            self._pages = ordered

    # ---- delete with Undo ----------------------------------------------
    def _purge_trash(self, force: bool = False) -> None:
        now = time.time()
        for pid in [i for i, (_, _, t) in self._trash.items() if force or now - t > TRASH_SECONDS]:
            for thumb in (False, True):
                (self.trash_dir / self._path(pid, thumb).name).unlink(missing_ok=True)
            self._trash.pop(pid, None)

    def trash(self, ids) -> None:
        """Remove pages from the list but keep their files for a minute so Undo can bring them back."""
        with self._lock:
            self._purge_trash()
            wanted = set(ids)
            for index, page in reversed(list(enumerate(self._pages))):
                if page.id not in wanted:
                    continue
                for thumb in (False, True):
                    src = self._path(page.id, thumb)
                    if src.exists():
                        shutil.move(str(src), str(self.trash_dir / src.name))
                self._trash[page.id] = (page, index, time.time())
                self._pages.remove(page)

    def restore(self, ids) -> None:
        with self._lock:
            self._purge_trash()
            entries = sorted(
                (self._trash[i] for i in ids if i in self._trash), key=lambda e: e[1]
            )
            for page, index, _ in entries:
                for thumb in (False, True):
                    src = self.trash_dir / self._path(page.id, thumb).name
                    if src.exists():
                        shutil.move(str(src), str(self._path(page.id, thumb)))
                self._pages.insert(min(index, len(self._pages)), page)
                self._trash.pop(page.id, None)

    # ---- PDF -----------------------------------------------------------
    def build_pdf(self, ids) -> tuple[str, int]:
        """Build a PDF from the given pages (in the given order). Returns (internal file name, pages)."""
        with self._lock:
            by_id = {p.id: p for p in self._pages}
            pages = [by_id[i] for i in ids if i in by_id]
            if not pages:
                raise ValueError("no pages selected")
            stem = datetime.now().strftime("scan_%Y%m%d_%H%M%S")
            name = _unique(self.pdf_dir, stem, ".pdf", "underscore")
            data = img2pdf.convert([str(self._path(p.id)) for p in pages])
            (self.pdf_dir / name).write_bytes(data)
        return name, len(pages)

    def pdf_path(self, name: str):
        if not PDF_NAME_RE.match(name):
            return None
        path = self.pdf_dir / name
        return path if path.is_file() else None

    @staticmethod
    def save_to_desktop(pdf: Path, name: str | None = None) -> Path:
        """Copy the PDF to the real Desktop folder, as `name` (sanitized) if given.
        Never overwrites: 'name (1).pdf', 'name (2).pdf'..."""
        target_dir = desktop_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        wanted = Path(sanitize_filename(name, default=pdf.name)) if name else pdf
        name = _unique(target_dir, wanted.stem, wanted.suffix, "paren")
        dest = target_dir / name
        shutil.copy2(pdf, dest)
        return dest
