"""Tests for the JDownloader engine behind ArkLoad.

The protocol is checked against a small stand-in that answers like the engine does, and the
backend is checked against the same stand-in: what ArkLoad puts into the engine, what it reads
back and how it turns that into the state of a download.

    pytest -q tests/test_arkload_jdownloader.py
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.core import Task  # noqa: E402
from tools.arkload.engine import EngineError, JDownloaderEngine  # noqa: E402
from tools.arkload.jdownloader import (EngineProcess, JDownloaderBackend, find_engine,  # noqa: E402
                                       package_name_for)


class FakeEngine(http.server.BaseHTTPRequestHandler):
    """A stand-in for the engine: it answers like the real one does."""

    #: the packages of the download list
    packages: list = []
    #: the packages of the link collector
    collector_packages: list = []
    #: the links that were not started yet
    links: list = []
    #: every path that was asked for
    calls: list = []
    #: the links that were started
    started: list = []
    #: the links that were disabled
    disabled: list = []
    #: the links that were enabled again
    enabled: list = []
    #: the links that were removed
    removed: list = []
    #: what the engine answers to addLinks
    accept: bool = True

    def do_GET(self):  # noqa: N802 (http.server naming)
        type(self).calls.append(self.path)
        path = self.path.split("?")[0]
        parts = path.strip("/").split("/")
        section, method = (parts + ["", ""])[:2]
        body = {"data": True}
        if section == "downloads" and method == "queryPackages":
            body = {"data": list(type(self).packages)}
        elif section == "linkcollector" and method == "queryPackages":
            body = {"data": list(type(self).collector_packages)}
        elif section == "linkcollector" and method == "queryLinks":
            body = {"data": list(type(self).links)}
        elif section == "linkcollector" and method == "addLinks":
            body = {"data": type(self).accept}
        elif section == "linkcollector" and method == "startDownloads":
            type(self).started.extend(json.loads(self._value("linkIds")))
            body = {"data": True}
        elif section == "downloads" and method == "disableLinks":
            type(self).disabled.extend(json.loads(self._value("linkIds")))
            body = {"data": True}
        elif section == "downloads" and method == "enableLinks":
            type(self).enabled.extend(json.loads(self._value("linkIds")))
            body = {"data": True}
        elif section == "downloads" and method == "removeLinks":
            type(self).removed.extend(json.loads(self._value("linkIds")))
            body = {"data": True}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _value(self, key: str) -> str:
        import urllib.parse
        query = self.path.split("?")[1] if "?" in self.path else ""
        values = urllib.parse.parse_qs(query)
        return values.get(key, ["[]"])[0]

    def log_message(self, *args):
        pass


@pytest.fixture
def fake():
    FakeEngine.packages = []
    FakeEngine.collector_packages = []
    FakeEngine.links = []
    FakeEngine.calls = []
    FakeEngine.started = []
    FakeEngine.disabled = []
    FakeEngine.enabled = []
    FakeEngine.removed = []
    FakeEngine.accept = True
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# -- the routing -------------------------------------------------------------
def test_the_mediafire_links_keep_the_arkload_engine(tmp_path):
    """MediaFire is not handed over to the JDownloader engine - it is verified by ArkLoad."""
    from tools.arkload.download_engine import DownloadEngine

    manager = DownloadEngine(state_dir=tmp_path / "state")
    try:
        assert manager.engine_of("https://www.mediafire.com/folder/abc/libs") == "arkload"
        assert manager.engine_of("https://mediafire.com/file/abc/file.zip") == "arkload"
        assert manager.engine_of("https://www.mfi.re/folder/abc") == "arkload"
        assert manager.engine_of("https://example.com/pack.zip") == "jdownloader"
        assert manager.engine_of("https://rapidgator.net/file/abc") == "jdownloader"
    finally:
        manager.shutdown()


def test_the_task_remembers_the_engine_it_runs(tmp_path):
    """The queue keeps the engine of a task, so it is known after a restart."""
    from tools.arkload.core import TaskStore

    store = TaskStore(tmp_path / "queue.json")
    task = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                backend="jdownloader", external_id='{"link_ids": [12]}')
    store.add(task)
    loaded = store.load()[0]
    assert loaded.backend == "jdownloader"
    assert loaded.external_id == '{"link_ids": [12]}'


# -- the names ---------------------------------------------------------------
def test_the_package_name_loses_the_extension():
    assert package_name_for("movie.mkv") == "movie"
    assert package_name_for("pack.zip") == "pack"
    assert package_name_for("libs") == "libs"
    assert package_name_for("2024.12 release") == "2024.12 release"
    assert package_name_for("") == "download"
    assert package_name_for("a<b>c") == "abc"


def test_the_engine_is_not_found_when_it_is_not_there(tmp_path):
    assert find_engine(tmp_path / "nothing.jar") is None


# -- the backend -------------------------------------------------------------
def test_a_link_goes_into_the_collector_with_its_folder(fake, tmp_path):
    client = JDownloaderEngine("127.0.0.1", fake)
    backend = JDownloaderBackend(client)
    record = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                  destination=str(tmp_path))
    assert backend.add(record) is True
    call = FakeEngine.calls[-1]
    assert "/linkcollector/addLinks?" in call
    assert "links=https" in call
    assert "packageName=pack" in call
    assert "destinationFolder=" in call
    import urllib.parse
    assert urllib.parse.quote(str(tmp_path)) in call


def test_the_transfer_starts_when_the_crawler_is_done(fake, tmp_path):
    client = JDownloaderEngine("127.0.0.1", fake)
    backend = JDownloaderBackend(client)
    record = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                  destination=str(tmp_path))
    backend.add(record)
    # the engine resolves the link and puts it into the collector
    FakeEngine.collector_packages = [{"name": "pack", "uuid": 11,
                                      "infoMap": {"size": 100, "childCount": 1}}]
    FakeEngine.links = [{"name": "pack.zip", "uuid": 12,
                         "infoMap": {"size": 100, "packageUUID": 11}}]
    update = backend.poll()["job1"]
    assert FakeEngine.started == [12]
    assert update["state"] == "running"
    assert update["bytes_total"] == 100


def test_the_progress_comes_from_the_files_on_disk(fake, tmp_path):
    client = JDownloaderEngine("127.0.0.1", fake)
    backend = JDownloaderBackend(client)
    record = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                  destination=str(tmp_path))
    backend.add(record)
    FakeEngine.collector_packages = [{"name": "pack", "uuid": 11,
                                      "infoMap": {"size": 1000, "childCount": 1}}]
    FakeEngine.links = [{"name": "pack.zip", "uuid": 12,
                         "infoMap": {"size": 1000, "packageUUID": 11}}]
    folder = tmp_path / "pack"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pack.zip").write_bytes(b"x" * 400)
    FakeEngine.packages = [{"name": "pack", "uuid": 13, "infoMap": {"size": 1000,
                                                                    "childCount": 1}}]
    update = backend.poll()["job1"]
    assert update["bytes_done"] == 400
    assert update["bytes_total"] == 1000
    assert update["state"] == "running"
    assert update["files"][0].path == "pack.zip"
    assert update["files"][0].size == 1000


def test_a_finished_download_is_complete(fake, tmp_path):
    client = JDownloaderEngine("127.0.0.1", fake)
    backend = JDownloaderBackend(client)
    record = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                  destination=str(tmp_path))
    backend.add(record)
    FakeEngine.collector_packages = [{"name": "pack", "uuid": 11,
                                      "infoMap": {"size": 10, "childCount": 1}}]
    FakeEngine.links = [{"name": "pack.zip", "uuid": 12,
                         "infoMap": {"size": 10, "packageUUID": 11}}]
    folder = tmp_path / "pack"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pack.zip").write_bytes(b"x" * 10)
    FakeEngine.packages = [{"name": "pack", "uuid": 13, "infoMap": {"size": 10, "childCount": 1}}]
    assert backend.poll()["job1"]["state"] == "complete"


def test_the_controls_use_the_ids_of_the_engine(fake, tmp_path):
    client = JDownloaderEngine("127.0.0.1", fake)
    backend = JDownloaderBackend(client)
    record = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                  destination=str(tmp_path))
    backend.add(record)
    FakeEngine.collector_packages = [{"name": "pack", "uuid": 11,
                                      "infoMap": {"size": 10, "childCount": 1}}]
    FakeEngine.links = [{"name": "pack.zip", "uuid": 12,
                         "infoMap": {"size": 10, "packageUUID": 11}}]
    backend.poll()
    assert backend.pause(record) is True
    assert FakeEngine.disabled == [12]
    assert backend.resume(record) is True
    assert FakeEngine.enabled == [12]
    assert backend.cancel(record) is True
    assert backend.remove(record) is True
    assert FakeEngine.removed == [12]


def test_a_task_survives_a_restart(fake, tmp_path):
    client = JDownloaderEngine("127.0.0.1", fake)
    backend = JDownloaderBackend(client)
    record = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                  destination=str(tmp_path))
    backend.add(record)
    FakeEngine.collector_packages = [{"name": "pack", "uuid": 11,
                                      "infoMap": {"size": 10, "childCount": 1}}]
    FakeEngine.links = [{"name": "pack.zip", "uuid": 12,
                         "infoMap": {"size": 10, "packageUUID": 11}}]
    backend.poll()
    record.external_id = backend.external_id(backend.tasks["job1"])

    # a new start of ArkLoad
    other = JDownloaderBackend(JDownloaderEngine("127.0.0.1", fake))
    other.restore(record)
    assert other.tasks["job1"].link_ids == [12]
    assert other.tasks["job1"].package_uuid == 11
    assert other.tasks["job1"].package_name == "pack"


def test_a_link_the_engine_cannot_handle_fails(fake, tmp_path, monkeypatch):
    client = JDownloaderEngine("127.0.0.1", fake)
    backend = JDownloaderBackend(client)
    record = Task(job_id="job1", url="https://example.com/pack.zip", title="pack.zip",
                  destination=str(tmp_path))
    backend.add(record)
    # no link ever shows up in the collector
    monkeypatch.setattr("tools.arkload.jdownloader.COLLECT_TIMEOUT", 0.0)
    update = backend.poll()["job1"]
    assert update["state"] == "failed"
    assert "JDownloader" in update["note"]


def test_a_missing_engine_is_reported(tmp_path):
    # nothing listens on that port
    backend = JDownloaderBackend(JDownloaderEngine("127.0.0.1", 1))
    backend.process = None
    with pytest.raises(EngineError):
        backend.ensure_running()
    assert backend.available() is False


def test_the_engine_process_writes_the_api_settings(tmp_path):
    jar = tmp_path / "JDownloader.jar"
    jar.write_bytes(b"")
    process = EngineProcess(jar, port=3199)
    process.prepare()
    settings = json.loads((tmp_path / "cfg" / "org.jdownloader.api.RemoteAPIConfig.json")
                           .read_text(encoding="utf-8"))
    assert settings["port"] == 3199
    assert settings["deprecatedapienabled"] is True
    assert settings["externinterfacelocalhostonly"] is True
