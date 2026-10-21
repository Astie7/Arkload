"""Offline tests for the standalone MediaFire downloader (URL parsing, planning, scraping, resume).

    uv run --with pytest pytest -q tests/test_mediafire_dl.py
"""
from __future__ import annotations

import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import mediafire_dl as mf  # noqa: E402


# --------------------------------------------------------------------------- URLs and names

@pytest.mark.parametrize("url,expected", [
    ("https://www.mediafire.com/folder/abc123/My+Mod", ("folder", "abc123")),
    ("https://www.mediafire.com/folder/abc123", ("folder", "abc123")),
    ("www.mediafire.com/folder/abc123/My%20Mod#top", ("folder", "abc123")),
    ("https://www.mediafire.com/folder/#abc123", ("folder", "abc123")),
    ("https://www.mediafire.com/file/q1/name.zip/file", ("file", "q1")),
    ("https://www.mediafire.com/download/file/q1/name.zip", ("file", "q1")),
    ("https://mediafire.com/?q1", ("file", "q1")),
    ("rww7bhhi0yc1l", ("folder", "rww7bhhi0yc1l")),
])
def test_parse_source(url, expected):
    source = mf.parse_source(url)
    assert (source.kind, source.key) == expected


@pytest.mark.parametrize("url", ["https://example.com/folder/abc", "https://www.mediafire.com/", ""])
def test_parse_source_rejects_other_links(url):
    with pytest.raises(mf.MediaFireError):
        mf.parse_source(url)


def test_safe_name_windows_rules():
    assert mf.safe_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert mf.safe_name("CON.txt") == "_CON.txt"
    assert mf.safe_name("con") == "_con"
    assert mf.safe_name("foo. ") == "foo"
    assert mf.safe_name("...") == "_"
    assert mf.safe_name("../../evil.txt") == ".._.._evil.txt"


def test_safe_name_limits_component_length():
    name = mf.safe_name("x" * 400 + ".jar")
    assert len(name) <= mf._MAX_NAME
    assert name.endswith(".jar")


def test_unique_name_is_case_insensitive_and_keeps_extension():
    used = {"same.txt", "same (2).txt"}
    assert mf.unique_name("SAME.TXT", used) == "SAME (3).TXT"
    assert mf.unique_name("folder", set()) == "folder"


def test_cookie_header_combines_browser_cookies_and_session_token():
    assert mf._cookie_header(None, None) is None
    assert mf._cookie_header("ukey=a; user=b", None) == "ukey=a; user=b"
    assert mf._cookie_header(None, "tok") == "session_token=tok"
    assert mf._cookie_header("ukey=a", "tok") == "ukey=a; session_token=tok"


# --------------------------------------------------------------------------- planning

def test_plan_folder_recurses_and_dedupes(monkeypatch):
    tree = {
        "root": {
            "files": [
                dict(filename="../../evil.txt", size="5", quickkey="f1",
                     links={"normal_download": "https://www.mediafire.com/file/f1/file"}),
                dict(filename="same.txt", size="6", quickkey="f2", links={}),
                dict(filename="same.txt", size="7", quickkey="f3", links={}),
            ],
            "folders": [
                dict(name="sub", folderkey="s1"),
                dict(name="sub", folderkey="s2"),
            ],
        },
        "s1": {"files": [dict(filename="a.bin", size="1", quickkey="f4", links={})], "folders": []},
        "s2": {"files": [], "folders": []},
    }
    monkeypatch.setattr(mf, "folder_info", lambda key, token=None, cookie=None: {"name": "Root"})
    monkeypatch.setattr(mf, "folder_content",
                        lambda key, kind, token=None, cookie=None: iter(tree[key][kind]))

    plan = mf.plan_folder("root")
    assert plan.root_name == "Root"
    assert plan.folders == 2
    assert [f.rel.as_posix() for f in plan.files] == [
        ".._.._evil.txt", "same.txt", "same (2).txt", "sub/a.bin",
    ]
    assert plan.total_bytes == 19


def test_plan_file(monkeypatch):
    monkeypatch.setattr(mf, "file_info", lambda key, token=None, cookie=None: {
        "filename": "thing.zip", "size": "42",
        "links": {"normal_download": "https://www.mediafire.com/file/q1/thing.zip/file"},
    })
    plan = mf.plan_file("q1")
    assert plan.root_name == ""
    assert plan.files == [mf.PlannedFile("q1", PurePosixPath("thing.zip"), 42,
                                         "https://www.mediafire.com/file/q1/thing.zip/file")]


# --------------------------------------------------------------------------- link scraping

def test_scrape_download_url_from_button():
    page = ('<a class="input" href="https://download123.mediafire.com/abc" '
            'id="downloadButton" rel="nofollow">Download</a>')
    assert mf.scrape_download_url(page) == "https://download123.mediafire.com/abc"


def test_scrape_download_url_id_before_href_and_entities():
    page = '<a id="downloadButton" href="https://download1.mediafire.com/a?x=1&amp;y=2">'
    assert mf.scrape_download_url(page) == "https://download1.mediafire.com/a?x=1&y=2"


def test_scrape_download_url_falls_back_to_cdn_url():
    page = '<script>var direct = "https://download7.mediafire.com/xyz";</script>'
    assert mf.scrape_download_url(page) == "https://download7.mediafire.com/xyz"


@pytest.mark.parametrize("page,message", [
    ('<form id="formPassword"><p>Enter the password</p></form>', "password"),
    ("<p>This file has been removed.</p>", "removed"),
    ("<p>Dangerous File Blocked</p>", "dangerous"),
])
def test_scrape_download_url_explains_failures(page, message):
    with pytest.raises(mf.MediaFireError, match=message):
        mf.scrape_download_url(page)


# --------------------------------------------------------------------------- resume / download

@pytest.fixture
def payload_server():
    payload = b"0123456789abcdef" * 4096  # 64 KiB

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if "cookie" in self.path and self.headers.get("Cookie") != "ukey=abc; user=def":
                self.send_response(403)
                self.end_headers()
                return
            rng = self.headers.get("Range")
            if rng and "ignore" not in self.path:
                start = int(re.match(r"bytes=(\d+)-", rng).group(1))
                body = payload[start:]
                self.send_response(206)
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            else:
                body = payload
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/file", payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stream_resumes_partial_file(tmp_path, payload_server):
    url, payload = payload_server
    part = tmp_path / "x.part"
    part.write_bytes(payload[:40000])

    written = mf._stream(url, part, len(payload), fresh=False)

    assert written == len(payload)
    assert part.read_bytes() == payload


def test_stream_sends_cookie_for_blocked_files(tmp_path, payload_server):
    url, payload = payload_server

    written = mf._stream(url + "?cookie", tmp_path / "x.part", len(payload), fresh=True,
                         cookie="ukey=abc; user=def")

    assert written == len(payload)


def test_stream_waits_while_paused(tmp_path, payload_server):
    url, payload = payload_server
    pause = threading.Event()
    pause.set()
    timer = threading.Timer(0.25, pause.clear)
    timer.start()
    started = time.monotonic()
    try:
        written = mf._stream(url, tmp_path / "x.part", len(payload), fresh=True, pause=pause)
    finally:
        pause.clear()
        timer.cancel()

    assert written == len(payload)
    assert time.monotonic() - started >= 0.2


def test_stream_restarts_when_server_ignores_range(tmp_path, payload_server):
    url, payload = payload_server
    part = tmp_path / "x.part"
    part.write_bytes(payload[:40000])

    written = mf._stream(url + "?ignore", part, len(payload), fresh=False)

    assert written == len(payload)
    assert part.read_bytes() == payload


def test_download_one_stops_before_a_cancelled_download(tmp_path):
    cancel = threading.Event()
    cancel.set()
    item = mf.PlannedFile("f1", PurePosixPath("a.bin"), 10, None)

    result = mf.download_one(item, tmp_path, overwrite=False, retries=1, timeout=5,
                             session_token=None, cancel=cancel)

    assert result.status == "cancelled"
    assert not (tmp_path / "a.bin").exists()
    assert not (tmp_path / "a.bin.part").exists()


def test_download_one_creates_zero_byte_file_without_a_request(tmp_path, monkeypatch):
    item = mf.PlannedFile("f1", PurePosixPath("empty.txt"), 0, None)
    monkeypatch.setattr(mf, "resolve_download_url",
                        lambda item, token=None, cookie=None: pytest.fail("must not resolve a zero-byte file"))

    result = mf.download_one(item, tmp_path, overwrite=False, retries=1, timeout=5,
                             session_token=None)

    assert result.status == "ok" and result.size == 0
    assert (tmp_path / "empty.txt").read_bytes() == b""


def test_download_one_writes_final_then_skips(tmp_path, payload_server, monkeypatch):
    url, payload = payload_server
    item = mf.PlannedFile("f1", PurePosixPath("sub/a.bin"), len(payload), None)
    monkeypatch.setattr(mf, "resolve_download_url", lambda item, token=None, cookie=None: url)

    first = mf.download_one(item, tmp_path, overwrite=False, retries=1, timeout=5,
                            session_token=None)
    assert first.status == "ok"
    assert first.size == len(payload)
    assert (tmp_path / "sub" / "a.bin").read_bytes() == payload
    assert not (tmp_path / "sub" / "a.bin.part").exists()

    second = mf.download_one(item, tmp_path, overwrite=False, retries=1, timeout=5,
                             session_token=None)
    assert second.status == "skip"
    assert second.size == len(payload)


# --------------------------------------------------------------------------- integrity

def test_download_one_verifies_the_server_hash(tmp_path, payload_server, monkeypatch):
    import hashlib

    url, payload = payload_server
    sha = hashlib.sha256(payload).hexdigest()
    item = mf.PlannedFile("f1", PurePosixPath("a.bin"), len(payload), None, sha)
    monkeypatch.setattr(mf, "resolve_download_url", lambda item, token=None, cookie=None: url)

    result = mf.download_one(item, tmp_path, overwrite=False, retries=1, timeout=5,
                             session_token=None)

    assert result.status == "ok"
    assert result.verified is True
    assert result.sha256 == sha


def test_download_one_fails_and_cleans_up_on_a_hash_mismatch(tmp_path, payload_server,
                                                             monkeypatch):
    import hashlib

    url, payload = payload_server
    wrong = hashlib.sha256(b"not the payload").hexdigest()
    item = mf.PlannedFile("f1", PurePosixPath("a.bin"), len(payload), None, wrong)
    monkeypatch.setattr(mf, "resolve_download_url", lambda item, token=None, cookie=None: url)

    result = mf.download_one(item, tmp_path, overwrite=False, retries=2, timeout=5,
                             session_token=None)

    assert result.status == "failed"
    assert "sha256 mismatch" in result.error
    assert not (tmp_path / "a.bin").exists()
    assert not (tmp_path / "a.bin.part").exists()


def test_download_one_replaces_a_corrupted_existing_file(tmp_path, payload_server, monkeypatch):
    import hashlib

    url, payload = payload_server
    sha = hashlib.sha256(payload).hexdigest()
    (tmp_path / "a.bin").write_bytes(b"x" * len(payload))
    item = mf.PlannedFile("f1", PurePosixPath("a.bin"), len(payload), None, sha)
    monkeypatch.setattr(mf, "resolve_download_url", lambda item, token=None, cookie=None: url)

    result = mf.download_one(item, tmp_path, overwrite=False, retries=1, timeout=5,
                             session_token=None)

    assert result.status == "ok"
    assert result.verified is True
    assert (tmp_path / "a.bin").read_bytes() == payload


def test_download_one_skips_an_existing_file_that_matches_the_hash(tmp_path, payload_server,
                                                                   monkeypatch):
    import hashlib

    url, payload = payload_server
    sha = hashlib.sha256(payload).hexdigest()
    (tmp_path / "a.bin").write_bytes(payload)
    item = mf.PlannedFile("f1", PurePosixPath("a.bin"), len(payload), None, sha)
    monkeypatch.setattr(mf, "resolve_download_url",
                        lambda item, token=None, cookie=None: pytest.fail("must not re-download"))

    result = mf.download_one(item, tmp_path, overwrite=False, retries=1, timeout=5,
                             session_token=None)

    assert result.status == "skip"
    assert result.verified is True
    assert result.sha256 == sha

# --------------------------------------------------------------------------- acceleration

def test_download_one_with_several_connections(tmp_path, payload_server, monkeypatch):
    """The file is split into parts that arrive at the same time and the hash still matches."""
    import hashlib
    url, payload = payload_server
    sha = hashlib.sha256(payload).hexdigest()
    item = mf.PlannedFile("f1", PurePosixPath("a.bin"), len(payload), None, sha)
    monkeypatch.setattr(mf, "resolve_download_url", lambda item, token=None, cookie=None: url)

    result = mf.download_one(item, tmp_path, overwrite=True, retries=2, timeout=5,
                             session_token=None, connections=4)

    assert result.status == "ok"
    assert result.verified is True
    assert (tmp_path / "a.bin").read_bytes() == payload
    # nothing is left behind
    assert not (tmp_path / "a.bin.part").exists()
    assert not (tmp_path / "a.bin.part.chunks.json").exists()


def test_a_split_download_keeps_its_state_when_it_is_interrupted(tmp_path, monkeypatch):
    """When the transfer stops, the state of every part is stored so the next run continues."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    payload = b"0123456789" * 8192
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            rng = self.headers.get("Range")
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            body = payload[start:]
            if rng and stop.is_set():
                # a broken connection: only half of the part arrives
                body = body[: len(body) // 2]
            self.send_response(206 if rng else 200)
            if rng:
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/file"
        item = mf.PlannedFile("f1", PurePosixPath("a.bin"), len(payload), None)
        monkeypatch.setattr(mf, "resolve_download_url",
                            lambda item, token=None, cookie=None: url)

        stop.set()
        result = mf.download_one(item, tmp_path, overwrite=True, retries=1, timeout=5,
                                 session_token=None, connections=2)
        assert result.status == "failed"

        state = tmp_path / "a.bin.part.chunks.json"
        assert state.is_file(), "the state of the parts has to be kept"
        data = json.loads(state.read_text(encoding="utf-8"))
        assert len(data["ranges"]) == 2
        assert data["done"]

        # the next run finishes the file
        stop.clear()
        result = mf.download_one(item, tmp_path, overwrite=True, retries=2, timeout=5,
                                 session_token=None, connections=2)
        assert result.status == "ok"
        assert (tmp_path / "a.bin").read_bytes() == payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_chunk_ranges_cover_the_file():
    ranges = mf._chunk_ranges(100, 3)
    assert ranges == [(0, 32), (33, 65), (66, 99)]
    assert sum(end - start + 1 for start, end in ranges) == 100

