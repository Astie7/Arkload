"""JDownloader 2 as a download engine for ArkLoad.

The engine is the Java application from the official JDownloader sources.  ArkLoad starts it
headless (``-Djava.awt.headless=true``), so the user never sees a JDownloader window, and drives
it through its local API (port 3128, bound to localhost) - the supported way to control it from
another program.

The engine is the one for the hosters: it knows thousands of sites, it extracts the link of a
website and it expands a folder into its files.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..engine import EngineError
from ..engine import JDownloaderEngine as JDownloaderApi
from .base import (Capabilities, DownloadEngine, DownloadRequest, EngineFile, EngineStatus,
                    ErrorKind)
from . import resources

#: the file the engine reads its API settings from
API_CONFIG = "org.jdownloader.api.RemoteAPIConfig.json"

#: the API settings ArkLoad needs: the local API on, bound to this machine only.
#: the port of the local API is called "deprecatedApiPort" in the settings of the engine.
API_SETTINGS = {
    "deprecatedapiport": 3128,
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


class EngineProcess:
    """The process of the JDownloader engine.

    It is started without a window (headless).  It is only stopped again when ArkLoad started it
    itself - an engine the user started by hand is left alone.
    """

    def __init__(self, jar: Path, port: int = 3128, java: str | None = None,
                 startup_timeout: float = 150.0):
        self.jar = Path(jar)
        self.port = int(port)
        self.java = java or resources.java_command()
        self.startup_timeout = float(startup_timeout)
        self.process: subprocess.Popen | None = None

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

    def prepare(self) -> None:
        """Write the API settings, so the engine offers its local API.

        The engine rewrites this file when it exits, so it is written again before every start.
        """
        self.cfg.mkdir(parents=True, exist_ok=True)
        settings = dict(API_SETTINGS)
        settings["deprecatedapiport"] = self.port
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
        client = JDownloaderApi("127.0.0.1", self.port, timeout=10)
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


class JDownloaderEngine(DownloadEngine):
    """The JDownloader 2 engine behind the common engine interface."""

    name = "JDownloader 2"

    def __init__(self, port: int = 3128, jar: Path | None = None):
        self.port = port
        self.jar = jar or resources.jdownloader_jar()
        self.process: EngineProcess | None = None
        self.client: JDownloaderApi | None = None
        self.tasks: dict[str, EngineTask] = {}
        self._starting = False

    # -- the life cycle -------------------------------------------------------
    def available(self) -> bool:
        """True when the engine is installed and its API answers."""
        if self.jar is None or not Path(self.jar).is_file():
            return False
        if self.client is not None and self.client.available():
            return True
        return False

    def capabilities(self) -> Capabilities:
        return Capabilities(
            protocols=("http", "https", "ftp", "magnet", "torrent"),
            resume=True,
            multi_connection=True,
            folders=True,
            hosters=True,
            torrents=True,
            authentication=True,
        )

    def ensure_running(self) -> None:
        """Start the engine when it is not answering yet.

        An engine that is already running (the user started it, or a test before) is used as it
        is - the engine holds a lock on its folder, so a second one would not even start.
        """
        if self.client is not None and self.client.available():
            return
        probe = JDownloaderApi("127.0.0.1", self.port, timeout=5)
        if probe.available():
            self.client = probe
            return
        if self.jar is None:
            raise EngineError(
                "the JDownloader engine is not installed with ArkLoad", ErrorKind.UNSUPPORTED)
        if self.process is None:
            self.process = EngineProcess(self.jar, port=self.port)
        self.process.start()
        self.client = JDownloaderApi("127.0.0.1", self.port)

    def start_in_background(self) -> bool:
        """Start the engine in a thread, so the window stays alive while the JVM starts."""
        if self.client is not None and self.client.available():
            return True
        if self.jar is None or self._starting:
            return False
        self._starting = True

        def run() -> None:
            try:
                self.ensure_running()
            except EngineError:
                pass
            finally:
                self._starting = False

        threading.Thread(target=run, name="arkload-jdownloader", daemon=True).start()
        return True

    def stop(self) -> None:
        """Stop the engine."""
        if self.process is not None:
            self.process.stop()
        self.process = None
        self.client = None

    # -- the downloads --------------------------------------------------------
    def add(self, request: DownloadRequest) -> str:
        """Hand a link to the engine.

        The link only goes into the engine's link collector first: ArkLoad reads the files and
        their sizes from there and starts the transfer afterwards, so it knows the ids it needs
        to pause, resume and cancel the download.
        """
        if self.client is None or not self.client.available():
            self.ensure_running()
        title = request.name or request.url
        task = EngineTask(job_id=request.url, title=title, destination=request.destination,
                          package_name=package_name_for(title))
        self.tasks[request.url] = task
        try:
            ok = self.client.collect(request.url, task.package_name, request.destination)
        except EngineError as e:
            task.failed = True
            raise EngineError(str(e), ErrorKind.NETWORK) from e
        if not ok:
            task.failed = True
            raise EngineError("the JDownloader engine refused the link", ErrorKind.UNKNOWN)
        return request.url

    def status(self, handle: str) -> EngineStatus | None:
        """What the engine knows about the download."""
        task = self.tasks.get(handle)
        if task is None or self.client is None:
            return None
        packages = self._packages()
        collector_packages = self._collector_packages()
        collector_links = self._collector_links()
        now = time.time()
        package = packages.get(task.package_name)
        collector_package = collector_packages.get(task.package_name)
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
        if task.link_ids and not task.started and not task.failed:
            quiet = now - task.last_change
            complete = bool(collector_package and collector_package["child_count"]
                            and collector_package["child_count"] <= len(task.link_ids))
            if quiet >= QUIET_PERIOD or complete:
                if self.client.start(task.link_ids):
                    task.started = True
        elif not task.link_ids and not task.started and task.collected_at \
                and now - task.collected_at > COLLECT_TIMEOUT:
            task.failed = True
        on_disk = self._folder_files(task.folder)
        done = sum(on_disk.values())
        total = package["size"] if package else sum(entry["size"] for entry in task.files)
        if not total:
            total = done
        state = "running"
        error = ""
        error_kind = ""
        if task.failed:
            state = "failed"
            error = "the JDownloader engine could not handle this link"
            error_kind = ErrorKind.UNSUPPORTED
        elif package and total and done >= total:
            state = "complete"
        elif not task.started:
            state = "queued"
        return EngineStatus(
            state=state,
            bytes_done=done,
            bytes_total=total,
            files=self._file_records(task, on_disk),
            error=error,
            error_kind=error_kind,
        )

    def pause(self, handle: str) -> bool:
        task = self.tasks.get(handle)
        if task is None or self.client is None or not task.link_ids:
            return False
        return self.client.cancel(task.link_ids)

    def resume(self, handle: str) -> bool:
        task = self.tasks.get(handle)
        if task is None or self.client is None or not task.link_ids:
            return False
        return self.client.resume(task.link_ids)

    def cancel(self, handle: str) -> bool:
        task = self.tasks.get(handle)
        if task is None or self.client is None or not task.link_ids:
            return False
        self.client.cancel(task.link_ids)
        return True

    def retry(self, handle: str) -> bool:
        task = self.tasks.get(handle)
        if task is None or self.client is None:
            return False
        task.failed = False
        task.started = False
        task.link_ids = []
        return self.client.collect(handle, task.package_name, task.destination)

    # -- the helpers ----------------------------------------------------------
    def _packages(self) -> dict:
        packages = {}
        for package in self.client.packages() if self.client else []:
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
        packages = {}
        for package in self.client.collector_packages() if self.client else []:
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
        if self.client is None:
            return []
        try:
            return [link for link in self.client.links() if isinstance(link, dict)]
        except EngineError:
            return []

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

    # -- the routing ----------------------------------------------------------
    def can_handle(self, url: str) -> bool:
        """JDownloader is the right engine for a website and for a hoster."""
        lowered = url.lower()
        if lowered.startswith("magnet:") or lowered.endswith(".torrent"):
            return True
        if not self.capabilities().supports(url):
            return False
        # a direct file is the job of aria2, a page is the job of the plugins
        return not _looks_like_a_file(url)


def _looks_like_a_file(url: str) -> bool:
    """True when the url points at a file and not at a page."""
    from urllib.parse import urlparse
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return False
    extension = name.rsplit(".", 1)[-1].lower()
    return 1 <= len(extension) <= 5 and extension.isalnum()
