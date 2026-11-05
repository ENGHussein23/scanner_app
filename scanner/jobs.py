"""Runs a scan in a background thread and exposes its progress to the web page."""
from __future__ import annotations

import logging
import threading
import uuid

from .base import ScanCancelled, ScanOptions, ScannerError, arabic_pages
from .manager import ScannerManager
from .store import PageStore

log = logging.getLogger(__name__)

NO_SCANNER_MESSAGE = (
    "ما لقيت أي سكانر. تأكد إنو السكانر شغّال وموصول بالكابل (USB) أو على نفس شبكة الواي فاي، "
    "ولو بالشبكة جرّب تكتب عنوان الـ IP تبعو بالإعدادات."
)


class BusyError(Exception):
    def __init__(self, job_id: str):
        super().__init__("a scan is already running")
        self.job_id = job_id


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex[:10]
        self.stage = "searching"
        self.message = "عم جهّز…"
        self.error: str | None = None
        self.warning: str | None = None
        self.finished = False
        self.device: str | None = None
        self.page_ids: list[str] = []
        self.log: list[str] = []
        self.cancel = threading.Event()

    def set(self, stage: str, message: str) -> None:
        self.stage, self.message = stage, message
        if not self.log or self.log[-1] != message:
            self.log.append(message)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "stage": self.stage,
            "message": self.message,
            "error": self.error,
            "warning": self.warning,
            "finished": self.finished,
            "device": self.device,
            "page_count": len(self.page_ids),
            "log": self.log[-10:],
        }


class JobManager:
    def __init__(self, manager: ScannerManager, store: PageStore):
        self.manager = manager
        self.store = store
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def active(self) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs.values() if not j.finished), None)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def start(self, opts: ScanOptions, mode: str, host: str | None, device: str | None = None) -> Job:
        with self._lock:
            running = next((j for j in self._jobs.values() if not j.finished), None)
            if running:
                raise BusyError(running.id)
            job = Job()
            self._jobs[job.id] = job
            for old in list(self._jobs)[:-20]:  # keep the dict small
                self._jobs.pop(old, None)
        threading.Thread(target=self._run, args=(job, opts, mode, host, device), daemon=True).start()
        return job

    def _run(self, job: Job, opts: ScanOptions, mode: str, host: str | None, device_uid: str | None = None) -> None:
        errors: list[str] = []
        try:
            job.set("searching", "عم دوّر على السكانر…")
            if device_uid:  # the user picked one scanner from the list: use only that one
                chosen = self.manager.known(device_uid)
                if chosen is None:
                    raise ScannerError("السكانر المختار ما عاد بالقائمة. اضغط «تحديث» واختاره من جديد.")
                candidates = iter([chosen])
            else:
                candidates = self.manager.candidates(mode, host, job.cancel, job.set)
            for device in candidates:
                if job.cancel.is_set():
                    raise ScanCancelled()
                job.device = device.name
                job.set("connecting", f"لقيت «{device.name}» — عم اتصل فيه…")
                backend = self.manager.backend(device.backend)
                got = 0
                try:
                    for result in backend.scan(device, opts, job.cancel, job.set):
                        page = self.store.add(result.image, result.dpi)
                        job.page_ids.append(page.id)
                        got += 1
                        job.set("scanning", f"تمت قراءة {arabic_pages(got)}")
                    if got == 0:
                        raise ScannerError("السكانر ما رجّع أي صفحة.")
                    break
                except ScannerError as exc:
                    if got:  # keep what we already have
                        job.warning = f"توقف السكانر بعد {arabic_pages(got)}: {exc}"
                        break
                    log.warning("%s failed: %s", device.name, exc)
                    errors.append(f"{device.name}: {exc}")
                    if exc.fatal:
                        break
            if job.page_ids:
                job.set("done", f"تمت قراءة {arabic_pages(len(job.page_ids))} ✓")
            elif len(errors) == 1:
                raise ScannerError(errors[0].split(": ", 1)[-1])
            elif errors:
                raise ScannerError("ما نجح الاتصال بأي سكانر:\n" + "\n".join(errors))
            else:
                raise ScannerError(NO_SCANNER_MESSAGE)
        except ScanCancelled:
            job.set("cancelled", "تم الإلغاء.")
        except ScannerError as exc:
            job.error = str(exc)
            job.set("error", str(exc))
        except Exception as exc:  # a bug or driver crash: don't leave the page hanging
            log.exception("scan job crashed")
            job.error = f"صار خطأ غير متوقع: {exc}"
            job.set("error", job.error)
        finally:
            job.finished = True
