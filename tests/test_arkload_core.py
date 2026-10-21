"""Tests for the core of ArkLoad: the task state machine and the queue.

    uv run --with pytest pytest -q tests/test_arkload_core.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.core import CATEGORIES, Task, TaskState, TaskStore  # noqa: E402


# --------------------------------------------------------------------------- the states

def test_every_state_has_a_label():
    for state in TaskState:
        assert state.label
        assert state.label[0].isupper()


def test_a_finished_state_does_not_move_on_its_own():
    assert TaskState.COMPLETED.finished is True
    assert TaskState.FAILED.finished is True
    assert TaskState.CANCELLED.finished is True
    assert TaskState.RUNNING.finished is False
    assert TaskState.QUEUED.finished is False
    assert TaskState.RUNNING.unfinished is True
    assert TaskState.COMPLETED.unfinished is False


def test_an_unknown_state_falls_back_to_queued():
    assert TaskState.parse("complete") is TaskState.COMPLETED
    assert TaskState.parse("nonsense") is TaskState.QUEUED
    assert TaskState.parse(TaskState.PAUSED) is TaskState.PAUSED


# --------------------------------------------------------------------------- the categories

def test_the_categories_are_backed_by_the_state():
    def task(state, **kwargs):
        return Task(job_id="x", url="u", state=state, **kwargs)

    running = task(TaskState.RUNNING)
    queued = task(TaskState.QUEUED)
    done = task(TaskState.COMPLETED)
    failed = task(TaskState.FAILED)

    assert running.matches_category("All downloads")
    assert running.matches_category("Downloading")
    assert not running.matches_category("Queued")
    assert queued.matches_category("Queued")
    assert done.matches_category("Completed")
    assert failed.matches_category("Failed")
    assert done.matches_category("History")
    assert not running.matches_category("History")


def test_the_category_list_is_complete():
    assert CATEGORIES == ("All downloads", "Downloading", "Queued", "Completed", "Failed",
                          "History", "Settings")


# --------------------------------------------------------------------------- the queue

def test_a_task_survives_a_save_and_a_load(tmp_path):
    store = TaskStore(tmp_path / "queue.json")
    task = Task(job_id="abc", url="https://www.mediafire.com/folder/abc", title="libs",
                destination=str(tmp_path), state=TaskState.QUEUED, priority="HIGH",
                tags=["mods"], mirrors=["https://www.mediafire.com/folder/other"])
    store.add(task)

    restored = TaskStore(tmp_path / "queue.json").load()
    assert len(restored) == 1
    assert restored[0].job_id == "abc"
    assert restored[0].title == "libs"
    assert restored[0].state is TaskState.QUEUED
    assert restored[0].tags == ["mods"]
    assert restored[0].mirrors == ["https://www.mediafire.com/folder/other"]


def test_the_queue_is_written_atomically(tmp_path):
    path = tmp_path / "queue.json"
    store = TaskStore(path)
    store.add(Task(job_id="a", url="u"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["tasks"][0]["job_id"] == "a"
    assert not path.with_name(path.name + ".tmp").exists()


def test_a_broken_queue_file_does_not_stop_the_application(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text("{not json", encoding="utf-8")
    store = TaskStore(path)
    assert store.load() == []


def test_the_queue_can_be_updated_and_removed(tmp_path):
    store = TaskStore(tmp_path / "queue.json")
    task = store.add(Task(job_id="a", url="u"))
    task.title = "renamed"
    store.update(task)
    assert TaskStore(tmp_path / "queue.json").load()[0].title == "renamed"

    assert store.remove("a") is True
    assert store.remove("a") is False
    assert TaskStore(tmp_path / "queue.json").load() == []


def test_the_queue_groups_by_state(tmp_path):
    store = TaskStore(tmp_path / "queue.json")
    store.add(Task(job_id="a", url="u", state=TaskState.RUNNING))
    store.add(Task(job_id="b", url="u", state=TaskState.COMPLETED))
    assert [task.job_id for task in store.by_state(TaskState.RUNNING)] == ["a"]
    assert len(store.unfinished()) == 1
    assert len(store) == 2


# --------------------------------------------------------------------------- the task

def test_a_task_knows_its_progress():
    task = Task(job_id="a", url="u", bytes_total=200, bytes_done=50)
    assert task.percent == 25
    assert task.active is True
    assert task.is_folder is True


def test_a_task_round_trips_through_a_dictionary():
    task = Task(job_id="a", url="u", title="libs", state=TaskState.PAUSED, priority="LOW",
                tags=["x"], start_after=123.0, bytes_total=10, bytes_done=5)
    again = Task.from_dict(task.to_dict())
    assert again.job_id == "a"
    assert again.state is TaskState.PAUSED
    assert again.priority == "LOW"
    assert again.tags == ["x"]
    assert again.start_after == 123.0
    assert again.bytes_done == 5


def test_the_status_text_describes_the_state():
    task = Task(job_id="a", url="u", state=TaskState.RUNNING, bytes_total=100, bytes_done=50)
    assert task.status_text() == "Downloading 50%"
    task.verifying = True
    assert task.status_text() == "Verifying"
    task.verifying = False
    task.state = TaskState.COMPLETED
    assert task.status_text() == "Completed"
    task.state = TaskState.QUEUED
    task.start_after = time.time() + 60
    assert task.status_text() == "Scheduled"
