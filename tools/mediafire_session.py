#!/usr/bin/env python3
"""The download session: one job, one thread, one event queue.

The session is deliberately free of any GUI framework so that the Tkinter window
(``tools/mediafire_gui.py``) and the PySide6 window (``tools/arkload/``) drive exactly the same
engine.  It plans the source, detects what is already on disk, verifies what it finds, downloads
only what is missing, retries what failed and reports every step through a queue.

    session = DownloadSession(spec, events)
    session.start()
"""
from __future__ import annotations

import dataclasses
import queue
import threading
import time
from pathlib import Path

try:  # package import
    from . import mediafire_dl as dl
    from . import mediafire_resume as rs
except ImportError:  # direct script run
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mediafire_dl as dl  # type: ignore[no-redef]
    import mediafire_resume as rs  # type: ignore[no-redef]


class DownloadSession(threading.Thread):
    """Plans, verifies and downloads one job, reporting to the UI through a queue.

    The session owns the work; the window owns nothing but the queue. Closing the window stops
    the thread but never loses the manifest, so the next start knows exactly what is missing.
    """

    def __init__(self, spec: dict, events: queue.Queue):
        super().__init__(daemon=True)
        self.spec = spec
        self.events = events
        self.job_id = spec["job_id"]
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.lock = threading.Lock()
        self.job: rs.Job | None = None
        self.stop_reason = ""
        self.downloaded_bytes = 0

    # -- plumbing -------------------------------------------------------------
    def post(self, kind: str, *payload) -> None:
        self.events.put((self.job_id, kind, *payload))

    def request_stop(self, reason: str = "cancelled") -> None:
        """Stop the session and remember why, so the manifest keeps the right state."""
        with self.lock:
            self.stop_reason = reason
            if self.job is not None:
                rs.normalize_interrupted(self.job, self.job.root)
                self.job.set_status(rs.JOB_INTERRUPTED if reason == "interrupted"
                                    else rs.JOB_CANCELLED)
                rs.save(self.job)
        self.cancel.set()
        self.pause.clear()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.pause.set()
        else:
            self.pause.clear()

    # -- main -----------------------------------------------------------------
    def run(self) -> None:
        started = time.monotonic()
        spec = self.spec
        try:
            source = dl.parse_source(spec["url"])
            self.post("planning", source.kind)
            plan = dl.make_plan(source, None, None, spec.get("cookies"))

            patterns = spec.get("patterns") or []
            if patterns and source.kind == "folder":
                plan.files = [item for item in plan.files if rs.matches_patterns(item.rel, patterns)]

            root = Path(spec["dest"]).expanduser()
            if source.kind == "folder" and spec.get("subfolder", True):
                root = root / (spec.get("name") or plan.root_name)
            root.mkdir(parents=True, exist_ok=True)
            self.post("plan", plan, root, source.kind)

            mirrors = self._mirror_items(spec)

            options = dict(spec.get("options") or {})
            options.setdefault("subfolder", bool(spec.get("subfolder", True)))
            job = rs.load_or_create(spec["url"], source.kind, source.key, spec["dest"],
                                    root, options, plan)
            with self.lock:
                self.job = job
            self.post("job", job)

            total = len(plan.files)
            self.post("scan_start", total)

            def on_scan(index: int, count: int, record: rs.FileRecord) -> None:
                self.post("file_state", index, record)

            rs.scan(job, root, cancel=self.cancel, progress=on_scan)
            self.post("scan_done", rs.summary(job))

            if self.cancel.is_set():
                self._finish(job, started, root, plan)
                return

            if spec.get("mode") == "verify":
                rs.validate(job, root, cancel=self.cancel)
                self._finish(job, started, root, plan)
                return

            rounds = max(0, min(10, int(spec.get("recovery_rounds", 3))))
            for round_no in range(rounds + 1):
                if self.cancel.is_set():
                    break
                pending = [index for index, record in enumerate(job.files, 1)
                           if record.status != rs.FILE_COMPLETE]
                if not pending:
                    break
                if round_no > 0:
                    job.recovery_round = round_no
                    rs.save(job)
                    self.post("recovery", round_no, len(pending))
                    self._sleep(min(8.0, 1.5 * (2 ** (round_no - 1))))
                    if self.cancel.is_set():
                        break
                self._run_batch(job, plan, pending, root, spec, mirrors)
                rs.validate(job, root, cancel=self.cancel)
            self._finish(job, started, root, plan)
        except dl.Cancelled:
            self.post("cancelled")
        except Exception as e:  # noqa: BLE001 - planning/network errors belong in the window
            with self.lock:
                if self.job is not None:
                    rs.normalize_interrupted(self.job, self.job.root)
                    self.job.set_status(rs.JOB_INTERRUPTED)
                    self.job.last_error = str(e)
                    rs.save(self.job)
            self.post("fatal", str(e) or type(e).__name__)

    # -- helpers --------------------------------------------------------------
    def _mirror_items(self, spec: dict) -> dict:
        """The files of the alternative sources, indexed by their relative path."""
        items: dict[str, object] = {}
        for url in spec.get("mirrors") or []:
            try:
                source = dl.parse_source(url)
                plan = dl.make_plan(source, None, None, spec.get("cookies"))
            except Exception:  # noqa: BLE001 - a broken mirror is simply skipped
                continue
            for item in plan.files:
                items.setdefault(item.rel.as_posix(), item)
        return items

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancel.is_set():
                return
            if self.pause.is_set():
                time.sleep(0.1)
                continue
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))

    def _run_batch(self, job: rs.Job, plan, indices: list[int], root: Path, spec: dict,
                   mirrors: dict | None = None) -> None:
        pending: queue.Queue = queue.Queue()
        for index in indices:
            pending.put(index)
        slow = bool(spec.get("slow_mode"))
        pause_between = float(spec.get("slow_pause", 1.0))

        def worker() -> None:
            while not self.cancel.is_set():
                if self.pause.is_set():
                    time.sleep(0.1)
                    continue
                try:
                    index = pending.get_nowait()
                except queue.Empty:
                    return
                record = job.record(index)
                item = plan.files[index - 1]
                self.post("file_start", index)
                try:
                    result = dl.download_one(
                        item, root,
                        overwrite=bool(spec.get("overwrite")),
                        retries=int(spec.get("retries", 3)),
                        timeout=float(spec.get("timeout", 60.0)),
                        session_token=None,
                        cookie=spec.get("cookies"),
                        cancel=self.cancel, pause=self.pause,
                        progress=lambda done, total, i=index: self.post("progress", i, done, total),
                        force=bool(record and record.status in (rs.FILE_INCOMPLETE,
                                                               rs.FILE_VERIFY_FAILED,
                                                               rs.FILE_FAILED)),
                        rate_limit=int(spec.get("rate_limit", 0) or 0),
                        connections=int(spec.get("connections", 1) or 1),
                    )
                except Exception as e:  # noqa: BLE001 - show it on the row, keep the batch alive
                    result = dl.Result("failed", error=f"{type(e).__name__}: {e}")
                if result.status == "failed" and mirrors:
                    mirror = mirrors.get(item.rel.as_posix())
                    if mirror is not None:
                        # A mirror is a second source for the same file; the expected hash of the
                        # primary source is kept, so a mirror with different bytes is rejected.
                        mirrored = dataclasses.replace(mirror, rel=item.rel, sha256=item.sha256)
                        self.post("mirror", index, mirror.page_url or "")
                        result = dl.download_one(
                            mirrored, root,
                            overwrite=bool(spec.get("overwrite")),
                            retries=int(spec.get("retries", 3)),
                            timeout=float(spec.get("timeout", 60.0)),
                            session_token=None,
                            cookie=spec.get("cookies"),
                            cancel=self.cancel, pause=self.pause,
                            progress=lambda done, total, i=index: self.post(
                                "progress", i, done, total),
                            force=True,
                            rate_limit=int(spec.get("rate_limit", 0) or 0),
                        )
                with self.lock:
                    updated = rs.record_result(job, index, result, root)
                    if result.status == "ok":
                        self.downloaded_bytes += result.size
                self.post("file_done", index, result, updated)
                if slow and pause_between > 0:
                    self._sleep(pause_between)   # "slow mode": one transfer, then a breather

        count = 1 if slow else max(1, min(int(spec.get("jobs", 4)), max(1, len(indices))))
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def _finish(self, job: rs.Job, started: float, root: Path, plan) -> None:
        cancelled = self.cancel.is_set()
        # the job is complete when every file is there and verified - even if the user
        # pressed cancel in the last moment
        complete = rs.all_complete(job)
        with self.lock:
            if cancelled:
                rs.normalize_interrupted(job, root)
            if complete:
                job.set_status(rs.JOB_COMPLETE)
            elif self.stop_reason == "interrupted":
                job.set_status(rs.JOB_INTERRUPTED)
            elif cancelled:
                job.set_status(rs.JOB_CANCELLED)
            else:
                job.set_status(rs.JOB_INCOMPLETE)
            job.finished = rs.now_iso()
            rs.save(job)
        stats = rs.summary(job)
        failures = [(record.path, record.error or "not verified")
                    for record in job.files if record.status != rs.FILE_COMPLETE]
        self.post("done", stats, time.monotonic() - started, cancelled, root, failures,
                  len(plan.files), job.verified_files, complete)
