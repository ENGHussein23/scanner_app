"""eSCL client against a mock network scanner running on localhost."""
import io
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, Response, request
from PIL import Image
from werkzeug.serving import make_server

from scanner.base import ScanCancelled, ScanOptions, ScannerError
from scanner.escl_backend import EsclBackend

NS = (
    'xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm" '
    'xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"'
)


def jpeg(w=200, h=300):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


class MockScanner:
    def __init__(self, adf=True, adf_pages=3, busy=False, location="relative", duplex=False):
        self.adf, self.adf_pages, self.busy, self.location, self.duplex = adf, adf_pages, busy, location, duplex
        self.posted, self.deleted, self.sent, self.source = [], [], 0, "Platen"
        app = Flask("mock")

        @app.get("/eSCL/ScannerCapabilities")
        def caps():
            adf = (
                "<scan:Adf><scan:AdfSimplexInputCaps><scan:MaxWidth>2550</scan:MaxWidth>"
                "<scan:MaxHeight>4200</scan:MaxHeight></scan:AdfSimplexInputCaps></scan:Adf>"
                if self.adf else ""
            )
            if self.adf and self.duplex:
                adf = adf.replace(
                    "</scan:Adf>",
                    "<scan:AdfDuplexInputCaps><scan:MaxWidth>2550</scan:MaxWidth>"
                    "<scan:MaxHeight>4200</scan:MaxHeight></scan:AdfDuplexInputCaps></scan:Adf>",
                )
            xml = f"""<?xml version="1.0"?>
<scan:ScannerCapabilities {NS}>
  <pwg:Version>2.0</pwg:Version>
  <pwg:MakeAndModel>Mock Scanner 9000</pwg:MakeAndModel>
  <scan:Platen><scan:PlatenInputCaps>
    <scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3507</scan:MaxHeight>
    <scan:SettingProfiles><scan:SettingProfile><scan:SupportedResolutions><scan:DiscreteResolutions>
      <scan:DiscreteResolution><scan:XResolution>150</scan:XResolution><scan:YResolution>150</scan:YResolution></scan:DiscreteResolution>
      <scan:DiscreteResolution><scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution></scan:DiscreteResolution>
    </scan:DiscreteResolutions></scan:SupportedResolutions></scan:SettingProfile></scan:SettingProfiles>
  </scan:PlatenInputCaps></scan:Platen>
  {adf}
</scan:ScannerCapabilities>"""
            return Response(xml, mimetype="text/xml")

        @app.post("/eSCL/ScanJobs")
        def create():
            if self.busy:
                return Response(status=503)
            body = request.get_data(as_text=True)
            self.posted.append(body)
            self.source = "Feeder" if "<pwg:InputSource>Feeder<" in body else "Platen"
            self.sent = 0
            self.duplex_requested = "<scan:Duplex>true</scan:Duplex>" in body
            path = "/eSCL/ScanJobs/job1"
            loc = {"relative": path, "wronghost": "http://scanner.invalid" + path}[self.location]
            return Response(status=201, headers={"Location": loc})

        @app.get("/eSCL/ScanJobs/job1/NextDocument")
        def nxt():
            limit = self.adf_pages if self.source == "Feeder" else 1
            if self.source == "Feeder" and getattr(self, "duplex_requested", False):
                limit *= 2  # front + back of every sheet
            if self.sent >= limit:
                return Response(status=404)
            self.sent += 1
            return Response(jpeg(), mimetype="image/jpeg")

        @app.delete("/eSCL/ScanJobs/job1")
        def delete():
            self.deleted.append(True)
            return Response(status=200)

        self.server = make_server("127.0.0.1", 0, app)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()

    def device(self):
        found = EsclBackend()._probe_host(f"127.0.0.1:{self.port}")
        assert found, "mock scanner not found"
        return found[0]


def run_scan(dev, **opts):
    status = []
    pages = list(
        EsclBackend().scan(dev, ScanOptions(**opts), threading.Event(), lambda s, m: status.append(m))
    )
    return pages, status


class EsclTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockScanner()
        self.addCleanup(self.mock.stop)

    def test_probe_host_finds_device_and_model(self):
        dev = self.mock.device()
        self.assertEqual(dev.name, "Mock Scanner 9000")
        self.assertEqual(dev.kind, "network")

    def test_discover_with_host_skips_mdns(self):
        devices = EsclBackend().discover(threading.Event(), f"127.0.0.1:{self.mock.port}")
        self.assertEqual([d.name for d in devices], ["Mock Scanner 9000"])

    def test_probe_wrong_host_returns_nothing(self):
        self.assertEqual(EsclBackend()._probe_host("127.0.0.1:1"), [])

    def test_flatbed_gives_one_page_and_cleans_up(self):
        pages, status = run_scan(self.mock.device(), source="flatbed", dpi=300)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].image.size, (200, 300))
        self.assertEqual(pages[0].dpi, 300)
        self.assertTrue(any("الصفحة 1" in m for m in status))
        self.assertEqual(len(self.mock.deleted), 1)
        xml = self.mock.posted[0]
        self.assertIn("<pwg:InputSource>Platen<", xml)
        self.assertIn("<scan:ColorMode>RGB24<", xml)
        self.assertIn("<pwg:Width>2550<", xml)  # uses the scanner's own max size

    def test_feeder_reads_every_sheet(self):
        pages, status = run_scan(self.mock.device(), source="feeder", dpi=300, color="gray")
        self.assertEqual(len(pages), 3)
        self.assertIn("<scan:ColorMode>Grayscale8<", self.mock.posted[0])
        self.assertIn("<pwg:Height>4200<", self.mock.posted[0])
        self.assertTrue(any("الصفحة 3" in m for m in status))

    def test_unsupported_dpi_snaps_to_nearest(self):
        pages, _ = run_scan(self.mock.device(), dpi=200)
        self.assertEqual(pages[0].dpi, 150)
        self.assertIn("<scan:XResolution>150<", self.mock.posted[0])

    def test_location_header_with_wrong_host_still_works(self):
        mock = MockScanner(location="wronghost")
        self.addCleanup(mock.stop)
        pages, _ = run_scan(mock.device(), source="feeder")
        self.assertEqual(len(pages), 3)

    def test_no_feeder_is_fatal(self):
        mock = MockScanner(adf=False)
        self.addCleanup(mock.stop)
        with self.assertRaises(ScannerError) as ctx:
            run_scan(mock.device(), source="feeder")
        self.assertTrue(ctx.exception.fatal)

    def test_busy_scanner_is_fatal(self):
        mock = MockScanner(busy=True)
        self.addCleanup(mock.stop)
        with self.assertRaises(ScannerError) as ctx:
            run_scan(mock.device())
        self.assertTrue(ctx.exception.fatal)

    def test_cancel_stops_and_deletes_job(self):
        cancel = threading.Event()
        cancel.set()
        gen = EsclBackend().scan(self.mock.device(), ScanOptions(source="feeder"), cancel, lambda s, m: None)
        with self.assertRaises(ScanCancelled):
            list(gen)
        self.assertEqual(len(self.mock.deleted), 1)


if __name__ == "__main__":
    unittest.main()


class EsclDuplexTests(unittest.TestCase):
    def test_duplex_sends_flag_and_reads_both_sides(self):
        mock = MockScanner(duplex=True, adf_pages=3)
        self.addCleanup(mock.stop)
        pages, _ = run_scan(mock.device(), source="feeder", duplex=True)
        self.assertEqual(len(pages), 6)
        self.assertIn("<scan:Duplex>true</scan:Duplex>", mock.posted[0])

    def test_simplex_does_not_send_duplex(self):
        mock = MockScanner(duplex=True)
        self.addCleanup(mock.stop)
        pages, _ = run_scan(mock.device(), source="feeder")
        self.assertEqual(len(pages), 3)
        self.assertNotIn("Duplex", mock.posted[0])

    def test_scanner_without_duplex_is_refused_clearly(self):
        mock = MockScanner(duplex=False)
        self.addCleanup(mock.stop)
        with self.assertRaises(ScannerError) as ctx:
            run_scan(mock.device(), source="feeder", duplex=True)
        self.assertIn("الوجهين", str(ctx.exception))
        self.assertEqual(mock.posted, [])  # nothing was started on the scanner
