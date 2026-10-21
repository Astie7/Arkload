"""Offline tests for the persistent manifest, status detector and integrity verifier.

    uv run --with pytest pytest -q tests/test_mediafire_resume.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import mediafire_dl as dl  # noqa: E402
from tools import mediafire_resume as rs  # noqa: E402


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Keep every manifest inside the test's temporary folder."""
    monkeypatch.setattr(rs, "STATE_DIR", tmp_path / "jobs")
    return tmp_path / "jobs"


def make_plan(files):
    """A tiny stand-in for dl.Plan."""
    return dl.Plan("folder", "mods", list(files), 0)


def planned(name, size, sha=None, quickkey="q"):
    return dl.PlannedFile(quickkey, PurePosixPath(name), size, None, sha)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- identity

def test_job_identity_is_stable_and_destination_specific():
    first = rs.job_identity("folder", "abc", r"C:\Games\Mods", True)
    second = rs.job_identity("folder", "abc", r"C:\Games\Mods", True)
    other = rs.job_identity("folder", "abc", r"C:\Games\Other", True)
    flat = rs.job_identity("folder", "abc", r"C:\Games\Mods", False)

    assert first == second
    assert first != other
    assert first != flat


def test_create_job_never_stores_cookies(state_dir):
    job = rs.create_job("https://www.mediafire.com/folder/abc", "folder", "abc",
                         r"C:\Games\Mods", {"subfolder": True, "cookies": "ukey=secret"})

    assert job.options.get("cookies") is None
    assert job.options["cookies_used"] is True
    stored = json.loads(rs.state_path(job.job_id).read_text(encoding="utf-8"))
    assert "secret" not in json.dumps(stored)


# --------------------------------------------------------------------------- storage

def test_job_survives_a_save_and_load_round_trip(state_dir):
    job = rs.create_job("url", "folder", "abc", "/tmp/x", {"subfolder": True})
    job.files = [rs.FileRecord(quickkey="q1", path="a.jar", size=10,
                               expected_sha256=digest(b"a"))]
    assert rs.save(job)

    loaded = rs.load_job(job.job_id)
    assert loaded is not None
    assert loaded.files[0].path == "a.jar"
    assert loaded.files[0].expected_sha256 == digest(b"a")
    assert [j.job_id for j in rs.list_jobs()] == [job.job_id]


def test_unreadable_manifests_are_skipped(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "broken.json").write_text("{not json", encoding="utf-8")
    rs.create_job("url", "folder", "abc", "/tmp/x", {"subfolder": True})

    assert len(rs.list_jobs()) == 1


# --------------------------------------------------------------------------- detection

def test_scan_classifies_missing_incomplete_and_complete(tmp_path):
    payload = b"0123456789" * 10
    (tmp_path / "good.bin").write_bytes(payload)
    (tmp_path / "half.bin.part").write_bytes(payload[:20])
    plan = make_plan([
        planned("good.bin", len(payload), digest(payload)),
        planned("half.bin", len(payload), digest(payload)),
        planned("gone.bin", len(payload), digest(payload)),
    ])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)

    rs.scan(job, tmp_path)

    assert job.files[0].status == rs.FILE_COMPLETE
    assert job.files[1].status == rs.FILE_INCOMPLETE
    assert job.files[2].status == rs.FILE_MISSING
    assert rs.summary(job)["verified"] == 1
    assert not rs.all_complete(job)


def test_scan_flags_a_corrupted_file_as_verification_failed(tmp_path):
    payload = b"real content" * 10
    (tmp_path / "mod.jar").write_bytes(b"x" * len(payload))
    plan = make_plan([planned("mod.jar", len(payload), digest(payload))])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)

    rs.scan(job, tmp_path)

    assert job.files[0].status == rs.FILE_VERIFY_FAILED
    assert "sha256 mismatch" in job.files[0].error
    assert not rs.all_complete(job)


def test_scan_accepts_a_local_baseline_when_no_server_hash_exists(tmp_path):
    payload = b"no checksum here"
    (tmp_path / "mod.bin").write_bytes(payload)
    plan = make_plan([planned("mod.bin", len(payload))])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)

    rs.scan(job, tmp_path)
    assert job.files[0].status == rs.FILE_COMPLETE
    assert job.files[0].actual_sha256 == digest(payload)
    assert job.files[0].verified_against_source is False

    # A later run notices the file was changed underneath us (same size, other bytes).
    (tmp_path / "mod.bin").write_bytes(b"NO checksum here"[:len(payload)].ljust(len(payload), b"!"))
    rs.scan(job, tmp_path)
    assert job.files[0].status == rs.FILE_VERIFY_FAILED


def test_scan_reports_a_size_mismatch_as_incomplete(tmp_path):
    (tmp_path / "mod.bin").write_bytes(b"12345")
    plan = make_plan([planned("mod.bin", 99)])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)

    rs.scan(job, tmp_path)

    assert job.files[0].status == rs.FILE_INCOMPLETE
    assert "size mismatch" in job.files[0].error


# --------------------------------------------------------------------------- reconciliation

def test_reconcile_keeps_finished_files_and_flags_changed_ones(tmp_path):
    payload = b"same" * 10
    plan = make_plan([planned("mod.jar", len(payload), digest(payload))])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)
    job.files[0].status = rs.FILE_COMPLETE
    job.files[0].actual_sha256 = digest(payload)

    rs.reconcile(job, plan)
    assert job.files[0].status == rs.FILE_COMPLETE

    changed = make_plan([planned("mod.jar", len(payload), digest(b"other"))])
    rs.reconcile(job, changed)
    assert job.files[0].status == rs.FILE_VERIFY_FAILED


def test_reconcile_keeps_the_state_of_renamed_files():
    plan = make_plan([planned("old.jar", 10, digest(b"x"), quickkey="q1")])
    job = rs.create_job("url", "folder", "abc", "/tmp/x", {"subfolder": False})
    rs.reconcile(job, plan)
    job.files[0].status = rs.FILE_COMPLETE

    renamed = make_plan([planned("new.jar", 10, digest(b"x"), quickkey="q1")])
    rs.reconcile(job, renamed)

    assert job.files[0].path == "new.jar"
    assert job.files[0].status == rs.FILE_COMPLETE


# --------------------------------------------------------------------------- results

def test_record_result_marks_a_download_complete(tmp_path):
    payload = b"downloaded"
    plan = make_plan([planned("mod.jar", len(payload), digest(payload))])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)
    (tmp_path / "mod.jar").write_bytes(payload)

    result = dl.Result("ok", len(payload), 0.1, sha256=digest(payload), verified=True)
    record = rs.record_result(job, 1, result, tmp_path)

    assert record.status == rs.FILE_COMPLETE
    assert record.verified_against_source is True
    assert record.attempts == 1
    assert rs.all_complete(job)


def test_record_result_keeps_a_failed_file_failed(tmp_path):
    plan = make_plan([planned("mod.jar", 10)])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)

    record = rs.record_result(job, 1, dl.Result("failed", error="404 not found"), tmp_path)

    assert record.status == rs.FILE_FAILED
    assert record.error == "404 not found"
    assert not rs.all_complete(job)


def test_record_result_reports_a_cancelled_partial_download_as_incomplete(tmp_path):
    (tmp_path / "mod.jar.part").write_bytes(b"1234")
    plan = make_plan([planned("mod.jar", 10)])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)

    record = rs.record_result(job, 1, dl.Result("cancelled"), tmp_path)

    assert record.status == rs.FILE_INCOMPLETE
    assert record.bytes == 4


def test_normalize_interrupted_turns_downloading_into_incomplete(tmp_path):
    (tmp_path / "mod.jar.part").write_bytes(b"12345")
    plan = make_plan([planned("mod.jar", 10), planned("other.jar", 10)])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)
    job.files[0].status = rs.FILE_DOWNLOADING
    job.files[1].status = rs.FILE_COMPLETE

    rs.normalize_interrupted(job, tmp_path)

    assert job.files[0].status == rs.FILE_INCOMPLETE
    assert job.files[0].bytes == 5
    assert job.files[1].status == rs.FILE_COMPLETE


# --------------------------------------------------------------------------- validation

def test_validate_keeps_untouched_files_and_catches_changed_ones(tmp_path):
    payload = b"stable content" * 4
    (tmp_path / "mod.bin").write_bytes(payload)
    plan = make_plan([planned("mod.bin", len(payload), digest(payload))])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)
    rs.scan(job, tmp_path)
    assert job.files[0].status == rs.FILE_COMPLETE

    changed = rs.validate(job, tmp_path)
    assert changed == []          # nothing moved since we hashed it
    assert job.files[0].status == rs.FILE_COMPLETE

    (tmp_path / "mod.bin").write_bytes(b"stable content" * 4 + b"")
    (tmp_path / "mod.bin").write_bytes(b"STABLE content" * 4)
    changed = rs.validate(job, tmp_path)
    assert len(changed) == 1
    assert job.files[0].status == rs.FILE_VERIFY_FAILED


def test_validate_notices_a_deleted_file(tmp_path):
    payload = b"gone soon"
    (tmp_path / "mod.bin").write_bytes(payload)
    plan = make_plan([planned("mod.bin", len(payload), digest(payload))])
    job = rs.create_job("url", "folder", "abc", str(tmp_path), {"subfolder": False})
    rs.reconcile(job, plan)
    rs.scan(job, tmp_path)

    (tmp_path / "mod.bin").unlink()
    rs.validate(job, tmp_path)

    assert job.files[0].status == rs.FILE_MISSING


# --------------------------------------------------------------------------- filters

@pytest.mark.parametrize("path,patterns,expected", [
    ("mods/a.jar", ["*.jar"], True),
    ("mods/a.jar", ["*.zip"], False),
    ("mods/textures/x.png", ["mods/*"], True),
    ("mods/textures/x.png", [], True),
])
def test_matches_patterns(path, patterns, expected):
    assert rs.matches_patterns(PurePosixPath(path), patterns) is expected
