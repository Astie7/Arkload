"""Offline tests for the Tkinter GUI's pure helpers (no window is opened).

    uv run --with pytest pytest -q tests/test_mediafire_gui.py

On Linux CI without Tkinter these tests skip rather than fail.
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("tkinter")

from tools import mediafire_gui as gui  # noqa: E402


def test_parse_patterns_splits_commas_semicolons_and_newlines():
    assert gui.parse_patterns("*.zip, *.jar; mods/*\nREADME*") == [
        "*.zip", "*.jar", "mods/*", "README*"]


def test_parse_patterns_ignores_blank_input():
    assert gui.parse_patterns(" , ; \n ") == []


@pytest.mark.parametrize("rel,patterns,expected", [
    ("libs/H2DB.jar", ["*.jar"], True),
    ("libs/H2DB.jar", ["h2db.jar"], True),
    ("libs/H2DB.jar", ["libs/*"], True),
    ("libs/H2DB.jar", ["mods/*", "*.zip"], False),
    ("mods/textures/stone.png", ["mods/*"], True),
    ("mods/textures/stone.png", ["*.png"], True),
    ("mods/textures/stone.png", [], True),
])
def test_matches_patterns(rel, patterns, expected):
    assert gui.matches_patterns(PurePosixPath(rel), patterns) is expected


@pytest.mark.parametrize("bytes_per_second,expected", [
    (0, "0.00 Mbps"),
    (12_500, "100 Kbps"),
    (125, "1 Kbps"),
    (5, "40 bps"),
    (125_000, "1.00 Mbps"),
    (416_250, "3.33 Mbps"),
])
def test_fmt_speed(bytes_per_second, expected):
    assert gui.fmt_speed(bytes_per_second) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "<10s"),
    (9.9, "<10s"),
    (45, "45s"),
    (125, "2m 05s"),
    (3725, "1h 02m"),
])
def test_fmt_duration(seconds, expected):
    assert gui.fmt_duration(seconds) == expected


def test_speed_meter_uses_a_rolling_window():
    meter = gui.SpeedMeter(window=3.0)
    assert meter.update(0, now=0.0) == 0.0
    assert meter.update(1_000, now=1.0) == 1_000.0
    assert meter.update(3_000, now=2.0) == 1_500.0
    # Samples older than the window are dropped; the two remaining samples
    # give (4000 - 3000) / (5 - 2) = 333.33 B/s.
    assert meter.update(4_000, now=5.0) == pytest.approx(333.333, abs=0.01)
    meter.reset()
    assert meter.update(10_000, now=10.0) == 0.0


# --------------------------------------------------------------------------- list model

@pytest.mark.parametrize("name,group", [
    ("mod.jar", "Archives"),
    ("pack.zip", "Archives"),
    ("texture.png", "Images"),
    ("music.ogg", "Audio"),
    ("clip.mp4", "Video"),
    ("readme.txt", "Text"),
    ("mystery.xyz", "Other"),
])
def test_file_group_maps_extensions_to_the_filter(name, group):
    assert gui.file_group(name) == group


def test_rounded_rect_points_close_the_shape():
    points = gui.rounded_rect_points(0, 0, 100, 50, 10)
    assert points[0] == points[-2] and points[1] == points[-1]
    assert len(points) > 16


def test_download_model_reports_status_and_progress():
    download = gui.Download("job1", "https://www.mediafire.com/folder/abc", "C:/mods",
                             "abc", "folder", "abc")
    assert download.status_label() == "Queued"
    assert download.status_tag() == "waiting"

    download.files = [gui.rs.FileRecord(path="a.jar", size=100),
                      gui.rs.FileRecord(path="b.jar", size=100)]
    download.files[0].status = gui.rs.FILE_COMPLETE
    assert download.verified == 1
    assert download.percent() == 50

    download.status = gui.rs.JOB_COMPLETE
    assert download.status_label() == "Completed"
    assert download.status_tag() == "done"

    download.status = gui.rs.JOB_INCOMPLETE
    assert download.status_label() == "Incomplete"
    assert download.status_tag() == "failed"
