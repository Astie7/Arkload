"""ArkLoad's own engine: the MediaFire downloader.

It is the engine that verifies a file against the hash MediaFire publishes, so the links of
the hosts ArkLoad knows itself never leave ArkLoad.

The downloader itself is the working code of ``tools/mediafire_session.py``; this class is the
adapter that gives it the same interface as the other engines, so the routing does not have to
know which engine runs a download.
"""
from __future__ import annotations

from .base import (Capabilities, DownloadEngine, DownloadRequest, EngineFile, EngineStatus,
                    ErrorKind)


class BuiltinEngine(DownloadEngine):
    """The downloader of ArkLoad, behind the common engine interface.

    The driver is the queue of the application: it owns the sessions, the manifests and the
    events of the MediaFire downloads.  This adapter only translates between the two.
    """

    name = "ArkLoad"

    def __init__(self, driver):
        self.driver = driver

    # -- the life cycle -------------------------------------------------------
    def available(self) -> bool:
        """ArkLoad's own engine is always there."""
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities(
            protocols=("http", "https"),
            resume=True,
            multi_connection=True,
            folders=True,
            hosters=False,
            torrents=False,
            authentication=True,
        )

    # -- the downloads --------------------------------------------------------
    def add(self, request: DownloadRequest) -> str:
        """Start a download with the downloader of ArkLoad."""
        return self.driver.start_builtin(request)

    def status(self, handle: str) -> EngineStatus | None:
        """What ArkLoad knows about the download."""
        return self.driver.status_builtin(handle)

    def pause(self, handle: str) -> bool:
        return bool(self.driver.pause_builtin(handle))

    def resume(self, handle: str) -> bool:
        return bool(self.driver.resume_builtin(handle))

    def cancel(self, handle: str) -> bool:
        return bool(self.driver.cancel_builtin(handle))

    def retry(self, handle: str) -> bool:
        return bool(self.driver.retry_builtin(handle))

    # -- the routing ----------------------------------------------------------
    def can_handle(self, url: str) -> bool:
        """The hosts ArkLoad knows itself: MediaFire and the add-ons."""
        try:
            return self.driver.hosts.plugin_for(url) is not None
        except Exception:  # noqa: BLE001 - a broken add-on must not stop the routing
            return False
