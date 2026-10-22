"""The core of ArkLoad: the task state machine and the persistent queue.

The window, the engine and the manifests all speak the same language:

* :class:`TaskState` is the single list of states a download can be in.
* :class:`Task` is one download in the queue: the link, the destination, the options and the
  state.
* :class:`TaskStore` is the queue on disk (``queue.json`` in the application data folder).

The per file state lives in the manifests (``jobs/<id>.json``), the queue holds the tasks.
Both are written by the engine, so there is exactly one place that knows the state of a
download.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from . import settings as settings_store


class TaskState(str, Enum):
    """Every state a download can be in."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    VERIFYING = "verifying"
    COMPLETED = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def label(self) -> str:
        return {
            TaskState.QUEUED: "Queued",
            TaskState.RUNNING: "Downloading",
            TaskState.PAUSED: "Paused",
            TaskState.VERIFYING: "Verifying",
            TaskState.COMPLETED: "Completed",
            TaskState.INCOMPLETE: "Incomplete",
            TaskState.FAILED: "Failed",
            TaskState.CANCELLED: "Stopped",
            TaskState.INTERRUPTED: "Interrupted",
        }[self]

    @property
    def finished(self) -> bool:
        """True when the task will not move on its own any more."""
        return self in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED,
                        TaskState.INCOMPLETE)

    @property
    def unfinished(self) -> bool:
        """True when the task still has work left (it is resumed on the next start)."""
        return not self.finished

    @classmethod
    def parse(cls, value, default: "TaskState" = None) -> "TaskState":
        default = default or cls.QUEUED
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return default


#: the categories of the sidebar
CATEGORIES = ("All downloads", "Downloading", "Queued", "Completed", "Failed", "History",
              "Settings")


@dataclass
class Task:
    """One download in the queue: the link, the destination, the files and the state.

    This is the only object that describes a download. The window renders it, the engine updates
    it and the queue stores it.

    ``backend`` says which engine runs the task: ``"arkload"`` is ArkLoad's own MediaFire engine
    (it verifies every file against MediaFire's SHA-256), ``"jdownloader"`` is the JDownloader 2
    engine, which knows thousands of hosts.  ``external_id`` is the id the task has in that
    engine, so a task survives a restart of ArkLoad.
    """

    job_id: str
    url: str
    title: str = ""
    destination: str = ""
    source_kind: str = "folder"
    source_key: str = ""
    root: str = ""
    state: TaskState = TaskState.QUEUED
    priority: str = "HIGH"
    tags: list[str] = field(default_factory=list)
    mirrors: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    start_after: float = 0.0
    error: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    finished: float = 0.0
    bytes_total: int = 0
    bytes_done: int = 0
    verified: int = 0
    file_count: int = 0
    speed_down: float = 0.0
    speed_up: float = 0.0
    verifying: bool = False
    note: str = ""
    errors: list = field(default_factory=list)
    files: list = field(default_factory=list)   # the per file records of the manifest
    backend: str = "arkload"
    external_id: str = ""

    # -- the texts the window shows ------------------------------------------
    @property
    def total_files(self) -> int:
        return self.file_count or len(self.files)

    @property
    def verified_files(self) -> int:
        return sum(1 for item in self.files if getattr(item, "status", "") == "complete")

    def matches_filter(self, mode: str) -> bool:
        """The file type filter of the toolbar."""
        if mode in ("", "All files"):
            return True
        if mode == "Missing Files":
            return self.missing > 0 or self.state in (TaskState.INCOMPLETE, TaskState.FAILED,
                                                     TaskState.INTERRUPTED)
        if mode == "Active":
            return self.state in (TaskState.RUNNING, TaskState.QUEUED, TaskState.VERIFYING)
        if mode == "Completed":
            return self.state == TaskState.COMPLETED
        if mode == "MediaFire folders":
            return self.is_folder
        suffix = Path(self.title).suffix.lower()
        if mode == "Video":
            return suffix in (".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv")
        if mode == "Music":
            return suffix in (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus")
        return True

    def matches_selection(self, mode: str) -> bool:
        """The selection modes of the toolbar."""
        if mode in ("", "All"):
            return True
        if mode == "Downloads only":
            return self.source_kind == "file"
        if mode == "Uploads only":
            return False          # there is no upload feature in this build
        if mode == "Completed":
            return self.state == TaskState.COMPLETED
        if mode == "Stopped":
            return self.state in (TaskState.CANCELLED, TaskState.FAILED, TaskState.INCOMPLETE,
                                  TaskState.PAUSED)
        return True

    def matches_category(self, category: str) -> bool:
        """The categories of the tabs, all of them backed by the real state."""
        if category in ("", "All downloads"):
            return True
        if category == "Active downloads":
            # everything that still has work left (running, queued, being verified)
            return self.state.unfinished
        if category == "Downloading":
            return self.state in (TaskState.RUNNING, TaskState.VERIFYING)
        if category == "Queued":
            return self.state == TaskState.QUEUED
        if category == "Completed":
            return self.state == TaskState.COMPLETED
        if category == "Failed":
            return self.state in (TaskState.FAILED, TaskState.INCOMPLETE)
        if category == "History":
            return self.state.finished
        return True

    def status_text(self) -> str:
        if self.state == TaskState.RUNNING and self.verifying:
            return "Verifying"
        if self.state == TaskState.RUNNING and (self.files or self.bytes_total):
            return f"Downloading {self.percent}%"
        if self.state == TaskState.COMPLETED:
            if self.total_files > 1:
                return f"Completed {self.verified_files}/{self.total_files}"
            return "Completed"
        if self.state == TaskState.INCOMPLETE:
            return f"Incomplete {self.verified_files}/{self.total_files}"
        if self.state == TaskState.QUEUED and self.start_after > time.time():
            # it waits for its time - as soon as the time has come it is a normal download
            return "Scheduled"
        return self.state.label

    def status_tag(self) -> str:
        if self.state == TaskState.COMPLETED:
            return "done"
        if self.state in (TaskState.FAILED, TaskState.INCOMPLETE):
            return "failed"
        if self.state in (TaskState.CANCELLED, TaskState.INTERRUPTED, TaskState.PAUSED):
            return "cancelled"
        if self.state in (TaskState.RUNNING, TaskState.VERIFYING):
            return "running"
        return "waiting"

    def status_key(self) -> int:
        order = {TaskState.RUNNING: 0, TaskState.VERIFYING: 0, TaskState.QUEUED: 1,
                 TaskState.PAUSED: 2, TaskState.INTERRUPTED: 3, TaskState.INCOMPLETE: 4,
                 TaskState.FAILED: 5, TaskState.CANCELLED: 6, TaskState.COMPLETED: 7}
        return order.get(self.state, 8)

    def matches_search(self, needle: str) -> bool:
        if not needle:
            return True
        needle = needle.casefold()
        if needle in self.title.casefold() or needle in self.url.casefold():
            return True
        return any(needle in str(getattr(item, "path", "")).casefold() for item in self.files)

    # -- derived -------------------------------------------------------------
    @property
    def status(self) -> TaskState:
        """The state, under the name the window used before."""
        return self.state

    @status.setter
    def status(self, value) -> None:
        self.state = TaskState.parse(value, self.state)

    @property
    def added(self) -> float:
        """When the download was added (the name the window uses)."""
        return self.created

    @property
    def total(self) -> int:
        return self.file_count or len(self.files)

    @property
    def missing(self) -> int:
        return sum(1 for item in self.files if getattr(item, "status", "") != "complete")

    @property
    def missing_files(self) -> int:
        return self.missing

    @property
    def percent(self) -> int:
        if self.bytes_total:
            return int(100 * min(self.bytes_done, self.bytes_total) / self.bytes_total)
        if self.files:
            return int(100 * self.verified / len(self.files))
        return 0

    @property
    def is_folder(self) -> bool:
        return self.source_kind == "folder"

    @property
    def active(self) -> bool:
        """True while the task is queued, running or being verified."""
        return self.state in (TaskState.QUEUED, TaskState.RUNNING, TaskState.VERIFYING)

    @property
    def running(self) -> bool:
        return self.state in (TaskState.RUNNING, TaskState.VERIFYING)

    @property
    def label(self) -> str:
        return self.state.label

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "title": self.title,
            "destination": self.destination,
            "source_kind": self.source_kind,
            "source_key": self.source_key,
            "state": self.state.value,
            "priority": self.priority,
            "tags": list(self.tags),
            "mirrors": list(self.mirrors),
            "options": dict(self.options),
            "start_after": self.start_after,
            "error": self.error,
            "created": self.created,
            "updated": self.updated,
            "finished": self.finished,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "verified": self.verified,
            "file_count": self.file_count,
            "root": self.root,
            "speed_down": self.speed_down,
            "speed_up": self.speed_up,
            "verifying": self.verifying,
            "note": self.note,
            "errors": [list(item) for item in self.errors],
            "backend": self.backend,
            "external_id": self.external_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            job_id=str(data.get("job_id") or data.get("id") or ""),
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            destination=str(data.get("destination") or ""),
            source_kind=str(data.get("source_kind") or "folder"),
            source_key=str(data.get("source_key") or ""),
            state=TaskState.parse(data.get("state")),
            priority=str(data.get("priority") or "HIGH"),
            tags=[str(tag) for tag in (data.get("tags") or [])],
            mirrors=[str(mirror) for mirror in (data.get("mirrors") or [])],
            options=dict(data.get("options") or {}),
            start_after=float(data.get("start_after") or 0.0),
            error=str(data.get("error") or ""),
            created=float(data.get("created") or time.time()),
            updated=float(data.get("updated") or time.time()),
            finished=float(data.get("finished") or 0.0),
            bytes_total=int(data.get("bytes_total") or 0),
            bytes_done=int(data.get("bytes_done") or 0),
            verified=int(data.get("verified") or 0),
            file_count=int(data.get("file_count") or data.get("files") or 0),
            root=str(data.get("root") or ""),
            speed_down=float(data.get("speed_down") or 0.0),
            speed_up=float(data.get("speed_up") or 0.0),
            verifying=bool(data.get("verifying", False)),
            note=str(data.get("note") or ""),
            errors=[tuple(item) for item in (data.get("errors") or [])],
            backend=str(data.get("backend") or "arkload"),
            external_id=str(data.get("external_id") or ""),
        )


class TaskStore:
    """The queue, persisted as one JSON file next to the manifests."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else settings_store.data_dir() / "queue.json"
        self._tasks: dict[str, Task] = {}

    # -- persistence ----------------------------------------------------------
    def load(self) -> list[Task]:
        """Read the queue; a broken file is ignored instead of stopping the application."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        tasks = []
        for raw in (data.get("tasks") or []):
            if isinstance(raw, dict) and (raw.get("job_id") or raw.get("id")):
                tasks.append(Task.from_dict(raw))
        self._tasks = {task.job_id: task for task in tasks}
        return tasks

    def save(self) -> bool:
        """Write the queue atomically, so a crash never leaves a half written file."""
        payload = {"version": 1, "tasks": [task.to_dict() for task in self._tasks.values()]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            return True
        except OSError:
            return False

    # -- the queue ------------------------------------------------------------
    def add(self, task: Task) -> Task:
        self._tasks[task.job_id] = task
        self.save()
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def remove(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            self.save()
            return True
        return False

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    def by_state(self, state: TaskState) -> list[Task]:
        return [task for task in self._tasks.values() if task.state == state]

    def unfinished(self) -> list[Task]:
        return [task for task in self._tasks.values() if task.state.unfinished]

    def update(self, task: Task) -> Task:
        task.updated = time.time()
        self._tasks[task.job_id] = task
        self.save()
        return task

    def __len__(self) -> int:
        return len(self._tasks)

    def __iter__(self) -> Iterable[Task]:
        return iter(self._tasks.values())
