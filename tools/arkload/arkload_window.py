"""The main window: toolbar, download list, drop zone, status bar and preferences.

``ArkLoadMainWindow`` is a plain ``QMainWindow`` – it can be imported and instantiated by any
application::

    from tools.arkload.arkload_window import ArkLoadMainWindow

    window = ArkLoadMainWindow()
    window.show()
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QKeySequence, QPainter,
                         QPalette, QPainterPath, QPen, QShortcut)
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QStackedWidget, QStyledItemDelegate,
                               QStyle, QSystemTrayIcon, QTableView, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

try:  # package import
    from .. import mediafire_resume as rs
except ImportError:  # direct script run
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mediafire_resume as rs  # type: ignore[no-redef]

from . import styles
from .core import TaskState
from .addons import AddonManager
from .ui import DropArea, OverviewBar, TabStrip
from .browser_extension import write_extension
from .converter import convert, find_ffmpeg
from .dialogs import (AboutDialog, AddDownloadDialog, AddonsDialog, ConvertDialog,
                       DeleteDownloadsDialog, MirrorDialog, MoveDialog, RenameDialog,
                       ScheduleDialog, TagsDialog, TextReportDialog, is_torrent)
from .portable import create_portable
from .download_engine import (DownloadEngine, EngineOptions, REPO_URL, open_in_file_manager,
                              open_url)
from .download_model import (DownloadRecord, DownloadTableModel, FILTER_MODES,
                             LinkGrabberModel, PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM,
                             RATE_PRESETS, RUNNING, SELECTION_MODES, SORT_MODES, fmt_added,
                             fmt_duration, fmt_rate, fmt_size, fmt_speed)
from .preferences_view import PreferencesView
from . import settings as settings_store
from .settings import default_destination, load_settings, save_settings

APP_NAME = "ArkLoad"
APP_SHORT_NAME = "ArkLoad"
VERSION = "1.0"

#: the tabs of the interface, the way JDownloader 2 shows them
TABS = ("Downloads", "LinkGrabber", "History")

#: the icon of every tab
TAB_ICONS = ("download", "link", "history")

#: what every tab shows (a category of the queue)
TAB_CATEGORIES = {"Downloads": "Active downloads", "LinkGrabber": "", "History": "History"}

#: the page every tab shows (the downloads and the history share the table)
TAB_PAGES = {"Downloads": 0, "LinkGrabber": 1, "History": 0}

#: the tab that shows the collected links
TAB_GRABBER = "LinkGrabber"
TORRENT_SUPPORTED = False          # this build does not download torrents


def engine_name(record) -> str:
    """The engine that runs a download, the way the details card shows it."""
    if getattr(record, "backend", "arkload") == "jdownloader":
        return "JDownloader 2"
    return "ArkLoad (MediaFire)"


class DownloadTableView(QTableView):
    """The download table, with a context menu and a per-row actions button."""

    context_requested = Signal(QPoint)
    actions_requested = Signal(QPoint)
    pause_requested = Signal()
    cancel_requested = Signal()
    delete_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.ExtendedSelection)
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setStretchLastSection(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and index.column() == DownloadTableModel.COLUMN_ACTIONS:
            self.actions_requested.emit(event.position().toPoint())
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Space pauses or resumes, Delete cancels the selected downloads."""
        key = event.key()
        if key in (Qt.Key_Space, Qt.Key_Pause):
            self.pause_requested.emit()
            return
        if key == Qt.Key_Delete:
            # the delete key takes the download out of the list, it does not cancel it
            self.delete_requested.emit()
            return
        if key == Qt.Key_Backspace:
            self.cancel_requested.emit()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """The menu of the row under the cursor, the way the file manager does it.

        The row under the cursor becomes the current one, so the menu always belongs to the
        download the user pointed at.  The event is accepted, so no other menu shows up.
        """
        position = event.pos()
        index = self.indexAt(position)
        if index.isValid():
            self.setCurrentIndex(index)
        event.accept()
        self.customContextMenuRequested.emit(position)
        self.context_requested.emit(event.globalPos())


class NameDelegate(QStyledItemDelegate):
    """Draws the name cell: the icon, the name and the facts under it."""

    expanded_requested = Signal(object)      # the model index of the row

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colours = dict(styles.DARK)

    def _expander_rect(self, option) -> QRectF:
        rect = option.rect.adjusted(8, 0, -6, 0)
        return QRectF(rect.left(), rect.top() + 15, 12, 12)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(self.colours.get("selection", "#20262E")))
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(8, 0, -6, 0)
        x = rect.left()
        if index.data(DownloadTableModel.EXPANDER_ROLE):
            box = self._expander_rect(option)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.colours.get("faint", "#5A626C")))
            path = QPainterPath()
            path.moveTo(box.left() + 3, box.top() + 2)
            path.lineTo(box.left() + 10, box.center().y())
            path.lineTo(box.left() + 3, box.bottom() - 2)
            path.closeSubpath()
            painter.drawPath(path)
            x = box.right() + 6
        icon = index.data(Qt.DecorationRole)
        if icon is not None:
            icon.paint(painter, QRect(int(x), option.rect.center().y() - 10, 20, 20))
            x += 26

        status = index.data(DownloadTableModel.STATUS_ROLE) or {}
        subtitle = str(status.get("subtitle") or "")

        # the name
        font = painter.font()
        font.setPixelSize(13)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(option.palette.color(QPalette.Text))
        painter.drawText(QRect(int(x), rect.top() + 6, rect.right() - int(x),
                               rect.height() // 2 + 4),
                         Qt.AlignLeft | Qt.AlignVCenter, str(index.data(Qt.DisplayRole) or ""))

        # the facts of the download under the name
        if subtitle:
            small = QFont(painter.font())
            small.setPixelSize(11)
            small.setWeight(QFont.Normal)
            painter.setFont(small)
            painter.setPen(QColor(self.colours.get("muted", "#8A929C")))
            painter.drawText(QRect(int(x), rect.top() + rect.height() // 2 - 4,
                                   rect.right() - int(x), rect.height() // 2),
                             Qt.AlignLeft | Qt.AlignVCenter, subtitle)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        """A click on the expander opens the files of that download."""
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if index.data(DownloadTableModel.EXPANDER_ROLE):
                box = self._expander_rect(option)
                if box.contains(event.pos()):
                    self.expanded_requested.emit(index)
                    return True
        return super().editorEvent(event, model, option, index)


class StatusDelegate(QStyledItemDelegate):
    """Draws the status cell: the progress bar and the percentage."""

    #: the row whose priority was clicked
    priority_requested = Signal(object)     # the model index of the row

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colours = dict(styles.DARK)

    def _bar_colour(self, data: dict) -> str:
        if data.get("failed"):
            return self.colours.get("danger", "#F04438")
        if data.get("complete"):
            return self.colours.get("success", "#3DD68C")
        if data.get("paused"):
            return self.colours.get("warning", "#F5A524")
        return self.colours.get("accent", "#3D9BFF")

    def _bar_width(self, rect) -> int:
        return max(48, min(150, rect.width() // 3))

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        data = index.data(DownloadTableModel.STATUS_ROLE) or {}
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(self.colours.get("selection", "#20262E")))
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(8, 0, -8, 0)
        text = str(data.get("text") or "")
        percent = int(data.get("percent") or 0)
        show_bar = data.get("active") or percent > 0

        if not show_bar:
            # nothing runs: the state of the download is enough
            font = painter.font()
            font.setPixelSize(13)
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(self.colours.get("accent", "#3D9BFF")))
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            painter.restore()
            return

        font = painter.font()
        metrics = QFontMetrics(font)
        percent_text = f"{percent}%"
        bar_width = self._bar_width(rect)
        text_width = metrics.horizontalAdvance(percent_text)
        bar_rect = QRectF(rect.left(), rect.center().y() - 3.5, bar_width, 7)

        # the track
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.colours.get("border", "#232932")))
        painter.drawRoundedRect(bar_rect, 3.5, 3.5)
        # the fill
        if percent > 0:
            filled = QRectF(bar_rect)
            filled.setWidth(max(4.0, bar_rect.width() * min(percent, 100) / 100.0))
            painter.setBrush(QColor(self._bar_colour(data)))
            painter.drawRoundedRect(filled, 3.5, 3.5)

        font.setPixelSize(12)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self._bar_colour(data)))
        painter.drawText(QRectF(bar_rect.right() + 8, rect.top(), text_width + 6, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, percent_text)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        """A click on the row opens the priority menu."""
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            data = index.data(DownloadTableModel.STATUS_ROLE) or {}
            if data.get("priority"):
                self.priority_requested.emit(index)
                return True
        return super().editorEvent(event, model, option, index)


class DetailsPanel(QWidget):
    """The details card at the bottom: the download, its facts and its files.

    It is collapsed by default and only opened with the arrow in the status bar or by clicking a
    download in the list.
    """

    priority_requested = Signal()

    #: the facts of the download, in the order they are shown
    FACTS = (("size", "Size"), ("speed", "Speed"), ("added", "Added"),
             ("engine", "Engine"), ("priority", "Priority"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DetailsCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)

        # -- the download: the name, the path and the progress
        head = QHBoxLayout()
        head.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.icon_label.setPixmap(styles.make_icon("folder", "#3D9BFF").pixmap(32, 32))
        head.addWidget(self.icon_label, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(6)

        self.title_label = QLabel("")
        self.title_label.setObjectName("Heading")
        column.addWidget(self.title_label)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self.path_label = QLabel("")
        self.path_label.setObjectName("Muted")
        self.path_label.setMaximumWidth(460)
        self.path_label.setMinimumWidth(180)
        path_row.addWidget(self.path_label)
        copy_button = QToolButton()
        copy_button.setIcon(styles.make_icon("clipboard", "#8A929C"))
        copy_button.setFixedSize(18, 18)
        copy_button.setToolTip("Copy the path")
        copy_button.clicked.connect(self._copy_path)
        path_row.addWidget(copy_button)
        path_row.addStretch(1)
        self.page_link = QLabel("Open the page")
        self.page_link.setObjectName("Link")
        self.page_link.setOpenExternalLinks(False)
        self.page_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        path_row.addWidget(self.page_link)
        column.addLayout(path_row)

        bar_row = QHBoxLayout()
        bar_row.setSpacing(10)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        bar_row.addWidget(self.progress_bar, 1)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("FactValue")
        bar_row.addWidget(self.percent_label)
        self.remaining_label = QLabel("")
        self.remaining_label.setObjectName("Muted")
        bar_row.addWidget(self.remaining_label)
        column.addLayout(bar_row)

        head.addLayout(column, 1)
        layout.addLayout(head)

        # -- the facts of the download, in one row
        facts = QHBoxLayout()
        facts.setSpacing(26)
        self.fact_labels: dict = {}
        for key, name in self.FACTS:
            pair = QHBoxLayout()
            pair.setSpacing(6)
            title = QLabel(name)
            title.setObjectName("FactKey")
            pair.addWidget(title)
            value = QLabel("—")
            value.setObjectName("FactValue")
            pair.addWidget(value)
            facts.addLayout(pair)
            self.fact_labels[key] = value
        facts.addStretch(1)
        layout.addLayout(facts)

        # -- the files of the download (only when there are some)
        self.files_caption = QLabel("Files")
        self.files_caption.setObjectName("SubHeading")
        self.files_caption.setVisible(False)
        layout.addWidget(self.files_caption)
        self.progress_list = QTreeWidget()
        self.progress_list.setHeaderLabels(["File", "Size", "State"])
        self.progress_list.setRootIsDecorated(False)
        self.progress_list.setColumnWidth(0, 420)
        self.progress_list.setColumnWidth(1, 90)
        self.progress_list.setColumnWidth(2, 120)
        self.progress_list.setFixedHeight(150)
        self.progress_list.setVisible(False)
        layout.addWidget(self.progress_list)

        self.close_button = QToolButton()
        self.close_button.setText("✕")
        self.close_button.setFixedSize(20, 20)
        self.close_button.setToolTip("Hide the details")
        self.close_button.clicked.connect(self._hide)
        layout.addWidget(self.close_button, 0, Qt.AlignRight | Qt.AlignTop)

    def _copy_path(self) -> None:
        QApplication.clipboard().setText(self.path_label.toolTip() or self.path_label.text())

    def _hide(self) -> None:
        self.setVisible(False)

    def show_record(self, record, options=None) -> None:
        """Fill the card with the details of one download."""
        if record is None:
            self.setVisible(False)
            return
        self.title_label.setText(record.title)
        self.path_label.setToolTip(str(record.root or record.destination))
        self.path_label.setText(self._elide(str(record.root or record.destination)))
        self.icon_label.setPixmap(styles.make_icon(
            "folder" if record.is_folder else "file", "#3D9BFF").pixmap(32, 32))

        percent = record.percent
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        remaining = self._remaining(record)
        self.remaining_label.setText(f"Remaining: {remaining}" if remaining else "")
        self.page_link.setText("Open the page")
        self.page_link.setToolTip(record.url)
        self.page_link.linkActivated.connect(open_url)

        # the facts of the download
        self.fact_labels["size"].setText(
            f"{fmt_size(record.bytes_done)} of {fmt_size(record.bytes_total)}"
            if record.bytes_total else "—")
        self.fact_labels["speed"].setText(fmt_speed(record.speed_down))
        self.fact_labels["added"].setText(fmt_added(record.added))
        self.fact_labels["engine"].setText(engine_name(record))
        self.fact_labels["priority"].setText(record.priority.lower())

        # the files: the list only shows up when the download has files
        self.progress_list.clear()
        for item in record.files:
            state = {
                "complete": "Verified", "missing": "Missing", "incomplete": "Incomplete",
                "verification_failed": "Verification failed", "failed": "Failed",
                "downloading": "Downloading", "pending": "Queued",
            }.get(item.status, item.status)
            if state == "Verified" and getattr(record, "backend", "arkload") != "arkload":
                # the JDownloader engine does not check a hash against the source
                state = "Downloaded"
            self.progress_list.addTopLevelItem(
                QTreeWidgetItem([item.path, fmt_size(item.size), state]))
        has_files = bool(record.files)
        self.progress_list.setVisible(has_files)
        self.files_caption.setVisible(has_files)

        options = options or {}
        self.connection_info = (
            f"{options.get('connections', 1)} connection(s) per file · "
            f"{options.get('jobs', 1)} file(s) in parallel · "
            f"{options.get('retries', 3)} attempt(s) per file · "
            f"limit {fmt_rate(int(options.get('rate_limit', 0) or 0))}")

    def _elide(self, text: str) -> str:
        """The path is shortened, so it never pushes the other things away."""
        width = max(180, min(460, self.path_label.width()) - 4)
        metrics = QFontMetrics(self.path_label.font())
        return metrics.elidedText(text, Qt.ElideMiddle, width)

    @staticmethod
    def _remaining(record) -> str:
        if not record.active or record.speed_down <= 0 or not record.bytes_total:
            return "-"
        left = max(0, record.bytes_total - record.bytes_done)
        return fmt_duration(left / record.speed_down)


class ArkLoadMainWindow(QMainWindow):
    """The main window of the ArkLoad download manager."""

    update_checked = Signal(str)
    source_checked = Signal(str)

    def __init__(self, parent: QWidget | None = None, state_dir: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(950, 640)
        self.setMinimumSize(820, 520)
        self.setAcceptDrops(True)

        self.settings = load_settings()
        self.theme = self.settings.get("theme") or ("dark" if self.settings.get("dark", True)
                                                   else "light")
        self.engine = DownloadEngine(state_dir=Path(state_dir) if state_dir else None,
                                     options=self._engine_options())
        self.model = DownloadTableModel(self)
        self.model.icon_provider = self._row_icon
        self.exit_when_done = False
        self._shutdown_armed = False
        self._shutdown_kind = ""
        self._shutdown_jobs: set[str] = set()
        self._current_page = 0
        self.addons = AddonManager(settings_store.addons_dir(), self)

        self._build()
        self._apply_theme(self.theme)
        # the first tab shows the downloads that still have work left
        self.model.set_category(TAB_CATEGORIES.get(TABS[0], ""))
        self.update_checked.connect(self._on_update_checked)
        self.source_checked.connect(self._on_source_checked)
        self._connect_engine()
        self._load_addons()
        QShortcut(QKeySequence.Paste, self, activated=self._paste_from_clipboard)
        self._restore_downloads()
        self._update_status_bar()
        QTimer.singleShot(400, self._auto_resume)
        QTimer.singleShot(1500, self._warm_up_engine)

    # ------------------------------------------------------------------ build

    def _engine_options(self) -> EngineOptions:
        settings = self.settings
        return EngineOptions(
            destination=settings.get("destination") or default_destination(),
            subfolder=bool(settings.get("subfolder", True)),
            overwrite=bool(settings.get("overwrite", False)),
            parallel=int(settings.get("jobs", 4) or 4),
            retries=int(settings.get("retries", 3) or 3),
            recovery_rounds=int(settings.get("recovery_rounds", 3) or 3),
            timeout=float(settings.get("timeout", 60) or 60),
            cookies=str(settings.get("cookies", "") or ""),
            slow_mode=bool(settings.get("slow_mode", False)),
            slow_pause=float(settings.get("slow_pause", 1.5) or 1.5),
            rate_limit=int(settings.get("rate_limit", 0) or 0),
            connections=int(settings.get("connections", 1) or 1),
            verify_hashes=bool(settings.get("verify_hashes", True)),
            auto_resume=bool(settings.get("auto_resume", True)),
            engine_port=int(settings.get("engine_port", 3128) or 3128),
            per_host=int(settings.get("per_host", 2) or 2),
        )

    def _build(self) -> None:
        self._setting_actions: dict = {}
        self._build_menu_bar()
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_dashboard())
        self.stack.addWidget(self._build_preferences())

        root.addWidget(self._build_status_bar())

    def _build_menu_bar(self) -> None:
        """The menu bar of JDownloader 2: the commands that do not fit into the bars."""
        bar = self.menuBar()
        bar.setNativeMenuBar(False)
        colours = styles.palette(self.theme)

        file_menu = bar.addMenu("&File")
        new_action = file_menu.addAction("&New download...")
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._add_download)
        file_menu.addSeparator()
        export_action = file_menu.addAction("&Export downloads...")
        export_action.triggered.connect(self._export_downloads)
        import_action = file_menu.addAction("&Import downloads...")
        import_action.triggered.connect(self._import_downloads)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("E&xit")
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)

        downloads_menu = bar.addMenu("&Downloads")
        start_action = downloads_menu.addAction("Start all")
        start_action.triggered.connect(self._start_all)
        pause_action = downloads_menu.addAction("Pause all")
        pause_action.triggered.connect(self._pause_all)
        stop_action = downloads_menu.addAction("Stop all")
        stop_action.triggered.connect(self._stop_all)
        downloads_menu.addSeparator()
        select_action = downloads_menu.addAction("Select all")
        select_action.triggered.connect(lambda: self._toggle_select_all(True))
        tags_action = downloads_menu.addAction("Tags...")
        tags_action.triggered.connect(self._edit_tags_of_the_selection)
        export_action = downloads_menu.addAction("Export the downloads...")
        export_action.triggered.connect(self._export_downloads)
        remove_action = downloads_menu.addAction("Remove the finished downloads")
        remove_action.triggered.connect(self._remove_finished)

        settings_menu = bar.addMenu("&Settings")

        preferences_action = settings_menu.addAction("&Preferences...")
        preferences_action.triggered.connect(self._show_preferences)
        settings_menu.addSeparator()

        # the shutdown, the three ways of it in one place
        shutdown_menu = settings_menu.addMenu("&Shutdown")
        for label, kind in (("Shutdown when the downloads finish", "shutdown"),
                            ("Restart when the downloads finish", "restart"),
                            ("Log off when the downloads finish", "logoff")):
            action = shutdown_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, value=kind:
                                     self._arm_shutdown(value))
        settings_menu.addSeparator()

        # the download settings of the reference
        self._add_setting_menu(settings_menu, "Max. chunks per download",
                               range(1, 17), self.engine.options.connections,
                               self._set_connections)
        self._add_setting_menu(settings_menu, "Max. simultaneous downloads",
                               range(1, 17), self.engine.options.parallel,
                               self._set_parallel)
        self._add_setting_menu(settings_menu, "Max. simultaneous downloads per hoster",
                               range(1, 17), self.engine.options.per_host,
                               self._set_per_host)
        self._add_setting_menu(settings_menu, "Speed limit",
                               [value for _label, value in RATE_PRESETS],
                               self.engine.options.rate_limit, self._set_rate_limit)

        tools_menu = bar.addMenu("&Tools")
        addons_action = tools_menu.addAction("&Add-ons...")
        addons_action.triggered.connect(self._show_addons)
        for _addon, label, callback in list(self.addons.menu_actions):
            action = tools_menu.addAction(label)
            action.triggered.connect(callback)
        tools_menu.addSeparator()
        update_action = tools_menu.addAction("Check for &updates")
        update_action.triggered.connect(self._check_for_updates)

        help_menu = bar.addMenu("&Help")
        about_action = help_menu.addAction("&About ArkLoad")
        about_action.triggered.connect(self._show_about)

        bar.setStyleSheet(
            f"QMenuBar {{ background-color: {colours['bar']};"
            f" border-bottom: 1px solid {colours['border_soft']}; }}"
            f"QMenuBar::item {{ padding: 5px 10px; border-radius: 5px; }}"
            f"QMenuBar::item:selected {{ background-color: {colours['hover']}; }}")

    # -- dashboard ------------------------------------------------------------
    def _add_setting_menu(self, menu: QMenu, title: str, values, current: int,
                          handler) -> None:
        """One setting in the menu, with the values the reference shows."""
        submenu = menu.addMenu(title)
        group = submenu.menuAction()
        actions = []
        for value in values:
            action = submenu.addAction(str(value))
            action.setCheckable(True)
            action.setChecked(int(value) == int(current))
            action.triggered.connect(lambda _checked=False, chosen=value: handler(chosen))
            actions.append(action)
        self._setting_actions[title] = actions

    def _set_connections(self, value: int) -> None:
        """The number of connections a file is fetched with."""
        self.engine.options.connections = int(value)
        self._after_setting_changed()
        self._on_engine_message(f"{value} connection(s) per file")

    def _set_parallel(self, value: int) -> None:
        """The number of files that are downloaded at the same time."""
        self.engine.options.parallel = int(value)
        self._after_setting_changed()
        self._on_engine_message(f"{value} file(s) in parallel")

    def _set_per_host(self, value: int) -> None:
        """The number of downloads that may run at the same time per host."""
        self.engine.options.per_host = int(value)
        self._after_setting_changed()
        self._on_engine_message(f"{value} download(s) at the same time per host")

    def _after_setting_changed(self) -> None:
        """Keep the settings, the menu and the preferences in step."""
        self._save_settings()
        self._update_setting_menus()

    def _update_setting_menus(self) -> None:
        """Show the value that is set in the menu."""
        values = {
            "Max. chunks per download": self.engine.options.connections,
            "Max. simultaneous downloads": self.engine.options.parallel,
            "Max. simultaneous downloads per hoster": self.engine.options.per_host,
            "Speed limit": self.engine.options.rate_limit,
        }
        for title, actions in getattr(self, "_setting_actions", {}).items():
            current = int(values.get(title, 0))
            for action, value in zip(actions, [int(a.text()) for a in actions]):
                action.setChecked(int(value) == current)

    def _build_dashboard(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_command_bar())
        layout.addWidget(self._build_navigation_bar())

        # the content of the tabs
        self.pages = QStackedWidget()

        outer = QVBoxLayout()
        outer.setContentsMargins(12, 10, 12, 10)

        # the list of the downloads
        area = QFrame()
        area.setObjectName("DownloadArea")
        area_layout = QVBoxLayout(area)
        area_layout.setContentsMargins(1, 1, 1, 1)
        area_layout.setSpacing(0)

        self.table = DownloadTableView()
        self.table.setModel(self.model)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.actions_requested.connect(self._on_actions_clicked)
        self.table.doubleClicked.connect(self._open_selected)
        self.table.pause_requested.connect(self._toggle_selected_pause)
        self.table.cancel_requested.connect(self._cancel_selected)
        self.table.delete_requested.connect(self._delete_selected)
        self.model.rowsInserted.connect(self._on_rows_changed)
        self.model.rowsRemoved.connect(self._on_rows_changed)
        self.model.modelReset.connect(self._on_rows_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(DownloadTableModel.COLUMN_INDEX, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_SIZE, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_STATUS, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_DOWN, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_UP, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_ETA, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_ADDED, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_ACTIONS, QHeaderView.Fixed)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_INDEX, 42)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_SIZE, 90)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_STATUS, 240)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_DOWN, 90)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_UP, 80)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_ETA, 84)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_ADDED, 118)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_ACTIONS, 40)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.name_delegate = NameDelegate(self.table)
        self.name_delegate.colours = dict(styles.palette(self.theme))
        self.name_delegate.expanded_requested.connect(self._on_row_expanded)
        self.table.setItemDelegateForColumn(DownloadTableModel.COLUMN_NAME, self.name_delegate)
        self.status_delegate = StatusDelegate(self.table)
        self.status_delegate.colours = dict(styles.palette(self.theme))
        self.status_delegate.priority_requested.connect(self._on_priority_clicked)
        self.table.setItemDelegateForColumn(DownloadTableModel.COLUMN_STATUS, self.status_delegate)
        area_layout.addWidget(self.table)

        self.drop_zone = DropArea()
        area_layout.addWidget(self.drop_zone, 1)
        self.pages.addWidget(area)

        # the link grabber of JDownloader 2
        self.grabber_model = LinkGrabberModel(self)
        self.grabber_table = QTableView()
        self.grabber_table.setObjectName("GrabberTable")
        self.grabber_table.setModel(self.grabber_model)
        self.grabber_table.setSelectionBehavior(QTableView.SelectRows)
        self.grabber_table.setEditTriggers(QTableView.NoEditTriggers)
        self.grabber_table.verticalHeader().setVisible(False)
        self.grabber_table.verticalHeader().setDefaultSectionSize(38)
        self.grabber_table.setShowGrid(False)
        self.grabber_table.setAlternatingRowColors(True)
        self.grabber_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.grabber_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.grabber_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.grabber_table.setColumnWidth(1, 110)
        self.grabber_table.setColumnWidth(2, 130)
        self.grabber_table.doubleClicked.connect(self._start_grabbed_link)
        self.grabber_table.setToolTip("The links that wait for their turn - a double click "
                                       "starts one of them.")
        grabber = QFrame()
        grabber.setObjectName("DownloadArea")
        grabber_layout = QVBoxLayout(grabber)
        grabber_layout.setContentsMargins(1, 1, 1, 1)
        grabber_layout.setSpacing(0)
        grabber_layout.addWidget(self.grabber_table)
        self.grabber_empty = QLabel("Nothing is waiting - every link is either running or done.")
        self.grabber_empty.setObjectName("Muted")
        self.grabber_empty.setAlignment(Qt.AlignCenter)
        self.grabber_empty.setVisible(False)
        grabber_layout.addWidget(self.grabber_empty, 1)

        # the input of JDownloader 2: a link can be pasted into the grabber
        add_row = QHBoxLayout()
        add_row.setContentsMargins(10, 8, 10, 8)
        add_row.setSpacing(8)
        add_label = QLabel("Add new links")
        add_label.setObjectName("Muted")
        add_row.addWidget(add_label)
        self.grabber_edit = QLineEdit()
        self.grabber_edit.setPlaceholderText("Paste a link here and press Enter.")
        self.grabber_edit.returnPressed.connect(self._add_grabbed_links)
        add_row.addWidget(self.grabber_edit, 1)
        grabber_layout.addLayout(add_row)
        self.pages.addWidget(grabber)

        outer.addWidget(self.pages, 1)

        self.details = DetailsPanel()
        self.details.priority_requested.connect(self._show_priority_menu)
        self.details.setVisible(False)        # collapsed, like the reference
        outer.addWidget(self.details)

        # the numbers of the whole list, the way JDownloader 2 shows them
        self.overview = OverviewBar()
        outer.addWidget(self.overview)

        layout.addLayout(outer)
        self.download_area = area
        return page

    # -- the head of the window ------------------------------------------------

    def _build_command_bar(self) -> QWidget:
        """The bar with the commands of JDownloader 2 and the actions of Free Download Manager.

        On the left are the buttons that work on the downloads (start, pause, stop, add), on the
        right the main action, the search field and the menu.
        """
        bar = QFrame()
        bar.setObjectName("Toolbar")
        self.toolbar = bar
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 7, 12, 7)
        row.setSpacing(6)

        self.start_all_button = QToolButton()
        self.start_all_button.setIcon(styles.make_icon("play",
                                                       styles.palette(self.theme)["text"]))
        self.start_all_button.setToolTip("Start every download that is waiting")
        self.start_all_button.clicked.connect(self._start_all)
        row.addWidget(self.start_all_button)

        self.pause_all_button = QToolButton()
        self.pause_all_button.setIcon(styles.make_icon("pause",
                                                       styles.palette(self.theme)["text"]))
        self.pause_all_button.setToolTip("Pause every download that is running")
        self.pause_all_button.clicked.connect(self._pause_all)
        row.addWidget(self.pause_all_button)

        self.stop_all_button = QToolButton()
        self.stop_all_button.setIcon(styles.make_icon("stop",
                                                      styles.palette(self.theme)["text"]))
        self.stop_all_button.setToolTip("Stop every download")
        self.stop_all_button.clicked.connect(self._stop_all)
        row.addWidget(self.stop_all_button)

        row.addWidget(self._bar_separator())

        self.add_links_button = QToolButton()
        self.add_links_button.setIcon(styles.make_icon("plus",
                                                       styles.palette(self.theme)["text"]))
        self.add_links_button.setToolTip("Add a link")
        self.add_links_button.clicked.connect(self._add_download)
        row.addWidget(self.add_links_button)

        row.addSpacing(14)

        # the pills of the sort, the filter and the selection
        self.sort_button = self._make_pill("sort", SORT_MODES, self._on_sort)
        row.addWidget(self.sort_button)
        row.addSpacing(6)
        self.filter_button = self._make_pill("filter", FILTER_MODES, self._on_filter)
        row.addWidget(self.filter_button)
        row.addSpacing(6)
        self.selection_mode = "All"
        self.selection_button = self._make_pill("checkbox", SELECTION_MODES,
                                                self._on_selection_mode)
        row.addWidget(self.selection_button)

        row.addStretch(1)

        self.add_button = QPushButton("Add download")
        self.add_button.setObjectName("AccentSmall")
        self.add_button.setIcon(styles.make_icon("plus",
                                                  styles.palette(self.theme)["accent_text"]))
        self.add_button.setToolTip("Add a link (Ctrl+V pastes the links of the clipboard)")
        self.add_button.clicked.connect(self._add_download)
        row.addWidget(self.add_button)
        row.addSpacing(16)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search in downloads...")
        self.search_icon = QAction("Search", self)
        self.search_icon.setIcon(styles.make_icon("search", styles.palette(self.theme)["muted"]))
        self.search_edit.addAction(self.search_icon, QLineEdit.LeadingPosition)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(260)
        self.search_edit.textChanged.connect(self._on_search)
        row.addWidget(self.search_edit)

        self.menu_button = QToolButton()
        self.menu_button.setIcon(styles.make_icon("menu", styles.palette(self.theme)["text"]))
        self.menu_button.setFixedSize(32, 32)
        self.menu_button.setToolTip("Main menu")
        self.menu_button.clicked.connect(self._show_main_menu)
        row.addWidget(self.menu_button)
        return bar

    def _build_navigation_bar(self) -> QWidget:
        """The row with the tabs of JDownloader 2 and the pills of Free Download Manager.

        The tabs switch between the downloads, the link grabber and the history; the pills on the
        right sort and filter the list.
        """
        bar = QFrame()
        bar.setObjectName("ToolStrip")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(6)

        self.tabs = TabStrip()
        for name, icon in zip(TABS, TAB_ICONS):
            self.tabs.add_tab(name, icon)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        row.addWidget(self.tabs)

        row.addStretch(1)

        row.addStretch(1)

        # the quiet line with the numbers of the list
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("Faint")
        row.addWidget(self.summary_label)
        return bar

    def _make_pill(self, icon_name: str, modes: tuple, handler) -> QToolButton:
        """A button that shows the current mode and opens the list of the other ones."""
        button = QToolButton()
        button.setObjectName("Pill")
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.InstantPopup)
        button.setIcon(styles.make_icon(icon_name, styles.palette(self.theme)["muted"]))
        menu = QMenu(self)
        for mode in modes:
            action = menu.addAction(mode)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=mode: handler(value))
        button.setMenu(menu)
        button.setText(modes[0])
        button.pill_actions = {mode: action for mode, action in zip(modes, menu.actions())}
        return button

    # -- preferences ----------------------------------------------------------
    def _build_preferences(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("Toolbar")
        head = QHBoxLayout(header)
        head.setContentsMargins(14, 8, 14, 8)
        back = QToolButton()
        back.setText("←")
        back.setFixedSize(30, 30)
        back.setToolTip("Back to the downloads")
        back.clicked.connect(self._show_dashboard)
        head.addWidget(back)
        title = QLabel("Preferences")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)
        menu_button = QToolButton()
        menu_button.setText("≡")
        menu_button.setFixedSize(30, 30)
        menu_button.setToolTip("Main menu")
        menu_button.clicked.connect(self._show_main_menu)
        head.addWidget(menu_button)
        layout.addWidget(header)

        self.preferences = PreferencesView(self.engine.options, self, self.settings)
        self.preferences.changed.connect(self._on_preferences_changed)
        self.preferences.theme_changed.connect(self._apply_theme)
        self.preferences.settings_changed.connect(self._on_settings_changed)
        self.preferences.action_requested.connect(self._on_preference_action)
        self.preferences.load(self.settings)
        layout.addWidget(self.preferences, 1)
        return page

    # -- status bar -----------------------------------------------------------
    def _build_status_bar(self) -> QWidget:
        """The bar at the bottom: the limiter, the speeds, the priority and the selection.

        It is built in groups that thin lines separate: what the user sets on the left, what
        happens on the right.
        """
        bar = QFrame()
        bar.setObjectName("StatusBar")
        self.status = bar
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)

        # -- the limiter: a flat control that opens the presets
        self.limiter_button = QToolButton()
        self.limiter_button.setText("OFF")
        self.limiter_button.setIcon(styles.make_icon("snail",
                                                     styles.palette(self.theme)["muted"]))
        self.limiter_button.setToolTip("The speed limit of the transfers")
        self.limiter_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.limiter_button.setPopupMode(QToolButton.InstantPopup)
        self.limiter_button.setObjectName("Limiter")
        self.limiter_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.limiter_menu = QMenu(self)
        self.limiter_menu.setToolTipsVisible(True)
        for label, value in RATE_PRESETS:
            action = self.limiter_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, limit=value:
                                     self._set_rate_limit(limit))
        self.limiter_button.setMenu(self.limiter_menu)
        layout.addWidget(self.limiter_button)

        # the priority is changed in the menu of the row; the button is only the shortcut
        self.priority_button = QPushButton(PRIORITY_HIGH)
        self.priority_button.setObjectName("Priority")
        self.priority_button.setToolTip("The traffic priority of the selected downloads")
        self.priority_button.clicked.connect(self._show_priority_menu)
        self.priority_button.setVisible(False)

        self.gear_button = QToolButton()
        self.gear_button.setIcon(styles.make_icon("gear", styles.palette(self.theme)["muted"]))
        self.gear_button.setFixedSize(28, 28)
        self.gear_button.setToolTip("Preferences")
        self.gear_button.clicked.connect(self._show_preferences)
        layout.addWidget(self.gear_button)

        layout.addWidget(self._bar_separator())

        # -- the live speeds
        self.down_speed_label = QLabel("0 B/s")
        self.up_speed_label = QLabel("0 B/s")
        self.down_speed_label.setObjectName("Speed.TLabel")
        self.up_speed_label.setObjectName("Speed.TLabel")
        self.down_speed_label.setToolTip("The speed of all the downloads together")
        self.up_speed_label.setToolTip("The upload speed")
        layout.addWidget(self.down_speed_label)
        layout.addWidget(self.up_speed_label)

        self.message_label = QLabel("")
        self.message_label.setObjectName("Faint")
        layout.addWidget(self.message_label)

        layout.addStretch(1)

        # -- the download that is selected
        self.selected_label = QLabel("")
        self.selected_label.setObjectName("Muted")
        layout.addWidget(self.selected_label)

        self.details_button = QToolButton()
        self.details_button.setIcon(styles.make_icon("chevron",
                                                     styles.palette(self.theme)["muted"]))
        self.details_button.setFixedSize(26, 26)
        self.details_button.setToolTip("Show the details of the selected download")
        self.details_button.clicked.connect(self._toggle_details)
        layout.addWidget(self.details_button)
        return bar

    @staticmethod
    def _bar_separator() -> QFrame:
        """A thin line that separates the groups of the bottom bar."""
        line = QFrame()
        line.setObjectName("Separator")
        line.setFixedWidth(1)
        line.setFixedHeight(18)
        return line

    # ------------------------------------------------------------------ theme

    def _apply_theme(self, theme: str) -> None:
        self.theme = theme if theme in ("dark", "light") else "dark"
        colours = styles.palette(self.theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(styles.stylesheet(self.theme))
        self.setWindowIcon(styles.app_icon(colours["accent"]))
        self.drop_zone.set_colours(colours)
        if hasattr(self, "status_delegate"):
            self.status_delegate.colours = dict(colours)
        if hasattr(self, "name_delegate"):
            self.name_delegate.colours = dict(colours)
        for button, icon in ((self.sort_button, "sort"), (self.filter_button, "filter")):
            button.setIcon(styles.make_icon(icon, colours["muted"]))
        for button, icon in ((self.start_all_button, "play"), (self.pause_all_button, "pause"),
                             (self.stop_all_button, "stop"), (self.add_links_button, "plus")):
            button.setIcon(styles.make_icon(icon, colours["text"]))
        self.search_icon.setIcon(styles.make_icon("search", colours["muted"]))
        if hasattr(self, "tabs"):
            self.tabs.set_icon_colour(colours["muted"])
        self.add_button.setIcon(styles.make_icon("plus", colours["accent_text"]))
        self.limiter_button.setIcon(styles.make_icon("snail", colours["muted"]))
        self._save_settings()

    # -- the commands of the bars ---------------------------------------------

    def _start_all(self) -> None:
        """Start every download that waits."""
        started = 0
        for record in self.model.records():
            if record.state in (TaskState.QUEUED, TaskState.PAUSED, TaskState.CANCELLED,
                                TaskState.INTERRUPTED):
                self.engine.start_record(record)
                started += 1
        self._on_engine_message(f"Started {started} download(s)" if started
                                else "There is nothing to start")

    def _pause_all(self) -> None:
        """Pause every download that runs."""
        paused = 0
        for record in self.model.records():
            if record.state == TaskState.RUNNING:
                self.engine.pause(record)
                paused += 1
        self._on_engine_message(f"Paused {paused} download(s)" if paused
                                else "There is nothing to pause")

    def _stop_all(self) -> None:
        """Stop every download."""
        stopped = 0
        for record in self.model.records():
            if record.active:
                self.engine.cancel(record)
                stopped += 1
        self._on_engine_message(f"Stopped {stopped} download(s)" if stopped
                                else "There is nothing to stop")

    def _remove_finished(self) -> None:
        """Take the finished downloads out of the list."""
        removed = 0
        for record in list(self.model.records()):
            if record.state.finished:
                self.engine.remove_from_list(record, delete_files=False)
                self.model.remove_record(record.job_id)
                removed += 1
        self._on_engine_message(f"Removed {removed} download(s)" if removed
                                else "There is nothing to remove")

    # ------------------------------------------------------------------ engine

    def _connect_engine(self) -> None:
        self.engine.job_added.connect(self._on_job_added)
        self.engine.job_changed.connect(self._on_job_changed)
        self.engine.job_finished.connect(self._on_job_finished)
        self.engine.job_error.connect(self._on_job_error)
        self.engine.stats_changed.connect(self._on_stats)
        self.engine.message.connect(self._on_engine_message)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def _load_addons(self) -> None:
        """Discover the add-ons and start the enabled ones."""
        enabled = self.settings.get("addons") or {}
        for addon in self.addons.discover():
            addon.enabled = bool(enabled.get(addon.id, False))
        self.addons.load_enabled()
        for message in self.addons.messages:
            self._on_engine_message(message)

    def _save_addons(self) -> None:
        state = {addon.id: addon.enabled for addon in self.addons.addons}
        self.settings["addons"] = state
        self._save_settings()

    def _restore_downloads(self) -> None:
        """Bring back the downloads that were in the list when the app was closed.

        The queue holds the tasks (title, priority, tags, schedule, state); the manifests hold
        the per file state.
        """
        for record in self.engine.load_tasks():
            self.model.add_record(record)
        self.model.set_sort(self.settings.get("sort") or SORT_MODES[0])
        self.model.set_filter(self.settings.get("filter") or FILTER_MODES[0])
        self.sort_button.setText(self.model.sort_mode)
        self.filter_button.setText(self.model.filter_mode)
        self.model.set_selection_mode(self.selection_mode)
        self._toggle_table_visibility()

    def _auto_resume(self) -> None:
        """Continue the downloads that were interrupted when the app was closed."""
        if not self.engine.options.auto_resume:
            return
        for record in self.model.records():
            if record.status in (rs.JOB_QUEUED, rs.JOB_RUNNING, rs.JOB_INTERRUPTED):
                if record.start_after and record.start_after > time.time():
                    continue
                self.engine.start_record(record)

    def _warm_up_engine(self) -> None:
        """Start the engines in the background, so the window does not wait for them.

        The JDownloader engine is a JVM: it takes a few seconds to come up, and the window must
        not wait for it.  aria2 starts when the first link needs it.
        """
        self.engine.registry.start_engines()

    def _on_job_added(self, job_id: str) -> None:
        record = self.engine.record(job_id)
        if record is not None and self.model.record(job_id) is None:
            self.model.add_record(record)
        self._toggle_table_visibility()

    def _on_job_changed(self, job_id: str) -> None:
        if self.engine.record(job_id) is None:
            # the download was taken out of the queue while it still reported; the row goes
            # and nothing must write it back into the list on disk
            self.model.remove_record(job_id)
            self._toggle_table_visibility()
            self._update_details()
            return
        self.model.update_record(job_id)
        record = self.model.record(job_id)
        if record is not None:
            self.engine.persist(record)
            self.addons.fire("status_change", record)
        self._update_details()
        self._refresh_grabber()

    def _maybe_exit(self) -> None:
        """Close the application when it was started with --exit-when-done."""
        if not self.exit_when_done:
            return
        if any(record.active for record in self.model.records()):
            return
        # the window may never have been shown (headless), so quit the application itself
        QTimer.singleShot(500, lambda: QApplication.quit())

    def _on_job_finished(self, job_id: str, complete: bool, message: str) -> None:
        self.model.update_record(job_id)
        self._on_engine_message(message)
        self._update_status_bar()
        self._update_details()
        record = self.model.record(job_id)
        if record is not None:
            self.addons.fire("after_download", record)
            if complete:
                # a finished download is Completed, whatever the row showed before
                record.state = TaskState.COMPLETED
                self.engine.persist(record)
                self.model.update_record(job_id)
                if bool(self.settings.get("notify_complete", True)):
                    self._notify("Download finished",
                                 f"{record.title} was downloaded and verified")
        self._maybe_exit()

    def _notify(self, title: str, text: str) -> None:
        """A message over the tray icon; the user can turn it off in the preferences."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if getattr(self, "_tray", None) is None:
            self._tray = QSystemTrayIcon(styles.app_icon(
                styles.palette(self.theme)["accent"]), self)
        self._tray.show()
        self._tray.showMessage(title, text, QSystemTrayIcon.Information, 5000)

    def _on_job_error(self, job_id: str, message: str) -> None:
        self.model.update_record(job_id)
        self._on_engine_message(message)

    def _on_engine_message(self, message: str) -> None:
        self.message_label.setText(message)

    def _on_stats(self, down: float, up: float, active: int, total: int) -> None:
        self.down_speed_label.setText(f"▼ {fmt_speed(down)}")
        self.up_speed_label.setText(f"▲ {fmt_speed(up)}")
        colours = styles.palette(self.theme)
        colours = styles.palette(self.theme)
        colour = (colours["warning"] if (self.engine.options.slow_mode
                    or self.engine.options.rate_limit) else colours["success"])
        self.up_speed_label.setStyleSheet(f"color: {colour};")
        self._update_status_bar()
        if self._shutdown_armed and active == 0 and self._shutdown_jobs:
            still_running = [job_id for job_id in self._shutdown_jobs
                             if self.model.record(job_id) and self.model.record(job_id).active]
            if not still_running:
                self._run_shutdown()

    def _update_job_row(self, record: DownloadRecord) -> None:
        """Repaint one row of the table."""
        if record is not None:
            self.model.update_record(record.job_id)

    def _update_summary(self, active: int, total: int) -> None:
        """The numbers of the list: the quiet line in the bar and the overview under it."""
        if not hasattr(self, "summary_label"):
            return
        records = self.model.visible_records()
        if not records:
            self.summary_label.setText("No downloads yet")
            self._update_overview(records, active)
            return
        size = sum(record.bytes_total or 0 for record in records)
        parts = [f"{len(records)} download{'s' if len(records) != 1 else ''}"]
        if active:
            parts.append(f"{active} active")
        if size:
            parts.append(fmt_size(size))
        self.summary_label.setText(" · ".join(parts))
        self._update_overview(records, active)

    def _update_overview(self, records: list, active: int) -> None:
        """The overview of JDownloader 2: the numbers of the whole list."""
        if not hasattr(self, "overview"):
            return
        if self.tabs.currentIndex() == TABS.index(TAB_GRABBER):
            # the grabber shows the links that wait, not the downloads
            rows = self.grabber_model.rows() if hasattr(self, "grabber_model") else []
            size = sum(int(row.get("size") or 0) for row in rows)
            self.overview.show_values({
                "packages": len(rows),
                "speed": "—",
                "loaded": fmt_size(size),
                "eta": "—",
                "connections": "—",
                "running": len(rows),
            })
            return
        speed = sum(record.speed_down or 0 for record in records)
        loaded = sum(record.bytes_done or 0 for record in records)
        # the time that is left is the slowest download of the list
        left = 0.0
        for record in records:
            if not record.active or record.speed_down <= 0 or not record.bytes_total:
                continue
            left = max(left, (record.bytes_total - record.bytes_done) / record.speed_down)
        connections = int(self.engine.options.connections or 1) * active
        self.overview.show_values({
            "packages": len(records),
            "speed": fmt_speed(speed),
            "loaded": fmt_size(loaded),
            "eta": fmt_duration(left) if left > 0 else "—",
            "connections": connections,
            "running": active,
        })

    def _update_status_bar(self) -> None:
        """The bar shows the limiter, the priority and the selection, the way the reference does."""
        total = len(self.model.records())
        active = sum(1 for record in self.model.records() if record.active)
        if total:
            self.details_button.setToolTip(f"{active} active of {total} downloads")
        self.limiter_button.setText(fmt_rate(self.engine.options.rate_limit))
        self._apply_snail_mode()
        priority = self._priority_of_selection()
        self.priority_button.setText(priority)
        colours = styles.palette(self.theme)
        colours = styles.palette(self.theme)
        colour = {"HIGH": colours["success"], "MEDIUM": colours["warning"], "LOW": colours["danger"]}.get(priority, colours["success"])
        self.priority_button.setStyleSheet(f"color: {colour}; border-color: {colour};")
        self._update_details_button()
        self._update_summary(active, total)

    def _apply_snail_mode(self) -> None:
        """In snail mode the whole bar turns amber, the way the reference shows it."""
        slow = self.engine.options.slow_mode or bool(self.engine.options.rate_limit)
        bar = self.status
        if getattr(bar, "_snail", False) == slow:
            return
        bar.setProperty("snail", "true" if slow else "false")
        bar.style().unpolish(bar)
        bar.style().polish(bar)
        bar._snail = slow
        self.limiter_button.setText("Snail mode" if slow
                                    else fmt_rate(self.engine.options.rate_limit))
        self.limiter_button.setIcon(styles.make_icon(
            "snail", "#E8B84B" if slow else styles.palette(self.theme)["muted"]))

    # ------------------------------------------------------------------ toolbar

    def _on_sort(self, text: str) -> None:
        self.model.set_sort(text)
        self.sort_button.setText(text)
        for mode, action in getattr(self.sort_button, "pill_actions", {}).items():
            action.setChecked(mode == text)
        self._save_settings()

    def _on_filter(self, text: str) -> None:
        self.model.set_filter(text)
        self.filter_button.setText(text)
        for mode, action in getattr(self.filter_button, "pill_actions", {}).items():
            action.setChecked(mode == text)
        self._save_settings()

    def _on_selection_mode(self, text: str) -> None:
        self.selection_mode = text
        for mode, action in getattr(self.selection_button, "pill_actions", {}).items():
            action.setChecked(mode == text)
        self.model.set_selection_mode(text)
        self._save_settings()

    def _on_search(self, text: str) -> None:
        self.model.set_search(text)
        self._toggle_table_visibility()

    def _toggle_select_all(self, checked: bool) -> None:
        """Select every download of the list, or none of them."""
        if checked:
            self.table.selectAll()
        else:
            self.table.clearSelection()

    def _set_rate_limit(self, limit: int) -> None:
        """Set the real bandwidth limit (bytes per second, 0 means unlimited)."""
        self.engine.options.rate_limit = int(limit or 0)
        self.preferences.rate_spin.setValue(int(limit or 0) // 1024)
        self._update_limiter_button()
        self._save_settings()
        self._on_engine_message("Speed limit: " + fmt_rate(self.engine.options.rate_limit))

    def _update_limiter_button(self) -> None:
        limit = int(self.engine.options.rate_limit or 0)
        self.limiter_button.setText(fmt_rate(limit))
        for action in self.limiter_menu.actions():
            action.setChecked(action.text() == fmt_rate(limit))
        self.limiter_button.setToolTip(
            "Speed limit: " + fmt_rate(limit) if limit else "Speed limit: off")

    def _pause_selected(self) -> None:
        for record in self._selected_records():
            self._pause_record(record)

    def _resume_selected(self) -> None:
        for record in self._selected_records():
            self._resume_record(record)

    def _delete_selected(self) -> None:
        """The Delete key: delete the files or only take them out of the list."""
        selected = self._selected_records()
        if selected:
            self._delete_records(selected)

    def _delete_record(self, record: DownloadRecord) -> None:
        """The Delete item of the row menu replaces the old Cancel: the transfer stops and
        the download leaves the list."""
        self._delete_records([record])

    def _delete_records(self, records: list[DownloadRecord]) -> None:
        """The reference dialog: delete the files or only take them out of the list."""
        dialog = DeleteDownloadsDialog(self, records)
        if dialog.exec() != DeleteDownloadsDialog.Accepted:
            return
        deleted = 0
        if dialog.delete_files:
            for record in records:
                if record.root and Path(record.root).exists():
                    shutil.rmtree(record.root, ignore_errors=True)
                    deleted += 1
        for record in records:
            self.engine.remove_from_list(record, delete_files=False)
            self.model.remove_record(record.job_id)
        self._toggle_table_visibility()
        self._update_status_bar()
        if deleted:
            self._on_engine_message(f"Deleted {deleted} download(s) and their files")

    def _show_priority_menu(self, position: QPoint | None = None) -> None:
        menu = QMenu(self)
        for priority in (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW):
            action = menu.addAction(priority)
            action.setCheckable(True)
            action.setChecked(self._priority_of_selection() == priority)
            action.triggered.connect(lambda _checked=False, value=priority:
                                     self._set_priority(value))
        if position is None:
            position = self.priority_button.mapToGlobal(
                self.priority_button.rect().bottomLeft())
        menu.exec(QPoint(int(position.x()), int(position.y())))

    def _priority_of_selection(self) -> str:
        selected = self._selected_records()
        if selected:
            return selected[0].priority
        return PRIORITY_HIGH

    def _on_row_expanded(self, index) -> None:
        """The expander of a row shows the files of that download."""
        record = self.model.record_at(index.row())
        if record is None:
            return
        self.table.setCurrentIndex(index)
        self.details.setVisible(True)
        self._update_details()

    def _on_priority_clicked(self, index) -> None:
        record = self.model.record_at(index.row())
        if record is None:
            return
        self.table.setCurrentIndex(index)
        self._show_priority_menu(self.table.visualRect(index).bottomLeft())

    def _set_priority(self, priority: str) -> None:
        records = self._selected_records() or self.model.records()
        for record in records:
            self.engine.set_priority(record, priority)
        self.priority_button.setText(priority)
        self._on_engine_message(f"Traffic priority: {priority}")

    # ------------------------------------------------------------------ menus

    def _build_main_menu(self) -> QMenu:
        """The hamburger menu of the reference interface."""
        menu = QMenu(self)

        paste = menu.addAction("Paste urls from clipboard")
        paste.triggered.connect(self._paste_from_clipboard)
        paste_file = menu.addAction("Paste urls from file")
        paste_file.triggered.connect(self._paste_from_file)

        export_menu = menu.addMenu("Export/Import")
        export_action = export_menu.addAction("Export downloads…")
        export_action.triggered.connect(self._export_downloads)
        import_action = export_menu.addAction("Import downloads…")
        import_action.triggered.connect(self._import_downloads)

        addons = menu.addAction("Add-ons")
        addons.triggered.connect(self._show_addons)
        if self.addons.menu_actions:
            addon_menu = menu.addMenu("Add-on tools")
            for _addon, label, callback in list(self.addons.menu_actions):
                action = addon_menu.addAction(label)
                action.triggered.connect(callback)

        preferences = menu.addAction("Preferences")
        preferences.triggered.connect(self._show_preferences)

        shutdown_menu = menu.addMenu("Auto shutdown")
        for label, kind in (("Shutdown when the downloads finish", "shutdown"),
                            ("Restart when the downloads finish", "restart"),
                            ("Log off when the downloads finish", "logoff")):
            action = shutdown_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, value=kind:
                                     self._arm_shutdown(value))
        self.shutdown_cancel_action = shutdown_menu.addAction("Cancel the scheduled action")
        self.shutdown_cancel_action.triggered.connect(self._cancel_shutdown)
        self.shutdown_cancel_action.setEnabled(self._shutdown_armed)

        portable = menu.addAction("Create portable version")
        portable.triggered.connect(self._create_portable)

        updates = menu.addAction("Check for updates")
        updates.triggered.connect(self._check_for_updates)

        menu.addSeparator()
        about = menu.addAction("About")
        about.triggered.connect(self._show_about)
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.close)

        return menu

    def _show_main_menu(self) -> None:
        menu = self._build_main_menu()
        menu.exec(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))

    def _toggle_selected_pause(self) -> None:
        """Space pauses the selected downloads, or continues them when they are paused."""
        for record in self._selected_records():
            if record.status == "paused":
                self._resume_record(record)
            else:
                self._pause_record(record)

    def _cancel_selected(self) -> None:
        """Delete cancels the selected downloads."""
        for record in self._selected_records():
            self._cancel_record(record)

    def _on_context_menu(self, position: QPoint) -> None:
        """The right click menu of the row under the cursor.

        The row under the cursor becomes the selected one, so the menu always belongs to the
        download the user pointed at - the way the file manager does it.  A right click on the
        empty space keeps the selection and opens the menu of the selected download.
        """
        index = self.table.indexAt(position)
        if index.isValid():
            self.table.setCurrentIndex(index)
            if not self.table.selectionModel().isRowSelected(index.row()):
                self.table.selectRow(index.row())
            record = self.model.record_at(index.row())
        else:
            selected = self._selected_records()
            record = selected[0] if selected else None
        if record is None:
            return
        self._show_row_menu(self.table.viewport().mapToGlobal(position), record)

    def _on_actions_clicked(self, position: QPoint) -> None:
        """The three dot button of a row."""
        index = self.table.indexAt(position)
        record = self.model.record_at(index.row()) if index.isValid() else None
        self._show_row_menu(self.table.viewport().mapToGlobal(position), record)

    def _show_row_menu(self, position: QPoint, record: DownloadRecord | None = None) -> None:
        """Show the menu of one download at the given position."""
        if record is None:
            index = self.table.currentIndex()
            record = self.model.record_at(index.row()) if index.isValid() else None
        if record is None:
            return
        self._build_row_menu(record).exec(position)

    def _build_row_menu(self, record: DownloadRecord) -> QMenu:
        """The menu of one download, in the order of the reference.

        The items are grouped: the file, the transfer, the list and the link.
        """
        menu = QMenu(self)
        # the downloads of ArkLoad are verified against the hash of the source, the downloads of
        # the JDownloader engine are not - the actions that need it are only offered when it
        # applies
        own_engine = getattr(record, "backend", "arkload") == "arkload"

        # the state of the download decides what can be used
        running = record.status in (RUNNING, "downloading")
        paused = record.status == "paused"
        stopped = record.status in ("cancelled", "failed", "incomplete")
        # the file is there when there is something to open
        has_file = bool(record.root) and Path(record.root).exists()

        # -- the file
        open_action = menu.addAction("Open")
        open_action.setEnabled(has_file)
        open_action.triggered.connect(lambda: self._open_record(record))
        show_action = menu.addAction("Show in folder")
        show_action.setEnabled(has_file)
        show_action.triggered.connect(lambda: self._show_in_folder(record))
        menu.addSeparator()

        # -- the transfer
        pause_action = menu.addAction("Pause")
        pause_action.setEnabled(running and not paused)
        pause_action.triggered.connect(lambda: self._pause_record(record))
        resume_action = menu.addAction("Resume")
        resume_action.setEnabled(paused or stopped or not running)
        resume_action.triggered.connect(lambda: self._resume_record(record))
        delete_action = menu.addAction("Delete")
        delete_action.setToolTip(
            "Stop the download and take it out of the list; delete its files when you want")
        delete_action.triggered.connect(lambda: self._delete_record(record))
        menu.addSeparator()

        # -- the list
        priority_menu = menu.addMenu("Set priority")
        for priority in (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW):
            action = priority_menu.addAction(priority)
            action.setChecked(priority == record.priority)
            action.triggered.connect(lambda _checked=False, value=priority:
                                     self.engine.set_priority(record, value))
        rename_action = menu.addAction("Rename file")
        rename_action.setEnabled(has_file)
        rename_action.triggered.connect(lambda: self._rename_record(record))
        move_action = menu.addAction("Move to...")
        move_action.setEnabled(has_file)
        move_action.triggered.connect(lambda: self._move_record(record))
        delete_action = menu.addAction("Delete file")
        delete_action.setEnabled(has_file)
        delete_action.triggered.connect(lambda: self._delete_files(record))
        remove_action = menu.addAction("Remove from list")
        remove_action.triggered.connect(lambda: self._remove_from_list(record))
        menu.addSeparator()

        # -- the link
        page_action = menu.addAction("Open download page")
        page_action.triggered.connect(lambda: open_url(record.url))
        copy_action = menu.addAction("Copy link")
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(record.url))
        menu.addSeparator()

        # -- the download itself
        integrity_action = menu.addAction("File integrity")
        integrity_action.setEnabled(bool(record.files))
        if not record.files:
            integrity_action.setToolTip("Nothing has been downloaded yet.")
        integrity_action.triggered.connect(lambda: self._show_integrity(record))
        schedule_action = menu.addAction("Schedule")
        schedule_action.triggered.connect(lambda: self._schedule_record(record))
        change_action = menu.addAction("Change URL")
        change_action.triggered.connect(lambda: self._change_url(record))
        if own_engine:
            mirror = menu.addAction("Add mirror")
            mirror.triggered.connect(lambda: self._add_mirror(record))
            update_action = menu.addAction("Check for update")
            update_action.triggered.connect(lambda: self._check_record_update(record))
        return menu

    # ------------------------------------------------------------------ actions

    def add_download_url(self, url: str, destination: str | None = None,
                         start: bool = True) -> DownloadRecord | None:
        """Add a link to the list (used by the button, the command line and the add-ons)."""
        url = (url or "").strip()
        if not url:
            return None
        try:
            record = self.engine.add_download(url, destination or self.engine.options.destination,
                                              self.engine.options)
        except Exception as e:  # noqa: BLE001 - the link may be malformed
            self._on_engine_message(f"{url}: {e}")
            return None
        if start:
            self.addons.fire("before_download", record)
        return record

    def _add_download(self) -> None:
        dialog = AddDownloadDialog(self, self.engine.options.destination,
                                   history=self._destination_history(),
                                   resolver=self.engine.engine_of)
        if dialog.exec() != AddDownloadDialog.Accepted:
            return
        result = dialog.accepted or {}
        options = self.engine.options
        options.destination = result.get("dest") or options.destination
        self._remember_destination(options.destination)
        self._save_settings()
        try:
            record = self.engine.add_download(result.get("url", ""), options.destination,
                                              options, name=result.get("name", ""))
        except Exception as e:  # noqa: BLE001 - the link may be malformed
            QMessageBox.warning(self, "New download",
                                f"The download could not be started:\n\n{e}")
            return
        if result.get("schedule"):
            self.engine.schedule(record, float(result["schedule"]))
            self._on_engine_message(f"{record.title} is scheduled")
        self.addons.fire("before_download", record)

    def _destination_history(self) -> list[str]:
        history = [str(item) for item in (self.settings.get("destinations") or [])]
        current = self.engine.options.destination
        if current and current not in history:
            history.insert(0, current)
        return history[:8]

    def _remember_destination(self, folder: str) -> None:
        history = [str(item) for item in (self.settings.get("destinations") or [])]
        if folder in history:
            history.remove(folder)
        history.insert(0, folder)
        self.settings["destinations"] = history[:8]

    def _paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self._on_engine_message("The clipboard is empty")
            return
        self._add_urls(text.splitlines())

    def _paste_from_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a file with URLs", "", "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            QMessageBox.warning(self, "Paste urls from file", f"The file cannot be read:\n\n{e}")
            return
        self._add_urls([line.strip() for line in text.splitlines() if line.strip()])

    def _add_urls(self, urls: list[str]) -> None:
        added = 0
        for url in urls:
            url = url.strip()
            if not url:
                continue
            try:
                self.engine.add_download(url)
                added += 1
            except Exception as e:  # noqa: BLE001 - one bad link must not stop the rest
                self._on_engine_message(f"{url}: {e}")
        if added:
            self._on_engine_message(f"Added {added} download(s)")

    def _export_downloads(self) -> None:
        records = self._selected_records() or self.model.records()
        if not records:
            self._on_engine_message("There is nothing to export")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export downloads", "downloads.json", "JSON (*.json)")
        if not path:
            return
        payload = {"version": 1, "downloads": [record.to_dict() for record in records]}
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "Export downloads", f"The file cannot be written:\n\n{e}")
            return
        self._on_engine_message(f"Exported {len(records)} download(s) to {path}")

    def _import_downloads(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import downloads", "", "JSON (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import downloads", f"The file cannot be read:\n\n{e}")
            return
        entries = payload.get("downloads") if isinstance(payload, dict) else None
        if not entries:
            QMessageBox.warning(self, "Import downloads", "The file has no downloads in it.")
            return
        added = 0
        for entry in entries:
            url = entry.get("url")
            if not url:
                continue
            try:
                record = DownloadRecord.from_dict(entry)
                self.engine.add_download(url, record.destination, self.engine.options, record)
                added += 1
            except Exception as e:  # noqa: BLE001
                self._on_engine_message(f"{url}: {e}")
        self._on_engine_message(f"Imported {added} download(s)")

    def _check_for_updates(self) -> None:
        """Ask GitHub for the latest release of this repository."""
        self._on_engine_message("Checking for updates…")

        def worker() -> None:
            import urllib.request
            try:
                request = urllib.request.Request(
                    f"{REPO_URL}/releases/latest",
                    headers={"User-Agent": "mediafire-download-manager",
                             "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    body = response.read().decode("utf-8", "replace")
                self.update_checked.emit(body)
            except Exception as e:  # noqa: BLE001 - being offline is normal
                self.update_checked.emit(f"ERROR:{e}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, payload: str) -> None:
        if payload.startswith("ERROR:"):
            self._on_engine_message("Could not check for updates: " + payload[6:])
            return
        version = ""
        try:
            version = str(json.loads(payload).get("tag_name") or "").lstrip("v")
        except ValueError:
            version = ""
        if version and version != VERSION:
            self._on_engine_message(f"Version {version} is available")
            open_url(f"{REPO_URL}/releases/latest")
        else:
            self._on_engine_message("The application is up to date")

    def _arm_shutdown(self, kind: str) -> None:
        """Arm an action that runs when the running downloads have finished."""
        if os.name != "nt":
            QMessageBox.information(self, "Auto shutdown",
                                    "This action is only available on Windows.")
            return
        active = [record for record in self.model.records() if record.active]
        if not active:
            QMessageBox.information(
                self, "Auto shutdown",
                "There is no running download.\n\n"
                "Start a download first - the action runs when the last download has finished.")
            return
        what = {"shutdown": "shut the computer down", "restart": "restart the computer",
                "logoff": "log the user off"}[kind]
        answer = QMessageBox.question(
            self, "Auto shutdown",
            f"Windows will {what} when the {len(active)} running download(s) have finished.\n\n"
            "Windows shows a one minute warning before it happens. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._shutdown_kind = kind
        self._shutdown_armed = True
        self._shutdown_jobs = {record.job_id for record in active}
        self._on_engine_message(f"Auto {kind} armed - it runs when the downloads finish")

    def _cancel_shutdown(self) -> None:
        if not self._shutdown_armed:
            return
        if os.name == "nt":
            subprocess.run(["shutdown", "/a"], capture_output=True)
        self._shutdown_armed = False
        self._shutdown_jobs = set()
        self._on_engine_message("The scheduled action was cancelled")

    def _run_shutdown(self) -> None:
        """Run the armed action (Windows only)."""
        if not self._shutdown_armed or os.name != "nt":
            return
        kind = self._shutdown_kind
        self._shutdown_armed = False
        self._shutdown_jobs = set()
        commands = {
            "shutdown": ["shutdown", "/s", "/t", "60", "/c", "ArkLoad: the downloads have finished"],
            "restart": ["shutdown", "/r", "/t", "60", "/c", "ArkLoad: the downloads have finished"],
            "logoff": ["shutdown", "/l"],
        }
        try:
            subprocess.Popen(commands.get(kind, commands["shutdown"]))
        except OSError as e:
            QMessageBox.warning(self, "Auto shutdown", f"Windows refused the command:\n\n{e}")
            return
        self._on_engine_message("Windows will act in one minute")

    def _show_about(self) -> None:
        AboutDialog(self, VERSION).exec()

    def _show_preferences(self) -> None:
        self.stack.setCurrentIndex(1)
        self._current_page = 1

    def _show_dashboard(self) -> None:
        self.stack.setCurrentIndex(0)
        self._current_page = 0

    # -- row actions ----------------------------------------------------------
    def _selected_records(self) -> list[DownloadRecord]:
        records = []
        for index in self.table.selectionModel().selectedRows():
            record = self.model.record_at(index.row())
            if record is not None:
                records.append(record)
        return records

    def _record_at(self, position: QPoint) -> DownloadRecord | None:
        index = self.table.indexAt(position)
        if index.isValid():
            return self.model.record_at(index.row())
        selected = self._selected_records()
        return selected[0] if selected else None

    def _on_selection_changed(self, *args) -> None:
        selected = self._selected_records()
        if selected:
            self.details.setVisible(True)     # clicking a row opens the card
        self._update_details()
        self._update_selection_mode()
        self._update_details_button()

    def _toggle_details(self) -> None:
        """The arrow in the status bar opens and closes the details card."""
        visible = self.details.isVisible()
        self.details.setVisible(not visible)
        self._update_details_button()

    def _update_details_button(self) -> None:
        """The name of the selected download and the button that opens its details."""
        selected = self._selected_records()
        record = selected[0] if selected else (self.model.records()[0]
                                               if self.model.records() else None)
        name = record.title if record is not None else ""
        self.selected_label.setText(name)
        colours = styles.palette(self.theme)
        self.details_button.setIcon(styles.make_icon(
            "chevron_up" if self.details.isVisible() else "chevron", colours["muted"]))
        self.details_button.setToolTip(
            "Hide the details of the selected download" if self.details.isVisible()
            else "Show the details of the selected download")

    def _update_details(self) -> None:
        """The details panel shows the download that is selected (or the first one)."""
        selected = self._selected_records()
        record = selected[0] if selected else (self.model.records()[0]
                                               if self.model.records() else None)
        if record is None:
            self.details.setVisible(False)
            self._update_details_button()
            return
        self.details.show_record(record, self.engine.options.to_spec())
        self._update_details_button()

    def _update_selection_mode(self) -> None:
        """The list shows what the selection mode says."""
        self._update_details_button()

    def _show_row_menu_at_selection(self) -> None:
        index = self.table.currentIndex()
        if index.isValid():
            rect = self.table.visualRect(index)
            self._show_row_menu(self.table.viewport().mapToGlobal(rect.bottomLeft()))
        else:
            self._show_row_menu(self.details_button.mapToGlobal(QPoint(0, 0)))

    def _cancel_record(self, record: DownloadRecord) -> None:
        """Stop a download; the finished files are kept."""
        self.engine.cancel(record)
        self._update_job_row(record)
        self._on_engine_message(f"{record.title} was cancelled")

    def _pause_record(self, record: DownloadRecord) -> None:
        """Pause a running download; the bytes that are already there are kept."""
        self.engine.pause(record)
        self._update_job_row(record)
        self._on_engine_message(f"{record.title} was paused")

    def _resume_record(self, record: DownloadRecord) -> None:
        """Continue a paused download, or start it again from the manifest.

        The status is not written here: the engine knows whether the download can start
        really (a scheduled or host limited one keeps waiting in the queue).
        """
        self.engine.resume(record)
        self._update_job_row(record)
        self._on_engine_message(f"{record.title} continues")

    def _open_record(self, record: DownloadRecord) -> None:
        if not record.root:
            return
        path = Path(record.root)
        target = path
        if path.is_dir() and record.files:
            first = record.files[0]
            candidate = path.joinpath(*Path(first.path).parts)
            if candidate.exists():
                target = candidate
        open_in_file_manager(target)

    def _open_selected(self, index) -> None:
        record = self.model.record_at(index.row())
        if record is not None:
            self._open_record(record)

    def _show_in_folder(self, record: DownloadRecord) -> None:
        if record.root:
            open_in_file_manager(Path(record.root))

    def _rename_record(self, record: DownloadRecord) -> None:
        current = Path(record.root).name if record.root else record.title
        dialog = RenameDialog(self, current)
        if dialog.exec() != RenameDialog.Accepted:
            return
        if not self.engine.rename(record, dialog.value()):
            QMessageBox.warning(self, "Rename file", "The download could not be renamed.")

    def _move_record(self, record: DownloadRecord) -> None:
        dialog = MoveDialog(self, record.destination)
        if dialog.exec() != MoveDialog.Accepted:
            return
        if not self.engine.move_to(record, dialog.value()):
            QMessageBox.warning(self, "Move to…", "The download could not be moved.")

    def _delete_files(self, record: DownloadRecord) -> None:
        """Delete the files of a download and take it out of the list."""
        answer = QMessageBox.question(
            self, "Delete file",
            f"Delete the files of '{record.title}'?\n\n"
            "The download is taken out of the list as well.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        if record.root and Path(record.root).exists():
            shutil.rmtree(record.root, ignore_errors=True)
        self.engine.remove_from_list(record, delete_files=True)
        self.model.remove_record(record.job_id)
        self._toggle_table_visibility()
        self._update_status_bar()
        self._on_engine_message(f"Deleted {record.title}")

    def _edit_tags_of_the_selection(self) -> None:
        """The tags of the download that is selected."""
        selected = self._selected_records()
        if not selected:
            self._on_engine_message("Select a download first")
            return
        self._edit_tags(selected[0])

    def _remove_from_list(self, record: DownloadRecord) -> None:
        answer = QMessageBox.question(
            self, "Remove from list",
            f"Remove '{record.title}' from the list?\n\nThe downloaded files stay on disk.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.engine.remove_from_list(record, delete_files=False)
        self.model.remove_record(record.job_id)
        self._toggle_table_visibility()

    def _check_record_update(self, record: DownloadRecord) -> None:
        self._on_engine_message("Comparing the files with the source…")

        def worker() -> None:
            report = self.engine.check_for_update(record)
            self.source_checked.emit(report)

        threading.Thread(target=worker, daemon=True).start()

    def _on_source_checked(self, report: str) -> None:
        QMessageBox.information(self, "Check for update", report)

    def _show_integrity(self, record: DownloadRecord) -> None:
        report = self.engine.file_integrity_report(record)
        TextReportDialog(self, "File integrity", report).exec()

    def _schedule_record(self, record: DownloadRecord) -> None:
        dialog = ScheduleDialog(self)
        if dialog.exec() != ScheduleDialog.Accepted:
            return
        self.engine.schedule(record, dialog.start_time())
        self._on_engine_message(f"{record.title} is scheduled")

    def _change_url(self, record: DownloadRecord) -> None:
        dialog = AddDownloadDialog(self, record.destination, url=record.url,
                                   torrent_supported=TORRENT_SUPPORTED)
        if dialog.exec() != AddDownloadDialog.Accepted:
            return
        record.url = dialog.accepted_url
        self.engine.persist(record)
        self.engine.start_record(record)
        self._on_engine_message("The URL was changed, the download continues")

    def _edit_tags(self, record: DownloadRecord) -> None:
        dialog = TagsDialog(self, record.tags)
        if dialog.exec() != TagsDialog.Accepted:
            return
        self.engine.set_tags(record, dialog.value())
        self._on_engine_message(f"Tags of {record.title}: {', '.join(record.tags) or 'none'}")


    # ------------------------------------------------------------------ add-ons

    def _show_addons(self) -> None:
        """The add-on manager: what is installed and what it does."""
        dialog = AddonsDialog(self, self.addons)
        if dialog.exec() == AddonsDialog.Accepted:
            self._save_addons()
            self._on_engine_message(f"{len(self.addons.enabled())} add-on(s) active")

    # ------------------------------------------------------------------ converting

    def _convert_record(self, record: DownloadRecord, kind: str) -> None:
        """Convert a finished download with ffmpeg."""
        if not record.root:
            self._on_engine_message("There is nothing to convert yet")
            return
        source = Path(record.root)
        if source.is_dir():
            files = [item for item in sorted(source.iterdir())
                     if item.is_file() and item.suffix.lower() not in (".jar", ".zip", ".rar")]
            if not files:
                QMessageBox.information(self, f"Convert to {kind}",
                                        "There is no media file in this download.")
                return
            source = files[0]
        if not find_ffmpeg(str(self.settings.get("ffmpeg_path") or "")):
            QMessageBox.information(
                self, f"Convert to {kind}",
                "ffmpeg was not found.\n\n"
                "Install it from https://ffmpeg.org and set the path in Preferences → Advanced.")
            return
        dialog = ConvertDialog(self, str(source), kind)
        if dialog.exec() != ConvertDialog.Accepted:
            return
        source_text, target_text = dialog.values()
        if not source_text or not target_text:
            return
        self._on_engine_message(f"Converting to {kind}…")

        def worker():
            result = convert(source_text, target_text, kind,
                             str(self.settings.get("ffmpeg_path") or ""))
            self.source_checked.emit(result.message + ("\n\n" + result.output if result.output
                                                       else ""))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ mirrors

    def _add_mirror(self, record: DownloadRecord) -> None:
        """Add an alternative source for a download."""
        dialog = MirrorDialog(self, record.mirrors)
        if dialog.exec() != MirrorDialog.Accepted:
            return
        if self.engine.add_mirror(record, dialog.value()):
            self._on_engine_message(f"Mirror added to {record.title}")
        else:
            QMessageBox.warning(self, "Add mirror",
                                "That is not a MediaFire link, or the mirror is already there.")

    # ------------------------------------------------------------------ portable

    def _create_portable(self) -> None:
        """Copy the application into a folder that runs from anywhere."""
        folder = QFileDialog.getExistingDirectory(self, "Where should the portable copy go?",
                                                  str(Path.home()))
        if not folder:
            return
        try:
            target = create_portable(Path(folder) / "ArkLoad")
        except OSError as e:
            QMessageBox.warning(self, "Create portable version",
                                f"The portable copy could not be created:\n\n{e}")
            return
        self._on_engine_message(f"Portable version created in {target}")
        QMessageBox.information(
            self, "Create portable version",
            f"The portable version is in:\n\n{target}\n\n"
            "Start it with ArkLoad.cmd. It keeps its settings and its download list in the data "
            "folder next to it.")

    # ------------------------------------------------------------------ browser

    def _create_browser_extension(self) -> None:
        """Write the browser extension that sends MediaFire links to ArkLoad."""
        folder = write_extension()
        self._on_engine_message(f"The browser extension is in {folder}")
        QMessageBox.information(
            self, "Browser extension",
            f"The extension was written to:\n\n{folder}\n\n"
            "Load it in your browser with 'Load unpacked' (chrome://extensions or "
            "edge://extensions).")

    # ------------------------------------------------------------------ misc

    def _row_icon(self, record: DownloadRecord):
        colours = styles.palette(self.theme)
        if record.is_folder:
            return styles.make_icon("folder", colours["accent"])
        return styles.make_icon("file", colours["muted"])

    def _on_tab_changed(self, index: int) -> None:
        """The tabs show the downloads, the collected links and the history."""
        if index < 0 or index >= len(TABS):
            return
        name = TABS[index]
        self.pages.setCurrentIndex(TAB_PAGES.get(name, 0))
        self.model.set_category(TAB_CATEGORIES.get(name, ""))
        self._update_status_bar()
        self._refresh_grabber()
        if name == TAB_GRABBER:
            self.details.setVisible(False)
        else:
            self.details.setVisible(bool(self._selected_records()))

    def _refresh_grabber(self) -> None:
        """Fill the link grabber with the links that wait for their turn."""
        rows = []
        for record in self.model.records():
            if record.state == TaskState.QUEUED:
                rows.append({"name": record.title, "size": record.bytes_total,
                             "state": "Queued", "job_id": record.job_id})
        try:
            rows.extend(self.engine.collected_links())
        except Exception:  # noqa: BLE001 - the grabber is a view, it may not break the app
            pass
        self.grabber_model.set_rows(rows)
        if hasattr(self, "grabber_empty"):
            self.grabber_empty.setVisible(not rows)

    def _add_grabbed_links(self) -> None:
        """Add the links that were pasted into the grabber."""
        text = self.grabber_edit.text().strip()
        if not text:
            return
        self.grabber_edit.clear()
        self._add_urls([line.strip() for line in text.splitlines() if line.strip()])

    def _start_grabbed_link(self, index) -> None:
        """Start a link of the grabber (a double click on the row)."""
        rows = self.grabber_model.rows()
        row = index.row()
        if row < 0 or row >= len(rows):
            return
        job_id = rows[row].get("job_id")
        if not job_id:
            self._on_engine_message("This link is already with the engine.")
            return
        record = self.model.record(job_id)
        if record is None:
            return
        self.engine.start_record(record)
        self._on_engine_message(f"{record.title} is starting")

    def _toggle_table_visibility(self) -> None:
        """The list takes the place of the empty state as soon as there is something."""
        has_records = bool(self.model.records())
        self.table.setVisible(has_records)
        self.drop_zone.setVisible(not has_records)

    def _on_rows_changed(self, *args) -> None:
        self._toggle_table_visibility()
        self._update_status_bar()
        self._refresh_grabber()

    def _save_settings(self) -> None:
        settings = self.settings
        settings.update({
            "theme": self.theme,
            "dark": self.theme == "dark",
            "destination": self.engine.options.destination,
            "jobs": self.engine.options.parallel,
            "retries": self.engine.options.retries,
            "recovery_rounds": self.engine.options.recovery_rounds,
            "timeout": self.engine.options.timeout,
            "subfolder": self.engine.options.subfolder,
            "overwrite": self.engine.options.overwrite,
            "slow_mode": self.engine.options.slow_mode,
            "slow_pause": self.engine.options.slow_pause,
            "rate_limit": self.engine.options.rate_limit,
            "connections": self.engine.options.connections,
            "per_host": self.engine.options.per_host,
            "verify_hashes": self.engine.options.verify_hashes,
            "auto_resume": self.engine.options.auto_resume,
            "sort": self.model.sort_mode,
            "filter": self.model.filter_mode,
        })
        save_settings(settings)

    def _on_settings_changed(self, settings: dict) -> None:
        self.settings.update(settings)
        self._save_settings()
        if "engine_port" in settings:
            # the port of the local API of the engine changed
            self.engine.options.engine_port = int(settings.get("engine_port", 3128) or 3128)
            self.engine.reset_jdownloader()

    def _on_preference_action(self, action: str, payload) -> None:
        """The preferences ask the window to do something it cannot do itself."""
        if action == "create_extension":
            self._create_browser_extension()
        elif action == "open_extension":
            from .browser_extension import EXTENSION_DIR
            open_in_file_manager(EXTENSION_DIR)
        elif action == "open_addon_folder":
            folder = settings_store.addons_dir()
            folder.mkdir(parents=True, exist_ok=True)
            open_in_file_manager(folder)

    def _on_preferences_changed(self, options: EngineOptions) -> None:
        self.engine.options = options
        if hasattr(self, "limiter_button"):
            self._update_limiter_button()
        self._save_settings()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        targets = []
        if mime.hasUrls():
            targets = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()] or \
                     [url.toString() for url in mime.urls()]
        elif mime.hasText():
            targets = [line.strip() for line in mime.text().splitlines() if line.strip()]
        for target in targets:
            if is_torrent(target):
                QMessageBox.information(
                    self, "Torrent file",
                    "This build downloads MediaFire links only – torrents are not supported.")
                continue
            try:
                self.engine.add_download(target)
            except Exception as e:  # noqa: BLE001
                self._on_engine_message(f"{target}: {e}")
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Closing the window stops the transfers but keeps the state on disk."""
        active = [record for record in self.model.records() if record.active]
        if active:
            answer = QMessageBox.question(
                self, "Quit",
                f"{len(active)} download(s) are still running.\n\n"
                "Stop them and quit? Finished files are kept and the next start continues "
                "where they stopped.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.engine.stop_all("interrupted")
        self._save_settings()
        self.engine.shutdown()
        event.accept()
