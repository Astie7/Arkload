"""End-to-end tests for the download session: detection, recovery and completion rules.

The network is faked: ``make_plan`` and ``download_one`` are replaced, everything else
(manifest, status detection, verification, recovery passes) is the real code.
"""
from __future__ import annotations

import hashlib
import queue
import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import mediafire_dl as dl  # noqa: E402
from tools import mediafire_gui as gui  # noqa: E402
from tools import mediafire_resume as rs  # noqa: E402

pytest.importorskip("tkinter")

PAYLOAD = {
    "a.jar": b"first file",
    "b.jar": b"second file",
    "c.jar": b"third file",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "STATE_DIR", tmp_path / "jobs")
    return tmp_path / "jobs"


def fake_plan():
    files = [dl.PlannedFile(f"q{index}", PurePosixPath(name), len(data), None, digest(data))
             for index, (name, data) in enumerate(PAYLOAD.items())]
    return dl.Plan("folder", "mods", files, 0)


def run_session(tmp_path, monkeypatch, dest, downloader, options=None, mode="download",
                resolver=None):
    """Run a session against the faked network and return (events, manifest)."""
    monkeypatch.setattr(dl, "make_plan", lambda source, name=None, token=None, cookie=None:
                        fake_plan())
    monkeypatch.setattr(dl, "download_one", downloader)
    spec = {
        "job_id": "job", "url": "https://www.mediafire.com/folder/abc", "dest": str(dest),
        "subfolder": True, "overwrite": False, "jobs": 1, "retries": 1,
        "recovery_rounds": 2, "patterns": [], "cookies": None, "timeout": 5.0,
        "mode": mode, "options": options or {},
    }
    if resolver is not None:
        spec["resolver"] = resolver
    events = queue.Queue()
    session = gui.DownloadSession(spec, events)
    session.start()
    session.join(60)
    assert not session.is_alive()
    collected = []
    while True:
        try:
            collected.append(events.get_nowait())
        except queue.Empty:
            break
    job_id = rs.job_identity("folder", "abc", str(dest), True)
    return collected, rs.load_job(job_id)


def kind_of(events, name):
    return [event for event in events if event[1] == name]


def test_the_session_works_with_a_host_plugin_resolver(tmp_path, monkeypatch):
    """The application always hands a resolver to the session (the host plugin layer).

    The session still needs the source of the link for the manifest - it used to fail with
    "cannot access local variable 'source'" in that case.
    """
    dest = tmp_path / "downloads"
    downloaded = []

    def downloader(item, root, **kwargs):
        data = PAYLOAD[item.rel.name]
        path = root.joinpath(*item.rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        downloaded.append(item.rel.name)
        return dl.Result("ok", len(data), 0.0, sha256=digest(data), verified=True)

    events, manifest = run_session(tmp_path, monkeypatch, dest, downloader,
                                   resolver=lambda url: fake_plan())

    assert sorted(downloaded) == ["a.jar", "b.jar", "c.jar"]
    assert manifest is not None
    assert manifest.status == rs.JOB_COMPLETE
    assert not kind_of(events, "fatal")


def test_session_downloads_only_what_is_missing(tmp_path, monkeypatch):
    dest = tmp_path / "downloads"
    (dest / "mods").mkdir(parents=True)
    (dest / "mods" / "a.jar").write_bytes(PAYLOAD["a.jar"])   # already there
    downloaded = []

    def downloader(item, root, **kwargs):
        data = PAYLOAD[item.rel.name]
        path = root.joinpath(*item.rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        downloaded.append(item.rel.name)
        return dl.Result("ok", len(data), 0.0, sha256=digest(data), verified=True)

    events, manifest = run_session(tmp_path, monkeypatch, dest, downloader)

    assert sorted(downloaded) == ["b.jar", "c.jar"]          # a.jar was left alone
    assert manifest.status == rs.JOB_COMPLETE
    assert all(record.status == rs.FILE_COMPLETE for record in manifest.files)
    assert kind_of(events, "done")[-1][-1] is True           # complete flag


def test_session_redownloads_a_corrupted_file(tmp_path, monkeypatch):
    dest = tmp_path / "downloads"
    (dest / "mods").mkdir(parents=True)
    for name, data in PAYLOAD.items():
        (dest / "mods" / name).write_bytes(data)
    (dest / "mods" / "b.jar").write_bytes(b"x" * len(PAYLOAD["b.jar"]))  # same size, wrong bytes
    downloaded = []

    def downloader(item, root, **kwargs):
        data = PAYLOAD[item.rel.name]
        path = root.joinpath(*item.rel.parts)
        path.write_bytes(data)
        downloaded.append(item.rel.name)
        return dl.Result("ok", len(data), 0.0, sha256=digest(data), verified=True)

    events, manifest = run_session(tmp_path, monkeypatch, dest, downloader)

    assert downloaded == ["b.jar"]                           # only the broken one
    assert manifest.status == rs.JOB_COMPLETE
    assert manifest.find("b.jar").attempts == 1
    assert manifest.find("a.jar").attempts == 0             # untouched


def test_session_retries_and_finishes_with_a_flaky_file(tmp_path, monkeypatch):
    dest = tmp_path / "downloads"
    attempts = {"c.jar": 0}

    def downloader(item, root, **kwargs):
        data = PAYLOAD[item.rel.name]
        path = root.joinpath(*item.rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        if item.rel.name == "c.jar":
            attempts["c.jar"] += 1
            if attempts["c.jar"] == 1:
                return dl.Result("failed", error="connection reset")
        path.write_bytes(data)
        return dl.Result("ok", len(data), 0.0, sha256=digest(data), verified=True)

    events, manifest = run_session(tmp_path, monkeypatch, dest, downloader)

    assert attempts["c.jar"] == 2                            # failed once, then recovered
    assert kind_of(events, "recovery")                       # a recovery pass happened
    assert manifest.status == rs.JOB_COMPLETE


def test_session_gives_up_after_the_retry_limit(tmp_path, monkeypatch):
    dest = tmp_path / "downloads"
    attempts = {"b.jar": 0}

    def downloader(item, root, **kwargs):
        if item.rel.name == "b.jar":
            attempts["b.jar"] += 1
            return dl.Result("failed", error="404 not found")
        data = PAYLOAD[item.rel.name]
        path = root.joinpath(*item.rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return dl.Result("ok", len(data), 0.0, sha256=digest(data), verified=True)

    events, manifest = run_session(tmp_path, monkeypatch, dest, downloader)

    assert attempts["b.jar"] == 3            # first pass + two recovery passes, then it stops
    assert manifest.status == rs.JOB_INCOMPLETE
    assert manifest.find("b.jar").status == rs.FILE_FAILED
    assert kind_of(events, "done")[-1][-1] is False


def test_session_resumes_a_partial_file(tmp_path, monkeypatch):
    dest = tmp_path / "downloads"
    (dest / "mods").mkdir(parents=True)
    (dest / "mods" / "a.jar.part").write_bytes(PAYLOAD["a.jar"][:5])
    seen = []

    def downloader(item, root, **kwargs):
        path = root.joinpath(*item.rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        seen.append(part.stat().st_size if part.is_file() else 0)
        data = PAYLOAD[item.rel.name]
        path.write_bytes(data)
        return dl.Result("ok", len(data), 0.0, sha256=digest(data), verified=True)

    events, manifest = run_session(tmp_path, monkeypatch, dest, downloader)

    assert seen[0] == 5          # the .part file was there and the download continued from it
    assert manifest.status == rs.JOB_COMPLETE

# --------------------------------------------------------------------------- pause and cancel

def test_a_cancelled_session_stops_and_keeps_the_partial_file(tmp_path, monkeypatch):
    """Cancelling must stop the transfer and leave the .part file behind."""
    import threading
    import time

    started = threading.Event()
    release = threading.Event()

    def downloader(item, root, **kwargs):
        started.set()
        part = root.joinpath(*item.rel.parts).with_name(
            root.joinpath(*item.rel.parts).name + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"half")
        cancel = kwargs.get("cancel")
        while not release.is_set():
            if cancel is not None and cancel.is_set():
                break
            time.sleep(0.05)
        return mf.Result("cancelled")

    monkeypatch.setattr(dl, "make_plan", lambda source, name=None, token=None, cookie=None:
                        fake_plan())
    monkeypatch.setattr(dl, "download_one", downloader)
    spec = {"job_id": "job", "url": "https://www.mediafire.com/folder/abc",
            "dest": str(tmp_path), "subfolder": True, "overwrite": False, "jobs": 1,
            "retries": 1, "recovery_rounds": 0, "patterns": [], "cookies": None,
            "timeout": 5.0, "mode": "download", "options": {}}
    events = queue.Queue()
    session = gui.DownloadSession(spec, events)
    session.start()
    assert started.wait(10)
    session.request_stop("cancelled")
    release.set()
    session.join(30)
    assert not session.is_alive()

    kinds = []
    while True:
        try:
            kinds.append(events.get_nowait()[1])
        except queue.Empty:
            break
    assert "done" in kinds or "cancelled" in kinds
    parts = list(tmp_path.rglob("*.part"))
    assert parts, "the partial file has to be kept"
    manifest = rs.load_job(rs.job_identity("folder", "abc", str(tmp_path), True))
    assert manifest.status in (rs.JOB_CANCELLED, rs.JOB_COMPLETE)


def test_a_paused_session_does_not_write_more(tmp_path, monkeypatch):
    """While the session is paused the download must not continue."""
    import threading
    import time

    calls = []
    running = threading.Event()

    def downloader(item, root, **kwargs):
        calls.append(item.rel.as_posix())
        running.set()
        pause = kwargs.get("pause")
        cancel = kwargs.get("cancel")
        deadline = time.time() + 3
        while time.time() < deadline:
            if pause is not None and pause.is_set():
                break
            if cancel is not None and cancel.is_set():
                break
            time.sleep(0.05)
        return mf.Result("cancelled")

    monkeypatch.setattr(dl, "make_plan", lambda source, name=None, token=None, cookie=None:
                        fake_plan())
    monkeypatch.setattr(dl, "download_one", downloader)
    spec = {"job_id": "job", "url": "https://www.mediafire.com/folder/abc",
            "dest": str(tmp_path), "subfolder": True, "overwrite": False, "jobs": 1,
            "retries": 1, "recovery_rounds": 0, "patterns": [], "cookies": None,
            "timeout": 5.0, "mode": "download", "options": {}}
    events = queue.Queue()
    session = gui.DownloadSession(spec, events)
    session.start()
    assert running.wait(10)
    session.set_paused(True)
    time.sleep(0.5)
    paused_calls = len(calls)
    time.sleep(0.5)
    assert len(calls) == paused_calls, "a paused session must not start another file"
    session.request_stop("cancelled")
    session.join(30)

