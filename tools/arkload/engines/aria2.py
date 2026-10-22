"""aria2 as a download engine for ArkLoad.

aria2 is a small, fast command line downloader that speaks HTTP, HTTPS, FTP and a few more.
ArkLoad drives it through its RPC interface (JSON-RPC over a local socket), which is the
supported way to control it from another program.

It is the second engine of ArkLoad: the direct links and the simple protocols go to it, while
the hosters and the websites that need a plugin go to JDownloader.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from .base import (Capabilities, DownloadEngine, DownloadRequest, EngineError, EngineFile,
                    EngineStatus, ErrorKind)
from . import resources

#: the port the RPC interface listens on (the default of aria2)
DEFAULT_PORT = 6800

#: the secret the RPC interface asks for - it is generated for every run
SECRET_LENGTH = 16


class Aria2RpcError(EngineError):
    """aria2 answered with an error."""


class Aria2Client:
    """The JSON-RPC client of aria2.

    aria2 speaks JSON-RPC over a TCP socket: every call is a JSON object with a method, the
    parameters and an id, and the answer comes back with the same id.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 secret: str = "", timeout: float = 20.0):
        self.host = host
        self.port = port
        self.secret = secret
        self.timeout = timeout
        self._id = 0
        self._lock = threading.Lock()

    def call(self, method: str, *params):
        """Call a method of aria2 and return its result."""
        with self._lock:
            self._id += 1
            request_id = self._id
        payload = {"jsonrpc": "2.0", "id": str(request_id), "method": method}
        if params:
            payload["params"] = list(params)
        if self.secret:
            payload["params"] = ["token:" + self.secret] + list(params)
        body = json.dumps(payload).encode("utf-8")
        request = (f"POST /jsonrpc HTTP/1.1\r\n"
                   f"Host: {self.host}:{self.port}\r\n"
                   "Content-Type: application/json\r\n"
                   f"Content-Length: {len(body)}\r\n"
                   "Connection: close\r\n\r\n").encode("ascii") + body
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        raw = b"".join(chunks)
        _, _, text = raw.partition(b"\r\n\r\n")
        if not text:
            raise Aria2RpcError("aria2 did not answer", ErrorKind.NETWORK)
        try:
            answer = json.loads(text.decode("utf-8", "replace"))
        except ValueError as e:
            raise Aria2RpcError(f"aria2 answered something strange: {e}", ErrorKind.NETWORK) from e
        if "error" in answer:
            error = answer["error"]
            message = str(error.get("message", error))
            raise Aria2RpcError(message, _classify(message))
        return answer.get("result")

    def alive(self) -> bool:
        """True when the RPC interface answers."""
        try:
            self.call("aria2.getVersion")
            return True
        except (OSError, Aria2RpcError):
            return False


def _classify(message: str) -> str:
    """What kind of error aria2 means."""
    lowered = message.lower()
    if "not supported" in lowered or "unsupported" in lowered or "no uri" in lowered:
        return ErrorKind.UNSUPPORTED
    if "authorization" in lowered or "401" in lowered or "403" in lowered \
            or "forbidden" in lowered or "credential" in lowered:
        return ErrorKind.AUTH
    if "not found" in lowered or "404" in lowered or "gone" in lowered:
        return ErrorKind.EXPIRED
    if "timeout" in lowered or "network" in lowered or "connection" in lowered \
            or "resolve" in lowered or "unreachable" in lowered:
        return ErrorKind.NETWORK
    if "no space" in lowered or "disk" in lowered:
        return ErrorKind.SPACE
    return ErrorKind.UNKNOWN


class Aria2Engine(DownloadEngine):
    """aria2 behind the common engine interface."""

    name = "aria2"

    def __init__(self, port: int = DEFAULT_PORT, executable: Path | None = None):
        self.port = port
        self.executable = executable or resources.aria2_exe()
        self.process: subprocess.Popen | None = None
        self.secret = ""
        self.client: Aria2Client | None = None
        self._handles: dict[str, str] = {}     # the id of ArkLoad -> the gid of aria2
        self._titles: dict[str, str] = {}
        self._lock = threading.Lock()

    # -- the life cycle -------------------------------------------------------
    def available(self) -> bool:
        """True when the engine is there and its RPC interface answers."""
        if self.executable is None or not Path(self.executable).is_file():
            return False
        if self.client is not None and self.client.alive():
            return True
        return False

    def capabilities(self) -> Capabilities:
        return Capabilities(
            protocols=("http", "https", "ftp", "ftps", "sftp"),
            resume=True,
            multi_connection=True,
            folders=False,
            hosters=False,
            torrents=False,
            authentication=True,
        )

    def start(self) -> bool:
        """Start aria2 with its RPC interface and wait for it."""
        if self.available():
            return True
        if self.executable is None or not Path(self.executable).is_file():
            raise EngineError("aria2 is not installed with ArkLoad", ErrorKind.UNSUPPORTED)
        self.secret = os.urandom(SECRET_LENGTH).hex()
        state = resources.engine_state_dir(self.name)
        command = [
            str(self.executable),
            "--enable-rpc=true",
            f"--rpc-listen-port={self.port}",
            "--rpc-listen-all=false",
            "--rpc-allow-origin-all=false",
            "--daemon=false",
            "--continue=true",
            "--file-allocation=none",
            f"--rpc-secret={self.secret}",
            f"--dir={state}",
            "--log-level=warn",
            "--console-log-level=warn",
            "--summary-interval=0",
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(command, cwd=str(state), stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            raise EngineError(f"aria2 could not be started ({e})", ErrorKind.CRASH) from e
        self.client = Aria2Client("127.0.0.1", self.port, self.secret)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise EngineError("aria2 stopped before it was ready", ErrorKind.CRASH)
            if self.client.alive():
                return True
            time.sleep(0.5)
        raise EngineError("aria2 did not answer on its RPC interface", ErrorKind.CRASH)

    def stop(self) -> None:
        """Stop aria2."""
        client, self.client = self.client, None
        if client is not None:
            try:
                client.call("aria2.forceShutdown")
            except (OSError, Aria2RpcError):
                pass
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=10)
            except Exception:  # noqa: BLE001 - the process has to go
                try:
                    process.kill()
                except Exception:  # noqa: BLE001
                    pass

    # -- the downloads --------------------------------------------------------
    def add(self, request: DownloadRequest) -> str:
        """Give a link to aria2."""
        if self.client is None:
            self.start()
        options = {
            "dir": request.destination,
            "continue": "true",
            "max-connection-per-server": str(int(request.options.get("connections", 4) or 4)),
            "split": str(int(request.options.get("connections", 4) or 4)),
        }
        if request.options.get("rate_limit"):
            options["max-download-limit"] = str(int(request.options["rate_limit"]))
        if request.options.get("user_agent"):
            options["user-agent"] = str(request.options["user_agent"])
        if request.options.get("header"):
            options["header"] = list(request.options["header"])
        if request.options.get("referer"):
            options["referer"] = str(request.options["referer"])
        try:
            gid = self.client.call("aria2.addUri", [request.url], options)
        except Aria2RpcError as e:
            raise EngineError(str(e), e.kind) from e
        handle = f"aria2:{gid}"
        with self._lock:
            self._handles[handle] = gid
            self._titles[handle] = request.name or request.url
        return handle

    def _gid(self, handle: str) -> str:
        with self._lock:
            return self._handles.get(handle, "")

    def status(self, handle: str) -> EngineStatus | None:
        """What aria2 knows about the download."""
        gid = self._gid(handle)
        if not gid or self.client is None:
            return None
        try:
            info = self.client.call("aria2.tellStatus", gid,
                                    ["totalLength", "completedLength", "status", "errorMessage",
                                     "files", "connections", "dir"])
        except Aria2RpcError as e:
            if "not found" in str(e).lower():
                return EngineStatus(state="failed", error=str(e), error_kind=ErrorKind.EXPIRED)
            return EngineStatus(state="failed", error=str(e), error_kind=e.kind)
        if not info:
            return None
        state = {
            "active": "running",
            "waiting": "queued",
            "paused": "paused",
            "complete": "complete",
            "error": "failed",
            "removed": "cancelled",
        }.get(info.get("status"), "queued")
        files = []
        for entry in info.get("files") or []:
            length = int(entry.get("length") or 0)
            done = int(entry.get("completedLength") or 0)
            files.append(EngineFile(
                path=entry.get("path", ""),
                size=length,
                bytes=done,
                status="complete" if length and done == length else "downloading"))
        error = info.get("errorMessage") or ""
        return EngineStatus(
            state=state,
            bytes_done=int(info.get("completedLength") or 0),
            bytes_total=int(info.get("totalLength") or 0),
            files=files,
            error=error,
            error_kind=_classify(error) if error else "",
        )

    def pause(self, handle: str) -> bool:
        gid = self._gid(handle)
        if not gid or self.client is None:
            return False
        try:
            self.client.call("aria2.pause", gid)
            return True
        except Aria2RpcError:
            return False

    def resume(self, handle: str) -> bool:
        gid = self._gid(handle)
        if not gid or self.client is None:
            return False
        try:
            self.client.call("aria2.unpause", gid)
            return True
        except Aria2RpcError:
            return False

    def cancel(self, handle: str) -> bool:
        gid = self._gid(handle)
        if not gid or self.client is None:
            return False
        try:
            self.client.call("aria2.remove", gid)
        except Aria2RpcError:
            return False
        with self._lock:
            self._handles.pop(handle, None)
        return True

    def retry(self, handle: str) -> bool:
        """Start the download again (aria2 keeps the file, so it continues)."""
        gid = self._gid(handle)
        if not gid or self.client is None:
            return False
        try:
            self.client.call("aria2.forcePause", gid)
            self.client.call("aria2.unpause", gid)
            return True
        except Aria2RpcError:
            return False

    # -- the routing ----------------------------------------------------------
    def can_handle(self, url: str) -> bool:
        """aria2 is the right engine for a direct link."""
        if not self.capabilities().supports(url):
            return False
        lowered = url.lower()
        if lowered.startswith("magnet:") or lowered.endswith(".torrent"):
            return False
        return True
