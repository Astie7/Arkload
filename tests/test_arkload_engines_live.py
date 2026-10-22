"""The engines of ArkLoad, for real: the bundled aria2 and the bundled JDownloader 2.

The tests use the engines that are bundled with the application (the ones the installer puts
into the data folder, or the ones in the source tree).  They are skipped when they are not there.

    pytest -q tests/test_arkload_engines_live.py
"""
from __future__ import annotations

import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.engines import resources
from tools.arkload.engines.aria2 import Aria2Engine
from tools.arkload.engines.base import DownloadRequest, EngineError, ErrorKind
from tools.arkload.engines.jdownloader import JDownloaderEngine
from tools.arkload.engines.registry import EngineRegistry

ARIA2 = resources.aria2_exe()
JDOWNLOADER = resources.jdownloader_jar()

pytestmark = pytest.mark.skipif(
    not (ARIA2 and Path(ARIA2).is_file() and JDOWNLOADER and Path(JDOWNLOADER).is_file()),
    reason="the engines are not bundled with this copy of ArkLoad")


class SlowHandler(SimpleHTTPRequestHandler):
    """A handler that sends the file slowly, so a download stays running."""

    chunk = 64 * 1024
    delay = 0.05

    def copyfile(self, source, outputfile):  # noqa: D102 (the name is from the base class)
        import time
        while True:
            data = source.read(self.chunk)
            if not data:
                break
            outputfile.write(data)
            outputfile.flush()
            time.sleep(self.delay)


class Server:
    """A small web server with one file."""

    def __init__(self, folder: Path, name: str, payload: bytes, slow: bool = False):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(payload)
        if slow:
            handler = partial(SlowHandler, directory=str(folder))
        else:
            handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/payload.bin"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def test_the_bundled_aria2_downloads_a_file(tmp_path):
    """The bundled aria2 starts, takes the link and reports the progress."""
    payload = b"aria2 live payload " * 150_000
    server = Server(tmp_path / "www", "payload.bin", payload)
    engine = Aria2Engine(port=6800)
    try:
        engine.start()
        assert engine.available()
        handle = engine.add(DownloadRequest(url=server.url, destination=str(tmp_path / "out"),
                                            name="payload.bin", options={"connections": 4}))
        state = None
        for _ in range(120):
            time.sleep(0.5)
            state = engine.status(handle)
            if state and state.state in ("complete", "failed"):
                break
        assert state is not None and state.state == "complete", state
        # the engine puts every download into a folder of its own
        targets = list((tmp_path / "out").rglob("payload.bin"))
        assert targets, "the file is not in the destination"
        assert targets[0].read_bytes() == payload
    finally:
        engine.stop()
        server.stop()


def test_the_bundled_aria2_can_pause_and_resume(tmp_path):
    """The controls of the window reach aria2."""
    payload = b"aria2 pause payload " * 400_000          # about 8 MB
    server = Server(tmp_path / "www", "payload.bin", payload, slow=True)
    engine = Aria2Engine(port=6800)
    try:
        engine.start()
        handle = engine.add(DownloadRequest(url=server.url, destination=str(tmp_path / "out"),
                                            name="payload.bin"))
        # aria2 only pauses a download that runs - a waiting one has to be started first
        running = False
        for _ in range(60):
            time.sleep(0.2)
            state = engine.status(handle)
            if state and state.state == "running":
                running = True
                break
        assert running, "the download did not start"
        assert engine.pause(handle) is True
        paused = engine.status(handle)
        assert paused is not None and paused.state == "paused", paused
        assert engine.resume(handle) is True
        assert engine.cancel(handle) is True
    finally:
        engine.stop()
        server.stop()


def test_the_bundled_jdownloader_downloads_a_file(tmp_path):
    """The bundled engine starts (with the bundled Java) and downloads a link."""
    payload = b"jdownloader live payload " * 150_000
    server = Server(tmp_path / "www", "payload.bin", payload)
    engine = JDownloaderEngine(port=3128)
    try:
        engine.ensure_running()
        assert engine.available()
        handle = engine.add(DownloadRequest(url=server.url, destination=str(tmp_path / "out"),
                                            name="payload.bin"))
        state = None
        for _ in range(180):
            time.sleep(1.0)
            state = engine.status(handle)
            if state and state.state in ("complete", "failed"):
                break
        assert state is not None and state.state == "complete", state
        # the engine puts every download into a folder of its own
        targets = list((tmp_path / "out").rglob("payload.bin"))
        assert targets, "the file is not in the destination"
        assert targets[0].read_bytes() == payload
    finally:
        engine.stop()
        server.stop()


def test_the_registry_runs_a_direct_link_with_aria2(tmp_path):
    """The routing sends a direct link to aria2 - the JVM is not even needed for it."""
    payload = b"registry live payload " * 100_000
    server = Server(tmp_path / "www", "payload.bin", payload)
    registry = EngineRegistry(jdownloader=JDownloaderEngine(port=3128),
                              aria2=Aria2Engine(port=6800))
    try:
        handle, engine = registry.add(server.url, str(tmp_path / "out"), "payload.bin")
        assert registry.key_of(engine) == "aria2"
        for _ in range(120):
            time.sleep(0.5)
            status = registry.status(handle)
            if status and status.state in ("complete", "failed"):
                break
        assert status is not None and status.state == "complete", status
        target = tmp_path / "out" / "payload.bin"
        assert target.is_file() and target.read_bytes() == payload
    finally:
        registry.stop_engines()
        server.stop()


def test_a_link_that_is_gone_is_not_downloaded_twice(tmp_path):
    """A link that is not there any more fails - and it does not run in a second engine.

    The error is the user's problem (the link is gone), so the other engine is not asked: it
    would fail the same way and the download would be there twice.
    """
    server = Server(tmp_path / "www", "payload.bin", b"x")
    registry = EngineRegistry(jdownloader=JDownloaderEngine(port=3128),
                              aria2=Aria2Engine(port=6800))
    try:
        handle, engine = registry.add(f"http://127.0.0.1:{server.port}/not-there.bin",
                                      str(tmp_path / "out"), "not-there")
        # aria2 takes the direct link and reports the error
        state = None
        for _ in range(60):
            time.sleep(0.5)
            state = registry.status(handle)
            if state and state.state in ("failed", "complete"):
                break
        assert state is not None and state.state == "failed", state
        assert state.error
        # nothing was downloaded
        assert not list((tmp_path / "out").rglob("*.bin"))
    finally:
        registry.stop_engines()
        server.stop()
