"""End-to-end tests of the web API using the demo scanner and a mock eSCL scanner."""
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image
from pypdf import PdfReader

from app import create_app
from test_escl import MockScanner


class ApiBase(unittest.TestCase):
    demo = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.desktop = Path(self.tmp.name) / "Desktop"
        os.environ["SCANNER_DESKTOP"] = str(self.desktop)
        self.addCleanup(os.environ.pop, "SCANNER_DESKTOP", None)
        self.app = create_app(demo=self.demo, data_dir=Path(self.tmp.name) / "data")
        self.c = self.app.test_client()

    def run_job(self, **body):
        r = self.c.post("/api/scan", json=body)
        self.assertEqual(r.status_code, 202, r.get_json())
        return self.wait(r.get_json()["job_id"])

    def wait(self, job_id, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            job = self.c.get(f"/api/jobs/{job_id}").get_json()
            if job["finished"]:
                return job
            time.sleep(0.1)
        self.fail("job did not finish")


class DemoFlow(ApiBase):
    def test_full_flow_scan_select_pdf_desktop(self):
        job = self.run_job(source="feeder", dpi=150, color="color", mode="auto")
        self.assertEqual(job["stage"], "done", job)
        self.assertEqual(job["page_count"], 3)
        self.assertIn("3 صفحات", job["message"])

        pages = self.c.get("/api/pages").get_json()["pages"]
        self.assertEqual(len(pages), 3)
        for p in pages:
            for kind in ("image", "thumb"):
                r = self.c.get(f"/api/pages/{p['id']}/{kind}")
                self.assertEqual((r.status_code, r.mimetype), (200, "image/jpeg"))
        w, h = pages[0]["width"], pages[0]["height"]

        # rotate page 1 -> dimensions swap
        rotated = self.c.post("/api/pages/rotate", json={"ids": [pages[0]["id"]], "degrees": 90}).get_json()["pages"]
        self.assertEqual((rotated[0]["width"], rotated[0]["height"]), (h, w))
        self.assertEqual(rotated[0]["rev"], 1)
        with Image.open(io.BytesIO(self.c.get(f"/api/pages/{pages[0]['id']}/image").data)) as im:
            self.assertEqual(im.size, (h, w))

        # "deselect" page 2 on the client: PDF only gets pages 1 and 3
        ids = [pages[0]["id"], pages[2]["id"]]
        pdf = self.c.post("/api/pdf", json={"ids": ids}).get_json()
        self.assertEqual(pdf["pages"], 2)
        dl = self.c.get(pdf["download_url"])
        self.assertEqual((dl.status_code, dl.mimetype), (200, "application/pdf"))
        self.assertIn("attachment", dl.headers["Content-Disposition"])
        reader = PdfReader(io.BytesIO(dl.data))
        self.assertEqual(len(reader.pages), 2)
        # page 1 was rotated to landscape, page 2 (old page 3) is A4 portrait at 150 dpi
        first, second = reader.pages[0].mediabox, reader.pages[1].mediabox
        self.assertAlmostEqual(float(second.width), 595, delta=3)
        self.assertAlmostEqual(float(second.height), 842, delta=3)
        self.assertGreater(float(first.width), float(first.height))

        # save to desktop, twice -> second one must not overwrite
        a = self.c.post(f"/api/pdf/{pdf['file']}/desktop").get_json()["path"]
        b = self.c.post(f"/api/pdf/{pdf['file']}/desktop").get_json()["path"]
        self.assertNotEqual(a, b)
        self.assertTrue(Path(a).is_file() and Path(b).is_file())
        self.assertEqual(Path(a).parent, self.desktop)

        # delete one page, then clear everything
        left = self.c.post("/api/pages/delete", json={"ids": [pages[1]["id"]]}).get_json()["pages"]
        self.assertEqual(len(left), 2)
        self.assertEqual(self.c.post("/api/pages/clear").get_json()["pages"], [])
        self.assertEqual(self.c.get(f"/api/pages/{pages[0]['id']}/thumb").status_code, 404)

    def test_scan_twice_appends_pages(self):
        self.run_job(source="flatbed", dpi=150)
        self.run_job(source="flatbed", dpi=150, color="gray")
        self.assertEqual(len(self.c.get("/api/pages").get_json()["pages"]), 2)

    def test_bw_and_gray_pages_make_valid_pdf(self):
        self.run_job(source="feeder", dpi=100, color="bw")
        self.run_job(source="flatbed", dpi=100, color="gray")
        ids = [p["id"] for p in self.c.get("/api/pages").get_json()["pages"]]
        pdf = self.c.post("/api/pdf", json={"ids": ids}).get_json()
        self.assertEqual(len(PdfReader(io.BytesIO(self.c.get(pdf["download_url"]).data)).pages), 4)

    def test_progress_reports_reading_each_page(self):
        r = self.c.post("/api/scan", json={"source": "feeder", "dpi": 150})
        job_id = r.get_json()["job_id"]
        seen = set()
        end = time.time() + 30
        while time.time() < end:
            job = self.c.get(f"/api/jobs/{job_id}").get_json()
            seen.add(job["message"])
            if job["finished"]:
                break
            time.sleep(0.1)
        self.assertTrue(any("جاري قراءة الصفحة 2" in m for m in seen), seen)
        self.assertTrue(any("تمت قراءة" in m for m in seen), seen)

    def test_second_scan_while_running_gets_409(self):
        first = self.c.post("/api/scan", json={"source": "feeder", "dpi": 150}).get_json()["job_id"]
        r = self.c.post("/api/scan", json={"source": "flatbed"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["job_id"], first)
        self.assertEqual(self.c.post("/api/pages/clear").status_code, 409)
        self.wait(first)

    def test_cancel(self):
        job_id = self.c.post("/api/scan", json={"source": "feeder", "dpi": 150}).get_json()["job_id"]
        time.sleep(0.3)
        self.assertEqual(self.c.post(f"/api/jobs/{job_id}/cancel").status_code, 200)
        job = self.wait(job_id)
        self.assertEqual(job["stage"], "cancelled")
        self.assertEqual(job["page_count"], 0)

    def test_validation(self):
        for bad in ({"dpi": 999}, {"dpi": "abc"}, {"color": "pink"}, {"source": "x"}, {"mode": "x"}):
            self.assertEqual(self.c.post("/api/scan", json=bad).status_code, 400, bad)
        self.assertEqual(self.c.post("/api/pdf", json={"ids": []}).status_code, 400)
        self.assertEqual(self.c.post("/api/pdf", json={"ids": ["nope"]}).status_code, 400)
        self.assertEqual(self.c.post("/api/pages/rotate", json={"ids": [], "degrees": 45}).status_code, 400)
        self.assertEqual(self.c.get("/api/pdf/evil.pdf").status_code, 404)
        self.assertEqual(self.c.get("/api/pdf/..%2f..%2fapp.py").status_code, 404)
        self.assertEqual(self.c.post("/api/pdf/evil.pdf/desktop").status_code, 404)
        self.assertEqual(self.c.get("/api/jobs/unknown").status_code, 404)

    def test_other_websites_and_hosts_are_rejected(self):
        self.assertEqual(self.c.get("/api/pages", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.c.post("/api/pages/clear", headers={"Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.c.post("/api/pages/clear", headers={"Origin": "null"}).status_code, 403)
        ok = self.c.post("/api/pages/clear", headers={"Origin": "http://127.0.0.1:5000"})
        self.assertEqual(ok.status_code, 200)

    def test_devices_and_ui_files(self):
        data = self.c.get("/api/devices").get_json()
        self.assertEqual([d["name"] for d in data["devices"]], ["سكانر تجريبي (Demo)"])
        for path in ("/", "/static/app.js", "/static/style.css"):
            self.assertEqual(self.c.get(path).status_code, 200, path)
        self.assertIn("سكان", self.c.get("/").get_data(as_text=True))


class RealBackendsOnLinux(ApiBase):
    demo = False

    def test_no_usb_backend_gives_clear_error(self):
        job = self.run_job(mode="usb")
        self.assertEqual(job["stage"], "error")
        self.assertIn("ما لقيت أي سكانر", job["error"])

    def test_network_scan_through_manual_ip(self):
        mock = MockScanner()
        self.addCleanup(mock.stop)
        job = self.run_job(mode="network", host=f"127.0.0.1:{mock.port}", source="feeder", dpi=300)
        self.assertEqual(job["stage"], "done", job)
        self.assertEqual(job["page_count"], 3)
        self.assertEqual(job["device"], "Mock Scanner 9000")
        pages = self.c.get("/api/pages").get_json()["pages"]
        pdf = self.c.post("/api/pdf", json={"ids": [p["id"] for p in pages]}).get_json()
        self.assertEqual(len(PdfReader(io.BytesIO(self.c.get(pdf["download_url"]).data)).pages), 3)

    def test_fatal_error_message_is_shown(self):
        mock = MockScanner(adf=False)
        self.addCleanup(mock.stop)
        job = self.run_job(mode="network", host=f"127.0.0.1:{mock.port}", source="feeder")
        self.assertEqual(job["stage"], "error")
        self.assertIn("feeder", job["error"])


if __name__ == "__main__":
    unittest.main()
