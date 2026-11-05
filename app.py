"""Web scanner: scan from a USB or network scanner, pick pages, export a PDF.

    python app.py            # normal use
    python app.py --demo     # fake scanner, no hardware needed
"""
from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_file, send_from_directory

from scanner.base import ScanOptions
from scanner.config import VERSION, Config
from scanner.jobs import BusyError, JobManager
from scanner.manager import ScannerManager, load_backends
from scanner.store import PageStore, sanitize_filename

BASE_DIR = Path(__file__).resolve().parent
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
DPI_CHOICES = {100, 150, 200, 300, 400, 600}
COLORS = {"color", "gray", "bw"}
SOURCES = {"flatbed", "feeder"}
MODES = {"auto", "usb", "network"}


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header[1 : host_header.find("]")]
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


def create_app(demo: bool = False, data_dir=None, config: Config | None = None, backends=None) -> Flask:
    app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")
    config = config or Config()
    started = time.time()
    store = PageStore(data_dir or BASE_DIR / "data")
    manager = ScannerManager(backends if backends is not None else load_backends(demo))
    jobs = JobManager(manager, store)
    app.config.update(store=store, manager=manager, jobs=jobs, demo=demo, scanner_config=config)

    def error(message: str, status: int = 400, **extra):
        return jsonify(error=message, **extra), status

    # ---- protection: this app serves private scans on localhost ---------
    @app.before_request
    def only_local():
        if _hostname(request.host) not in LOCAL_HOSTS:
            return error("Forbidden", 403)
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
            return error("Forbidden", 403)

    @app.after_request
    def add_headers(resp):
        if resp.mimetype == "application/json":
            resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.path == "/":
            # Only the sites listed in the settings may show the scanner page in an iframe.
            ancestors = " ".join(["'self'", *config.allowed_origins])
            resp.headers["Content-Security-Policy"] = f"frame-ancestors {ancestors}"
        elif request.path == "/settings":
            resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return resp

    def _is_framed() -> bool:
        return request.headers.get("Sec-Fetch-Dest", "").lower() in ("iframe", "frame", "embed", "object")

    # ---- pages of the UI --------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/settings")
    def settings_page():
        if _is_framed():
            return error("Forbidden", 403)
        return send_from_directory(app.static_folder, "settings.html")

    # ---- service status / embedding ----------------------------------------
    @app.get("/api/health")
    def api_health():
        return jsonify(
            ok=True, app="HomsScanner", version=VERSION, demo=demo,
            port=config.port, uptime=int(time.time() - started),
        )

    @app.get("/api/config")
    def api_config():
        """What the scan page needs: the default scan options."""
        return jsonify(defaults=config.to_json()["defaults"])

    @app.get("/api/embed/check")
    def api_embed_check():
        return jsonify(allowed=config.origin_allowed(request.args.get("origin")))

    @app.get("/api/settings")
    def api_settings_get():
        if _is_framed():
            return error("Forbidden", 403)
        return jsonify(config.to_json())

    @app.post("/api/settings")
    def api_settings_set():
        if _is_framed():
            return error("Forbidden", 403)
        body = request.get_json(silent=True)
        old_port = config.port
        try:
            config.update(body)
        except ValueError as exc:
            return error(str(exc))
        hook = app.config.get("on_settings_changed")
        if hook:
            hook(config)
        return jsonify(settings=config.to_json(), restart_needed=config.port != old_port)

    # ---- scanning -----------------------------------------------------------
    @app.get("/api/devices")
    def api_devices():
        if jobs.active():
            return error("في عملية سكان شغّالة حالياً.", 409)
        host = (request.args.get("host") or "").strip()[:100] or None
        return jsonify(manager.diagnose(host))

    @app.get("/api/scanners")
    def api_scanners():
        """All scanners found (USB + network) so the user can pick one before scanning."""
        if jobs.active():
            return error("في عملية سكان شغّالة حالياً.", 409)
        host = (request.args.get("host") or "").strip()[:100] or None
        devices, errors = manager.list_devices(host, threading.Event())
        return jsonify(devices=[d.to_json() for d in devices], errors=errors)

    @app.post("/api/scan")
    def api_scan():
        body = request.get_json(silent=True) or {}
        try:
            dpi = int(body.get("dpi", 300))
        except (TypeError, ValueError):
            dpi = 0
        color = body.get("color", "color")
        source = body.get("source", "flatbed")
        mode = body.get("mode", "auto")
        host = str(body.get("host") or "").strip()[:100] or None
        duplex = body.get("duplex", False)
        device = body.get("device") or None
        if dpi not in DPI_CHOICES or color not in COLORS or source not in SOURCES or mode not in MODES:
            return error("إعدادات غير صحيحة.")
        if not isinstance(duplex, bool) or (device is not None and (not isinstance(device, str) or len(device) > 80)):
            return error("إعدادات غير صحيحة.")
        if duplex and source != "feeder":
            return error("مسح الوجهين بيحتاج التغذية التلقائية (feeder).")
        try:
            job = jobs.start(ScanOptions(dpi=dpi, color=color, source=source, duplex=duplex), mode, host, device)
        except BusyError as exc:
            return error("في عملية سكان شغّالة حالياً.", 409, job_id=exc.job_id)
        return jsonify(job_id=job.id), 202

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id):
        job = jobs.get(job_id)
        if job is None:
            return error("المهمة مو موجودة.", 404)
        return jsonify(job.to_json())

    @app.post("/api/jobs/<job_id>/cancel")
    def api_cancel(job_id):
        job = jobs.get(job_id)
        if job is None:
            return error("المهمة مو موجودة.", 404)
        job.cancel.set()
        return jsonify(ok=True)

    # ---- pages ---------------------------------------------------------------
    @app.get("/api/pages")
    def api_pages():
        return jsonify(pages=[p.to_json() for p in store.list()])

    def _image(pid, thumb):
        path = store.image_path(pid, thumb)
        if path is None:
            return error("الصفحة مو موجودة.", 404)
        return send_file(path, mimetype="image/jpeg", max_age=3600)

    @app.get("/api/pages/<pid>/image")
    def api_image(pid):
        return _image(pid, False)

    @app.get("/api/pages/<pid>/thumb")
    def api_thumb(pid):
        return _image(pid, True)

    def _ids(body):
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return None
        return ids

    @app.post("/api/pages/rotate")
    def api_rotate():
        body = request.get_json(silent=True) or {}
        ids = _ids(body)
        if ids is None or body.get("degrees") not in (90, -90, 180):
            return error("طلب غير صحيح.")
        store.rotate(ids, body["degrees"])
        return jsonify(pages=[p.to_json() for p in store.list()])

    @app.post("/api/pages/delete")
    def api_delete():
        ids = _ids(request.get_json(silent=True) or {})
        if ids is None:
            return error("طلب غير صحيح.")
        store.delete(ids)
        return jsonify(pages=[p.to_json() for p in store.list()])

    @app.post("/api/pages/reorder")
    def api_reorder():
        ids = _ids(request.get_json(silent=True) or {})
        if ids is None:
            return error("طلب غير صحيح.")
        store.reorder(ids)
        return jsonify(pages=[p.to_json() for p in store.list()])

    @app.post("/api/pages/trash")
    def api_trash():
        """Delete pages but keep them for a minute so «تراجع» (undo) works."""
        ids = _ids(request.get_json(silent=True) or {})
        if ids is None:
            return error("طلب غير صحيح.")
        store.trash(ids)
        return jsonify(pages=[p.to_json() for p in store.list()])

    @app.post("/api/pages/restore")
    def api_restore():
        ids = _ids(request.get_json(silent=True) or {})
        if ids is None:
            return error("طلب غير صحيح.")
        store.restore(ids)
        return jsonify(pages=[p.to_json() for p in store.list()])

    @app.post("/api/pages/clear")
    def api_clear():
        if jobs.active():
            return error("استنى لحد ما يخلص السكان.", 409)
        store.clear()
        return jsonify(pages=[])

    # ---- PDF -----------------------------------------------------------------
    @app.post("/api/pdf")
    def api_pdf():
        body = request.get_json(silent=True) or {}
        ids = _ids(body)
        if not ids:
            return error("اختار صفحة وحدة على الأقل.")
        try:
            name, count = store.build_pdf(ids)
        except ValueError:
            return error("الصفحات المختارة ما عادت موجودة.")
        size = store.pdf_path(name).stat().st_size
        # What the user typed (cleaned). The internal file name stays scan_<time>.pdf.
        display = sanitize_filename(body.get("name"), default=name)
        return jsonify(
            file=name, display_name=display, pages=count, size=size,
            download_url=f"/api/pdf/{name}",
        )

    @app.get("/api/pdf/<name>")
    def api_pdf_download(name):
        path = store.pdf_path(name)
        if path is None:
            return error("الملف مو موجود.", 404)
        download_name = sanitize_filename(request.args.get("name"), default=name) if request.args.get("name") else name
        return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=download_name)

    @app.post("/api/pdf/<name>/desktop")
    def api_pdf_desktop(name):
        path = store.pdf_path(name)
        if path is None:
            return error("الملف مو موجود.", 404)
        wanted = (request.get_json(silent=True) or {}).get("name")
        try:
            dest = store.save_to_desktop(path, wanted if isinstance(wanted, str) else None)
        except OSError as exc:
            return error(f"ما قدرت احفظ على سطح المكتب: {exc}", 500)
        return jsonify(path=str(dest))

    return app


def pick_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("No free port found.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web scanner")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--demo", action="store_true", help="use a fake scanner (no hardware)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    app = create_app(demo=args.demo)
    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Scanner app is running at {url}")
    print("  Keep this window open while you scan. Press Ctrl+C to quit.\n")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
