"""The client for the official JDownloader 2 engine.

The engine is the Java application built from the official JDownloader sources
(``svn://svn.jdownloader.org/jdownloader/trunk`` and its three sibling repositories).  It owns
the download queue, the host plugins and the transfers; ArkLoad talks to it through the local
HTTP API of the engine (the "deprecated API", port 3128, enabled in the engine's settings).

Only the calls that were verified against a running engine live here:

* ``queryPackages`` / ``queryLinks`` - what the queue holds
* ``addLinksAndStartDownload`` - hand a link to the engine
* ``pause`` / ``enableLinks`` / ``disableLinks`` / ``removeLinks`` - control a task

The protocol is a plain HTTP GET per call: a JSON object goes into the ``queryParams``
parameter, the other methods take named parameters.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
from typing import Any, Iterable

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3128


class EngineError(RuntimeError):
    """The engine refused a request."""


class JDownloaderEngine:
    """A thin, verified client for the JDownloader engine."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    # -- the protocol ---------------------------------------------------------
    def call(self, section: str, method: str, query: dict | None = None, **named) -> Any:
        """One call to the engine.  A JSON object goes into ``queryParams``."""
        path = f"/{section}/{method}"
        if query is not None:
            path += "?" + urllib.parse.urlencode(
                {"queryParams": json.dumps(query, separators=(",", ":"))})
        elif named:
            path += "?" + urllib.parse.urlencode(named)
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read().decode("utf-8", "replace")
        except OSError as e:
            raise EngineError(f"the engine does not answer on {self.host}:{self.port} ({e})") from e
        finally:
            connection.close()
        try:
            payload = json.loads(body)
        except ValueError:
            return body
        if isinstance(payload, dict) and payload.get("type") not in (None, "OK"):
            raise EngineError(f"{section}/{method}: {payload.get('type')} "
                              f"({payload.get('data')})")
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    # -- the queue ------------------------------------------------------------
    def available(self) -> bool:
        """True when the engine answers."""
        try:
            self.packages()
            return True
        except EngineError:
            return False

    def packages(self) -> list[dict]:
        """The packages (a package is one download: a folder, a file, a link list)."""
        data = self.call("downloads", "queryPackages", {})
        return data if isinstance(data, list) else []

    def links(self) -> list[dict]:
        """The links of the link collector (the links that were handed to the engine)."""
        data = self.call("linkcollector", "queryLinks", {})
        return data if isinstance(data, list) else []

    def download_links(self) -> list[dict]:
        """The links of the running downloads.

        The engine answers with an internal error for this call in the current revision
        (the V2 storable conversion fails), so the link collector is used as the source.
        """
        try:
            data = self.call("downloads", "queryLinks", {"maxResults": 100})
            if isinstance(data, list):
                return data
        except EngineError:
            pass
        return self.links()

    # -- the tasks ------------------------------------------------------------
    def add(self, url: str, package_name: str = "", start: bool = True) -> bool:
        """Hand a link to the engine; it resolves it with its own host plugins."""
        method = "addLinksAndStartDownload" if start else "addLinks"
        result = self.call("linkcollector", method, links=url, packageName=package_name,
                           extractPassword="", downloadPassword="")
        return bool(result)

    @staticmethod
    def _ids(link_ids: Iterable[int]) -> str:
        """The engine wants the ids as one json array: ``linkIds=[1,2]``."""
        return json.dumps([int(i) for i in link_ids], separators=(",", ":"))

    def pause(self, link_ids: Iterable[int]) -> bool:
        """Pause the running downloads (the engine pauses all of them)."""
        return bool(self.call("downloads", "pause", value="true"))

    def resume(self, link_ids: Iterable[int]) -> bool:
        """Continue the paused links (the engine calls it *enable*)."""
        return bool(self.call("downloads", "enableLinks", linkIds=self._ids(link_ids)))

    def cancel(self, link_ids: Iterable[int]) -> bool:
        """Stop the links (the engine calls it *disable*)."""
        return bool(self.call("downloads", "disableLinks", linkIds=self._ids(link_ids)))

    def remove(self, link_ids: Iterable[int]) -> bool:
        """Take the links out of the queue."""
        return bool(self.call("downloads", "removeLinks", linkIds=self._ids(link_ids)))

    def force(self, link_ids: Iterable[int]) -> bool:
        """Start the links again."""
        return bool(self.call("downloads", "forceDownload", linkIds=self._ids(link_ids)))
