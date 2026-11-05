"""Exercises the WIA backend's control flow with a fake pywin32 (no Windows needed).

This checks OUR logic (settings, feeder loop, paper-empty handling, PNG->BMP
fallback). It cannot prove that a specific real scanner driver behaves.
"""
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from scanner.base import ScanCancelled, ScanOptions, ScannerError
from scanner.wia_backend import FORMAT_BMP, FORMAT_PNG, WiaBackend


class FakeComError(Exception):
    def __init__(self, scode):
        super().__init__(hex(scode))
        self.hresult = -2147352567  # DISP_E_EXCEPTION, like real pywin32
        self.excepinfo = (0, None, None, None, 0, scode - 2**32)


class Prop:
    def __init__(self, props, name):
        self._p, self._n = props, name

    @property
    def Value(self):
        return self._p.values[self._n]

    @Value.setter
    def Value(self, v):
        self._p.values[self._n] = v


class Props:
    def __init__(self, values):
        self.values = dict(values)

    def __call__(self, name):
        if name not in self.values:
            raise Exception(f"property {name} not supported")
        return Prop(self, name)


class ImageFile:
    def __init__(self, fmt):
        self.fmt = fmt

    def SaveFile(self, path):
        Image.new("RGB", (100, 150), "white").save(path, "PNG" if self.fmt == FORMAT_PNG else "BMP")


class Item:
    def __init__(self, script, png_supported=True):
        self.Properties = Props(
            {
                "Current Intent": 1, "Bits Per Pixel": 24,
                "Horizontal Resolution": 0, "Vertical Resolution": 0,
                "Horizontal Start Position": 0, "Vertical Start Position": 0,
                "Horizontal Extent": 0, "Vertical Extent": 0,
            }
        )
        self.script, self.png_supported, self.formats = list(script), png_supported, []

    def Transfer(self, fmt):
        self.formats.append(fmt)
        if fmt == FORMAT_PNG and not self.png_supported:
            raise FakeComError(0x80210001)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return ImageFile(fmt)


class Device:
    def __init__(self, item, feeder=True, duplex_caps=None):
        values = {"Horizontal Bed Size": 8500, "Vertical Bed Size": 11700}
        if feeder:
            values.update({"Document Handling Select": 2, "Document Handling Status": 3, "Pages": 1,
                           "Horizontal Sheet Feed Size": 8500, "Vertical Sheet Feed Size": 14000})
        else:
            values["Document Handling Select"] = 2
        if duplex_caps is not None:
            values["Document Handling Capabilities"] = duplex_caps
        self.Properties = Props(values)
        if not feeder:  # flatbed-only: feeder handling can't be selected
            self.Properties.values.pop("Document Handling Select")
        self._item = item
        self.Items = types.SimpleNamespace(Item=lambda i: item)


class Info:
    Type = 1
    DeviceID = "{FAKE-1}"

    def __init__(self, device):
        self.Properties = Props({"Name": "Fake WIA Scanner"})
        self._device = device

    def Connect(self):
        return self._device


class Manager:
    def __init__(self, device):
        info = Info(device)
        self.DeviceInfos = types.SimpleNamespace(Count=1, Item=lambda i: info)


PAPER_EMPTY = FakeComError(0x80210003)


class WiaTests(unittest.TestCase):
    def install(self, item, feeder=True, duplex_caps=None):
        self.device = Device(item, feeder, duplex_caps)
        manager = Manager(self.device)
        pythoncom = types.ModuleType("pythoncom")
        pythoncom.CoInitialize = pythoncom.CoUninitialize = lambda: None
        pywintypes = types.ModuleType("pywintypes")
        pywintypes.com_error = FakeComError
        win32com = types.ModuleType("win32com")
        client = types.ModuleType("win32com.client")
        client.Dispatch = lambda name: manager
        win32com.client = client
        patcher = mock.patch.dict(
            sys.modules,
            {"pythoncom": pythoncom, "pywintypes": pywintypes, "win32com": win32com, "win32com.client": client},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return manager

    def scan(self, **opts):
        backend = WiaBackend()
        dev = backend.discover(threading.Event())[0]
        status = []
        pages = list(backend.scan(dev, ScanOptions(**opts), threading.Event(), lambda s, m: status.append(m)))
        return pages, status

    def test_discover_lists_scanner(self):
        self.install(Item([]))
        devices = WiaBackend().discover(threading.Event())
        self.assertEqual([d.name for d in devices], ["Fake WIA Scanner"])

    def test_flatbed_single_page_and_settings(self):
        item = Item([object()])
        self.install(item)
        pages, status = self.scan(source="flatbed", dpi=300, color="color")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].image.size, (100, 150))
        v = item.Properties.values
        self.assertEqual((v["Horizontal Resolution"], v["Vertical Resolution"]), (300, 300))
        self.assertEqual((v["Horizontal Extent"], v["Vertical Extent"]), (2550, 3510))
        self.assertEqual(v["Current Intent"], 1)
        self.assertEqual(self.device.Properties.values["Document Handling Select"], 2)
        self.assertTrue(any("الصفحة 1" in m for m in status))

    def test_gray_uses_gray_intent(self):
        item = Item([object()])
        self.install(item)
        self.scan(color="gray")
        self.assertEqual((item.Properties.values["Current Intent"], item.Properties.values["Bits Per Pixel"]), (2, 8))

    def test_feeder_reads_until_paper_empty(self):
        item = Item([object(), object(), object(), PAPER_EMPTY])
        self.install(item)
        pages, _ = self.scan(source="feeder", dpi=200)
        self.assertEqual(len(pages), 3)
        self.assertEqual(self.device.Properties.values["Document Handling Select"], 1)
        self.assertEqual(item.Properties.values["Vertical Extent"], 2800)  # 14 in * 200 dpi

    def test_empty_feeder_on_first_page_is_fatal(self):
        self.install(Item([PAPER_EMPTY]))
        with self.assertRaises(ScannerError) as ctx:
            self.scan(source="feeder")
        self.assertTrue(ctx.exception.fatal)
        self.assertIn("feeder", str(ctx.exception))

    def test_feeder_status_not_ready_is_fatal(self):
        self.install(Item([object()]))
        self.device.Properties.values["Document Handling Status"] = 2  # flatbed ready only
        with self.assertRaises(ScannerError) as ctx:
            self.scan(source="feeder")
        self.assertTrue(ctx.exception.fatal)

    def test_scanner_without_feeder_is_fatal(self):
        self.install(Item([object()]), feeder=False)
        with self.assertRaises(ScannerError) as ctx:
            self.scan(source="feeder")
        self.assertTrue(ctx.exception.fatal)

    def test_png_unsupported_falls_back_to_bmp(self):
        item = Item([object()], png_supported=False)
        self.install(item)
        pages, _ = self.scan()
        self.assertEqual(len(pages), 1)
        self.assertEqual(item.formats, [FORMAT_PNG, FORMAT_BMP])

    def test_paper_jam_mid_scan_is_reported(self):
        self.install(Item([object(), FakeComError(0x80210002)]))
        backend = WiaBackend()
        dev = backend.discover(threading.Event())[0]
        gen = backend.scan(dev, ScanOptions(source="feeder"), threading.Event(), lambda s, m: None)
        next(gen)  # first page is fine
        with self.assertRaises(ScannerError) as ctx:
            next(gen)
        self.assertTrue(ctx.exception.fatal)

    def test_cancel(self):
        self.install(Item([object()]))
        backend = WiaBackend()
        dev = backend.discover(threading.Event())[0]
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ScanCancelled):
            list(backend.scan(dev, ScanOptions(), cancel, lambda s, m: None))


class WiaDuplexTests(unittest.TestCase):
    install = WiaTests.install
    scan = WiaTests.scan
    def test_duplex_selects_feeder_plus_duplex_and_reads_all_pages(self):
        item = Item([object()] * 4 + [PAPER_EMPTY])
        self.install(item, duplex_caps=1 | 2 | 4)
        pages, _ = self.scan(source="feeder", duplex=True)
        self.assertEqual(len(pages), 4)  # 2 sheets, both sides
        v = self.device.Properties.values
        self.assertEqual(v["Document Handling Select"], 1 | 4)
        self.assertEqual(v["Pages"], 0)

    def test_scanner_without_duplex_is_refused(self):
        self.install(Item([object()]), duplex_caps=1 | 2)
        with self.assertRaises(ScannerError) as ctx:
            self.scan(source="feeder", duplex=True)
        self.assertIn("الوجهين", str(ctx.exception))

    def test_simplex_keeps_one_page_per_transfer(self):
        self.install(Item([object(), PAPER_EMPTY]), duplex_caps=1 | 2 | 4)
        self.scan(source="feeder")
        self.assertEqual(self.device.Properties.values["Pages"], 1)
        self.assertEqual(self.device.Properties.values["Document Handling Select"], 1)


if __name__ == "__main__":
    unittest.main()