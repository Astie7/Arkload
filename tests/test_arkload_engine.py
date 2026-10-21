"""Tests for the JDownloader engine client (the protocol, against a fake server).

    uv run --with pytest pytest -q tests/test_arkload_engine.py
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.engine import EngineError, JDownloaderEngine  # noqa: E402


class FakeEngine(http.server.BaseHTTPRequestHandler):
    """A tiny stand-in that records what the client sends."""

    calls: list[str] = []
    answer: dict = {"data": True}

    def do_GET(self):  # noqa: N802 (http.server naming)
        type(self).calls.append(self.path)
        body = json.dumps(type(self).answer).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def engine():
    FakeEngine.calls = []
    FakeEngine.answer = {"data": True}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield JDownloaderEngine("127.0.0.1", server.server_port), FakeEngine
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_json_query_goes_into_query_params(engine):
    client, fake = engine
    client.packages()
    assert fake.calls[0].startswith("/downloads/queryPackages?queryParams=")
    assert "%7B%22size%22%3Atrue" in fake.calls[0]   # the size is asked for as well


def test_named_parameters_are_used_for_the_task_calls(engine):
    client, fake = engine
    client.add("https://www.mediafire.com/folder/abc", "mods")
    assert "addLinksAndStartDownload" in fake.calls[0]
    assert "links=https" in fake.calls[0]
    assert "packageName=mods" in fake.calls[0]


def test_the_task_controls_are_sent(engine):
    client, fake = engine
    assert client.pause([1]) is True
    assert "pause" in fake.calls[-1]
    assert client.resume([1, 2]) is True
    assert "enableLinks" in fake.calls[-1]
    # the engine wants the ids as one json array
    assert "linkIds=%5B1%2C2%5D" in fake.calls[-1]
    assert client.cancel([3]) is True
    assert "disableLinks" in fake.calls[-1]
    assert client.remove([4]) is True
    assert "removeLinks" in fake.calls[-1]


def test_an_engine_error_is_reported(engine):
    client, fake = engine
    fake.answer = {"src": "DEVICE", "data": "/downloads/queryLinks", "type": "INTERNAL_SERVER_ERROR"}
    with pytest.raises(EngineError):
        client.call("downloads", "queryLinks", {})


def test_a_missing_engine_is_reported():
    client = JDownloaderEngine("127.0.0.1", 1)      # nothing listens there
    with pytest.raises(EngineError):
        client.packages()
    assert client.available() is False


def test_the_lists_are_read_from_the_answer(engine):
    client, fake = engine
    fake.answer = {"data": [{"name": "libs", "uuid": 1}]}
    assert client.packages()[0]["name"] == "libs"
    fake.answer = {"data": [{"name": "H2DB.jar", "uuid": 2}]}
    assert client.links()[0]["name"] == "H2DB.jar"
