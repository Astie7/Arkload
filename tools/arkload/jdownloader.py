"""The JDownloader 2 engine that runs behind ArkLoad.

ArkLoad is the program the user sees.  The JDownloader 2 engine is what actually moves the bytes
for everything that is not a MediaFire link, because it knows thousands of hosts.

Three things make this work without the user ever noticing JDownloader:

* **the engine runs headless** (``-Djava.awt.headless=true``), so no JDownloader window is
  opened - ArkLoad is the only interface,
* **the engine is reached through its local API** (port 3128 by default, bound to localhost),
  which is the supported way to drive it from another program,
* **ArkLoad keeps the queue**: the engine is the transport, ``queue.json`` is the list of the
  downloads, so the application shows exactly one list of downloads.

The MediaFire downloads keep running on ArkLoad's own engine, which verifies every file against
MediaFire's published SHA-256 - that behaviour is not replaced by the JDownloader plugin.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .engine import EngineError, JDownloaderEngine

#: the jar the engine is started from
ENGINE_JAR = "JDownloader.jar"

#: an environment variable that points at the engine (the jar or its folder)
ENGINE_ENV = "ARKLOAD_JDOWNLOADER"

#: the file the engine reads its API settings from
API_CONFIG = "org.jdownloader.api.RemoteAPIConfig.json"

#: the API settings ArkLoad needs: the local API on, bound to this machine only
API_SETTINGS = {
    "port": 3128,
    "externinterfaceenabled": True,
    "deprecatedapienabled": True,
    "jdanywhereapienabled": False,
    "externinterfacelocalhostonly": True,
    "enabled": True,
}

#: how long the crawler may take to resolve a link before ArkLoad gives up on it
COLLECT_TIMEOUT = 45.0

#: how long the link set has to stay unchanged before the transfer is started
QUIET_PERIOD = 2.0


def engine_candidates() -> list[Path]:
    """The places the engine can live, most specific first.

    The engine is not part of the ArkLoad installation (it is GPLv3+/AGPL), so the user points
    ArkLoad at it, or it is dropped into the application data folder.
    """
    candidates: list[Path] = []
    override = os.environ.get(ENGINE_ENV)
    if override:
        candidates.append(Path(override))
    here = Path(__file__).resolve().parent
    candidates.append(here.parent.parent / "packaging" / "jdownloader" / "dist" / ENGINE_JAR)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        candidates.append(Path(base) / "ArkLoad" / "engine" / ENGINE_JAR)
    candidates.append(Path.home() / "ArkLoad" / "engine" / ENGINE_JAR)
    return candidates


def find_engine(explicit: str | Path | None = None) -> Path | None:
    """Find the engine jar.

    ``explicit`` is what the user typed in the preferences; a folder is accepted as well and the
    jar inside it is used.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_dir():
            path = path / ENGINE_JAR
        return path if path.is_file() else None
    for candidate in engine_candidates():
        if candidate.is_dir():
            candidate = candidate / ENGINE_JAR
        if candidate.is_file():
            return candidate
    return None


def find_java() -> str:
    """The java command: the one of the JRE of the engine, or the one on the path."""
    override = os.environ.get("ARKLOAD_JAVA")
    if override and Path(override).exists():
        return override
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    return shutil.which("java") or "java"


#: the characters a folder name may not hold on Windows
BAD_CHARACTERS = '<>:"/\\|?*'


def package_name_for(title: str) -> str:
    """The name of the folder the engine creates for a download.

    The engine puts every download into its own folder, named after the package.  A file name
    loses its extension there (``movie.mkv`` becomes the folder ``movie``), and the characters
    a folder name may not hold are removed.
    """
    name = (title or "").strip() or "download"
    stem, dot, extension = name.rpartition(".")
    if dot and 0 < len(extension) <= 5 and extension.isalnum():
        name = stem or name
    cleaned = "".join(character for character in name if character not in BAD_CHARACTERS)
    return cleaned.strip(" .") or "download"


class EngineProcess:
    """The engine process.

    It is started without a window (headless).  It is only stopped again when ArkLoad started it
    itself - an engine the user started by hand is left alone.
    """

    def __init__(self, jar: Path, port: int = 3128, java: str | None = None,
                 startup_timeout: float = 150.0):
        self.jar = Path(jar)
        self.port = int(port)
        self.java = java or find_java()
        self.startup_timeout = float(startup_timeout)
        self.process: subprocess.Popen | None = None

    # -- the layout -----------------------------------------------------------
    @property
    def dist(self) -> Path:
        """The folder the engine keeps its configuration in."""
        return self.jar.parent

    @property
    def cfg(self) -> Path:
        return self.dist / "cfg"

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    # -- the lifecycle --------------------------------------------------------
    def prepare(self) -> None:
        """Write the API settings, so the engine offers its local API.

        The engine rewrites this file when it exits, so it is written again before every start.
        """
        self.cfg.mkdir(parents=True, exist_ok=True)
        settings = dict(API_SETTINGS)
        settings["port"] = self.port
        path = self.cfg / API_CONFIG
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing.update(settings)
                settings = existing
        except (OSError, ValueError):
            pass
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    def start(self, wait: bool = True) -> bool:
        """Start the engine (headless) and wait until its API answers."""
        if self.running:
            return True
        if not self.jar.is_file():
            raise EngineError(f"the JDownloader engine is not there: {self.jar}")
        self.prepare()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                [self.java, "-Djava.awt.headless=true", "-jar", str(self.jar)],
                cwd=str(self.dist), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags)
        except OSError as e:
            raise EngineError(f"the JDownloader engine could not be started ({e})") from e
        if not wait:
            return True
        client = JDownloaderEngine("127.0.0.1", self.port, timeout=10)
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise EngineError("the JDownloader engine stopped before it was ready")
            if client.available():
                return True
            time.sleep(1.0)
        raise EngineError("the JDownloader engine did not answer on its local API")

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the engine (only the one ArkLoad started)."""
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=timeout)
        except Exception:  # noqa: BLE001 - the process has to go, whatever it says
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass


@dataclass
class EngineFile:
    """One file of a download the JDownloader engine runs.

    It carries the same fields as the file records of the MediaFire manifests, so the table, the
    details card and the reports do not have to know which engine produced them.
    """

    path: str
    size: int = 0
    bytes: int = 0
    status: str = "pending"
    error: str = ""
    expected_sha256: str = ""
    verified_against_source: bool = False


@dataclass
class EngineTask:
    """What ArkLoad remembers about a download the JDownloader engine runs."""

    job_id: str
    title: str = ""
    destination: str = ""
    package_name: str = ""
    link_ids: list = field(default_factory=list)
    package_uuid: int | None = None
    files: list = field(default_factory=list)     # [{"path": name, "size": bytes}]
    started: bool = False
    failed: bool = False
    added_at: float = field(default_factory=time.time)
    collected_at: float = 0.0
    last_change: float = field(default_factory=time.time)

    @property
    def folder(self) -> Path:
        """The folder the engine downloads into."""
        return Path(self.destination) / (self.package_name or self.title)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "destination": self.destination,
            "package_name": self.package_name,
            "link_ids": list(self.link_ids),
            "package_uuid": self.package_uuid,
            "files": list(self.files),
            "started": self.started,
            "failed": self.failed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EngineTask":
        return cls(
            job_id=str(data.get("job_id") or ""),
            title=str(data.get("title") or ""),
            destination=str(data.get("destination") or ""),
            package_name=str(data.get("package_name") or ""),
            link_ids=[int(i) for i in (data.get("link_ids") or [])],
            package_uuid=data.get("package_uuid"),
            files=list(data.get("files") or []),
            started=bool(data.get("started")),
            failed=bool(data.get("failed")),
        )


class JDownloaderBackend:
    """The JDownloader engine as a backend of the ArkLoad queue.

    It offers the same operations as ArkLoad's own engine - add, pause, resume, cancel, remove -
    and it reports the progress of the downloads it runs.
    """

    name = "JDownloader"

    def __init__(self, client: JDownloaderEngine, process: EngineProcess | None = None):
        self.client = client
        self.process = process
        self.tasks: dict[str, EngineTask] = {}
        #: the links that wait for the engine to come up
        self.pending: dict[str, tuple] = {}
        self._starting = False

    # -- the engine -----------------------------------------------------------
    def available(self) -> bool:
        return self.client.available()

    def ensure_running(self) -> None:
        """Start the engine when it is not answering yet (this waits for it)."""
        if self.client.available():
            return
        if self.process is None:
            raise EngineError(
                "the JDownloader engine is not installed - set its location in the preferences")
        self.process.start()
        self.client.port = self.process.port

    def start_in_background(self) -> bool:
        """Start the engine in a thread, so the window stays alive while the JVM starts.

        Returns True when the engine was started (or is already running).
        """
        if self.client.available():
            return True
        if self.process is None or self._starting:
            return False
        self._starting = True

        def run() -> None:
            try:
                self.process.start()
            except EngineError:
                pass
            finally:
                self._starting = False

        thread = threading.Thread(target=run, name="arkload-jdownloader", daemon=True)
        thread.start()
        return True

    def restore(self, record) -> None:
        """Take a task of the queue back after a restart of ArkLoad."""
        if record.job_id in self.tasks:
            return
        task = EngineTask(job_id=record.job_id, title=record.title,
                           destination=record.destination,
                           package_name=package_name_for(record.title))
        if record.external_id:
            try:
                data = json.loads(record.external_id)
            except ValueError:
                data = {}
            if isinstance(data, dict):
                task.link_ids = [int(i) for i in (data.get("link_ids") or [])]
                task.package_uuid = data.get("package_uuid")
                task.files = list(data.get("files") or [])
                task.started = bool(data.get("started"))
        self.tasks[record.job_id] = task
        record.root = str(task.folder)

    def external_id(self, task: EngineTask) -> str:
        """The state of a task, as it is stored in the ArkLoad queue."""
        return json.dumps({"link_ids": list(task.link_ids),
                           "package_uuid": task.package_uuid,
                           "package_name": task.package_name,
                           "files": list(task.files),
                           "started": task.started})

    # -- the tasks ------------------------------------------------------------
    def add(self, record, options=None) -> bool:
        """Hand a link to the engine.

        The link only goes into the engine's link collector first: ArkLoad reads the files and
        their sizes from there and starts the transfer afterwards, so it knows the ids it needs
        to pause, resume and cancel the download.

        When the engine is not up yet, the link waits for it - the window must not freeze while
        the JVM starts.
        """
        destination = str(record.destination or getattr(options, "destination", "") or "")
        title = record.title or "download"
        task = EngineTask(job_id=record.job_id, title=title, destination=destination,
                          package_name=package_name_for(title))
        self.tasks[record.job_id] = task
        record.root = str(task.folder)
        if not self.client.available():
            self.pending[record.job_id] = record
            self.start_in_background()
            return True
        return self._collect(record, task)

    def _collect(self, record, task: EngineTask) -> bool:
        """Put the link into the engine's link collector."""
        try:
            ok = self.client.collect(record.url, task.package_name, task.destination)
        except EngineError as e:
            task.failed = True
            raise EngineError(str(e)) from e
        if ok:
            task.collected_at = time.time()
        else:
            task.failed = True
        return ok

    def poll(self) -> dict:
        """Read the state of the downloads the engine runs.

        Returns ``job_id -> {"state", "bytes_done", "bytes_total", "files", "note"}``.
        """
        if not self.tasks:
            return {}
        self._drain_pending()
        packages = self._packages()
        collector_packages = self._collector_packages()
        collector_links = self._collector_links()
        result: dict = {}
        for job_id, task in list(self.tasks.items()):
            update = self._poll_task(task, packages, collector_packages, collector_links)
            if update:
                result[job_id] = update
        return result

    def _drain_pending(self) -> None:
        """Give the links that waited for the engine to the engine."""
        if not self.pending:
            return
        if not self.client.available():
            return
        for job_id, record in list(self.pending.items()):
            task = self.tasks.get(job_id)
            if task is None:
                self.pending.pop(job_id, None)
                continue
            try:
                self._collect(record, task)
            except EngineError:
                continue
            self.pending.pop(job_id, None)

    def _packages(self) -> dict:
        """The packages of the download list: name -> {uuid, size, child_count}."""
        packages = {}
        for package in self.client.packages():
            if not isinstance(package, dict):
                continue
            info = package.get("infoMap") or {}
            packages[str(package.get("name") or "")] = {
                "uuid": package.get("uuid"),
                "size": int(info.get("size") or 0),
                "child_count": int(info.get("childCount") or 0),
            }
        return packages

    def _collector_packages(self) -> dict:
        """The packages of the link collector: name -> {uuid, size, child_count}."""
        packages = {}
        for package in self.client.collector_packages():
            if not isinstance(package, dict):
                continue
            info = package.get("infoMap") or {}
            packages[str(package.get("name") or "")] = {
                "uuid": package.get("uuid"),
                "size": int(info.get("size") or 0),
                "child_count": int(info.get("childCount") or 0),
            }
        return packages

    def _collector_links(self) -> list:
        """The links that are not started yet (with their sizes)."""
        try:
            return [link for link in self.client.links() if isinstance(link, dict)]
        except EngineError:
            return []

    def _poll_task(self, task: EngineTask, packages: dict, collector_packages: dict,
                   collector_links: list) -> dict | None:
        """The state of one task."""
        now = time.time()
        package = packages.get(task.package_name)
        collector_package = collector_packages.get(task.package_name)
        # the links of the task are in the collector as long as they were not started
        if collector_package is not None and task.package_uuid is None:
            task.package_uuid = collector_package["uuid"]
        links = [link for link in collector_links
                 if (link.get("infoMap") or {}).get("packageUUID") == task.package_uuid]
        if links:
            ids = [link.get("uuid") for link in links if link.get("uuid")]
            if ids != task.link_ids:
                task.link_ids = ids
                task.last_change = now
                task.files = []
            for link in links:
                info = link.get("infoMap") or {}
                name = str(link.get("name") or "")
                if name and not any(entry["path"] == name for entry in task.files):
                    task.files.append({"path": name, "size": int(info.get("size") or 0)})
        # start the transfer when the crawler is done
        if task.link_ids and not task.started and not task.failed:
            quiet = now - task.last_change
            complete = bool(collector_package and collector_package["child_count"]
                            and collector_package["child_count"] <= len(task.link_ids))
            if quiet >= QUIET_PERIOD or complete:
                if self.client.start(task.link_ids):
                    task.started = True
        elif not task.link_ids and not task.started and task.collected_at \
                and now - task.collected_at > COLLECT_TIMEOUT:
            # the crawler had long enough and did not find anything
            task.failed = True

        on_disk = self._folder_files(task.folder)
        done = sum(on_disk.values())
        total = package["size"] if package else sum(entry["size"] for entry in task.files)
        if not total:
            total = done
        note = ""
        state = "running"
        if task.failed:
            state = "failed"
            note = "the JDownloader engine could not handle this link"
        elif package and total and done >= total:
            state = "complete"
        elif task.started or package or on_disk:
            # the engine runs it (the package is in the download list, or files are arriving)
            state = "running"
        else:
            state = "queued"
        files = self._file_records(task, on_disk)
        return {
            "state": state,
            "bytes_done": done,
            "bytes_total": total,
            "files": files,
            "note": note,
        }

    @staticmethod
    def _file_records(task: EngineTask, on_disk: dict) -> list:
        """The file records of a download, in the shape the window expects."""
        records = []
        known = {entry["path"]: entry for entry in task.files}
        for name, size in sorted(on_disk.items()):
            entry = known.get(name) or {}
            records.append(EngineFile(
                path=name,
                size=int(entry.get("size") or size),
                bytes=size,
                status="complete" if size and not name.endswith(".part") else "downloading"))
        for name, entry in known.items():
            if name in on_disk:
                continue
            records.append(EngineFile(path=name, size=int(entry.get("size") or 0),
                                       status="pending"))
        return records

    @staticmethod
    def _folder_files(folder: Path) -> dict:
        """The files of a package folder: name -> size.

        The engine writes a file under its final name and keeps the part of a running transfer
        in a ``.part`` file next to it; the part counts as long as the final file is not there
        yet, so a transfer is never counted twice.
        """
        found: dict = {}
        parts: dict = {}
        try:
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if path.name.endswith(".part"):
                    parts[path.name[:-5]] = size
                else:
                    found[path.name] = size
        except OSError:
            pass
        for name, size in parts.items():
            found.setdefault(name, size)
        return found

    # -- the controls ---------------------------------------------------------
    def pause(self, record) -> bool:
        task = self.tasks.get(record.job_id)
        if task is None or not task.link_ids:
            return False
        return self.client.cancel(task.link_ids)

    def resume(self, record) -> bool:
        task = self.tasks.get(record.job_id)
        if task is None or not task.link_ids:
            return False
        return self.client.resume(task.link_ids)

    def cancel(self, record) -> bool:
        task = self.tasks.get(record.job_id)
        if task is None or not task.link_ids:
            return False
        self.client.cancel(task.link_ids)
        return True

    def remove(self, record) -> bool:
        task = self.tasks.get(record.job_id)
        if task is None:
            return False
        if task.link_ids:
            self.client.remove(task.link_ids)
        self.tasks.pop(record.job_id, None)
        return True
