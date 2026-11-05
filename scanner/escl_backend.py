"""Network scanners through eSCL (AirScan / Mopria).

Almost every network scanner/MFP made after ~2015 speaks eSCL (HP, Canon, Epson,
Brother, Xerox...). We find them with mDNS (Bonjour) or by IP typed by the user,
then talk plain HTTP(S):

    POST  /eSCL/ScanJobs                 -> 201 + Location header
    GET   <job>/NextDocument             -> one page (404 = no more pages)
    DELETE <job>
"""
from __future__ import annotations

import io
import logging
import threading
import time
import xml.etree.ElementTree as ET
from typing import Iterator, Optional
from urllib.parse import urljoin, urlparse

from PIL import Image

from .base import Backend, Device, ScanCancelled, ScanOptions, ScanResult, ScannerError, StatusFn

log = logging.getLogger(__name__)

SERVICES = ("_uscan._tcp.local.", "_uscans._tcp.local.")  # http, https
COLOR_MODES = {"color": "RGB24", "gray": "Grayscale8", "bw": "BlackAndWhite1"}
A4_300 = (2480, 3508)  # width, height in 1/300 inch

SETTINGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:escl="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>{width}</pwg:Width>
      <pwg:Height>{height}</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>{source}</pwg:InputSource>{duplex}
  <scan:ColorMode>{color}</scan:ColorMode>
  <scan:XResolution>{dpi}</scan:XResolution>
  <scan:YResolution>{dpi}</scan:YResolution>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
</scan:ScanSettings>
"""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(root, name):
    for el in root.iter():
        if _local(el.tag) == name:
            return el
    return None


def _child_int(parent, name) -> Optional[int]:
    if parent is None:
        return None
    for el in parent.iter():
        if _local(el.tag) == name and el.text and el.text.strip().isdigit():
            return int(el.text.strip())
    return None


class EsclBackend(Backend):
    name = "escl"
    kind = "network"

    def available(self) -> tuple[bool, str]:
        try:
            import requests  # noqa: F401
        except ImportError:
            return False, "مكتبة requests مو مثبتة (pip install requests)"
        try:
            import zeroconf  # noqa: F401
        except ImportError:
            # Still usable: the user can type the scanner's IP by hand.
            return True, "مكتبة zeroconf مو مثبتة، فالبحث التلقائي بالشبكة معطّل (اكتب عنوان الـ IP يدوياً)"
        return True, ""

    # ---- discovery -----------------------------------------------------
    def discover(
        self, cancel: threading.Event, host: Optional[str] = None, timeout: float = 4.0
    ) -> list[Device]:
        devices: list[Device] = []
        if host:
            devices = self._probe_host(host)
            if devices:  # the user told us exactly where it is, no need to search
                return devices
        try:
            devices.extend(self._mdns(cancel, timeout))
        except ScanCancelled:
            raise
        except Exception as exc:  # no network, firewall...
            log.warning("mDNS discovery failed: %s", exc)
        seen, unique = set(), []
        for dev in devices:
            if dev.id not in seen:
                seen.add(dev.id)
                unique.append(dev)
        return unique

    def _mdns(self, cancel: threading.Event, timeout: float) -> list[Device]:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

        found: dict[tuple[str, str], bool] = {}

        class Listener(ServiceListener):
            def add_service(self, zc, type_, name):
                found[(type_, name)] = True

            def update_service(self, zc, type_, name):
                found[(type_, name)] = True

            def remove_service(self, zc, type_, name):
                pass

        zc = Zeroconf()
        try:
            browsers = [ServiceBrowser(zc, t, Listener()) for t in SERVICES]
            start = time.monotonic()
            while time.monotonic() - start < timeout:
                if cancel.is_set():
                    raise ScanCancelled()
                time.sleep(0.25)
                if found and time.monotonic() - start > 1.5:
                    break
            devices: dict[str, Device] = {}
            for (type_, name) in sorted(found):  # "_uscan" (http) sorts before "_uscans"
                info = zc.get_service_info(type_, name, timeout=2000)
                if info is None:
                    continue
                addresses = [a for a in info.parsed_addresses() if ":" not in a]  # IPv4 only
                if not addresses:
                    continue
                props = {
                    (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                    for k, v in (info.properties or {}).items()
                    if v is not None
                }
                rs = (props.get("rs") or "eSCL").strip("/")
                scheme = "https" if type_.startswith("_uscans") else "http"
                base = f"{scheme}://{addresses[0]}:{info.port}/{rs}"
                label = props.get("ty") or name.split("._")[0]
                if addresses[0] not in devices:  # one entry per scanner, http preferred
                    devices[addresses[0]] = Device("escl", base, label, "network", {"base": base})
            for b in browsers:
                b.cancel()
            return list(devices.values())
        finally:
            zc.close()

    def _probe_host(self, host: str) -> list[Device]:
        """Try an IP / hostname typed by the user."""
        import requests

        raw = host.strip()
        if "://" in raw:
            parsed = urlparse(raw)
            hostport = parsed.netloc
            path = parsed.path.strip("/") or "eSCL"
        else:
            hostport, _, path = raw.partition("/")
            path = path.strip("/") or "eSCL"
        if not hostport:
            return []
        if ":" in hostport:
            port = hostport.rsplit(":", 1)[1]
            candidates = [f"{'https' if port == '443' else 'http'}://{hostport}/{path}"]
        else:
            candidates = [
                f"http://{hostport}/{path}",
                f"https://{hostport}/{path}",
                f"http://{hostport}:8080/{path}",
            ]
        for base in candidates:
            try:
                resp = requests.get(
                    base + "/ScannerCapabilities", timeout=3, verify=not base.startswith("https")
                )
            except requests.RequestException:
                continue
            if resp.status_code == 200:
                model = self._model_name(resp.content) or hostport
                return [Device("escl", base, model, "network", {"base": base})]
        return []

    @staticmethod
    def _model_name(xml_bytes: bytes) -> Optional[str]:
        try:
            el = _first(ET.fromstring(xml_bytes), "MakeAndModel")
        except ET.ParseError:
            return None
        return el.text.strip() if el is not None and el.text else None

    # ---- scanning ------------------------------------------------------
    def scan(
        self, device: Device, opts: ScanOptions, cancel: threading.Event, status: StatusFn
    ) -> Iterator[ScanResult]:
        import requests

        base = device.extra["base"]
        origin = "{0.scheme}://{0.netloc}".format(urlparse(base))
        session = requests.Session()
        if base.startswith("https"):
            # Scanners use self-signed certificates on the local network.
            session.verify = False
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        feeder = opts.source == "feeder"
        duplex = feeder and opts.duplex
        try:
            caps_resp = session.get(base + "/ScannerCapabilities", timeout=10)
            caps_resp.raise_for_status()
            caps = ET.fromstring(caps_resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            raise ScannerError(f"ما قدرت اتصل بالسكانر على الشبكة: {exc}")

        if duplex and _first(caps, "AdfDuplexInputCaps") is None:
            raise ScannerError("هاد السكانر ما بيدعم مسح الوجهين (duplex).")
        input_caps = _first(caps, "AdfDuplexInputCaps" if duplex else "AdfSimplexInputCaps" if feeder else "PlatenInputCaps")
        if input_caps is None:
            if feeder and _first(caps, "Adf") is None:
                raise ScannerError("هاد السكانر ما عنده تغذية تلقائية (feeder).", fatal=True)
            if not feeder and _first(caps, "Platen") is None:
                raise ScannerError("هاد السكانر ما عنده سطح زجاجي (flatbed).", fatal=True)
        width = _child_int(input_caps, "MaxWidth") or A4_300[0]
        height = _child_int(input_caps, "MaxHeight") or A4_300[1]

        dpi = opts.dpi
        available = sorted(
            {
                int(el.text)
                for dr in caps.iter()
                if _local(dr.tag) == "DiscreteResolution"
                for el in dr
                if _local(el.tag) == "XResolution" and el.text and el.text.strip().isdigit()
            }
        )
        if available and dpi not in available:
            dpi = min(available, key=lambda r: abs(r - dpi))

        body = SETTINGS_XML.format(
            width=width,
            height=height,
            source="Feeder" if feeder else "Platen",
            duplex="\n  <scan:Duplex>true</scan:Duplex>" if duplex else "",
            color=COLOR_MODES[opts.color],
            dpi=dpi,
        )
        try:
            resp = session.post(
                base + "/ScanJobs", data=body.encode("utf-8"),
                headers={"Content-Type": "text/xml"}, timeout=15,
            )
        except requests.RequestException as exc:
            raise ScannerError(f"فشل الاتصال بالسكانر: {exc}")
        if resp.status_code == 503:
            raise ScannerError("السكانر مشغول. استنى شوي وجرّب كمان مرة.", fatal=True)
        if resp.status_code not in (200, 201):
            raise ScannerError(f"السكانر رفض الطلب (HTTP {resp.status_code}).")
        location = resp.headers.get("Location")
        if not location:
            raise ScannerError("السكانر ما رجّع رابط للمهمة.")
        job_url = origin + urlparse(urljoin(base + "/ScanJobs/", location)).path

        page_no = 0
        try:
            while True:
                if cancel.is_set():
                    raise ScanCancelled()
                status("scanning", f"جاري قراءة الصفحة {page_no + 1}…")
                data = self._next_document(session, job_url, cancel)
                if data is None:
                    break
                try:
                    with Image.open(io.BytesIO(data)) as im:
                        pil = im.copy()
                except Exception:
                    raise ScannerError("السكانر رجّع صورة مو مفهومة.")
                page_no += 1
                yield ScanResult(pil, dpi)
                if not feeder:
                    break  # flatbed = one sheet per job
        except requests.RequestException as exc:
            raise ScannerError(f"انقطع الاتصال بالسكانر: {exc}")
        finally:
            try:
                session.delete(job_url, timeout=5)
            except Exception:
                pass
            session.close()

    @staticmethod
    def _next_document(session, job_url: str, cancel: threading.Event) -> Optional[bytes]:
        """Bytes of the next page, or None when there are no more pages."""
        for _ in range(40):  # 503 = still warming up / scanning
            resp = session.get(job_url + "/NextDocument", timeout=(5, 180))
            if resp.status_code == 200:
                return resp.content
            if resp.status_code == 404:
                return None
            if resp.status_code == 503:
                if cancel.wait(1.0):
                    raise ScanCancelled()
                continue
            raise ScannerError(f"السكانر رجّع خطأ (HTTP {resp.status_code}).")
        raise ScannerError("السكانر ما جاوب بالوقت المناسب.")
