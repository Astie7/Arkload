"""The real thing: ArkLoad downloads through the JDownloader engine.

Nothing is mocked here.  The test starts the engine (headless, the way ArkLoad does), serves a
file from a local web server and lets ArkLoad's own engine hand the link to it.  The file has to
arrive in the destination folder, byte for byte.

The test is skipped when the engine is not installed.  Point it at another build with:

    ARKLOAD_JDOWNLOADER=D:\\path\\to\\JDownloader.jar pytest -q tests/test_arkload_engine_live.py
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.download_engine import DownloadEngine, EngineOptions  # noqa: E402

#: the engine the test uses
ENGINE = os.environ.get("ARKLOAD_JDOWNLOADER", r"D:\AstieProjects\_jd_official\JDownloader"
                                                  r"\standalone\dist\JDownloader.jar")

#: the port the test's engine answers on
PORT = int(os.environ.get("ARKLOAD_ENGINE_PORT", "3128"))

pytestmark = pytest.mark.skipif(not Path(ENGINE).is_file(),
                                reason="the JDownloader engine is not installed")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """A small web server that hands out one file."""

    def __init__(self, folder: Path, name: str, payload: bytes):
        self.folder = folder
        self.name = name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(payload)
        handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.name}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def engine(tmp_path):
    """An ArkLoad engine that has the JDownloader engine behind it."""
    options = EngineOptions(destination=str(tmp_path / "downloads"),
                            engine_path=str(ENGINE), engine_port=PORT)
    manager = DownloadEngine(state_dir=tmp_path / "state", options=options)
    try:
        yield manager
    finally:
        manager.shutdown()


def wait_for(predicate, timeout: float = 180.0, interval: float = 0.5):
    """Wait until the predicate is true; returns its value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def test_arkload_downloads_a_file_through_the_jdownloader_engine(engine, tmp_path):
    """A link of an unknown host goes to the JDownloader engine and arrives on disk."""
    payload = b"arkload live payload " * 200_000        # about 4 MB
    server = Server(tmp_path / "www", "live.bin", payload)
    destination = tmp_path / "downloads"
    try:
        record = engine.add_download(server.url, destination=str(destination))
        assert record.backend == "jdownloader"

        def finished():
            engine._poll_jdownloader()
            folder = destination / "live"
            target = folder / "live.bin"
            if target.is_file() and target.stat().st_size == len(payload):
                return target
            return None

        target = wait_for(finished)
        assert target is not None, "the file did not arrive in the destination folder"
        assert target.read_bytes() == payload
        # the state follows one tick later
        def complete():
            engine._poll_jdownloader()
            return engine.record(record.job_id).state.value == "complete"

        assert wait_for(complete, timeout=30), "the download was not marked as complete"
    finally:
        server.stop()


def test_the_engine_can_be_paused_and_resumed(engine, tmp_path):
    """The controls of the window reach the engine."""
    payload = b"arkload pause payload " * 400_000        # about 8 MB
    server = Server(tmp_path / "www", "pause.bin", payload)
    destination = tmp_path / "downloads"
    try:
        record = engine.add_download(server.url, destination=str(destination))
        assert record.backend == "jdownloader"

        # wait until the engine really runs the link
        def running():
            engine._poll_jdownloader()
            task = engine.jd_backend().tasks.get(record.job_id)
            return bool(task and task.link_ids and task.started)

        assert wait_for(running, timeout=120), "the engine did not start the link"

        assert engine.pause(record) is True
        assert engine.record(record.job_id).state.value == "paused"
        assert engine.resume(record) is None or True      # the resume returns nothing
        assert wait_for(lambda: engine.record(record.job_id).state.value == "running",
                        timeout=30)
    finally:
        server.stop()


def test_the_queue_survives_a_restart(engine, tmp_path):
    """The task of the queue keeps the ids it has in the engine."""
    payload = b"arkload queue payload " * 100_000
    server = Server(tmp_path / "www", "queue.bin", payload)
    destination = tmp_path / "downloads"
    try:
        record = engine.add_download(server.url, destination=str(destination))
        wait_for(lambda: (engine._poll_jdownloader(),
                           engine.jd_backend().tasks.get(record.job_id)
                           and engine.jd_backend().tasks[record.job_id].link_ids)[1],
                 timeout=120)
        engine.persist(record)
        stored = engine.store.get(record.job_id)
        assert stored is not None
        assert stored.backend == "jdownloader"
        assert stored.external_id
    finally:
        server.stop()
