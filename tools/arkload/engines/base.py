"""The common interface of the download engines.

ArkLoad drives three engines:

* **ArkLoad** - its own downloader, the one that verifies a MediaFire file against the hash the
  service publishes,
* **JDownloader 2** - the engine with the hosters, the link collectors and the plugins,
* **aria2** - the small, fast engine for the direct links.

They do not have the same abilities, so the interface says what an engine *can* do
(:class:`Capabilities`) and leaves the rest to the engine.  The window only ever talks to this
interface, so a new engine does not change the application.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ErrorKind:
    """Why a download failed - the routing needs to know."""

    UNSUPPORTED = "unsupported"    # the engine does not know this url
    AUTH = "auth"                  # a password, a cookie or an account is missing
    EXPIRED = "expired"            # the link is not valid any more
    NETWORK = "network"            # no connection, a timeout, a reset
    SERVER = "server"              # the server answered with an error
    SPACE = "space"                # the disk is full
    CRASH = "crash"                # the engine died
    UNKNOWN = "unknown"

    #: the kinds another engine may try - the others are the user's problem
    RETRYABLE = (UNSUPPORTED,)

    @classmethod
    def is_retryable(cls, kind: str) -> bool:
        """May another engine take over this download?"""
        return kind in cls.RETRYABLE


class EngineError(RuntimeError):
    """Something went wrong in an engine."""

    def __init__(self, message: str, kind: str = ErrorKind.UNKNOWN):
        super().__init__(message)
        self.kind = kind


@dataclass
class Capabilities:
    """What an engine can do."""

    #: the protocols it speaks ("http", "https", "ftp", "magnet", "torrent", ...)
    protocols: tuple = ("http", "https")
    #: can it continue an interrupted download?
    resume: bool = True
    #: can it fetch the parts of a file at the same time?
    multi_connection: bool = False
    #: can it expand a folder into its files?
    folders: bool = False
    #: does it know the hosters (does it extract the link of a website)?
    hosters: bool = False
    #: does it handle torrents and magnets?
    torrents: bool = False
    #: does it need an account, a password or a cookie?
    authentication: bool = False

    def supports(self, url: str) -> bool:
        """True when the protocol of the url is one the engine speaks."""
        lowered = (url or "").lower()
        if "://" in lowered:
            scheme = lowered.split("://", 1)[0]
        elif ":" in lowered:
            scheme = lowered.split(":", 1)[0]
        else:
            scheme = "http"
        return scheme in self.protocols


@dataclass
class DownloadRequest:
    """A download the user asked for.

    ``record`` is the task of the queue the download belongs to.  It is opaque for the engines
    that do not need it, but ArkLoad's own engine uses it to attach the session to the row the
    user sees.
    """

    url: str
    destination: str
    name: str = ""
    options: dict = field(default_factory=dict)
    record: object = None


@dataclass
class EngineFile:
    """One file of a download, the same shape as the file records of the manifests.

    The window, the table and the details card do not have to know which engine produced the
    file - they always get objects with these fields.
    """

    path: str = ""
    size: int = 0
    bytes: int = 0
    status: str = "pending"
    error: str = ""
    expected_sha256: str = ""
    actual_sha256: str = ""
    verified_against_source: bool = False
    attempts: int = 0

    @property
    def complete(self) -> bool:
        return self.status == "complete"


@dataclass
class EngineStatus:
    """What an engine knows about one of its downloads."""

    state: str = "queued"      # queued | running | paused | complete | failed | cancelled
    bytes_done: int = 0
    bytes_total: int = 0
    files: list = field(default_factory=list)
    error: str = ""
    error_kind: str = ""
    message: str = ""

    @property
    def finished(self) -> bool:
        return self.state in ("complete", "failed", "cancelled")

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running", "paused")


class DownloadEngine(ABC):
    """The operations every engine of ArkLoad offers."""

    #: the name of the engine, the way the user sees it
    name: str = ""

    # -- the life cycle -------------------------------------------------------
    @abstractmethod
    def available(self) -> bool:
        """True when the engine can be used right now."""

    @abstractmethod
    def capabilities(self) -> Capabilities:
        """What the engine can do."""

    def start(self) -> bool:
        """Start the engine (or the process behind it)."""
        return self.available()

    def stop(self) -> None:
        """Stop the engine and release its resources."""

    # -- the downloads --------------------------------------------------------
    @abstractmethod
    def add(self, request: DownloadRequest) -> str:
        """Hand a download to the engine and return its handle."""

    @abstractmethod
    def status(self, handle: str) -> EngineStatus | None:
        """What the engine knows about the download (None when it does not know it)."""

    @abstractmethod
    def pause(self, handle: str) -> bool:
        """Pause a download; the bytes that are there are kept."""

    @abstractmethod
    def resume(self, handle: str) -> bool:
        """Continue a paused download."""

    @abstractmethod
    def cancel(self, handle: str) -> bool:
        """Stop a download; the files that are there are kept."""

    def retry(self, handle: str) -> bool:
        """Try the download again."""
        return False

    # -- the routing ----------------------------------------------------------
    def can_handle(self, url: str) -> bool:
        """True when this engine is the right one for the url.

        The default is the protocol: an engine that speaks the protocol of the url may try it.
        """
        return self.capabilities().supports(url)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<{type(self).__name__} {self.name}>"
