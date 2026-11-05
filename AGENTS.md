# AGENTS.md — facts and rules for AI agents

Read this file first. It states what this project is, how it is laid out, and what must not be changed or invented. If something is not written here or visible in the code, **say you do not know** instead of guessing.

## What this project is

**HomsScanner** (version `1.1.0`, `VERSION` in `scanner/config.py`): a **local Windows service** written in Python 3.10+ / Flask. It controls a scanner, stores scanned pages, builds PDFs, and serves a static web UI at `http://127.0.0.1:17650` (default port). The UI can be embedded in a host app (iframe / WebView2) which receives the PDF via `postMessage`.

It is **not** a cloud service, **not** a database app, and has **no front-end build step** (plain HTML/CSS/JS, no npm, no framework).

The Flutter app that embeds it is **a separate project and is not in this repository**. Its Dart files (`lib/services/scanner_embed/*`, `hardware_scanner_page.dart`) are only described in the docs. Never claim to have compiled or tested them.

## Layout

```
app.py              Flask app factory: all /api routes, localhost guard, CSP frame-ancestors
service_main.py     entry point; flags --demo --background --settings --no-tray
scanner/base.py     Device, ScannerError(message, fatal)
scanner/manager.py  device discovery and backend choice (USB first, then network)
scanner/wia_backend.py  twain_backend.py  escl_backend.py  demo_backend.py
scanner/jobs.py     scan jobs; messages are Msg objects translated per request
scanner/store.py    pages on disk, PDF building, save to Desktop
scanner/config.py   config.json, defaults, validation, origin matching, VERSION
scanner/i18n.py     Msg, plural rules, catalog loader, LANGUAGES, RTL
scanner/tray.py  autostart.py  icon.py   Windows tray, autostart, icon
static/             index.html settings.html app.js settings.js i18n.js style.css
static/locales/     en.json ar.json   (single source of truth for all UI/API text)
tests/              pytest suite (88 tests)
packaging/          PyInstaller spec, Inno Setup script, icon script
build_windows.bat   builds the exe, installer and release/homeScanner1.1.0.zip
release/            build output (git-ignored)
docs/images/        README screenshots (en + .ar.png) and SVG diagrams
```

Docs: `README.md` (landing + download), `INSTRUCTIONS*.md` (users), `DEVELOPERS*.md` (developers), `INTEGRATION.md` (older Arabic deployment notes).

## Fixed facts (do not change or contradict)

- Default port `17650`. Config: `%APPDATA%\HomsScanner\config.json`. Config keys: `port`, `language` (`auto`/`en`/`ar`), `autostart`, `allowed_origins`, `defaults`.
- Scanner backends: **WIA** (default, needs `pywin32`), **TWAIN** (optional, `pytwain`), **eSCL/AirScan** (network, mDNS or manual IP), **demo** (`--demo`). WIA/TWAIN/tray/autostart are Windows-only; the rest runs anywhere.
- Security model: every request must have a `Host`/`Origin` of `localhost`, `127.0.0.1` or `::1`; `/` may only be framed by origins in `allowed_origins` (CSP `frame-ancestors`); `/settings` and `/api/settings` are refused inside frames. Do not weaken this.
- Embed URL: `/?embed=1&theme=dark|light&parent=<origin>&lang=en|ar`.
- Messages UI → host (JSON **string**): `homsscan:ready {version, lang}`, `homsscan:pdf {name, pages, size, base64}`, `homsscan:cancel`. iframe: `postMessage(text, parentOrigin)` only after `/api/embed/check` allows the origin; WebView2: `chrome.webview.postMessage(text)`.
- API routes are exactly those in `app.py` (`/api/health config devices scanners scan jobs/<id> pages ... pdf settings embed/check`). Do not invent endpoints.
- Languages: `en` (default, LTR) and `ar` (RTL). Language precedence — server: `X-Lang` > `?lang=` > `Accept-Language` > `config.language` > `en`; UI: `?lang=` > localStorage `homsscan.lang` > service setting > browser > `en`.
- API errors return `{error, code}`; `code` is the stable catalog key (`err.*`).

## Rules when changing code

1. **No user-visible text in code.** Add a key to **both** `static/locales/en.json` and `ar.json` (same placeholders, plural dicts use `zero/one/two/few/many/other`). In Python use `Msg("key", **params)`; in JS use `t("key")` or `data-i18n*` attributes. `tests/test_i18n.py` fails on Arabic literals in code, missing keys, or mismatched placeholders.
2. Plural rules exist twice (`scanner/i18n.py` and `static/i18n.js`); keep them identical.
3. Use CSS **logical properties** (`margin-inline-start`, `inset-inline-*`), never left/right for layout that must mirror in RTL.
4. Adding a language: new `static/locales/<code>.json`, `LANGUAGES`/`RTL` in `scanner/i18n.py`, `LANGS` in `static/i18n.js`.
5. `.bat` files must keep **CRLF** line endings.
6. Keep new Python code compatible with 3.10 and importable on Linux (Windows-only imports must be lazy/guarded) so tests run in CI.
7. Bundled data: `packaging/HomsScanner.spec` ships the whole `static/` folder (including `locales`).
8. Version bump: `scanner/config.py` `VERSION`, `packaging/HomsScanner.iss` `AppVersion`, zip name in `build_windows.bat`, download link in `README.md`.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                      # ~1 minute, all should pass
python service_main.py --demo --no-tray   # UI at http://127.0.0.1:17650
```

Release build (Windows only): `build_windows.bat` → `release\homeScanner1.1.0.zip`.

## What has NOT been verified (do not claim otherwise)

- Real scanner hardware, WIA duplex, TWAIN, the tray icon, autostart, the Inno Setup installer and `build_windows.bat` end-to-end: not run in the Linux environment where the code was written.
- The Flutter integration was never compiled.
- The download link in `README.md` is a placeholder until the zip is uploaded to GitHub Releases.

## Behaviour for agents

- Do not invent files, flags, endpoints, config keys or settings that are not in this repo.
- Do not run `pkill -f` / `pgrep -f` patterns that can match your own shell.
- Prefer small, tested changes; run `python -m pytest` before finishing.
- The project owner writes in Syrian Arabic; reply in the language the user uses. Code, comments and identifiers stay in English.
