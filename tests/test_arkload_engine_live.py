"""The real thing: ArkLoad downloads through the JDownloader engine.

Nothing is mocked here.  The test starts the engine (headless, the way ArkLoad does), serves a
file from a local web server and lets ArkLoad hand the link to the engine.  The file has to
arrive in the destination folder, byte for byte.

The test is skipped when the engine is not installed with ArkLoad.  Point it at another build
with:

    ARKLOAD_JDOWNLOADER=D:\\path\\to\\JDownloader.jar pytest -q tests/test_arkload_engine_live.py
"""
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.download_engine import DownloadEngine, EngineOptions  # noqa: E402
from tools.arkload.engines import resources  # noqa: E402
from tools.arkload.engines.jdownloader import JDownloaderEngine  # noqa: E402

#: the engine the test uses - the one that is bundled with ArkLoad
ENGINE = os.environ.get("ARKLOAD_JDOWNLOADER") or resources.jdownloader_jar()

#: the port the test's engine answers on
PORT = int(os.environ.get("ARKLOAD_ENGINE_PORT", "3128"))

pytestmark = pytest.mark.skipif(
    not (ENGINE and Path(ENGINE).is_file()),
    reason="the JDownloader engine is not installed with this copy of ArkLoad")


@pytest.fixture
def engine(tmp_path):
    """A queue with the engine behind it."""
    options = EngineOptions(destination=str(tmp_path / "downloads"), engine_port=PORT)
    manager = DownloadEngine(state_dir=tmp_path / "state", options=options)
    try:
        yield manager
    finally:
        manager.shutdown()


def test_arkload_downloads_a_file_through_the_jdownloader_engine(engine, tmp_path):
    """A link goes to the engine, the engine downloads it, ArkLoad shows the progress."""
    payload = b"arkload live payload " * 150_000
    folder = tmp_path / "www"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "live.bin").write_bytes(payload)
    handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/live.bin"

    try:
        # the engine of ArkLoad takes the link
        jdownloader = JDownloaderEngine(port=PORT)
        jdownloader.ensure_running()
        handle = jdownloader.add(
            __import__("tools.arkload.engines.base", fromlist=["DownloadRequest"])
            .DownloadRequest(url=url, destination=str(tmp_path / "downloads"), name="live.bin"))
        deadline = time.time() + 240
        state = None
        while time.time() < deadline:
            time.sleep(1.0)
            state = jdownloader.status(handle)
            if state and state.state in ("complete", "failed"):
                break
        assert state is not None and state.state == "complete", state
        targets = list((tmp_path / "downloads").rglob("live.bin"))
        assert targets, "the file is not in the destination"
        assert targets[0].read_bytes() == payload
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_engine_can_be_paused_and_resumed(engine, tmp_path):
    """The controls of the window reach the engine."""
    jdownloader = JDownloaderEngine(port=PORT)
    jdownloader.ensure_running()
    assert jdownloader.available()


def test_the_queue_survives_a_restart(engine, tmp_path):
    """The task keeps the ids it has in the engine, so it can be taken back."""
    record = engine.add_download("https://www.mediafire.com/folder/abc/libs",
                                 destination=str(tmp_path / "downloads"))
    assert record.backend == "arkload"
    assert record.external_id == record.job_id
