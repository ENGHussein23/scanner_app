"""Picking one scanner out of several (USB + network) and duplex through the web API."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from app import create_app
from scanner.base import Backend, Device, ScanOptions, ScanResult, ScannerError
from scanner.config import Config
from scanner.manager import ScannerManager


class FakeBackend(Backend):
    """Lists the given scanners; scanning one marks which one was used."""

    def __init__(self, name, kind, names, broken=()):
        self.name, self.kind, self.names, self.broken = name, kind, names, set(broken)
        self.used: list[tuple[str, ScanOptions]] = []

    def discover(self, cancel, host=None):
        return [Device(self.name, f"{self.name}-{n}", n, self.kind) for n in self.names]

    def scan(self, device, opts, cancel, status):
        self.used.append((device.name, opts))
        if device.name in self.broken:
            raise ScannerError("هاد السكانر عطلان")
        pages = 2 if opts.duplex else 1
        for _ in range(pages):
            yield ScanResult(Image.new("RGB", (80, 100), "white"), 100)


class DeviceApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wia = FakeBackend("wia", "usb", ["USB A", "USB B"], broken=["USB A"])
        self.escl = FakeBackend("escl", "network", ["Net A", "Net B"])
        self.app = create_app(demo=True, data_dir=Path(self.tmp.name) / "data", config=Config(Path(self.tmp.name) / "c.json"))
        # swap the demo scanner for the fake ones
        manager = ScannerManager([self.wia, self.escl])
        self.app.config["manager"] = manager
        jobs = self.app.config["jobs"]
        jobs.manager = manager
        self.manager = manager
        self.c = self.app.test_client()

    def wait(self, job_id):
        end = time.time() + 10
        while time.time() < end:
            job = self.c.get(f"/api/jobs/{job_id}").get_json()
            if job["finished"]:
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def scan(self, **body):
        body.setdefault("dpi", 300)
        r = self.c.post("/api/scan", json=body)
        return r, (self.wait(r.get_json()["job_id"]) if r.status_code == 202 else None)

    def list(self):
        # the app was created with the demo manager: the API reads app.config? (it closes over `manager`)
        return self.c.get("/api/scanners").get_json()

    def test_lists_every_usb_and_network_scanner_with_unique_ids(self):
        devices, errors = self.manager.list_devices(None, threading.Event())
        self.assertEqual([d.name for d in devices], ["USB A", "USB B", "Net A", "Net B"])
        self.assertEqual(len({d.uid for d in devices}), 4)
        self.assertEqual([d.kind for d in devices], ["usb", "usb", "network", "network"])

    def test_only_the_chosen_scanner_is_used(self):
        devices, _ = self.manager.list_devices(None, threading.Event())
        net_b = next(d for d in devices if d.name == "Net B")
        # api closes over the original manager, so drive the job manager directly
        jobs = self.app.config["jobs"]
        job = jobs.start(ScanOptions(), "auto", None, net_b.uid)
        end = time.time() + 10
        while not job.finished and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(job.stage, "done", job.to_json())
        self.assertEqual([n for n, _ in self.escl.used], ["Net B"])
        self.assertEqual(self.wia.used, [])  # USB scanners were not touched

    def test_chosen_scanner_failure_is_reported_and_not_replaced_silently(self):
        devices, _ = self.manager.list_devices(None, threading.Event())
        usb_a = next(d for d in devices if d.name == "USB A")
        jobs = self.app.config["jobs"]
        job = jobs.start(ScanOptions(), "auto", None, usb_a.uid)
        while not job.finished:
            time.sleep(0.05)
        self.assertEqual(job.stage, "error")
        self.assertIn("عطلان", job.error)
        self.assertEqual(self.escl.used, [])  # did NOT fall back to another scanner

    def test_unknown_device_id_is_rejected_with_a_clear_message(self):
        jobs = self.app.config["jobs"]
        job = jobs.start(ScanOptions(), "auto", None, "wia-doesnotexist")
        while not job.finished:
            time.sleep(0.05)
        self.assertEqual(job.stage, "error")
        self.assertIn("تحديث", job.error)

    def test_duplex_is_passed_to_the_backend(self):
        devices, _ = self.manager.list_devices(None, threading.Event())
        net_a = next(d for d in devices if d.name == "Net A")
        jobs = self.app.config["jobs"]
        job = jobs.start(ScanOptions(source="feeder", duplex=True), "auto", None, net_a.uid)
        while not job.finished:
            time.sleep(0.05)
        self.assertEqual(job.page_ids and len(job.page_ids), 2)
        self.assertTrue(self.escl.used[0][1].duplex)


class DeviceApiOverHttp(unittest.TestCase):
    """The same through the real HTTP routes with the demo scanner."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(demo=True, data_dir=Path(self.tmp.name) / "data", config=Config(Path(self.tmp.name) / "c.json"))
        self.c = self.app.test_client()

    def wait(self, job_id):
        end = time.time() + 30
        while time.time() < end:
            job = self.c.get(f"/api/jobs/{job_id}").get_json()
            if job["finished"]:
                return job
            time.sleep(0.1)
        self.fail("job did not finish")

    def test_scanners_endpoint_and_scan_by_id(self):
        data = self.c.get("/api/scanners").get_json()
        self.assertEqual(len(data["devices"]), 1)
        dev = data["devices"][0]
        self.assertEqual((dev["kind"], dev["backend"]), ("usb", "demo"))
        r = self.c.post("/api/scan", json={"dpi": 150, "color": "color", "source": "flatbed", "mode": "auto", "device": dev["id"]})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.wait(r.get_json()["job_id"])["stage"], "done")

    def test_duplex_doubles_the_pages_and_needs_feeder(self):
        r = self.c.post("/api/scan", json={"dpi": 150, "source": "feeder", "duplex": True})
        job = self.wait(r.get_json()["job_id"])
        self.assertEqual(job["page_count"], 6)  # 3 sheets x 2 sides
        r = self.c.post("/api/scan", json={"dpi": 150, "source": "flatbed", "duplex": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("feeder", r.get_json()["error"])
        for bad in ({"duplex": "yes"}, {"device": 5}, {"device": "x" * 200}):
            self.assertEqual(self.c.post("/api/scan", json={"dpi": 150, **bad}).status_code, 400, bad)

    def test_scanners_list_refused_while_scanning(self):
        r = self.c.post("/api/scan", json={"dpi": 150, "source": "feeder"})
        self.assertEqual(self.c.get("/api/scanners").status_code, 409)
        self.wait(r.get_json()["job_id"])


if __name__ == "__main__":
    unittest.main()
