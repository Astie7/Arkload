"""A real MediaFire download through ArkLoad's own engine.

The MediaFire downloads are not handed over to the JDownloader engine: they run on ArkLoad's own
engine, which verifies every file against the SHA-256 MediaFire publishes.  This test proves
that the routing and the verification still work.

    pytest -q tests/test_arkload_mediafire_live.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.download_engine import DownloadEngine, EngineOptions  # noqa: E402

#: a public MediaFire folder with five files
FOLDER = "https://www.mediafire.com/folder/dpqhf3ughd4j5/libs"


@pytest.fixture
def engine(tmp_path):
    manager = DownloadEngine(state_dir=tmp_path / "state",
                             options=EngineOptions(destination=str(tmp_path / "downloads")))
    yield manager
    manager.shutdown()


def test_the_mediafire_links_run_on_the_arkload_engine(engine):
    """The link is not handed over to the JDownloader engine."""
    assert engine.engine_of(FOLDER) == "arkload"


def test_a_real_mediafire_folder_is_downloaded_and_verified(engine):
    record = engine.add_download(FOLDER, destination=engine.options.destination)
    assert record.backend == "arkload"

    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(2)
        engine._drain()
        if record.state.value in ("complete", "incomplete", "failed"):
            break

    assert record.state.value == "complete", f"the download failed: {record.note}"
    assert record.total_files == 5
    assert record.verified_files == 5
    for item in record.files:
        assert item.status == "complete"
        # every file was checked against the hash MediaFire published
        assert item.verified_against_source is True
    on_disk = [path for path in Path(record.root).rglob("*") if path.is_file()]
    assert len(on_disk) == 5
