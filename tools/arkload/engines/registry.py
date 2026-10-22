"""The engines of ArkLoad and the rule that decides which one runs a download.

The order is the one of the references:

1. **ArkLoad** runs the links of the hosts it knows itself (MediaFire and the add-ons): it
   verifies every file against the hash the service publishes,
2. **JDownloader 2** runs the hosters and the websites: it knows the plugins, it extracts the
   link of a page and it expands a folder into its files,
3. **aria2** runs the direct links and the simple protocols: it is small, it starts at once
   and it does the plain downloads well.

When an engine reports that it does not know a url, the next one may try it.  A download that
failed because of a password, a permission or an expired link is never handed to another engine -
the other engine would fail the same way, and the user would see the same download twice.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from .base import DownloadEngine, DownloadRequest, EngineError, EngineStatus, ErrorKind
from .aria2 import Aria2Engine
from .jdownloader import JDownloaderEngine

log = logging.getLogger("arkload.engines")


class EngineRegistry:
    """Holds the engines of ArkLoad and decides which one runs a download."""

    def __init__(self, builtin=None, jdownloader: JDownloaderEngine | None = None,
                 aria2: Aria2Engine | None = None):
        self.builtin = builtin
        self.jdownloader = jdownloader if jdownloader is not None else JDownloaderEngine()
        self.aria2 = aria2 if aria2 is not None else Aria2Engine()
        #: the engine that took a task, so the controls find their way back
        self._owners: dict[str, DownloadEngine] = {}

    # -- the engines ----------------------------------------------------------
    @property
    def engines(self) -> list[DownloadEngine]:
        """The engines of ArkLoad, in the order of the routing."""
        found = []
        if self.builtin is not None:
            found.append(self.builtin)
        found.append(self.jdownloader)
        found.append(self.aria2)
        return found

    #: the key every engine is known by in the queue
    KEYS = {"ArkLoad": "arkload", "JDownloader 2": "jdownloader", "aria2": "aria2"}

    @classmethod
    def key_of(cls, engine: DownloadEngine | None) -> str:
        """The key of an engine in the queue ("arkload", "jdownloader", "aria2")."""
        if engine is None:
            return ""
        return cls.KEYS.get(engine.name, engine.name.lower())

    def engine_for(self, handle: str) -> DownloadEngine | None:
        """The engine that owns a task."""
        return self._owners.get(handle)

    def owner_of(self, job_id: str) -> DownloadEngine | None:
        """The engine that runs a task of the queue."""
        return self._owners.get(job_id)

    # -- the routing ----------------------------------------------------------
    def select(self, url: str) -> DownloadEngine | None:
        """The engine that should run this url.

        The first engine that can handle the url wins; the built-in engine of ArkLoad is asked
        first, because it is the one that verifies the files.
        """
        for engine in self.engines:
            try:
                if engine.can_handle(url):
                    return engine
            except Exception:  # noqa: BLE001 - a broken engine must not stop the routing
                log.warning("the engine %s could not be asked", engine.name)
        return None

    def add(self, url: str, destination: str, name: str = "",
            options: dict | None = None, record=None) -> tuple[str, DownloadEngine]:
        """Hand a download to the right engine.

        When the engine reports that it does not know the url, the next one is tried - that is
        the fallback of the references.  A download that failed for another reason is not tried
        anywhere else.
        """
        options = options or {}
        tried: list[str] = []
        candidates = self._candidates(url)
        for engine in candidates:
            tried.append(engine.name)
            request = DownloadRequest(url=url, destination=destination, name=name,
                                      options=options, record=record)
            try:
                handle = engine.add(request)
            except EngineError as e:
                if ErrorKind.is_retryable(e.kind):
                    log.info("%s does not know the url, the next engine tries it", engine.name)
                    continue
                raise
            self._owners[handle] = engine
            self._owners.setdefault(self._key(engine, handle), engine)
            return handle, engine
        raise EngineError(
            f"no engine of ArkLoad can download this link (tried: {', '.join(tried)})",
            ErrorKind.UNSUPPORTED)

    def _candidates(self, url: str) -> list[DownloadEngine]:
        """The engines that may try this url, the best one first."""
        chosen = self.select(url)
        ordered = []
        if chosen is not None:
            ordered.append(chosen)
        for engine in self.engines:
            if engine is chosen:
                continue
            try:
                # an engine that does not know the protocol is not worth a try
                if engine.can_handle(url):
                    ordered.append(engine)
            except Exception:  # noqa: BLE001 - a broken engine must not stop the routing
                continue
        return ordered

    @staticmethod
    def _key(engine: DownloadEngine, handle: str) -> str:
        """The key under which the engine of a task is remembered."""
        return f"{engine.name}:{handle}"

    # -- the tasks ------------------------------------------------------------
    def status(self, handle: str) -> EngineStatus | None:
        engine = self._owners.get(handle)
        if engine is None:
            return None
        return engine.status(handle)

    def pause(self, handle: str) -> bool:
        engine = self._owners.get(handle)
        return bool(engine and engine.pause(handle))

    def resume(self, handle: str) -> bool:
        engine = self._owners.get(handle)
        return bool(engine and engine.resume(handle))

    def cancel(self, handle: str) -> bool:
        engine = self._owners.get(handle)
        return bool(engine and engine.cancel(handle))

    def retry(self, handle: str) -> bool:
        engine = self._owners.get(handle)
        return bool(engine and engine.retry(handle))

    # -- the life cycle -------------------------------------------------------
    def start_engines(self) -> None:
        """Start the engines that are needed, in the background.

        The JDownloader engine needs a JVM and takes a while to come up, so it is started in a
        thread; aria2 is started when the first link needs it.
        """
        try:
            self.jdownloader.start_in_background()
        except Exception:  # noqa: BLE001 - the engine is optional
            log.warning("the JDownloader engine could not be started")

    def stop_engines(self) -> None:
        """Stop the engines ArkLoad started."""
        for engine in self.engines:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001 - the process has to go
                log.warning("the engine %s could not be stopped", engine.name)

    def describe(self) -> list[dict]:
        """What the engines of ArkLoad can do (for the preferences and the reports)."""
        rows = []
        for engine in self.engines:
            capabilities = engine.capabilities()
            rows.append({
                "name": engine.name,
                "available": engine.available(),
                "protocols": list(capabilities.protocols),
                "hosters": capabilities.hosters,
                "folders": capabilities.folders,
                "resume": capabilities.resume,
                "torrents": capabilities.torrents,
            })
        return rows


def is_direct_file(url: str) -> bool:
    """True when the url points at a file and not at a page."""
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return False
    extension = name.rsplit(".", 1)[-1].lower()
    return 1 <= len(extension) <= 5 and extension.isalnum()
