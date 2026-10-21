"""The download engine: it drives the real MediaFire downloader and reports to the UI.

The heavy lifting happens in :mod:`tools.mediafire_session` (a plain thread, no GUI framework).
This module owns the queue, turns the session events into Qt signals and keeps the persisted
manifest in sync, so the window only ever deals with signals and slots.
"""
from __future__ import annotations

import dataclasses
import json
import os
import queue
import shutil
import subprocess
import sys
import time
import webbrowser
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

try:  # package import
    from .. import mediafire_dl as dl
    from .. import mediafire_resume as rs
    from ..mediafire_session import DownloadSession
except ImportError:  # direct script run
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mediafire_dl as dl  # type: ignore[no-redef]
    import mediafire_resume as rs  # type: ignore[no-redef]
    from mediafire_session import DownloadSession  # type: ignore[no-redef]

from . import settings as settings_store
from .download_model import (COMPLETE, DownloadRecord, INCOMPLETE, RUNNING, records_from_manifests)

REPO_URL = "https://github.com/rehan-remade/ArkLoad"
ISSUES_URL = f"{REPO_URL}/issues"


class Rate:
    """A rolling window over transferred bytes, so the speed label does not jitter."""

    def __init__(self, window: float = 3.0):
        self.window = window
        self.samples: deque[tuple[float, int]] = deque()

    def update(self, total: int, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        self.samples.append((now, int(total)))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.popleft()
        if len(self.samples) < 2:
            return 0.0
        t0, v0 = self.samples[0]
        t1, v1 = self.samples[-1]
        return max(0.0, (v1 - v0) / (t1 - t0)) if t1 > t0 else 0.0


@dataclass
class EngineOptions:
    """Everything the user can change in the preferences."""

    destination: str = str(Path.home() / "Downloads")
    subfolder: bool = True
    overwrite: bool = False
    parallel: int = 4
    retries: int = 3
    recovery_rounds: int = 3
    timeout: float = 60.0
    cookies: str = ""
    slow_mode: bool = False
    slow_pause: float = 1.5
    rate_limit: int = 0            # bytes per second, 0 means unlimited
    connections: int = 1           # parts of a file that are fetched at the same time
    verify_hashes: bool = True
    auto_resume: bool = True

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def to_spec(self) -> dict:
        """The options the download session understands."""
        return {
            "subfolder": self.subfolder,
            "overwrite": self.overwrite,
            "jobs": self.parallel,
            "retries": self.retries,
            "recovery_rounds": self.recovery_rounds,
            "timeout": self.timeout,
            "patterns": [],
            "slow_mode": self.slow_mode,
            "slow_pause": self.slow_pause,
            "rate_limit": self.rate_limit,
            "connections": self.connections,
        }


class DownloadEngine(QObject):
    """Owns the download sessions and turns their events into Qt signals."""

    job_added = Signal(str)                      # job id
    job_changed = Signal(str)                    # job id
    job_finished = Signal(str, bool, str)        # job id, complete, message
    job_error = Signal(str, str)                 # job id, message
    stats_changed = Signal(float, float, int, int)   # down, up, active, total
    message = Signal(str)
    busy_changed = Signal(bool)
    remote_action = Signal(str, str, object)

    def __init__(self, state_dir: Path | None = None, options: EngineOptions | None = None,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.options = options or EngineOptions()
        self.state_dir = Path(state_dir) if state_dir else settings_store.state_dir()
        # the manifest module keeps the folder in a module global, so the engine
        # and the session always write into the same place
        rs.STATE_DIR = self.state_dir
        self._events: queue.Queue = queue.Queue()
        self._sessions: dict[str, DownloadSession] = {}
        self._records: dict[str, DownloadRecord] = {}
        self._file_bytes: dict[tuple[str, int], int] = {}
        self._rates: dict[str, Rate] = {}
        self._active = 0
        self._poller = QTimer(self)
        self._poller.setInterval(120)
        self._poller.timeout.connect(self._tick)
        self._poller.start()

    # -- persistence ----------------------------------------------------------
    def state_path(self) -> Path:
        return self.state_dir

    def _set_state_dir(self, path: Path) -> None:
        self.state_dir = Path(path)

    def records(self) -> list[DownloadRecord]:
        return list(self._records.values())

    def record(self, job_id: str) -> DownloadRecord | None:
        return self._records.get(job_id)

    def load_records(self) -> list[DownloadRecord]:
        """Restore the downloads that were in the list when the app was closed."""
        records = records_from_manifests(rs.list_jobs())
        for record in records:
            self._records[record.job_id] = record
        return records

    def persist_meta(self, record: DownloadRecord) -> None:
        """Store the list entry itself (title, priority, tags) next to the manifest."""
        path = self.state_dir / f"{record.job_id}.meta"
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass

    def load_meta(self) -> dict[str, dict]:
        meta: dict[str, dict] = {}
        try:
            for path in self.state_dir.glob("*.meta"):
                try:
                    meta[path.name[:-len(".meta")]] = json.loads(
                        path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        return meta

    # -- queue ----------------------------------------------------------------
    def add_download(self, url: str, destination: str | None = None,
                     options: EngineOptions | None = None,
                     record: DownloadRecord | None = None, name: str = "") -> DownloadRecord:
        """Queue a new download; the row appears immediately, the work starts right away."""
        options = options or self.options
        destination = str(destination or options.destination)
        source = dl.parse_source(url)
        job_id = rs.job_identity(source.kind, source.key, destination, options.subfolder)
        if record is None:
            record = DownloadRecord(job_id=job_id, url=url, title=source.key,
                                    destination=destination, source_kind=source.kind,
                                    source_key=source.key)
        record.job_id = job_id
        record.destination = destination
        record.options = options.to_spec()
        if name:
            record.options["name"] = name
        self._records[job_id] = record
        self.persist_meta(record)
        self.job_added.emit(job_id)
        self._start(record, options)
        return record

    def _start(self, record: DownloadRecord, options: EngineOptions,
               mode: str = "download") -> None:
        spec = {
            "job_id": record.job_id,
            "url": record.url,
            "dest": record.destination,
            "subfolder": options.subfolder,
            "overwrite": options.overwrite,
            "jobs": options.parallel,
            "retries": options.retries,
            "recovery_rounds": options.recovery_rounds,
            "timeout": options.timeout,
            "patterns": [],
            "cookies": options.cookies or None,
            "rate_limit": int(options.rate_limit or 0),
            "connections": int(options.connections or 1),
            "mirrors": list(record.mirrors),
            "name": str(record.options.get("name") or ""),
            "mode": mode,
            "options": {**options.to_spec(), "cookies_used": bool(options.cookies)},
        }
        session = DownloadSession(spec, self._events)
        self._sessions[record.job_id] = session
        record.status = RUNNING
        session.start()
        self._active += 1
        self.busy_changed.emit(True)

    def start_record(self, record: DownloadRecord, mode: str = "download") -> None:
        if self._session(record) is not None:
            return          # it is already running
        options = self.options
        self._start(record, options, mode=mode)

    def _session(self, record: DownloadRecord):
        session = self._sessions.get(record.job_id)
        if session is not None and session.is_alive():
            return session
        return None

    def session_for(self, record: DownloadRecord):
        """The running session of a download (None when it is not running)."""
        return self._session(record)

    def is_running(self, record: DownloadRecord) -> bool:
        return self._session(record) is not None

    def pause(self, record: DownloadRecord) -> bool:
        """Pause a download. The bytes that are already there are kept."""
        session = self._session(record)
        if session is not None:
            session.set_paused(True)
        record.status = "paused"
        job = rs.load_job(record.job_id)
        if job is not None:
            job.set_status(rs.JOB_PAUSED)
            rs.save(job)
        self.job_changed.emit(record.job_id)
        return session is not None

    def resume(self, record: DownloadRecord) -> None:
        """Continue a paused download; a stopped one starts again from the manifest."""
        session = self._session(record)
        if session is not None:
            session.set_paused(False)
            record.status = RUNNING
            job = rs.load_job(record.job_id)
            if job is not None:
                job.set_status(rs.JOB_RUNNING)
                rs.save(job)
            self.job_changed.emit(record.job_id)
            return
        self.start_record(record)

    def cancel(self, record: DownloadRecord) -> bool:
        """Stop a download. The finished files and the .part files are kept."""
        session = self._session(record)
        if session is not None:
            session.request_stop("cancelled")
        else:
            record.status = "cancelled"
            job = rs.load_job(record.job_id)
            if job is not None:
                job.set_status(rs.JOB_CANCELLED)
                rs.save(job)
            self.job_changed.emit(record.job_id)
        return session is not None

    def stop_all(self, reason: str = "interrupted") -> None:
        for session in self._sessions.values():
            if session.is_alive():
                session.request_stop(reason)

    # -- events ---------------------------------------------------------------
    def _tick(self) -> None:
        """Move the session events onto the GUI thread and refresh the live counters."""
        self._drain()
        self._emit_stats()

    def _drain(self) -> None:
        """Move the session events onto the GUI thread."""
        try:
            while True:
                job_id, kind, *payload = self._events.get_nowait()
                self._handle(job_id, kind, *payload)
        except queue.Empty:
            pass

    def _handle(self, job_id: str, kind: str, *payload) -> None:
        record = self._records.get(job_id)
        if kind == "planning":
            if record:
                record.status = RUNNING
                self.job_changed.emit(job_id)
        elif kind == "plan":
            plan, root, _kind = payload
            if record:
                record.title = plan.root_name or record.title
                record.root = str(root)
                record.bytes_total = plan.total_bytes
                self.job_changed.emit(job_id)
        elif kind == "job":
            job = payload[0]
            if record:
                record.files = list(job.files)
                record.bytes_total = job.total_bytes
                record.bytes_done = job.verified_bytes
                self.job_changed.emit(job_id)
        elif kind == "scan_start":
            if record:
                record.verifying = True
                record.status = RUNNING
                self.job_changed.emit(job_id)
                self.message.emit(f"Verifying the files already in {record.title}…")
        elif kind == "file_state":
            index, item = payload
            if record and 0 < index <= len(record.files):
                record.files[index - 1] = item
                record.bytes_done = sum(
                    (entry.size or entry.bytes or 0) for entry in record.files
                    if entry.status == "complete")
                self.job_changed.emit(job_id)
        elif kind == "scan_done":
            stats = payload[0]
            if record:
                record.verifying = False
                record.files = list(record.files)
                self.job_changed.emit(job_id)
            self.message.emit(
                f"{stats.get('verified', 0)} of {stats.get('total', 0)} files verified")
        elif kind == "file_start":
            index = payload[0]
            if record and 0 < index <= len(record.files):
                record.files[index - 1].status = "downloading"
                self.job_changed.emit(job_id)
        elif kind == "progress":
            index, done, _total = payload
            if record:
                self._file_bytes[(job_id, index)] = done
                record.bytes_done = self._job_bytes(record)
                self.job_changed.emit(job_id)
        elif kind == "file_done":
            index, result, item = payload
            if record:
                if item is not None and 0 < index <= len(record.files):
                    record.files[index - 1] = item
                if result.status in ("ok", "skip"):
                    self._file_bytes[(job_id, index)] = result.size
                record.bytes_done = self._job_bytes(record)
                self.job_changed.emit(job_id)
        elif kind == "recovery":
            round_no, remaining = payload
            if record:
                record.status = RUNNING
                self.job_changed.emit(job_id)
            self.message.emit(
                f"Recovery pass {round_no}: retrying {remaining} missing or unverified file(s)…")
        elif kind == "done":
            stats, elapsed, cancelled, root, failures, total, processed, complete = payload
            if record:
                record.status = (COMPLETE if complete else
                                 ("cancelled" if cancelled else INCOMPLETE))
                record.errors = list(failures)
                record.root = str(root) or record.root
                record.verifying = False
                self.job_changed.emit(job_id)
            self._sessions.pop(job_id, None)
            self._active = max(0, self._active - 1)
            self.busy_changed.emit(self._active > 0)
            if complete:
                text = (f"All {total} files downloaded and verified"
                        if total > 1 else "Download finished")
                self.job_finished.emit(job_id, True, text)
            elif cancelled:
                self.job_finished.emit(job_id, False, "Cancelled")
            else:
                self.job_finished.emit(
                    job_id, False,
                    f"{processed} of {total} files verified, {len(failures)} still missing "
                    "or unverified")
        elif kind == "cancelled":
            if record:
                record.status = "cancelled"
                self.job_changed.emit(job_id)
        elif kind == "fatal":
            text = str(payload[0]) if payload else "Unknown error"
            if record:
                record.status = "failed"
                record.note = text
                self.job_changed.emit(job_id)
            self._sessions.pop(job_id, None)
            self._active = max(0, self._active - 1)
            self.busy_changed.emit(self._active > 0)
            self.job_error.emit(job_id, text)
        self._emit_stats()

    def _job_bytes(self, record: DownloadRecord) -> int:
        """The verified bytes plus the bytes that are currently in flight."""
        total = 0
        for index, item in enumerate(record.files, 1):
            if item.status == "complete":
                total += item.size or item.bytes or 0
            else:
                total += self._file_bytes.get((record.job_id, index), 0)
        return total

    def _emit_stats(self) -> None:
        active = sum(1 for record in self._records.values() if record.active)
        total = len(self._records)
        down = 0.0
        up = 0.0
        for record in self._records.values():
            transferred = sum(value for (job, _index), value in self._file_bytes.items()
                              if job == record.job_id)
            record.speed_down = self._rates.setdefault(record.job_id, Rate()).update(transferred)
            down += record.speed_down
            up += record.speed_up
        self.stats_changed.emit(down, up, active, total)

    # -- file operations ------------------------------------------------------
    def remove_from_list(self, record: DownloadRecord, delete_files: bool = False) -> None:
        """Remove a row from the list; the files are only deleted when asked for."""
        session = self._sessions.get(record.job_id)
        if session is not None and session.is_alive():
            session.request_stop("cancelled")
        if delete_files and record.root:
            try:
                shutil.rmtree(record.root)
            except OSError:
                pass
        try:
            rs.state_path(record.job_id).unlink(missing_ok=True)
            (self.state_dir / f"{record.job_id}.meta").unlink(missing_ok=True)
        except OSError:
            pass
        self._records.pop(record.job_id, None)
        self.job_changed.emit(record.job_id)
        self._emit_stats()

    def rename(self, record: DownloadRecord, new_name: str) -> bool:
        """Rename the downloaded folder (or the file) and remember the new name."""
        new_name = new_name.strip()
        if not new_name or not record.root:
            return False
        source = Path(record.root)
        target = source.with_name(new_name)
        if target == source or target.exists():
            return False
        try:
            source.rename(target)
        except OSError:
            return False
        record.root = str(target)
        record.title = new_name
        self.persist_meta(record)
        job = rs.load_job(record.job_id)
        if job is not None:
            job.root = str(target)
            job.title = new_name
            rs.save(job)
        self.job_changed.emit(record.job_id)
        return True

    def move_to(self, record: DownloadRecord, folder: str) -> bool:
        """Move a finished download to another folder."""
        if not record.root:
            return False
        source = Path(record.root)
        target_root = Path(folder)
        target = target_root / source.name
        if not source.exists():
            return False
        try:
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        except OSError:
            return False
        record.root = str(target)
        record.destination = str(target_root)
        self.persist_meta(record)
        job = rs.load_job(record.job_id)
        if job is not None:
            job.root = str(target)
            job.destination = str(target_root)
            rs.save(job)
        self.job_changed.emit(record.job_id)
        return True

    def add_mirror(self, record: DownloadRecord, url: str) -> bool:
        """Remember an alternative source for a download."""
        url = url.strip()
        if not url or url in record.mirrors:
            return False
        try:
            dl.parse_source(url)
        except Exception:  # noqa: BLE001 - the mirror must be a MediaFire link
            return False
        record.mirrors.append(url)
        self.persist_meta(record)
        job = rs.load_job(record.job_id)
        if job is not None:
            job.mirrors = list(record.mirrors)
            rs.save(job)
        self.job_changed.emit(record.job_id)
        return True

    def set_priority(self, record: DownloadRecord, priority: str) -> None:
        record.priority = priority
        self.persist_meta(record)
        self.job_changed.emit(record.job_id)

    def set_tags(self, record: DownloadRecord, tags: list[str]) -> None:
        record.tags = [tag.strip() for tag in tags if tag.strip()]
        self.persist_meta(record)
        self.job_changed.emit(record.job_id)

    def schedule(self, record: DownloadRecord, when: float) -> None:
        record.start_after = when
        self.persist_meta(record)
        self.job_changed.emit(record.job_id)

    def check_for_update(self, record: DownloadRecord) -> str:
        """Compare the files on disk with what MediaFire currently publishes."""
        try:
            source = dl.parse_source(record.url)
            plan = dl.make_plan(source, None, None, self.options.cookies or None)
        except Exception as e:  # noqa: BLE001 - reported to the user
            return f"Could not read the source: {e}"
        remote = {item.rel.as_posix(): (item.size, item.sha256) for item in plan.files}
        local = {item.path: (item.size, item.expected_sha256) for item in record.files}
        changed = [path for path, (size, sha) in remote.items()
                   if path in local and local[path][0] != size]
        missing = [path for path in remote if path not in local]
        extra = [path for path in local if path not in remote]
        if not (changed or missing or extra):
            return "The source matches the files on disk."
        parts = []
        if missing:
            parts.append(f"{len(missing)} new file(s)")
        if changed:
            parts.append(f"{len(changed)} changed file(s)")
        if extra:
            parts.append(f"{len(extra)} file(s) no longer on the source")
        return "Source changed: " + ", ".join(parts)

    # -- remote access --------------------------------------------------------
    def snapshot(self) -> list[dict]:
        """The download list for the remote access page."""
        rows = []
        for record in self._records.values():
            rows.append({
                "id": record.job_id,
                "title": record.title,
                "url": record.url,
                "status": record.status_text(),
                "size": record.bytes_total,
                "speed": record.speed_down,
                "active": record.active,
                "complete": record.status == COMPLETE,
                "failed": record.status in ("failed", "incomplete"),
                "verified": record.verified_files,
                "files": record.total_files,
            })
        return rows

    def remote_command(self, action: str, job_id: str, payload: dict) -> None:
        """Handle a command from the remote page; it runs on the GUI thread."""
        self.remote_action.emit(action, job_id, payload)

    def verify(self, record: DownloadRecord) -> None:
        """Re-verify the files of a download against the manifest and MediaFire's hashes."""
        self.start_record(record, mode="verify")

    def file_integrity_report(self, record: DownloadRecord) -> str:
        """A human readable integrity report for one download."""
        if not record.files:
            return "Nothing has been downloaded yet."
        lines = []
        for item in record.files:
            if item.status == "complete":
                how = "verified against MediaFire's SHA-256" if item.verified_against_source \
                    else "verified against the locally recorded hash"
                lines.append(f"OK   {item.path} ({how})")
            else:
                lines.append(f"{item.status.replace('_', ' ').upper():<6} {item.path}"
                             + (f" – {item.error}" if item.error else ""))
        return "\n".join(lines)


def open_in_file_manager(path: Path) -> bool:
    """Open the file manager, selecting the file when the platform supports it."""
    path = Path(path)
    try:
        if os.name == "nt":
            if path.is_dir():
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
        return True
    except OSError:
        return False


def open_url(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001 - never let a browser failure kill the app
        return False
