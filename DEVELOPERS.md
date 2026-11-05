<div align="center">

# HomsScanner — Developer guide

**A local document-scanning service with a web UI, built to be embedded in your own web or desktop app.**

English · [العربية](DEVELOPERS.ar.md)

</div>

HomsScanner is a small Python (Flask) service that runs on the PC the scanner is plugged into. It talks to the scanner (WIA, TWAIN or network eSCL/AirScan), serves a modern web UI on `http://127.0.0.1:17650`, and turns the scanned pages into a PDF. The same UI can be opened in a browser, or **embedded inside your own application** (an `<iframe>`, Flutter Web, a Windows WebView2, …) which receives the finished PDF through `postMessage`.

![Scan page](docs/images/01-scan-page.png)

## Features

- **Scanner access**: USB via WIA (built into Windows) or TWAIN, and network scanners via eSCL / AirScan (auto-discovery with mDNS or a manual IP).
- **Scan options**: DPI (100–600), colour / grayscale / black & white, flatbed or document feeder, **duplex** (both sides), scanner picker.
- **Page editor**: select pages, rotate, delete per page with **Undo**, drag-and-drop **reorder**, full-screen viewer with zoom and pan.
- **PDF**: name the file, download it, or save it to the desktop. Embedded mode hands the PDF to the host app.
- **Multilingual**: English (default) and Arabic. The UI switches between **LTR and RTL** automatically.
- **Light / dark theme**, controllable by the host app.
- **Windows tray app**: autostart, settings page, `homsscan://start` protocol so a web page can start the service.
- **Private by design**: listens on localhost only, rejects foreign `Host`/`Origin`, and only sites you list may embed it.
- Packaged as a single installer (PyInstaller + Inno Setup); employees do not need Python.

| Pages | Viewer | Embedded in a host app |
|---|---|---|
| ![Pages](docs/images/04-pages.png) | ![Viewer](docs/images/06-viewer.png) | ![Embedded](docs/images/09-embedded.png) |

## How it works

![Architecture](docs/images/architecture.svg)

The service owns the hardware and the page storage; the UI is plain static HTML/CSS/JS served by the same process, so there is no build step for the front end.

## Quick start (from source)

Requirements: Python 3.10+.

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python service_main.py --demo   # fake scanner, no hardware needed
```

Open <http://127.0.0.1:17650>. On Windows you can simply double-click `run.bat` (add `--demo` for the fake scanner).

Command-line flags of `service_main.py`:

| Flag | Meaning |
|---|---|
| `--demo` | fake scanner (no hardware) |
| `--background` | start quietly (used by autostart and `homsscan://start`) |
| `--settings` | open the settings page |
| `--no-tray` | do not create the tray icon |

### Real scanners

1. **WIA** (default on Windows): needs `pywin32`; the scanner must appear in *Windows Scan* / *Fax and Scan*.
2. **TWAIN** (optional): `pip install -r requirements-twain.txt`. A 32-bit TWAIN driver needs 32-bit Python.
3. **Network (eSCL / AirScan)**: HP, Canon, Epson, Brother and most post-2015 models. Found by mDNS, or enter the IP in the settings.

Use **Check available devices** in the UI (or `GET /api/devices`) to see what was found.

![Device check](docs/images/03-device-check.png)

## Build the Windows installer

On a Windows PC with Python 3.10+ (and optionally [Inno Setup 6](https://jrsoftware.org/isinfo.php)):

```bat
build_windows.bat
```

Output: `dist\HomsScanner\HomsScanner.exe` and, if Inno Setup is installed, `dist\HomsScanner-Setup.exe` — the single file to hand to users. The installer registers autostart and the `homsscan://` protocol. `static/` (including `static/locales/`) is bundled by `packaging/HomsScanner.spec`.

## Configuration

Settings live in `%APPDATA%\HomsScanner\config.json` (`~/.config/HomsScanner` on non-Windows). Edit them from the tray icon → **Settings** or at <http://127.0.0.1:17650/settings> (not available inside an iframe).

![Settings](docs/images/10-settings.png)

| Key | Default | Notes |
|---|---|---|
| `port` | `17650` | restart needed after a change |
| `language` | `"auto"` | `auto`, `en`, `ar` |
| `autostart` | `true` | start with Windows |
| `allowed_origins` | `["http://localhost:*", "http://127.0.0.1:*"]` | sites allowed to embed the UI; **add your production site** |
| `defaults` | dpi 300, colour, flatbed, simplex, mode auto | initial scan options |

## Languages (i18n)

English is the default. The language is picked in this order:

- **Server** (API messages): `X-Lang` header › `?lang=` › `Accept-Language` › `config.language` (`auto` = Windows language) › `en`.
- **UI**: `?lang=` (set by a host app; the selector is then hidden) › the user's choice (`localStorage`) › service setting › browser language › `en`.

Direction (`ltr`/`rtl`) follows the language. Strings live in `static/locales/<code>.json` and are shared by the Python side and the browser. API errors also carry a stable `code` (the message key) so your app can translate them itself.

### Add a language

1. Copy `static/locales/en.json` to `static/locales/<code>.json` and translate the values (keep `{placeholders}`; plural entries use `zero/one/two/few/many/other`).
2. `scanner/i18n.py`: add the code to `LANGUAGES` (and to `RTL` if it is right-to-left).
3. `static/i18n.js`: add it to `LANGS` (`{ name, dir }`). If the plural rules differ, update both `plural_form` (Python) and the JS equivalent.
4. `python -m pytest tests/test_i18n.py` verifies key and placeholder parity across languages.

## Embedding it in your app

Load the UI and listen for its messages:

```
http://127.0.0.1:17650/?embed=1&theme=dark|light&parent=<your-origin>&lang=en|ar
```

| Parameter | Meaning |
|---|---|
| `embed=1` | embedded layout; the PDF is sent to the host instead of downloaded |
| `theme` | `dark` or `light` |
| `parent` | your page's origin, used as the `postMessage` target (iframe mode) |
| `lang` | `en` / `ar`; locks the language and hides the selector |

![Embed flow](docs/images/embed-flow.svg)

**Messages** (the UI → your app; the payload is a JSON **string**):

| `type` | Fields | When |
|---|---|---|
| `homsscan:ready` | `version`, `lang` | the UI finished loading |
| `homsscan:pdf` | `name`, `pages`, `size`, `base64` | the user pressed *Save & attach* |
| `homsscan:cancel` | – | the user closed / cancelled |

### Generic web app (any framework)

1. In **Settings → “Websites allowed to show the scan page”** add your site's origin, e.g. `https://docs.example.org` (this controls the `frame-ancestors` CSP; wildcards like `http://localhost:*` are allowed).
2. Embed the iframe and listen:

```html
<iframe id="scanner" allow="" style="width:100%;height:720px;border:0"></iframe>
<script>
  const SERVICE = "http://127.0.0.1:17650";
  const frame = document.getElementById("scanner");
  frame.src = `${SERVICE}/?embed=1&theme=light&lang=en&parent=${encodeURIComponent(location.origin)}`;

  window.addEventListener("message", (e) => {
    if (e.origin !== SERVICE) return;               // always validate the sender
    const msg = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
    if (msg.type === "homsscan:pdf") {
      const bytes = Uint8Array.from(atob(msg.base64), c => c.charCodeAt(0));
      const file = new File([bytes], msg.name, { type: "application/pdf" });
      // upload `file`, attach it to a form, etc.
    }
  });
</script>
```

Optionally check first that the service is running (`GET /api/health`) and that your origin is allowed (`GET /api/embed/check?origin=<origin>`); if it is not running, open `homsscan://start` to launch it.

### Flutter

The ready-made integration lives in `lib/services/scanner_embed/` of the Flutter app (not part of this repo's Python code):

- **Flutter Web**: an `<iframe>` through `HtmlElementView` (`scanner_embed_web.dart`).
- **Windows desktop**: `webview_windows` / WebView2 (`scanner_embed_io.dart`); messages arrive through `chrome.webview.postMessage`, so no `parent` is needed.
- Add `webview_windows: ^0.4.0` to `pubspec.yaml`, then use the `ScannerEmbedView` widget (`baseUrl`, `darkTheme`, `onMessage`). Pass `languageCode: Get.locale?.languageCode` so the scanner follows the app language (it becomes `lang=`).
- Port or URL changes go in `homs_scanner_config.dart`.

### Other hosts

Anything that can show a web page works: **WebView2** (WPF/WinForms/.NET MAUI — read `window.chrome.webview` messages via `WebMessageReceived`), Electron/Tauri, or an Android/iOS WebView pointing at the PC's service (only localhost is accepted, so run it on the same machine). In a WebView (no iframe) messages are sent with `window.chrome.webview.postMessage(text)`.

### Security notes

- The service only answers requests whose `Host` and `Origin` are `localhost`/`127.0.0.1`.
- Only origins in `allowed_origins` may frame the page; the settings page can never be framed.
- Validate `event.origin` and the message `type` in your app; treat the PDF as untrusted input.

## HTTP API (summary)

| Method & path | Purpose |
|---|---|
| `GET /api/health`, `/api/config`, `/api/devices` | status, defaults, discovered scanners |
| `POST /api/scan` · `GET /api/jobs/<id>` · `POST /api/jobs/<id>/cancel` | scan and follow progress |
| `GET /api/pages` · `/api/pages/<id>/image` · `/thumb` | scanned pages |
| `POST /api/pages/rotate` `delete` `reorder` `trash` `restore` `clear` | page editing |
| `POST /api/pdf` · `GET /api/pdf/<name>` · `POST /api/pdf/<name>/desktop` | build / download / save to desktop |
| `GET/POST /api/settings` | settings (blocked inside iframes) |

Send `X-Lang: en|ar` to receive translated messages.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest            # unit + API tests, incl. i18n
python service_main.py --demo --no-tray
```

Project layout:

```
app.py                 Flask app: API, CSP, localhost guard
service_main.py        entry point (tray, autostart, CLI flags)
scanner/               backends (wia, twain, escl, demo), jobs, config, i18n, tray
static/                index.html, settings.html, app.js, settings.js, i18n.js, style.css
static/locales/        en.json, ar.json
tests/                 pytest suite
packaging/             PyInstaller spec, Inno Setup script, icon
docs/images/           README screenshots and diagrams
```

Notes: never hard-code user-facing text — add a key to every `static/locales/*.json` (tests enforce it). Use CSS logical properties (`margin-inline-start`, …) so RTL keeps working.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No scanner found" | Open **Check available devices**. Make sure the scanner shows up in Windows Scan. |
| "pywin32 is not installed" | Re-run `run.bat`, or `pip install pywin32`. |
| Only TWAIN works | `pip install -r requirements-twain.txt` (match Python bitness to the driver). |
| The embedded page is blank | Add your site to the allowed websites list in Settings, then reload. |
| Service not running | Open `homsscan://start` or start the tray app. |

## Contributing & license

Issues and pull requests are welcome; run `python -m pytest` before sending one. A license has not been chosen yet — add a `LICENSE` file before publishing.

See also [`INTEGRATION.md`](INTEGRATION.md) (Arabic) for deployment notes.
