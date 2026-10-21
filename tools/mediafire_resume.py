#!/usr/bin/env python3
"""Persistent job manifests, per-file status detection and integrity verification.

The downloader in ``mediafire_dl.py`` can resume a single transfer from a ``.part`` file, but it
forgets everything as soon as the process ends. This module adds the missing half:

* **Job manifests** – one small JSON file per download (source, destination, options and the state
  of every file) written atomically under ``~/.mediafire-dl/jobs``. The GUI reloads them on start,
  so a crash, a closed window or a reboot never costs more than the bytes still in flight.
* **Status detection** – every file is classified as ``missing``, ``incomplete``,
  ``verification failed``, ``failed`` or ``complete`` by looking at the destination folder.
* **Integrity verification** – MediaFire's API exposes a SHA-256 per file, so a finished download
  is compared against the server's own hash. When a file has no authoritative hash, the local
  SHA-256 recorded in the manifest is used as a baseline instead, and the result is reported as
  *locally verified* rather than *verified against the source*.

Nothing here talks to the network, so it is easy to test offline:

    python -m pytest -q tests/test_mediafire_resume.py
"""
from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

try:  # package import (python -m tools.mediafire_resume)
    from . import mediafire_dl as dl
except ImportError:  # direct script run
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mediafire_dl as dl  # type: ignore[no-redef]

SCHEMA = 1
STATE_DIR = Path.home() / ".mediafire-dl" / "jobs"

# --- per file states ---------------------------------------------------------
FILE_PENDING = "pending"
FILE_DOWNLOADING = "downloading"
FILE_COMPLETE = "complete"
FILE_INCOMPLETE = "incomplete"
FILE_MISSING = "missing"
FILE_VERIFY_FAILED = "verification_failed"
FILE_FAILED = "failed"

FILE_STATES = (FILE_PENDING, FILE_DOWNLOADING, FILE_COMPLETE, FILE_INCOMPLETE,
               FILE_MISSING, FILE_VERIFY_FAILED, FILE_FAILED)

# --- per job states ----------------------------------------------------------
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_PAUSED = "paused"
JOB_INTERRUPTED = "interrupted"
JOB_CANCELLED = "cancelled"
JOB_COMPLETE = "complete"
JOB_INCOMPLETE = "incomplete"
JOB_FAILED = "failed"

JOB_STATES = (JOB_QUEUED, JOB_RUNNING, JOB_PAUSED, JOB_INTERRUPTED, JOB_CANCELLED,
              JOB_COMPLETE, JOB_INCOMPLETE, JOB_FAILED)

#: job states that still have work left to do
UNFINISHED_STATES = (JOB_QUEUED, JOB_RUNNING, JOB_PAUSED, JOB_INTERRUPTED, JOB_CANCELLED,
                     JOB_INCOMPLETE, JOB_FAILED)

#: job states that are picked up automatically when the application starts
RESUMABLE_STATES = (JOB_QUEUED, JOB_RUNNING, JOB_INTERRUPTED)


# --------------------------------------------------------------------------- helpers

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise_hash(value) -> str | None:
    """Accept only a full SHA-256 hex digest; anything else is treated as unknown."""
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
        return value.strip().lower()
    return None


def matches_patterns(rel, patterns: Sequence[str]) -> bool:
    """True when a planned path matches any glob (``*.jar``, ``mods/*`` …)."""
    if not patterns:
        return True
    path = PurePosixPath(rel).as_posix().casefold()
    name = PurePosixPath(rel).name.casefold()
    return any(fnmatch.fnmatchcase(path, pattern.casefold())
               or fnmatch.fnmatchcase(name, pattern.casefold())
               for pattern in patterns)


def job_identity(kind: str, key: str, destination: str, subfolder: bool) -> str:
    """A stable id for "this source into this destination", so a job is always found again."""
    dest = os.path.normcase(str(Path(destination).expanduser().resolve()))
    raw = "\n".join([kind or "", key or "", dest, "subfolder" if subfolder else "flat"])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def state_path(job_id: str) -> Path:
    return STATE_DIR / f"{job_id}.json"


def record_path(root: Path | str, record: "FileRecord") -> Path:
    """Where a record lives on disk (a planned path is always POSIX style)."""
    parts = PurePosixPath(record.path).parts
    return Path(root).joinpath(*parts)


def part_path(root: Path | str, record: "FileRecord") -> Path:
    final = record_path(root, record)
    return final.with_name(final.name + ".part")


# --------------------------------------------------------------------------- records

@dataclasses.dataclass
class FileRecord:
    """The state of one file inside a job."""

    quickkey: str = ""
    path: str = ""
    size: int | None = None
    expected_sha256: str | None = None   # the hash MediaFire itself publishes
    actual_sha256: str | None = None     # what is on disk (or the local baseline)
    status: str = FILE_PENDING
    attempts: int = 0
    error: str = ""
    verified_at: str = ""
    verified_mtime: float = 0.0          # mtime at the moment we hashed it
    bytes: int = 0                       # bytes currently on disk for this file

    @property
    def complete(self) -> bool:
        return self.status == FILE_COMPLETE

    @property
    def needs_work(self) -> bool:
        return self.status != FILE_COMPLETE

    @property
    def verified_against_source(self) -> bool:
        """True when the bytes matched MediaFire's own SHA-256."""
        return bool(self.expected_sha256) and self.actual_sha256 == self.expected_sha256

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FileRecord":
        size = data.get("size")
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        status = str(data.get("status") or FILE_PENDING)
        if status not in FILE_STATES:
            status = FILE_PENDING
        return cls(
            quickkey=str(data.get("quickkey") or ""),
            path=str(data.get("path") or ""),
            size=size,
            expected_sha256=normalise_hash(data.get("expected_sha256")),
            actual_sha256=normalise_hash(data.get("actual_sha256")),
            status=status,
            attempts=int(data.get("attempts") or 0),
            error=str(data.get("error") or ""),
            verified_at=str(data.get("verified_at") or ""),
            verified_mtime=float(data.get("verified_mtime") or 0.0),
            bytes=int(data.get("bytes") or 0),
        )


@dataclasses.dataclass
class Job:
    """A persisted download: one MediaFire folder or file going into one destination."""

    job_id: str = ""
    source_url: str = ""
    source_kind: str = "folder"
    source_key: str = ""
    destination: str = ""
    root: str = ""
    title: str = ""
    options: dict = dataclasses.field(default_factory=dict)
    status: str = JOB_QUEUED
    created: str = dataclasses.field(default_factory=now_iso)
    updated: str = dataclasses.field(default_factory=now_iso)
    started: str = ""
    finished: str = ""
    recovery_round: int = 0
    last_error: str = ""
    mirrors: list[str] = dataclasses.field(default_factory=list)
    files: list[FileRecord] = dataclasses.field(default_factory=list)

    # -- serialisation --------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": SCHEMA,
            "job_id": self.job_id,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "source_key": self.source_key,
            "destination": self.destination,
            "root": self.root,
            "title": self.title,
            "options": self.options,
            "status": self.status,
            "created": self.created,
            "updated": self.updated,
            "started": self.started,
            "finished": self.finished,
            "recovery_round": self.recovery_round,
            "last_error": self.last_error,
            "mirrors": list(self.mirrors),
            "files": [record.to_dict() for record in self.files],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        if not isinstance(data, dict):
            raise ValueError("manifest is not an object")
        version = data.get("version")
        if version is not None and int(version) > SCHEMA:
            raise ValueError(f"manifest written by a newer version ({version})")
        status = str(data.get("status") or JOB_QUEUED)
        if status not in JOB_STATES:
            status = JOB_QUEUED
        options = data.get("options")
        files = []
        for raw in data.get("files") or []:
            if isinstance(raw, dict) and raw.get("path"):
                files.append(FileRecord.from_dict(raw))
        return cls(
            job_id=str(data.get("job_id") or ""),
            source_url=str(data.get("source_url") or ""),
            source_kind=str(data.get("source_kind") or "folder"),
            source_key=str(data.get("source_key") or ""),
            destination=str(data.get("destination") or ""),
            root=str(data.get("root") or ""),
            title=str(data.get("title") or ""),
            options=options if isinstance(options, dict) else {},
            status=status,
            created=str(data.get("created") or now_iso()),
            updated=str(data.get("updated") or now_iso()),
            started=str(data.get("started") or ""),
            finished=str(data.get("finished") or ""),
            recovery_round=int(data.get("recovery_round") or 0),
            last_error=str(data.get("last_error") or ""),
            mirrors=[str(item) for item in (data.get("mirrors") or [])],
            files=files,
        )

    # -- helpers --------------------------------------------------------------
    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def verified_files(self) -> int:
        return sum(1 for record in self.files if record.status == FILE_COMPLETE)

    @property
    def total_bytes(self) -> int:
        return sum(record.size or 0 for record in self.files)

    @property
    def verified_bytes(self) -> int:
        return sum((record.size or record.bytes or 0) for record in self.files
                   if record.status == FILE_COMPLETE)

    def record(self, index: int) -> FileRecord | None:
        """Records are addressed by their 1-based position in the plan."""
        if 1 <= index <= len(self.files):
            return self.files[index - 1]
        return None

    def find(self, path: str) -> FileRecord | None:
        for record in self.files:
            if record.path == path:
                return record
        return None

    def set_status(self, status: str) -> None:
        if status in JOB_STATES:
            self.status = status
        self.updated = now_iso()


# --------------------------------------------------------------------------- storage

def save(job: Job) -> bool:
    """Write a manifest atomically; a crash mid-write can never corrupt the old copy."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        target = state_path(job.job_id)
        temporary = target.with_name(target.name + ".tmp")
        payload = json.dumps(job.to_dict(), indent=2, sort_keys=False)
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)
        return True
    except OSError:
        return False


def load_job(job_id: str) -> Job | None:
    path = state_path(job_id)
    try:
        return Job.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def list_jobs() -> list[Job]:
    """Every manifest on disk, newest first. Unreadable manifests are skipped."""
    jobs: list[Job] = []
    try:
        paths = sorted(STATE_DIR.glob("*.json"))
    except OSError:
        return jobs
    for path in paths:
        try:
            job = Job.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
        if job.job_id:
            jobs.append(job)
    jobs.sort(key=lambda job: job.updated or job.created, reverse=True)
    return jobs


def unfinished_jobs() -> list[Job]:
    return [job for job in list_jobs() if is_unfinished(job)]


def is_unfinished(job: Job) -> bool:
    if job.status in UNFINISHED_STATES:
        return True
    return job.status == JOB_COMPLETE and not all_complete(job)


def is_complete(job: Job) -> bool:
    return job.status == JOB_COMPLETE and all_complete(job)


# --------------------------------------------------------------------------- planning

def _clean_options(options: dict) -> dict:
    """Never persist cookies – they are credentials, and the manifest lives on disk."""
    clean: dict = {}
    for key, value in (options or {}).items():
        if key in ("cookies", "cookie"):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        elif isinstance(value, (list, tuple)):
            clean[key] = [str(item) for item in value]
        else:
            clean[key] = str(value)
    clean["cookies_used"] = bool((options or {}).get("cookies_used")
                                 or (options or {}).get("cookies"))
    return clean


def create_job(source_url: str, source_kind: str, source_key: str, destination: str,
               options: dict, title: str = "") -> Job:
    """Create (and persist) an empty job; the plan fills in the files later."""
    subfolder = bool((options or {}).get("subfolder", True))
    job_id = job_identity(source_kind, source_key, destination, subfolder)
    job = Job(
        job_id=job_id,
        source_url=source_url,
        source_kind=source_kind,
        source_key=source_key,
        destination=str(destination),
        root=str(destination),
        title=title or source_key,
        options=_clean_options(options),
        status=JOB_QUEUED,
    )
    save(job)
    return job


def reconcile(job: Job, plan) -> None:
    """Merge a fresh plan into the manifest, keeping the state of files we already know."""
    old_by_path = {record.path: record for record in job.files}
    old_by_key: dict[str, FileRecord] = {}
    for record in job.files:
        if record.quickkey and record.quickkey not in old_by_key:
            old_by_key[record.quickkey] = record

    merged: list[FileRecord] = []
    for item in plan.files:
        path = PurePosixPath(item.rel).as_posix()
        record = old_by_path.get(path)
        if record is None and item.quickkey:
            record = old_by_key.get(item.quickkey)
        if record is None:
            record = FileRecord(quickkey=item.quickkey, path=path, size=item.size,
                                expected_sha256=item.sha256)
        else:
            record.quickkey = item.quickkey
            record.path = path
            if record.size is not None and item.size is not None and record.size != item.size:
                record.status = FILE_INCOMPLETE
                record.error = "the file on MediaFire changed size"
            if (record.expected_sha256 and item.sha256
                    and record.expected_sha256 != item.sha256):
                record.status = FILE_VERIFY_FAILED
                record.error = "the file on MediaFire was replaced"
            record.size = item.size
            record.expected_sha256 = item.sha256
            if (record.status == FILE_COMPLETE and record.expected_sha256
                    and record.actual_sha256
                    and record.expected_sha256 != record.actual_sha256):
                record.status = FILE_VERIFY_FAILED
                record.error = "the file on MediaFire was replaced"
        merged.append(record)
    job.files = merged
    job.updated = now_iso()


def load_or_create(source_url: str, source_kind: str, source_key: str, destination: str,
                   root: Path | str, options: dict, plan) -> Job:
    """Find the manifest for this source/destination pair (or create it) and sync it with the plan."""
    subfolder = bool((options or {}).get("subfolder", True))
    job_id = job_identity(source_kind, source_key, destination, subfolder)
    job = load_job(job_id)
    if job is None:
        job = Job(job_id=job_id, source_url=source_url, source_kind=source_kind,
                  source_key=source_key, destination=str(destination), root=str(root),
                  title=source_key, options=_clean_options(options), status=JOB_QUEUED)
    job.source_url = source_url
    job.source_kind = source_kind
    job.source_key = source_key
    job.destination = str(destination)
    job.root = str(root)
    job.options = _clean_options(options)
    job.status = JOB_RUNNING
    job.started = now_iso()
    job.finished = ""
    job.updated = now_iso()
    reconcile(job, plan)
    save(job)
    return job


# --------------------------------------------------------------------------- detection

def summary(job: Job) -> dict:
    """Counts per state, used by the status bar and the summary line."""
    counts = {state: 0 for state in FILE_STATES}
    for record in job.files:
        counts[record.status] = counts.get(record.status, 0) + 1
    counts["total"] = len(job.files)
    counts["bytes"] = job.verified_bytes
    counts["total_bytes"] = job.total_bytes
    counts["verified"] = job.verified_files
    return counts


def all_complete(job: Job) -> bool:
    return all(record.status == FILE_COMPLETE for record in job.files)


def verify_record(record: FileRecord, root: Path | str, cancel: threading.Event | None = None) -> bool:
    """Check one file on disk against the manifest. Returns True when it is complete and verified."""
    final = record_path(root, record)
    partial = part_path(root, record)

    if final.is_file():
        stat = final.stat()
        record.bytes = stat.st_size
        if record.size is not None and stat.st_size != record.size:
            record.status = FILE_INCOMPLETE
            record.error = (f"size mismatch: expected {record.size} bytes, "
                            f"found {stat.st_size}")
            record.verified_at = ""
            record.verified_mtime = 0.0
            return False
        digest = dl.sha256_file(final, cancel)
        expected = record.expected_sha256 or record.actual_sha256
        if expected:
            if digest.lower() != expected.lower():
                record.status = FILE_VERIFY_FAILED
                record.error = (f"sha256 mismatch: expected {expected}, got {digest}")
                record.verified_at = ""
                record.verified_mtime = 0.0
                return False
            record.actual_sha256 = digest
        else:
            # No authoritative hash: record a local baseline so later runs can spot changes.
            record.actual_sha256 = digest
        record.status = FILE_COMPLETE
        record.error = ""
        record.verified_at = now_iso()
        record.verified_mtime = stat.st_mtime
        return True

    if partial.is_file():
        record.status = FILE_INCOMPLETE
        record.bytes = partial.stat().st_size
        record.error = "partial download"
        record.verified_at = ""
        record.verified_mtime = 0.0
        return False

    record.status = FILE_MISSING
    record.bytes = 0
    record.error = ""
    record.verified_at = ""
    record.verified_mtime = 0.0
    return False


def scan(job: Job, root: Path | str, cancel: threading.Event | None = None,
         progress: Callable[[int, int, FileRecord], None] | None = None) -> dict:
    """Classify every file of the job by looking at the destination folder.

    ``progress(index, total, record)`` is called after each file so the UI can show the work.
    """
    total = len(job.files)
    for index, record in enumerate(job.files, 1):
        if cancel is not None and cancel.is_set():
            raise dl.Cancelled()
        verify_record(record, root, cancel)
        if progress is not None:
            progress(index, total, record)
    job.updated = now_iso()
    save(job)
    return summary(job)


def validate(job: Job, root: Path | str, cancel: threading.Event | None = None) -> list[FileRecord]:
    """Re-check the files we already called complete.

    Files that were not touched since they were hashed are trusted (their hash was verified
    already); anything that changed on disk is hashed again and compared to the manifest.
    """
    changed: list[FileRecord] = []
    for record in job.files:
        if cancel is not None and cancel.is_set():
            raise dl.Cancelled()
        if record.status != FILE_COMPLETE:
            continue
        final = record_path(root, record)
        if not final.is_file():
            record.status = FILE_MISSING
            record.error = "the file disappeared from the destination"
            record.bytes = 0
            record.verified_at = ""
            record.verified_mtime = 0.0
            changed.append(record)
            continue
        stat = final.stat()
        if (record.size is not None and stat.st_size != record.size) or (
                record.bytes and stat.st_size != record.bytes):
            changed.append(record)
            verify_record(record, root, cancel)
            continue
        if record.verified_mtime and abs(stat.st_mtime - record.verified_mtime) < 1e-6:
            continue  # untouched since we hashed it
        changed.append(record)
        verify_record(record, root, cancel)
    if changed:
        job.updated = now_iso()
        save(job)
    return changed


def normalize_interrupted(job: Job, root: Path | str | None = None) -> None:
    """After an interruption, a file that was 'downloading' is really 'incomplete'.

    The worker never got to report its result, so the manifest would otherwise keep a file in
    the 'downloading' state forever.
    """
    for record in job.files:
        if record.status != FILE_DOWNLOADING:
            continue
        record.status = FILE_INCOMPLETE
        record.error = "interrupted"
        record.verified_at = ""
        record.verified_mtime = 0.0
        if root:
            partial = part_path(root, record)
            if partial.is_file():
                record.bytes = partial.stat().st_size
            else:
                final = record_path(root, record)
                record.bytes = final.stat().st_size if final.is_file() else 0


def record_result(job: Job, index: int, result, root: Path | str) -> FileRecord | None:
    """Store the outcome of one download and re-derive the file's state from disk."""
    record = job.record(index)
    if record is None:
        return None
    record.attempts += 1
    if result.status in ("ok", "skip"):
        final = record_path(root, record)
        digest = getattr(result, "sha256", "") or ""
        if not digest and final.is_file():
            try:
                digest = dl.sha256_file(final)
            except OSError:
                digest = ""
        if digest:
            record.actual_sha256 = digest
        if final.is_file():
            stat = final.stat()
            record.bytes = stat.st_size
            record.verified_mtime = stat.st_mtime
        record.status = FILE_COMPLETE
        record.error = ""
        record.verified_at = now_iso()
        # A file is only complete when it really matches what MediaFire publishes.
        if record.expected_sha256 and record.actual_sha256 \
                and record.expected_sha256 != record.actual_sha256:
            record.status = FILE_VERIFY_FAILED
            record.error = ("sha256 mismatch: expected "
                            f"{record.expected_sha256}, got {record.actual_sha256}")
            record.verified_at = ""
        elif record.size is not None and record.bytes and record.bytes != record.size:
            record.status = FILE_INCOMPLETE
            record.error = (f"size mismatch: expected {record.size} bytes, "
                            f"found {record.bytes}")
            record.verified_at = ""
    elif result.status == "failed":
        record.status = FILE_FAILED
        record.error = getattr(result, "error", "") or "download failed"
    else:  # cancelled
        final = record_path(root, record)
        partial = part_path(root, record)
        if partial.is_file():
            record.status = FILE_INCOMPLETE
            record.bytes = partial.stat().st_size
        elif final.is_file():
            record.status = FILE_INCOMPLETE
            record.bytes = final.stat().st_size
        else:
            record.status = FILE_MISSING
            record.bytes = 0
        record.error = "cancelled"
    job.updated = now_iso()
    save(job)
    return record
