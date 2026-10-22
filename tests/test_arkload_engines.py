"""Tests for the engines of ArkLoad: the routing, the fallback and the clients.

The engines are faked here (the real ones are tested in the live tests), so the rules can be
checked without a network and without a JVM.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.engines import (Aria2Engine, BuiltinEngine, EngineRegistry, ErrorKind,
                                   JDownloaderEngine, resources)
from tools.arkload.engines.base import Capabilities, DownloadEngine, DownloadRequest, EngineError, \
    EngineStatus
from tools.arkload.engines.aria2 import Aria2Client


# --------------------------------------------------------------------------- the fakes
class FakeEngine(DownloadEngine):
    """An engine that does what the test tells it to.

    The routing rules are the real ones, so the test checks the same decisions the application
    makes.
    """

    def __init__(self, name: str, handles=("unsupported", "auth", "network", "crash"),
                 can_handle=None, works=True):
        self.name = name
        self.handles = handles
        self._can_handle = can_handle
        self._works = works
        self.added: list[DownloadRequest] = []
        self.statuses: dict[str, EngineStatus] = {}
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities(protocols=("http", "https"))

    def add(self, request: DownloadRequest) -> str:
        self.added.append(request)
        if not self._works:
            raise EngineError("this engine does not know the link", self.handles[0])
        handle = f"{self.name}:{len(self.added)}"
        self.statuses[handle] = EngineStatus(state="running", bytes_total=100)
        return handle

    def status(self, handle: str) -> EngineStatus | None:
        return self.statuses.get(handle)

    def pause(self, handle: str) -> bool:
        self.calls.append("pause")
        return True

    def resume(self, handle: str) -> bool:
        self.calls.append("resume")
        return True

    def cancel(self, handle: str) -> bool:
        self.calls.append("cancel")
        return True

    def can_handle(self, url: str) -> bool:
        if self._can_handle is not None:
            return self._can_handle
        if self.name == "ArkLoad":
            return "mediafire.com" in url or "mfi.re" in url
        if self.name == "aria2":
            return Aria2Engine().can_handle(url)
        return JDownloaderEngine().can_handle(url)


class FakeBuiltin(FakeEngine):
    """ArkLoad's own engine."""

    def __init__(self, **kwargs):
        super().__init__("ArkLoad", **kwargs)


# --------------------------------------------------------------------------- the routing
def test_a_mediafire_link_goes_to_the_own_engine():
    registry = EngineRegistry(builtin=FakeBuiltin(), jdownloader=FakeEngine("JDownloader 2"),
                              aria2=FakeEngine("aria2"))
    engine = registry.select("https://www.mediafire.com/folder/abc/libs")
    assert registry.key_of(engine) == "arkload"


def test_a_website_goes_to_jdownloader():
    registry = EngineRegistry(builtin=FakeBuiltin(), jdownloader=FakeEngine("JDownloader 2"),
                              aria2=FakeEngine("aria2"))
    engine = registry.select("https://www.example.com/download/12345")
    assert registry.key_of(engine) == "jdownloader"


def test_a_direct_link_goes_to_aria2():
    registry = EngineRegistry(builtin=FakeBuiltin(), jdownloader=FakeEngine("JDownloader 2"),
                              aria2=FakeEngine("aria2"))
    engine = registry.select("https://www.example.com/files/pack.zip")
    assert registry.key_of(engine) == "aria2"


def test_an_ftp_link_goes_to_aria2():
    registry = EngineRegistry(builtin=FakeBuiltin(), jdownloader=FakeEngine("JDownloader 2"),
                              aria2=FakeEngine("aria2"))
    engine = registry.select("ftp://ftp.example.com/pub/file.bin")
    assert registry.key_of(engine) == "aria2"


def test_a_magnet_goes_to_jdownloader():
    registry = EngineRegistry(builtin=FakeBuiltin(), jdownloader=FakeEngine("JDownloader 2"),
                              aria2=FakeEngine("aria2"))
    engine = registry.select("magnet:?xt=urn:btih:0123456789abcdef")
    assert registry.key_of(engine) == "jdownloader"


# --------------------------------------------------------------------------- the fallback
def test_an_unsupported_link_goes_to_the_next_engine():
    """JDownloader does not know the page, aria2 may be able to get the file."""
    jdownloader = FakeEngine("JDownloader 2", handles=("unsupported",), works=False)
    aria2 = FakeEngine("aria2")
    registry = EngineRegistry(builtin=FakeBuiltin(can_handle=False), jdownloader=jdownloader,
                              aria2=aria2)
    handle, engine = registry.add("https://www.example.com/page/1", "C:/out", "page")
    assert registry.key_of(engine) == "aria2"
    assert len(jdownloader.added) == 1        # it tried
    assert len(aria2.added) == 1               # and then the next one


def test_a_link_that_needs_a_password_is_not_tried_anywhere_else():
    """A permission problem is the user's problem - the other engine would fail the same way."""
    jdownloader = FakeEngine("JDownloader 2", handles=("auth",), works=False)
    aria2 = FakeEngine("aria2")
    registry = EngineRegistry(builtin=FakeBuiltin(can_handle=False), jdownloader=jdownloader,
                              aria2=aria2)
    with pytest.raises(EngineError) as error:
        registry.add("https://www.example.com/private/1", "C:/out", "private")
    assert error.value.kind == ErrorKind.AUTH
    assert not aria2.added                      # aria2 never saw the link


def test_an_expired_link_is_not_tried_anywhere_else():
    jdownloader = FakeEngine("JDownloader 2", handles=("expired",), works=False)
    aria2 = FakeEngine("aria2")
    registry = EngineRegistry(builtin=FakeBuiltin(can_handle=False), jdownloader=jdownloader,
                              aria2=aria2)
    with pytest.raises(EngineError) as error:
        registry.add("https://www.example.com/gone/1", "C:/out", "gone")
    assert error.value.kind == ErrorKind.EXPIRED
    assert not aria2.added


def test_a_network_failure_is_not_tried_anywhere_else():
    """A network problem is not the engine's fault - a second engine would fail as well."""
    jdownloader = FakeEngine("JDownloader 2", handles=("network",), works=False)
    aria2 = FakeEngine("aria2")
    registry = EngineRegistry(builtin=FakeBuiltin(can_handle=False), jdownloader=jdownloader,
                              aria2=aria2)
    with pytest.raises(EngineError) as error:
        registry.add("https://www.example.com/file/1", "C:/out", "file")
    assert error.value.kind == ErrorKind.NETWORK
    assert not aria2.added


def test_a_download_is_only_given_to_one_engine():
    """The same url never runs twice: the second engine only tries it when the first one
    refused it."""
    jdownloader = FakeEngine("JDownloader 2", handles=("unsupported",), works=False)
    aria2 = FakeEngine("aria2")
    registry = EngineRegistry(builtin=FakeBuiltin(can_handle=False), jdownloader=jdownloader,
                              aria2=aria2)
    handle, engine = registry.add("https://www.example.com/page/1", "C:/out", "page")
    assert handle.startswith("aria2:")
    assert registry.owner_of(handle) is aria2


def test_the_controls_find_the_engine_of_the_task():
    jdownloader = FakeEngine("JDownloader 2")
    aria2 = FakeEngine("aria2")
    registry = EngineRegistry(builtin=FakeBuiltin(), jdownloader=jdownloader, aria2=aria2)
    handle, engine = registry.add("https://www.example.com/files/pack.zip", "C:/out", "pack")
    assert registry.pause(handle) is True
    assert registry.resume(handle) is True
    assert registry.cancel(handle) is True
    assert aria2.calls == ["pause", "resume", "cancel"]
    assert not jdownloader.calls


def test_no_engine_that_can_do_it_is_an_error():
    registry = EngineRegistry(builtin=FakeBuiltin(can_handle=False),
                              jdownloader=FakeEngine("JDownloader 2", can_handle=False),
                              aria2=FakeEngine("aria2", can_handle=False))
    with pytest.raises(EngineError) as error:
        registry.add("https://www.example.com/x", "C:/out", "x")
    assert error.value.kind == ErrorKind.UNSUPPORTED


# --------------------------------------------------------------------------- the clients
def test_the_aria2_client_speaks_json_rpc():
    """The client sends a JSON-RPC request and reads the answer."""
    answer = {}

    def serve(sock: socket.socket) -> None:
        connection, _ = sock.accept()
        with connection:
            data = b""
            while b"\r\n\r\n" not in data:
                data += connection.recv(4096)
            head, _, body = data.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length"):
                    length = int(line.split(b":")[1])
            while len(body) < length:
                body += connection.recv(4096)
            request = json.loads(body.decode("utf-8"))
            answer.update(request)
            reply = json.dumps({"id": request["id"], "result": "ok"}).encode("utf-8")
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: "
                               + str(len(reply)).encode() + b"\r\n\r\n" + reply)

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    thread = threading.Thread(target=serve, args=(server,), daemon=True)
    thread.start()
    try:
        client = Aria2Client("127.0.0.1", port, secret="abc")
        assert client.call("aria2.getVersion") == "ok"
        assert answer["method"] == "aria2.getVersion"
        assert answer["params"][0] == "token:abc"
    finally:
        server.close()
        thread.join(timeout=5)


def test_the_aria2_client_reports_a_dead_engine():
    client = Aria2Client("127.0.0.1", 1, secret="x")
    assert client.alive() is False


def test_the_aria2_client_needs_the_engine():
    engine = Aria2Engine(port=6899, executable=Path("C:/nowhere/aria2c.exe"))
    assert engine.available() is False
    with pytest.raises(EngineError):
        engine.start()


def test_the_aria2_client_knows_what_it_can_do():
    engine = Aria2Engine()
    capabilities = engine.capabilities()
    assert capabilities.supports("https://example.com/a.zip")
    assert capabilities.supports("ftp://example.com/a.zip")
    assert not capabilities.supports("magnet:?xt=urn:btih:1")
    assert engine.can_handle("https://example.com/a.zip")
    assert not engine.can_handle("magnet:?xt=urn:btih:1")


def test_jdownloader_knows_what_it_can_do():
    engine = JDownloaderEngine()
    capabilities = engine.capabilities()
    assert capabilities.hosters
    assert capabilities.torrents
    # a page is the job of the plugins, a file the job of aria2
    assert engine.can_handle("https://www.example.com/watch?v=1")
    assert not engine.can_handle("https://www.example.com/files/pack.zip")


# --------------------------------------------------------------------------- the resources
def test_the_resources_find_the_bundled_engines():
    """The engines of the source tree are found without any configuration."""
    assert resources.jdownloader_jar() is not None
    assert resources.aria2_exe() is not None


def test_the_resources_look_in_the_data_folder_first():
    assert resources.engine_roots()[0].parts[-2:] == ("ArkLoad", "engines")


def test_the_state_of_an_engine_goes_into_the_data_folder():
    folder = resources.engine_state_dir("aria2")
    assert folder.is_dir()
    assert "aria2" in str(folder)
