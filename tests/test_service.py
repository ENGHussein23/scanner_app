"""Service features: health, embed origin checks, CSP, settings API, config file, autostart command."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from scanner import autostart  # noqa: E402
from scanner.config import Config, origin_matches, validate_origins  # noqa: E402


class OriginTests(unittest.TestCase):
    def test_matching(self):
        self.assertTrue(origin_matches("http://localhost:*", "http://localhost:5173"))
        self.assertTrue(origin_matches("http://localhost:*", "http://localhost"))
        self.assertFalse(origin_matches("http://localhost:*", "https://localhost:5173"))
        self.assertTrue(origin_matches("https://docs.gov.sy", "https://docs.gov.sy"))
        self.assertTrue(origin_matches("https://docs.gov.sy", "https://docs.gov.sy:443"))
        self.assertFalse(origin_matches("https://docs.gov.sy", "https://docs.gov.sy:8443"))
        self.assertFalse(origin_matches("https://docs.gov.sy", "https://evil.docs.gov.sy"))
        self.assertFalse(origin_matches("https://docs.gov.sy", "https://docs.gov.sy.evil.com"))
        self.assertFalse(origin_matches("https://docs.gov.sy", "https://docs.gov.sy/path"))

    def test_validation(self):
        self.assertEqual(validate_origins(["HTTPS://Docs.gov.sy/", " ", "https://docs.gov.sy"]), ["https://docs.gov.sy"])
        for bad in ("*", "docs.gov.sy", "https://*.gov.sy", "javascript:alert(1)", "https://a.sy/path", "https://a.sy:abc"):
            with self.assertRaises(ValueError, msg=bad):
                validate_origins([bad])


class ServiceApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = Path(self.tmp.name) / "config.json"
        self.config = Config(self.cfg_path)
        self.config.update({"allowed_origins": ["https://docs.gov.sy", "http://localhost:*"]})
        self.app = create_app(demo=True, data_dir=Path(self.tmp.name) / "data", config=self.config)
        self.c = self.app.test_client()

    def test_health(self):
        r = self.c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["app"], "HomsScanner")
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_embed_check(self):
        ok = self.c.get("/api/embed/check?origin=https://docs.gov.sy").get_json()
        bad = self.c.get("/api/embed/check?origin=https://evil.example").get_json()
        none = self.c.get("/api/embed/check").get_json()
        self.assertEqual((ok["allowed"], bad["allowed"], none["allowed"]), (True, False, False))

    def test_frame_ancestors_only_for_allowed_sites(self):
        csp = self.c.get("/").headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'self' https://docs.gov.sy http://localhost:*", csp)
        self.assertNotIn("evil", csp)
        self.assertEqual(self.c.get("/settings").headers["Content-Security-Policy"], "frame-ancestors 'none'")

    def test_settings_not_available_inside_iframe(self):
        for path in ("/settings", "/api/settings"):
            r = self.c.get(path, headers={"Sec-Fetch-Dest": "iframe"})
            self.assertEqual(r.status_code, 403, path)
        self.assertEqual(self.c.get("/api/settings").status_code, 200)

    def test_foreign_origin_still_blocked(self):
        self.assertEqual(self.c.get("/api/health", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.c.post("/api/scan", headers={"Origin": "https://docs.gov.sy"}, json={}).status_code, 403)
        self.assertEqual(self.c.get("/api/health", headers={"Host": "evil.example"}).status_code, 403)

    def test_settings_roundtrip_and_validation(self):
        r = self.c.post("/api/settings", json={
            "port": 18000, "autostart": False,
            "allowed_origins": ["https://new.gov.sy"],
            "defaults": {"dpi": 200, "color": "gray", "source": "feeder", "mode": "usb", "host": "192.168.1.9"},
        })
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(j["restart_needed"])
        saved = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["port"], 18000)
        self.assertEqual(saved["defaults"]["dpi"], 200)
        self.assertEqual(self.c.get("/api/config").get_json()["defaults"]["color"], "gray")
        self.assertEqual(Config(self.cfg_path).allowed_origins, ["https://new.gov.sy"])
        for body in ({"port": 80}, {"port": "abc"}, {"allowed_origins": ["*"]}, {"defaults": {"dpi": 123}}, {"defaults": {"color": "x"}}, [1]):
            r = self.c.post("/api/settings", json=body)
            self.assertEqual(r.status_code, 400, body)
        # a rejected update changes nothing
        self.assertEqual(Config(self.cfg_path).port, 18000)

    def test_settings_hook_called(self):
        calls = []
        self.app.config["on_settings_changed"] = calls.append
        self.c.post("/api/settings", json={"autostart": False})
        self.assertEqual(len(calls), 1)


class ConfigFileTests(unittest.TestCase):
    def test_damaged_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(Config(p).port, 17650)
            p.write_text(json.dumps({"port": 5, "allowed_origins": ["*"]}), encoding="utf-8")
            self.assertEqual(Config(p).port, 17650)  # invalid values are ignored as a whole


class AutostartTests(unittest.TestCase):
    def test_command_line_is_quoted(self):
        cmd = autostart.command_line()
        self.assertTrue(cmd.startswith('"'))
        self.assertTrue(cmd.endswith("--background"))
        self.assertIn("service_main.py", cmd)

    def test_noop_off_windows(self):
        if sys.platform != "win32":
            self.assertFalse(autostart.supported())
            autostart.set_enabled(True)  # must not raise
            self.assertFalse(autostart.is_enabled())


if __name__ == "__main__":
    unittest.main()
