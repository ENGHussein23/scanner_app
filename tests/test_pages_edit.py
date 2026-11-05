"""Reorder / trash+undo / rename / desktop-save (server side of the page editor)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pypdf import PdfReader

from scanner.store import sanitize_filename
from test_api import ApiBase


class Sanitize(unittest.TestCase):
    def test_cases(self):
        f = sanitize_filename
        self.assertEqual(f("طلب ترخيص"), "طلب ترخيص.pdf")
        self.assertEqual(f('a/b\\c:d*e?f"g<h>i|j'), "abcdefghij.pdf")
        self.assertEqual(f("report.PDF"), "report.pdf")
        self.assertEqual(f("  a   b  "), "a b.pdf")
        self.assertEqual(f("...", "scan"), "scan.pdf")
        self.assertTrue(f("...").startswith("scan_"))
        self.assertEqual(f("", "scan_1"), "scan_1.pdf")
        self.assertTrue(f("CON").startswith("_"))
        self.assertLessEqual(len(f("x" * 500)), 104)
        self.assertEqual(f("a\x00b\x1f"), "ab.pdf")


class PageEdit(ApiBase):
    def setUp(self):
        super().setUp()
        self.run_job(source="feeder", dpi=100, color="color", mode="auto")
        self.ids = [p["id"] for p in self.c.get("/api/pages").get_json()["pages"]]
        self.assertGreaterEqual(len(self.ids), 3)

    def order(self):
        return [p["id"] for p in self.c.get("/api/pages").get_json()["pages"]]

    def test_reorder(self):
        new = list(reversed(self.ids))
        r = self.c.post("/api/pages/reorder", json={"ids": new})
        self.assertEqual([p["id"] for p in r.get_json()["pages"]], new)
        self.assertEqual(self.order(), new)

    def test_reorder_partial_and_unknown(self):
        r = self.c.post("/api/pages/reorder", json={"ids": [self.ids[-1], "nope"]})
        got = [p["id"] for p in r.get_json()["pages"]]
        self.assertEqual(got[0], self.ids[-1])
        self.assertEqual(sorted(got), sorted(self.ids))

    def test_trash_restore_position(self):
        mid = self.ids[1]
        r = self.c.post("/api/pages/trash", json={"ids": [mid]})
        self.assertNotIn(mid, [p["id"] for p in r.get_json()["pages"]])
        r = self.c.post("/api/pages/restore", json={"ids": [mid]})
        self.assertEqual(self.order(), self.ids)
        self.assertEqual(self.c.get(f"/api/pages/{mid}/thumb").status_code, 200)

    def test_pdf_follows_order_and_name(self):
        new = list(reversed(self.ids))
        self.c.post("/api/pages/reorder", json={"ids": new})
        r = self.c.post("/api/pdf", json={"ids": new, "name": 'طلب/ترخيص:1'})
        data = r.get_json()
        self.assertEqual(data["display_name"], "طلبترخيص1.pdf")
        self.assertEqual(data["pages"], len(new))
        pdf = self.c.get(data["download_url"])
        self.assertEqual(pdf.status_code, 200)
        self.assertIn("filename", pdf.headers["Content-Disposition"])
        import io
        self.assertEqual(len(PdfReader(io.BytesIO(pdf.data)).pages), len(new))

    def test_desktop_named_and_unique(self):
        data = self.c.post("/api/pdf", json={"ids": self.ids, "name": "مستند"}).get_json()
        url = f"/api/pdf/{data['file']}/desktop"
        a = self.c.post(url, json={"name": "مستند"}).get_json()["path"]
        b = self.c.post(url, json={"name": "مستند"}).get_json()["path"]
        self.assertNotEqual(a, b)
        self.assertTrue(Path(a).exists() and Path(b).exists())
        self.assertEqual(Path(a).name, "مستند.pdf")


if __name__ == "__main__":
    unittest.main()
