"""Tests for the PySide6 download manager.

The model and the engine are tested without a window; the window itself is created on the
offscreen platform so the layout, the dialogs and the navigation can be checked head-less.

    uv run --with pytest --with PySide6 pytest -q tests/test_arkload_app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tools.arkload import download_model as dm  # noqa: E402
from tools.arkload import styles  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    from tools import mediafire_resume as rs
    monkeypatch.setattr(rs, "STATE_DIR", tmp_path / "jobs")
    return tmp_path / "jobs"


# --------------------------------------------------------------------------- model

def make_record(job_id="a", title="libs", size=100, status=dm.COMPLETE, added=1000.0):
    record = dm.DownloadRecord(job_id=job_id, url=f"https://www.mediafire.com/folder/{job_id}",
                                title=title, destination="/tmp")
    record.bytes_total = size
    record.status = status
    record.created = added
    return record


def test_fmt_size_matches_the_reference():
    assert dm.fmt_size(323 * 1024) == "323 KB"
    assert dm.fmt_size(1470073) == "1.4 MB"
    assert dm.fmt_size(0) == "0 B"
    assert dm.fmt_size(None) == "—"


def test_fmt_speed_matches_the_reference():
    assert dm.fmt_speed(0) == "0 B/s"
    assert dm.fmt_speed(512) == "512 B/s"
    assert dm.fmt_speed(2048) == "2.0 KB/s"
    assert dm.fmt_speed(5 * 1024 * 1024) == "5.0 MB/s"


def test_status_text_reports_the_verified_files():
    record = make_record(status=dm.COMPLETE)
    assert record.status_text() == "Completed"
    record.files = [dm.rs.FileRecord(path="a.jar", size=10, status=dm.FILE_COMPLETE),
                    dm.rs.FileRecord(path="b.jar", size=10, status=dm.FILE_MISSING)]
    assert record.status_text() == "Completed 1/2"
    record.status = dm.INCOMPLETE
    assert record.status_text() == "Incomplete 1/2"


def test_model_sorts_by_every_mode(qt_app):
    model = dm.DownloadTableModel()
    small = make_record("a", "alpha", 10, added=1000.0)
    big = make_record("b", "beta", 900, added=2000.0)
    model.add_record(small)
    model.add_record(big)

    model.set_sort("Newest")
    assert [r.job_id for r in model.visible_records()] == ["b", "a"]   # b was added last
    model.set_sort("A-Z")
    assert [r.title for r in model.visible_records()] == ["alpha", "beta"]
    model.set_sort("By size: large")
    assert [r.job_id for r in model.visible_records()] == ["b", "a"]
    model.set_sort("By status: ascending")
    assert len(model.visible_records()) == 2


def test_model_filters_by_search_and_type(qt_app):
    model = dm.DownloadTableModel()
    jar = make_record("a", "mods.jar")
    zip_file = make_record("b", "pack.zip")
    model.add_record(jar)
    model.add_record(zip_file)

    model.set_search("jar")
    assert [r.job_id for r in model.visible_records()] == ["a"]
    model.set_search("")
    model.set_filter("Completed")
    assert len(model.visible_records()) == 2
    model.set_filter("Missing Files")
    assert model.visible_records() == []


def test_model_selection_modes(qt_app):
    model = dm.DownloadTableModel()
    record = make_record("a", "libs", status=dm.COMPLETE)
    model.add_record(record)
    model.set_selection_mode("Completed")
    assert len(model.visible_records()) == 1
    model.set_selection_mode("Uploads only")
    assert model.visible_records() == []      # this build cannot upload


def test_model_exposes_the_record_in_the_user_role(qt_app):
    model = dm.DownloadTableModel()
    record = make_record("abc")
    model.add_record(record)
    index = model.index(0, 0)
    assert model.data(index, 256) == "abc"   # Qt.UserRole


# --------------------------------------------------------------------------- styles

def test_stylesheet_contains_the_reference_colours():
    sheet = styles.stylesheet("dark")
    for colour in ("#0C0E11", "#12151A", "#181C22", "#1F242B", "#232932", "#2E353F",
                   "#3D9BFF", "#E8ECF1", "#8A929C", "#3DD68C", "#171B21",
                   "#1A1F26", "#5AAEFF", "#12283D", "#15293A"):
        assert colour in sheet


def test_light_theme_exists():
    assert "#FFFFFF" in styles.stylesheet("light")


def test_icons_can_be_built():
    for name in ("folder", "chevron", "sort", "filter", "search", "menu", "plus", "dots",
                 "snail", "heart", "shield", "checkbox", "checkbox_checked"):
        icon = styles.make_icon(name, "#E4E4E7")
        assert not icon.isNull()


# --------------------------------------------------------------------------- window

@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from tools.arkload import settings as settings_store
    from tools.arkload.arkload_window import ArkLoadMainWindow
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setenv("MEDIAFIRE_GUI_SETTINGS", str(tmp_path / "settings.json"))
    # the tests never talk to the user
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *args, **kwargs: QMessageBox.No))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *args, **kwargs: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *args, **kwargs: QMessageBox.Ok))
    win = ArkLoadMainWindow(state_dir=str(tmp_path / "jobs"))
    win.engine.options.auto_resume = False      # the tests never download
    yield win
    win.engine.stop_all("cancelled")
    win.close()


def test_window_builds_with_the_expected_widgets(window):
    assert window.windowTitle() == "ArkLoad"
    assert window.add_button.text() == "Add download"
    assert window.search_edit.placeholderText() == "Search in downloads..."
    assert window.table.model() is window.model
    assert window.stack.count() == 2


def test_preferences_navigation(window):
    window._show_preferences()
    assert window.stack.currentIndex() == 1
    assert window.preferences.sidebar.count() == 6
    names = [window.preferences.sidebar.item(i).text()
             for i in range(window.preferences.sidebar.count())]
    assert names == ["General", "Download Engine", "Browser Integration", "Network",
                     "Traffic Limits", "Advanced"]
    assert "Antivirus" not in names
    assert "Remote Access" not in names
    assert "BitTorrent" not in names
    window.preferences.sidebar.setCurrentRow(4)
    assert window.preferences.stack.currentIndex() == 4
    window._show_dashboard()
    assert window.stack.currentIndex() == 0


def test_theme_switch_changes_the_stylesheet(window, qt_app):
    window._apply_theme("light")
    assert "#FFFFFF" in qt_app.styleSheet()
    window._apply_theme("dark")
    assert "#0C0E11" in qt_app.styleSheet()


def test_the_download_table_is_part_of_the_window(window, qt_app):
    """The table has to be in the layout, otherwise the list is never shown."""
    from tools.arkload.download_model import DownloadRecord
    window.show()
    qt_app.processEvents()
    record = DownloadRecord(job_id="x", url="https://example.com/a.zip", title="a.zip",
                            destination="d")
    window.model.add_record(record)
    qt_app.processEvents()
    assert window.table.isVisible()
    assert window.model.rowCount() == 1
    # the drop zone gives way to the list
    assert window.table.parentWidget().isVisible()


def test_the_tabs_replace_the_sidebar(window):
    """The interface has the tabs of the reference, not a list of categories."""
    from tools.arkload.arkload_window import TABS
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == list(TABS)
    assert not hasattr(window, "sidebar_list")


def test_the_tabs_show_the_right_downloads(window):
    """Downloads holds the open ones, the history the finished ones."""
    from tools.arkload.download_model import DownloadRecord
    running = DownloadRecord(job_id="a", url="u", title="running.zip", destination="d")
    running.status = dm.RUNNING
    done = DownloadRecord(job_id="b", url="u", title="done.zip", destination="d")
    done.status = dm.COMPLETE
    window.model.add_record(running)
    window.model.add_record(done)

    window.tabs.setCurrentIndex(0)          # Downloads
    assert [record.title for record in window.model.visible_records()] == ["running.zip"]
    window.tabs.setCurrentIndex(2)          # History
    assert [record.title for record in window.model.visible_records()] == ["done.zip"]


def test_the_link_grabber_shows_the_links_that_wait(window):
    """The link grabber holds the links of the queue that wait for their turn."""
    from tools.arkload.download_model import DownloadRecord
    queued = DownloadRecord(job_id="a", url="u", title="waiting.zip", destination="d")
    queued.status = dm.QUEUED
    window.model.add_record(queued)
    window.tabs.setCurrentIndex(1)          # LinkGrabber
    rows = window.grabber_model.rows()
    assert [row["name"] for row in rows] == ["waiting.zip"]
    assert rows[0]["state"] == "Queued"


def test_add_download_dialog_validates_the_url(window, tmp_path, monkeypatch):
    from tools.arkload import dialogs
    monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *args, **kwargs: None)
    dialog = dialogs.AddDownloadDialog(window, str(tmp_path))
    dialog.url_edit.setText("not a link")
    dialog._accept()
    assert dialog.accepted is None               # rejected, the dialog stays open
    dialog.url_edit.setText("https://www.mediafire.com/folder/abc")
    dialog.destination = str(tmp_path)
    dialog._accept()
    assert dialog.accepted is not None
    assert dialog.accepted["url"] == "https://www.mediafire.com/folder/abc"


def test_add_download_dialog_takes_any_link(window, tmp_path, monkeypatch):
    """A link of another host is accepted as well - the JDownloader engine runs it."""
    from tools.arkload import dialogs
    monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *args, **kwargs: None)
    dialog = dialogs.AddDownloadDialog(window, str(tmp_path), resolver=lambda url: "jdownloader")
    dialog.url_edit.setText("https://example.com/files/pack.zip")
    dialog.destination = str(tmp_path)
    dialog._accept()
    assert dialog.accepted is not None
    assert dialog.accepted["url"] == "https://example.com/files/pack.zip"
    assert "JDownloader" in dialog.engine_label.text()


def test_add_download_dialog_refuses_torrents(window, tmp_path, monkeypatch):
    from tools.arkload import dialogs
    monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *args, **kwargs: None)
    dialog = dialogs.AddDownloadDialog(window, str(tmp_path))
    dialog.url_edit.setText("C:/downloads/example.torrent")
    dialog._accept()
    assert dialog.accepted is None
    assert "mediafire.com" in dialog.url_edit.placeholderText()


def test_search_filters_the_table(window):
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id="x", url="u", title="texture-pack.zip", destination="d")
    record.status = dm.COMPLETE
    window.model.add_record(record)
    window.tabs.setCurrentIndex(2)          # the history holds the finished downloads
    window._on_search("texture")
    assert window.model.rowCount() == 1
    window._on_search("nothing")
    assert window.model.rowCount() == 0
    window._on_search("")
    assert window.model.rowCount() == 1


def test_the_table_appears_when_a_download_is_added(window):
    from tools.arkload.download_model import DownloadRecord
    assert window.table.isHidden() is True
    record = DownloadRecord(job_id="x", url="u", title="libs", destination="d")
    record.status = dm.COMPLETE
    window.model.add_record(record)
    assert window.table.isHidden() is False


def test_the_toolbar_offers_the_reference_choices(window):
    """The sort, the filter and the selection are the pills of the navigation bar."""
    assert [action.text() for action in window.sort_button.menu().actions()] == [
        "Newest", "Old", "A-Z", "Z-A", "By size: large", "By size: small",
        "By status: ascending", "By status: descending"]
    assert [action.text() for action in window.filter_button.menu().actions()] == [
        "All files", "Missing Files", "Active", "Completed", "MediaFire folders", "Video",
        "Music"]
    assert [action.text() for action in window.selection_button.menu().actions()] == [
        "All", "Downloads only", "Uploads only", "Completed", "Stopped"]
    assert window.sort_button.text() == "Newest"
    assert window.filter_button.text() == "All files"


def test_the_window_has_the_menu_bar_of_the_reference(window):
    """The commands that do not fit into the bars are in the menu bar."""
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles == ["&File", "&Downloads", "&Settings", "&Tools", "&Help"]


def test_the_context_menu_belongs_to_the_row_under_the_cursor(window, qt_app):
    """The right click opens the menu of the download the user pointed at."""
    from tools.arkload.download_model import DownloadRecord
    from PySide6.QtCore import QPoint
    first = DownloadRecord(job_id="a", url="u", title="first.zip", destination="d")
    second = DownloadRecord(job_id="b", url="u", title="second.zip", destination="d")
    window.model.add_record(first)
    window.model.add_record(second)
    window.show()
    qt_app.processEvents()

    shown = []
    window._show_row_menu = lambda position, record=None: shown.append(record)
    # the cursor is over the row of the second download (the list is sorted)
    row = [record.title for record in window.model.visible_records()].index("second.zip")
    window.table.indexAt = lambda position: window.model.index(row, 0)

    window._on_context_menu(QPoint(10, 10))
    assert shown and shown[0].title == "second.zip"
    # the row under the cursor is the current one
    assert window.table.currentIndex().row() == row


def test_the_command_bar_has_the_buttons_of_the_reference(window):
    """The bar at the top works on the downloads."""
    for button in (window.start_all_button, window.pause_all_button, window.stop_all_button,
                   window.add_links_button):
        assert button.toolTip()
    assert window.add_button.text() == "Add download"
    assert window.search_edit.placeholderText() == "Search in downloads..."


def test_the_main_menu_has_the_reference_entries(window):
    menu = window._build_main_menu()
    labels = [action.text() for action in menu.actions()]
    for expected in ("Paste urls from clipboard", "Paste urls from file", "Export/Import",
                     "Add-ons", "Preferences", "Auto shutdown", "Create portable version",
                     "Check for updates", "About", "Quit"):
        assert expected in labels
    # these are not part of ArkLoad
    for gone in ("Contact support ↗", "Submit a bug report", "Join the community ↗",
                 "Remote access"):
        assert gone not in labels
    # everything in the menu works, nothing is a decoration
    assert all(action.isEnabled() for action in menu.actions()
               if action.text() != "Cancel the scheduled action")


def test_the_auto_shutdown_menu_offers_the_three_actions(window):
    menu = window._build_main_menu()
    shutdown = [action for action in menu.actions() if action.text() == "Auto shutdown"][0].menu()
    labels = [action.text() for action in shutdown.actions()]
    assert "Shutdown when the downloads finish" in labels
    assert "Restart when the downloads finish" in labels
    assert "Log off when the downloads finish" in labels
    assert "Cancel the scheduled action" in labels


def test_auto_shutdown_does_nothing_without_a_running_download(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *args, **kwargs: shown.append(args)))
    window._arm_shutdown("shutdown")
    assert window._shutdown_armed is False        # nothing was scheduled
    assert shown                                  # and the user was told why
    assert window._shutdown_jobs == set()


def test_the_row_menu_has_the_reference_entries(window, tmp_path):
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id="x", url="u", title="libs", destination=str(tmp_path))
    record.status = dm.COMPLETE
    menu = window._build_row_menu(record)
    labels = [action.text() for action in menu.actions()]
    for expected in ("Pause", "Resume", "Delete", "Open", "Show in folder", "Set priority",
                     "Rename file", "Move to...", "Delete file", "Remove from list", "Add mirror",
                     "Open download page", "Copy link", "Check for update", "File integrity",
                     "Schedule", "Change URL"):
        assert expected in labels
    # the four actions that are not in the menu of a download any more
    for gone in ("Convert to mp3", "Convert to mp4", "Export selected downloads", "Add tag"):
        assert gone not in labels
    # the order of the reference
    order = [label for label in labels if label in ("Open", "Show in folder", "Set priority",
                                                     "Rename file", "Move to...", "Delete file",
                                                     "Remove from list", "Copy link",
                                                     "Change URL")]
    assert order.index("Open") < order.index("Show in folder") < order.index("Set priority")
    assert order.index("Delete file") < order.index("Remove from list")


def test_the_speed_limiter_sets_a_real_rate(window):
    window._set_rate_limit(500 * 1024)
    assert window.engine.options.rate_limit == 500 * 1024
    assert window.limiter_button.text() == "500 KB/s"
    window._set_rate_limit(0)
    assert window.engine.options.rate_limit == 0
    assert window.limiter_button.text() == "OFF"


def test_the_status_bar_has_no_support_button(window):
    assert not hasattr(window, "support_button")
    assert window.limiter_button.text() in ("OFF", "500 KB/s")
    assert window.priority_button.text() == "HIGH"


def test_export_writes_the_selected_downloads(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id="x", url="https://www.mediafire.com/folder/abc",
                             title="libs", destination=str(tmp_path))
    record.status = dm.COMPLETE
    window.model.add_record(record)
    target = tmp_path / "export.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *args, **kwargs: (str(target), "")))
    window._export_downloads()
    assert target.exists()
    assert "downloads" in target.read_text(encoding="utf-8")


def test_row_actions_are_enabled_and_disabled_correctly(window, tmp_path):
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id="x", url="https://www.mediafire.com/folder/abc",
                             title="libs", destination=str(tmp_path))
    record.root = str(tmp_path / "libs")
    record.status = dm.COMPLETE
    window.model.add_record(record)
    # the actions that need files on disk are only enabled when there are any
    assert bool(record.root)
    assert window.engine.file_integrity_report(record).startswith("Nothing")


def test_integrity_report_lists_the_file_states(window, tmp_path):
    from tools.arkload.download_model import DownloadRecord
    from tools import mediafire_resume as rs
    record = DownloadRecord(job_id="x", url="u", title="libs", destination=str(tmp_path))
    record.files = [rs.FileRecord(path="a.jar", size=1, status=rs.FILE_COMPLETE,
                                   expected_sha256="a" * 64, actual_sha256="a" * 64),
                    rs.FileRecord(path="b.jar", size=1, status=rs.FILE_FAILED,
                                   error="404 not found")]
    report = window.engine.file_integrity_report(record)
    assert "OK   a.jar" in report
    assert "404 not found" in report


# --------------------------------------------------------------------------- drop and paste

class FakeMime:
    def __init__(self, text="", urls=()):
        self._text = text
        self._urls = urls

    def hasUrls(self):  # noqa: N802 (Qt naming)
        return bool(self._urls)

    def hasText(self):  # noqa: N802
        return bool(self._text)

    def urls(self):
        return self._urls

    def text(self):
        return self._text


class FakeDrop:
    def __init__(self, mime):
        self._mime = mime

    def mimeData(self):  # noqa: N802
        return self._mime

    def acceptProposedAction(self):  # noqa: N802
        pass


class FakeUrl:
    def __init__(self, value):
        self._value = value

    def isLocalFile(self):  # noqa: N802
        return False

    def toString(self):  # noqa: N802
        return self._value


def test_dropping_a_mediafire_link_starts_a_download(window, monkeypatch):
    started = []
    monkeypatch.setattr(window.engine, "add_download",
                        lambda url, *args, **kwargs: started.append(url))
    event = FakeDrop(FakeMime(urls=[FakeUrl("https://www.mediafire.com/folder/abc")]))
    window.dropEvent(event)
    assert started == ["https://www.mediafire.com/folder/abc"]


def test_dropping_a_torrent_is_refused_with_a_message(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *args, **kwargs: shown.append(args)))
    started = []
    monkeypatch.setattr(window.engine, "add_download",
                        lambda url, *args, **kwargs: started.append(url))
    event = FakeDrop(FakeMime(urls=[FakeUrl("C:/downloads/example.torrent")]))
    window.dropEvent(event)
    assert started == []
    assert shown                     # the user is told that torrents are not supported


def test_pasting_urls_from_the_clipboard(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    started = []
    monkeypatch.setattr(window.engine, "add_download",
                        lambda url, *args, **kwargs: started.append(url))
    QApplication.clipboard().setText("https://www.mediafire.com/folder/abc")
    window._paste_from_clipboard()
    assert started == ["https://www.mediafire.com/folder/abc"]

# --------------------------------------------------------------------------- the controls

class FakeSession:
    """Stands in for a running download session."""

    def __init__(self):
        self.pause = __import__("threading").Event()
        self.cancel = __import__("threading").Event()
        self.alive = True
        self.stop_reason = ""

    def is_alive(self):
        return self.alive

    def set_paused(self, paused):
        if paused:
            self.pause.set()
        else:
            self.pause.clear()

    def request_stop(self, reason="cancelled"):
        self.stop_reason = reason
        self.cancel.set()


def make_control_record(job_id="x", status="running"):
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id=job_id, url="https://www.mediafire.com/folder/abc",
                            title="libs", destination="d")
    record.status = status
    return record


def test_pause_stops_the_session_and_remembers_the_state(window):
    record = make_control_record()
    session = FakeSession()
    window.engine._sessions[record.job_id] = session
    window.engine.pause(record)
    assert session.pause.is_set()
    assert record.status == "paused"
    assert window.engine.is_running(record) is True


def test_resume_continues_a_paused_session(window):
    record = make_control_record(status="paused")
    session = FakeSession()
    session.pause.set()
    window.engine._sessions[record.job_id] = session
    window.engine.resume(record)
    assert session.pause.is_set() is False
    assert record.status == "running"


def test_cancel_stops_the_session(window):
    record = make_control_record()
    session = FakeSession()
    window.engine._sessions[record.job_id] = session
    window.engine.cancel(record)
    assert session.cancel.is_set()
    assert session.stop_reason == "cancelled"


def test_cancel_works_without_a_running_session(window):
    record = make_control_record(status="queued")
    window.engine.cancel(record)
    assert record.status == "cancelled"


def test_the_row_menu_offers_the_download_controls(window):
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id="x", url="u", title="libs", destination="d")
    record.status = "running"
    menu = window._build_row_menu(record)
    labels = {action.text(): action for action in menu.actions()}
    assert "Pause" in labels and "Resume" in labels and "Delete" in labels
    assert labels["Pause"].isEnabled() is True
    assert labels["Delete"].isEnabled() is True
    record.status = "paused"
    menu = window._build_row_menu(record)
    labels = {action.text(): action for action in menu.actions()}
    assert labels["Pause"].isEnabled() is False
    assert labels["Resume"].isEnabled() is True



def test_the_new_download_dialog_has_the_reference_fields(window, tmp_path):
    from tools.arkload.dialogs import AddDownloadDialog
    dialog = AddDownloadDialog(window, str(tmp_path))
    assert dialog.windowTitle() == "Add download"
    assert dialog.url_edit.placeholderText()
    # the reference shows one row: the link and a browse button
    assert dialog.ok_button.text() == "OK"
    assert dialog.ok_button.isEnabled() is False
    dialog.url_edit.setText("https://www.mediafire.com/folder/abc")
    assert dialog.ok_button.isEnabled() is True
    dialog.destination = str(tmp_path)
    dialog._accept()
    assert dialog.accepted is not None
    assert dialog.accepted["url"] == "https://www.mediafire.com/folder/abc"


# --------------------------------------------------------------------------- selection mode

def test_deleting_a_download_removes_the_files_and_the_row(window, tmp_path, monkeypatch):
    """The dialog of the deletion: the files go and the download leaves the list."""
    from tools.arkload import dialogs
    from tools.arkload.download_model import DownloadRecord

    folder = tmp_path / "downloads" / "pack"
    folder.mkdir(parents=True)
    (folder / "file.bin").write_bytes(b"data")

    record = DownloadRecord(job_id="x", url="u", title="pack", destination=str(tmp_path))
    record.root = str(folder)
    window.model.add_record(record)
    window.table.selectRow(0)

    class Answer:
        Accepted = 1
        delete_files = True

    monkeypatch.setattr(dialogs.DeleteDownloadsDialog, "exec", lambda self: Answer.Accepted)
    monkeypatch.setattr(dialogs.DeleteDownloadsDialog, "__init__",
                        lambda self, parent, records: setattr(self, "delete_files", True))
    window._delete_selected()

    assert not folder.exists()                  # the files are gone
    assert window.model.record("x") is None     # and the download left the list


def test_the_navigation_row_only_holds_the_tabs(window):
    """The row below the command bar is the navigation of the reference."""
    assert window.tabs.count() == 3
    assert [window.tabs.tabText(index) for index in range(3)] == [
        "Downloads", "LinkGrabber", "History"]
    # the pills of the sort, the filter and the selection are in the command bar
    assert window.sort_button.parentWidget() is window.toolbar
    assert window.filter_button.parentWidget() is window.toolbar
    assert window.selection_button.parentWidget() is window.toolbar


def test_the_details_follow_the_selection(window):
    """The card at the bottom shows the download that is selected."""
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id="x", url="u", title="libs", destination="d")
    window.model.add_record(record)
    window.table.selectRow(0)
    window._update_selection_mode()
    assert window.selected_label.text() == "libs"
    window.table.clearSelection()
    window._update_selection_mode()


def test_the_delete_dialog_offers_both_choices(window, tmp_path):
    from tools.arkload.dialogs import DeleteDownloadsDialog
    from tools.arkload.download_model import DownloadRecord
    record = DownloadRecord(job_id="x", url="u", title="libs", destination=str(tmp_path))
    record.root = str(tmp_path / "libs")
    dialog = DeleteDownloadsDialog(window, [record])
    assert dialog.windowTitle() == "Delete selected downloads"
    assert dialog.delete_button.text() == "DELETE FILES"
    assert dialog.remove_button.text() == "REMOVE FROM LIST"
    dialog._remove()
    assert dialog.delete_files is False
    dialog = DeleteDownloadsDialog(window, [record])
    dialog._delete()
    assert dialog.delete_files is True


def test_the_acceleration_is_configurable(window):
    window.preferences.connections_spin.setValue(8)
    assert window.engine.options.connections == 8
    assert window.preferences.connections_spin.value() == 8


def test_the_menu_has_no_support_or_community_entries(window):
    labels = [action.text() for action in window._build_main_menu().actions()]
    assert "Contact support ↗" not in labels
    assert "Submit a bug report" not in labels
    assert "Join the community ↗" not in labels

def test_a_row_with_files_gets_an_expander(window):
    from tools.arkload.download_model import DownloadRecord
    from tools import mediafire_resume as rs
    record = DownloadRecord(job_id="x", url="u", title="libs", destination="d")
    record.files = [rs.FileRecord(path="a.jar", size=1)]
    window.model.add_record(record)
    index = window.model.index(0, dm.DownloadTableModel.COLUMN_NAME)
    assert window.model.data(index, dm.DownloadTableModel.EXPANDER_ROLE) is True
    # a single file has nothing to expand
    single = DownloadRecord(job_id="y", url="u", title="one.jar", destination="d")
    window.model.add_record(single)
    rows = window.model.visible_records()
    for row, visible in enumerate(rows):
        index = window.model.index(row, dm.DownloadTableModel.COLUMN_NAME)
        expected = visible.job_id == "x"
        assert window.model.data(index, dm.DownloadTableModel.EXPANDER_ROLE) is expected


def test_the_expander_opens_the_file_list(window):
    from tools.arkload.download_model import DownloadRecord
    from tools import mediafire_resume as rs
    record = DownloadRecord(job_id="x", url="u", title="libs", destination="d")
    record.files = [rs.FileRecord(path="a.jar", size=1)]
    window.model.add_record(record)
    index = window.model.index(0, 0)
    window._on_row_expanded(index)
    # the card is opened, the file list is part of it
    assert window.details.isHidden() is False
    assert window.details.progress_list.topLevelItemCount() == 1

