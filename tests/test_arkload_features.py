"""Tests for the feature modules of ArkLoad: scanner, converter, add-ons, remote, portable, extension.

    uv run --with pytest --with PySide6 pytest -q tests/test_arkload_features.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("PySide6")

from tools import mediafire_dl as dl  # noqa: E402
from tools.arkload import addons as addons_mod  # noqa: E402
from tools.arkload import browser_extension, converter, portable  # noqa: E402


# --------------------------------------------------------------------------- converter

def test_converter_reports_a_missing_ffmpeg(tmp_path):
    source = tmp_path / "song.wav"
    source.write_bytes(b"RIFF")
    result = converter.convert(source, tmp_path / "song.mp3", "mp3", custom="")
    if not converter.find_ffmpeg():
        assert result.ok is False
        assert "ffmpeg" in result.message


def test_converter_uses_a_configured_ffmpeg(tmp_path):
    stub = tmp_path / "ffmpeg.cmd"
    stub.write_text("@echo off\r\necho %* > \"%~dp0args.txt\"\r\n"
                    "copy /Y \"%~dp0in.bin\" \"%~dp0out.mp3\" >nul\r\nexit /b 0\r\n",
                    encoding="utf-8")
    source = tmp_path / "in.bin"
    source.write_bytes(b"audio")
    target = tmp_path / "out.mp3"
    result = converter.convert(source, target, "mp3", custom=str(stub))
    assert result.ok is True
    assert (tmp_path / "args.txt").read_text(encoding="utf-8").count("-vn") == 1


def test_converter_knows_what_it_can_convert():
    assert converter.can_convert("movie.mp4") is True
    assert converter.can_convert("song.mp3") is True
    assert converter.can_convert("mod.jar") is False


# --------------------------------------------------------------------------- add-ons

def make_addon(folder: Path, name: str = "Hello", body: str = "") -> Path:
    addon = folder / name.lower()
    addon.mkdir(parents=True, exist_ok=True)
    (addon / "addon.json").write_text(json.dumps(
        {"name": name, "version": "1.0", "description": "A test add-on", "entry": "main.py"}),
        encoding="utf-8")
    (addon / "main.py").write_text(body or (
        "def setup(api):\n"
        "    api.log('loaded')\n"
        "    api.register_hook('after_download', lambda record: api.log('done'))\n"),
        encoding="utf-8")
    return addon


def test_addons_are_discovered_and_loaded(tmp_path):
    make_addon(tmp_path)
    manager = addons_mod.AddonManager(tmp_path)
    found = manager.discover()
    assert [addon.name for addon in found] == ["Hello"]
    assert found[0].version == "1.0"

    found[0].enabled = True
    loaded = manager.load_enabled()
    assert [addon.name for addon in loaded] == ["Hello"]
    assert any("loaded" in message for message in manager.messages)


def test_addon_hooks_are_fired(tmp_path):
    make_addon(tmp_path)
    manager = addons_mod.AddonManager(tmp_path)
    manager.discover()[0].enabled = True
    manager.load_enabled()
    manager.fire("after_download", "record")
    assert any("done" in message for message in manager.messages)


def test_a_broken_addon_does_not_stop_the_application(tmp_path):
    make_addon(tmp_path, "Broken", "def setup(api):\n    raise RuntimeError('boom')\n")
    manager = addons_mod.AddonManager(tmp_path)
    addon = manager.discover()[0]
    addon.enabled = True
    assert manager.load_enabled() == []
    assert "boom" in addon.error


def test_addons_can_be_switched_off(tmp_path):
    make_addon(tmp_path)
    manager = addons_mod.AddonManager(tmp_path)
    addon = manager.discover()[0]
    addon.enabled = True
    manager.load_enabled()
    addon.enabled = False
    manager.reload(addon)
    assert manager.hooks["after_download"] == []


# --------------------------------------------------------------------------- extension

def test_the_browser_extension_is_written(tmp_path):
    folder = browser_extension.write_extension(tmp_path / "extension")
    names = sorted(item.name for item in folder.iterdir())
    assert "manifest.json" in names
    assert "background.js" in names
    assert "popup.html" in names
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"].startswith("ArkLoad")
    assert "background" in manifest


# --------------------------------------------------------------------------- portable

def test_the_portable_version_is_complete(tmp_path):
    target = portable.create_portable(tmp_path / "ArkLoad")
    assert (target / "ArkLoad.cmd").is_file()
    assert (target / "app" / "portable.marker").is_file()
    assert (target / "app" / "tools" / "arkload" / "arkload_window.py").is_file()
    assert (target / "app" / "tools" / "mediafire_dl.py").is_file()
    assert (target / "data").is_dir()
    assert "ArkLoad" in (target / "ArkLoad.cmd").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- limiter

def test_the_rate_limiter_slows_the_transfer_down(tmp_path, monkeypatch):
    """A limited transfer must not be faster than the limit."""
    payload = b"x" * 64 * 1024

    class Handler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/file"
        item = dl.PlannedFile("f1", __import__("pathlib", fromlist=["PurePosixPath"]).PurePosixPath("a.bin"),
                              len(payload), None)
        monkeypatch.setattr(dl, "resolve_download_url", lambda item, token=None, cookie=None: url)
        started = time.monotonic()
        result = dl.download_one(item, tmp_path, overwrite=True, retries=1, timeout=10,
                                 session_token=None, rate_limit=32 * 1024)
        elapsed = time.monotonic() - started
        assert result.status == "ok"
        # 64 KiB at 32 KiB/s takes about two seconds
        assert elapsed >= 1.0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
