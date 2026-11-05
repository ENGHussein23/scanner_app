"""mDNS discovery logic of the eSCL backend, using a fake `zeroconf` module.

Checks how we turn Bonjour records into scanner URLs. It cannot prove that the
real zeroconf package behaves exactly like the fake, only that our handling of
the data it returns is right.
"""
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.base import ScanCancelled
from scanner.escl_backend import EsclBackend


class FakeInfo:
    def __init__(self, addresses, port, properties):
        self._addresses, self.port, self.properties = addresses, port, properties

    def parsed_addresses(self):
        return self._addresses


def fake_zeroconf(services):
    mod = types.ModuleType("zeroconf")

    class ServiceListener:
        pass

    class Zeroconf:
        closed = False

        def get_service_info(self, type_, name, timeout=3000):
            return services.get((type_, name))

        def close(self):
            Zeroconf.closed = True

    class ServiceBrowser:
        def __init__(self, zc, type_, handlers=None):
            for (t, name) in services:
                if t == type_:
                    handlers.add_service(zc, t, name)

        def cancel(self):
            pass

    mod.ServiceListener, mod.Zeroconf, mod.ServiceBrowser = ServiceListener, Zeroconf, ServiceBrowser
    return mod


class MdnsTests(unittest.TestCase):
    def discover(self, services, timeout=4.0, cancel=None):
        mod = fake_zeroconf(services)
        with mock.patch.dict(sys.modules, {"zeroconf": mod}):
            result = EsclBackend()._mdns(cancel or threading.Event(), timeout)
        self.assertTrue(mod.Zeroconf.closed, "Zeroconf must always be closed")
        return result

    def test_builds_urls_names_and_dedupes(self):
        services = {
            # printer A advertises both http and https -> one entry, http wins
            ("_uscan._tcp.local.", "HP LaserJet._uscan._tcp.local."): FakeInfo(
                ["192.168.1.20", "fe80::1"], 80, {b"rs": b"eSCL", b"ty": b"HP LaserJet MFP"}
            ),
            ("_uscans._tcp.local.", "HP LaserJet._uscans._tcp.local."): FakeInfo(
                ["192.168.1.20"], 443, {b"rs": b"eSCL", b"ty": b"HP LaserJet MFP"}
            ),
            # printer B: https only, rs has stray slashes, no `ty` -> name from the service name
            ("_uscans._tcp.local.", "Brother DCP._uscans._tcp.local."): FakeInfo(
                ["192.168.1.30"], 443, {b"rs": b"/eSCL/", b"note": None}
            ),
            # printer C: IPv6 only -> unusable
            ("_uscan._tcp.local.", "Six._uscan._tcp.local."): FakeInfo(["fe80::9"], 80, {b"rs": b"eSCL"}),
            # printer D: vanished before we could resolve it
            ("_uscan._tcp.local.", "Gone._uscan._tcp.local."): None,
        }
        found = {d.name: d for d in self.discover(services)}
        self.assertEqual(sorted(found), ["Brother DCP", "HP LaserJet MFP"])
        self.assertEqual(found["HP LaserJet MFP"].extra["base"], "http://192.168.1.20:80/eSCL")
        self.assertEqual(found["Brother DCP"].extra["base"], "https://192.168.1.30:443/eSCL")
        self.assertTrue(all(d.kind == "network" and d.backend == "escl" for d in found.values()))

    def test_nothing_found_returns_empty_after_timeout(self):
        self.assertEqual(self.discover({}, timeout=0.3), [])

    def test_cancel_raises_and_still_closes(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ScanCancelled):
            self.discover({}, cancel=cancel)

    def test_missing_zeroconf_does_not_break_manual_ip(self):
        # discover() must swallow the ImportError and just return what the manual probe found
        with mock.patch.dict(sys.modules, {"zeroconf": None}):
            self.assertEqual(EsclBackend().discover(threading.Event(), None, timeout=0.3), [])
            ok, note = EsclBackend().available()
        self.assertTrue(ok)
        self.assertIn("zeroconf", note)


if __name__ == "__main__":
    unittest.main()
