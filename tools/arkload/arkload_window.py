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
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QSizePolicy, QStackedWidget,
                               QStyledItemDelegate, QStyle, QTableView, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

try:  # package import
    from .. import mediafire_resume as rs
except ImportError:  # direct script run
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mediafire_resume as rs  # type: ignore[no-redef]

from . import styles
from .addons import AddonManager
from .browser_extension import write_extension
from .converter import convert, find_ffmpeg
from .dialogs import (AboutDialog, AddDownloadDialog, AddonsDialog, ConvertDialog,
                       DeleteDownloadsDialog, MirrorDialog, MoveDialog, RenameDialog,
                       ScheduleDialog, TagsDialog, TextReportDialog, is_torrent)
from .portable import create_portable
from .download_engine import (DownloadEngine, EngineOptions, REPO_URL, open_in_file_manager,
                              open_url)
from .download_model import (DownloadRecord, DownloadTableModel, FILTER_MODES, PRIORITY_HIGH,
                             PRIORITY_LOW, PRIORITY_MEDIUM, RATE_PRESETS, RUNNING,
                             SELECTION_MODES, SORT_MODES, fmt_added, fmt_duration, fmt_rate,
                             fmt_size, fmt_speed)
from .preferences_view import PreferencesView
from . import settings as settings_store
from .settings import default_destination, load_settings, save_settings

APP_NAME = "ArkLoad"
APP_SHORT_NAME = "ArkLoad"
VERSION = "1.0"
TORRENT_SUPPORTED = False          # this build downloads MediaFire links only


class DropZone(QWidget):
    """The dashed area that accepts dropped links."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(False)      # the main window handles the drop
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.colours = dict(styles.DARK)

    def set_colours(self, colours: dict) -> None:
        self.colours = dict(colours)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(self.colours.get("border", "#333336")))
        pen.setStyle(Qt.DashLine)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

        centre = self.rect().center()
        icon_size = 18
        gap = 8
        text = "Drag-and-drop a MediaFire link or folder key"
        font = QFont(self.font())
        font.setPixelSize(15)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(text)
        group_width = icon_size + gap + text_width
        start_x = centre.x() - group_width // 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.colours.get("muted", "#8E8E93")))
        self._draw_drop_glyph(painter, start_x, centre.y() - icon_size // 2, icon_size)
        painter.setPen(QColor(self.colours.get("muted", "#8E8E93")))
        painter.drawText(start_x + icon_size + gap, centre.y() + 5, text)
        painter.end()

    def _draw_drop_glyph(self, painter: QPainter, x: int, y: int, size: int) -> None:
        """A small 'drag' arrow, drawn with the painter so no image files are needed."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainterPath
        painter.save()
        pen = QPen(QColor(self.colours.get("muted", "#8E8E93")))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(QPointF(x + size * 0.15, y + size * 0.05))
        path.lineTo(QPointF(x + size * 0.15, y + size * 0.75))
        path.lineTo(QPointF(x + size * 0.42, y + size * 0.5))
        path.lineTo(QPointF(x + size * 0.62, y + size * 0.9))
        path.lineTo(QPointF(x + size * 0.78, y + size * 0.82))
        path.lineTo(QPointF(x + size * 0.58, y + size * 0.42))
        path.lineTo(QPointF(x + size * 0.9, y + size * 0.35))
        path.closeSubpath()
        painter.drawPath(path)
        painter.restore()


class DownloadTableView(QTableView):
    """The download table, with a context menu and a per-row actions button."""

    context_requested = Signal(QPoint)
    actions_requested = Signal(QPoint)
    pause_requested = Signal()
    cancel_requested = Signal()

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
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.cancel_requested.emit()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.pos())
        if index.isValid():
            self.setCurrentIndex(index)
        self.context_requested.emit(event.globalPos())


class NameDelegate(QStyledItemDelegate):
    """Draws the name cell: a folder icon, the name and the expander of the reference."""

    expanded_requested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colours = dict(styles.DARK)

    def _expander_rect(self, option) -> QRectF:
        rect = option.rect.adjusted(6, 0, -6, 0)
        return QRectF(rect.left(), rect.center().y() - 5, 11, 11)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(6, 0, -6, 0)
        x = rect.left()
        if index.data(DownloadTableModel.EXPANDER_ROLE):
            box = self._expander_rect(option)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.colours.get("muted", "#8E8E93")))
            path = QPainterPath()
            path.moveTo(box.left() + 3, box.top() + 2)
            path.lineTo(box.left() + 9, box.center().y())
            path.lineTo(box.left() + 3, box.bottom() - 2)
            path.closeSubpath()
            painter.drawPath(path)
            x = box.right() + 6
        icon = index.data(Qt.DecorationRole)
        if icon is not None:
            icon.paint(painter, QRect(int(x), option.rect.center().y() - 8, 16, 16))
            x += 22
        painter.setPen(option.palette.color(QPalette.Text))
        painter.drawText(QRect(int(x), option.rect.top(), option.rect.right() - int(x),
                               option.rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, str(index.data(Qt.DisplayRole) or ""))
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
    """Draws the status cell of the reference: a progress bar, the percentage and the priority."""

    #: the row whose priority chip was clicked
    priority_requested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colours = dict(styles.DARK)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        data = index.data(DownloadTableModel.STATUS_ROLE) or {}
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(6, 0, -6, 0)
        text = str(data.get("text") or "")
        percent = int(data.get("percent") or 0)
        show_bar = data.get("active") or percent > 0

        if not show_bar:
            painter.setPen(option.palette.color(QPalette.Text))
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            painter.restore()
            return

        font = painter.font()
        metrics = QFontMetrics(font)
        percent_text = f"{percent}%"
        bar_width = max(40, min(110, rect.width() // 3))
        text_width = metrics.horizontalAdvance(percent_text)
        bar_rect = QRectF(rect.left(), rect.center().y() - 3, bar_width, 6)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.colours.get("border", "#333336")))
        painter.drawRoundedRect(bar_rect, 3, 3)
        if percent > 0:
            filled = QRectF(bar_rect)
            filled.setWidth(max(3.0, bar_rect.width() * percent / 100.0))
            painter.setBrush(QColor(self.colours.get("accent", "#4CD3FF")))
            painter.drawRoundedRect(filled, 3, 3)

        painter.setPen(QColor(self.colours.get("muted", "#8E8E93")))
        painter.drawText(QRectF(bar_rect.right() + 8, rect.top(), text_width + 4, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, percent_text)

        # the priority, the way the reference shows it in the row
        if not data.get("failed"):
            chip = "⌄ " + str(data.get("priority") or "HIGH")
            chip_x = bar_rect.right() + 16 + text_width
            if chip_x + metrics.horizontalAdvance(chip) < rect.right():
                painter.setPen(QColor(self.colours.get("success", "#22C55E")))
                painter.drawText(QRectF(chip_x, rect.top(), 120, rect.height()),
                                 Qt.AlignLeft | Qt.AlignVCenter, chip)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        """A click on the priority chip opens the priority menu."""
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            data = index.data(DownloadTableModel.STATUS_ROLE) or {}
            if not data.get("failed") and data.get("priority"):
                rect = option.rect.adjusted(6, 0, -6, 0)
                metrics = QFontMetrics(self.parent().font() if self.parent() else QFont())
                bar_width = max(40, min(110, rect.width() // 3))
                chip_x = rect.left() + bar_width + 16 + metrics.horizontalAdvance("100%")
                if chip_x <= event.pos().x() <= chip_x + 80:
                    self.priority_requested.emit(index)
                    return True
        return super().editorEvent(event, model, option, index)


class DetailsPanel(QWidget):
    """The details card at the bottom: progress, speeds, the path and the links.

    It is collapsed by default and only opened with the arrow in the status bar or by
    clicking a download in the list.
    """

    priority_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DetailsCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(16)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(46, 46)
        self.icon_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.icon_label.setPixmap(styles.make_icon("folder", "#4CD3FF").pixmap(46, 46))
        layout.addWidget(self.icon_label, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(4)
        self.title_label = QLabel("")
        self.title_label.setObjectName("Title")
        info.addWidget(self.title_label)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self.path_label = QLabel("")
        self.path_label.setObjectName("Muted")
        path_row.addWidget(self.path_label)
        copy_button = QToolButton()
        copy_button.setIcon(styles.make_icon("clipboard", "#8E8E93"))
        copy_button.setFixedSize(20, 20)
        copy_button.setToolTip("Copy the path")
        copy_button.clicked.connect(self._copy_path)
        path_row.addWidget(copy_button)
        path_row.addStretch(1)
        info.addLayout(path_row)

        bar_row = QHBoxLayout()
        bar_row.setSpacing(8)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(8)
        bar_row.addWidget(self.progress_bar, 1)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Muted")
        bar_row.addWidget(self.percent_label)
        self.remaining_label = QLabel("")
        self.remaining_label.setObjectName("Muted")
        bar_row.addWidget(self.remaining_label)
        info.addLayout(bar_row)

        self.stats_row = QLabel("")
        self.stats_row.setObjectName("Muted")
        info.addWidget(self.stats_row)

        self.priority_link = QLabel("Priority: high")
        self.priority_link.setStyleSheet("color: #22C55E;")
        self.priority_link.setCursor(Qt.PointingHandCursor)
        self.priority_link.mousePressEvent = lambda _event: self.priority_requested.emit()
        info.addWidget(self.priority_link)

        self.page_link = QLabel("Web page:")
        self.page_link.setStyleSheet("color: #4CD3FF;")
        self.page_link.setOpenExternalLinks(False)
        self.page_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        info.addWidget(self.page_link)

        self.file_link = QLabel("File:")
        self.file_link.setStyleSheet("color: #4CD3FF;")
        self.file_link.setOpenExternalLinks(False)
        self.file_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        info.addWidget(self.file_link)
        layout.addLayout(info, 1)

        # the state of every file of this download
        files_box = QVBoxLayout()
        files_box.setSpacing(2)
        caption = QLabel("Files")
        caption.setObjectName("Muted")
        files_box.addWidget(caption)
        self.progress_list = QTreeWidget()
        self.progress_list.setHeaderLabels(["File", "Size", "State"])
        self.progress_list.setRootIsDecorated(False)
        self.progress_list.setColumnWidth(0, 300)
        self.progress_list.setColumnWidth(1, 70)
        self.progress_list.setColumnWidth(2, 90)
        self.progress_list.setFixedWidth(460)
        files_box.addWidget(self.progress_list, 1)
        layout.addLayout(files_box, 1)

        self.close_button = QToolButton()
        self.close_button.setText("✕")
        self.close_button.setFixedSize(22, 22)
        self.close_button.setToolTip("Hide the details")
        self.close_button.clicked.connect(self._hide)
        layout.addWidget(self.close_button, 0, Qt.AlignTop)

    def _copy_path(self) -> None:
        QApplication.clipboard().setText(self.path_label.text())

    def _hide(self) -> None:
        self.setVisible(False)

    def show_record(self, record, options=None) -> None:
        """Fill the card with the details of one download."""
        if record is None:
            self.setVisible(False)
            return
        self.title_label.setText(record.title)
        self.path_label.setText(str(record.root or record.destination))
        self.icon_label.setPixmap(styles.make_icon(
            "folder" if record.is_folder else "file", "#4CD3FF").pixmap(46, 46))

        percent = record.percent
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        remaining = self._remaining(record)
        self.remaining_label.setText(f"Remaining: {remaining}" if remaining else "")
        self.stats_row.setText(
            f"Downloaded: {fmt_size(record.bytes_done)} / {fmt_size(record.bytes_total)}"
            f"      Added at: {fmt_added(record.added)}"
            f"      Download speed: {fmt_speed(record.speed_down)}"
            f"      Upload speed: {fmt_speed(record.speed_up)}")
        self.priority_link.setText(f"Priority: {record.priority.lower()}")
        colour = {"HIGH": "#22C55E", "MEDIUM": "#E0A93A", "LOW": "#EF5F5F"}.get(
            record.priority, "#22C55E")
        self.priority_link.setStyleSheet(f"color: {colour};")
        self.page_link.setText(f"Web page: {record.url}")
        self.page_link.linkActivated.connect(open_url)
        self.file_link.setText(f"File: {record.url}")
        self.file_link.linkActivated.connect(open_url)

        self.progress_list.clear()
        for item in record.files:
            state = {
                "complete": "Verified", "missing": "Missing", "incomplete": "Incomplete",
                "verification_failed": "Verification failed", "failed": "Failed",
                "downloading": "Downloading", "pending": "Queued",
            }.get(item.status, item.status)
            self.progress_list.addTopLevelItem(
                QTreeWidgetItem([item.path, fmt_size(item.size), state]))

        options = options or {}
        self.connection_info = (
            f"{options.get('connections', 1)} connection(s) per file · "
            f"{options.get('jobs', 1)} file(s) in parallel · "
            f"{options.get('retries', 3)} attempt(s) per file · "
            f"limit {fmt_rate(int(options.get('rate_limit', 0) or 0))}")

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
        self.update_checked.connect(self._on_update_checked)
        self.source_checked.connect(self._on_source_checked)
        self._connect_engine()
        self._load_addons()
        QShortcut(QKeySequence.Paste, self, activated=self._paste_from_clipboard)
        self._restore_downloads()
        self._update_status_bar()
        QTimer.singleShot(400, self._auto_resume)

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
        )

    def _build(self) -> None:
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

    # -- dashboard ------------------------------------------------------------
    def _build_dashboard(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("Toolbar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(14, 10, 14, 10)
        bar.setSpacing(8)

        self.select_all_button = QToolButton()
        self.select_all_button.setCheckable(True)
        self.select_all_button.setFixedSize(28, 28)
        self.select_all_button.setToolTip("Select all downloads")
        self.select_all_button.setIcon(styles.make_icon("checkbox",
                                                        styles.palette(self.theme)["text"]))
        self.select_all_button.clicked.connect(self._toggle_select_all)
        bar.addWidget(self.select_all_button)

        self.selection_button = QToolButton()
        self.selection_button.setFixedSize(28, 28)
        self.selection_button.setIcon(styles.make_icon("chevron", styles.palette(self.theme)["muted"]))
        self.selection_button.setToolTip("Show: all / downloads only / uploads only / completed / "
                                         "stopped")
        self.selection_button.setPopupMode(QToolButton.InstantPopup)
        self.selection_mode = "All"
        self.selection_menu = QMenu(self)
        self.selection_actions = {}
        for mode in SELECTION_MODES:
            action = self.selection_menu.addAction(mode)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=mode:
                                     self._on_selection_mode(value))
            self.selection_actions[mode] = action
        self.selection_button.setMenu(self.selection_menu)
        bar.addWidget(self.selection_button)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(SORT_MODES)
        self.sort_combo.setFixedWidth(120)
        self.sort_combo.currentTextChanged.connect(self._on_sort)
        bar.addWidget(self.sort_combo)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(FILTER_MODES)
        self.filter_combo.setFixedWidth(120)
        self.filter_combo.currentTextChanged.connect(self._on_filter)
        bar.addWidget(self.filter_combo)

        # the actions of the selection, they sit right next to the filters
        self.selection_bar = QWidget()
        selection_layout = QHBoxLayout(self.selection_bar)
        selection_layout.setContentsMargins(8, 0, 0, 0)
        selection_layout.setSpacing(6)
        self.selected_label_toolbar = QLabel("")
        self.selected_label_toolbar.setObjectName("Tool.TLabel")
        selection_layout.addWidget(self.selected_label_toolbar)
        self.resume_all_button = QToolButton()
        self.resume_all_button.setIcon(styles.make_icon("play", styles.palette(self.theme)["text"]))
        self.resume_all_button.setToolTip("Continue the selected downloads")
        self.resume_all_button.clicked.connect(self._resume_selected)
        selection_layout.addWidget(self.resume_all_button)
        self.pause_all_button = QToolButton()
        self.pause_all_button.setIcon(styles.make_icon("pause", styles.palette(self.theme)["text"]))
        self.pause_all_button.setToolTip("Pause the selected downloads")
        self.pause_all_button.clicked.connect(self._pause_selected)
        selection_layout.addWidget(self.pause_all_button)
        self.remove_all_button = QToolButton()
        self.remove_all_button.setIcon(styles.make_icon("trash", styles.palette(self.theme)["text"]))
        self.remove_all_button.setToolTip("Delete the selected downloads")
        self.remove_all_button.clicked.connect(self._delete_selected)
        selection_layout.addWidget(self.remove_all_button)
        self.selection_bar.setVisible(False)
        bar.addWidget(self.selection_bar)

        self.add_button = QPushButton("+ Add download")
        self.add_button.setObjectName("Accent")
        self.add_button.setIcon(styles.make_icon("plus", styles.palette(self.theme)["accent_text"]))
        self.add_button.clicked.connect(self._add_download)
        bar.addWidget(self.add_button)

        bar.addStretch(1)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search in downloads...")
        self.search_icon = QAction("Search", self)
        self.search_icon.setIcon(styles.make_icon("search", styles.palette(self.theme)["muted"]))
        self.search_edit.addAction(self.search_icon, QLineEdit.LeadingPosition)
        self.search_edit.setFixedWidth(260)
        self.search_edit.textChanged.connect(self._on_search)
        bar.addWidget(self.search_edit)

        self.menu_button = QToolButton()
        self.menu_button.setText("≡")
        self.menu_button.setFixedSize(30, 30)
        self.menu_button.setToolTip("Main menu")
        self.menu_button.clicked.connect(self._show_main_menu)
        bar.addWidget(self.menu_button)

        layout.addWidget(toolbar)
        self.toolbar = toolbar

        # download area: the table on top, the drop zone below it
        area = QFrame()
        area.setObjectName("DownloadArea")
        area_layout = QVBoxLayout(area)
        area_layout.setContentsMargins(1, 1, 1, 1)
        area_layout.setSpacing(0)
        outer = QVBoxLayout()
        outer.setContentsMargins(10, 8, 10, 8)

        self.table = DownloadTableView()
        self.table.setModel(self.model)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.actions_requested.connect(self._on_actions_clicked)
        self.table.doubleClicked.connect(self._open_selected)
        self.table.pause_requested.connect(self._toggle_selected_pause)
        self.table.cancel_requested.connect(self._cancel_selected)
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
        header.setSectionResizeMode(DownloadTableModel.COLUMN_ADDED, QHeaderView.Fixed)
        header.setSectionResizeMode(DownloadTableModel.COLUMN_ACTIONS, QHeaderView.Fixed)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_INDEX, 36)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_SIZE, 80)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_STATUS, 210)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_DOWN, 85)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_UP, 80)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_ADDED, 120)
        self.table.setColumnWidth(DownloadTableModel.COLUMN_ACTIONS, 30)
        self.name_delegate = NameDelegate(self.table)
        self.name_delegate.colours = dict(styles.palette(self.theme))
        self.name_delegate.expanded_requested.connect(self._on_row_expanded)
        self.table.setItemDelegateForColumn(DownloadTableModel.COLUMN_NAME, self.name_delegate)
        self.status_delegate = StatusDelegate(self.table)
        self.status_delegate.colours = dict(styles.palette(self.theme))
        self.status_delegate.priority_requested.connect(self._on_priority_clicked)
        self.table.setItemDelegateForColumn(DownloadTableModel.COLUMN_STATUS, self.status_delegate)
        area_layout.addWidget(self.table)

        self.drop_zone = DropZone()
        area_layout.addWidget(self.drop_zone, 1)
        outer.addWidget(area, 1)

        self.details = DetailsPanel()
        self.details.priority_requested.connect(self._show_priority_menu)
        self.details.setVisible(False)        # collapsed, like the reference
        outer.addWidget(self.details)

        layout.addLayout(outer)
        self.download_area = area
        return page

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
        """The slim bar at the bottom: limiter, live speeds, priority and the selection."""
        bar = QFrame()
        bar.setObjectName("StatusBar")
        self.status = bar
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        # the speed limiter - a chip that shows the current limit and opens the presets
        self.limiter_button = QToolButton()
        self.limiter_button.setText("OFF")
        self.limiter_button.setIcon(styles.make_icon("snail", styles.palette(self.theme)["muted"]))
        self.limiter_button.setToolTip("Speed limiter")
        self.limiter_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.limiter_button.setPopupMode(QToolButton.InstantPopup)
        self.limiter_menu = QMenu(self)
        self.limiter_menu.setToolTipsVisible(True)
        for label, value in RATE_PRESETS:
            action = self.limiter_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, limit=value:
                                     self._set_rate_limit(limit))
        self.limiter_button.setMenu(self.limiter_menu)
        layout.addWidget(self.limiter_button)

        self.gear_button = QToolButton()
        self.gear_button.setIcon(styles.make_icon("gear", styles.palette(self.theme)["muted"]))
        self.gear_button.setFixedSize(24, 24)
        self.gear_button.setToolTip("Preferences")
        self.gear_button.clicked.connect(self._show_preferences)
        layout.addWidget(self.gear_button)

        self.down_speed_label = QLabel("▼ 0 B/s")
        self.up_speed_label = QLabel("▲ 0 B/s")
        self.down_speed_label.setObjectName("Speed.TLabel")
        self.up_speed_label.setObjectName("Speed.TLabel")
        self.down_speed_label.setToolTip("Download speed")
        self.up_speed_label.setToolTip("Upload speed")
        layout.addWidget(self.down_speed_label)
        layout.addWidget(self.up_speed_label)

        self.message_label = QLabel("")
        self.message_label.setObjectName("StatusMuted.TLabel")
        layout.addWidget(self.message_label)

        self.priority_button = QPushButton(PRIORITY_HIGH)
        self.priority_button.setObjectName("Priority")
        self.priority_button.setToolTip("Traffic priority")
        self.priority_button.clicked.connect(self._show_priority_menu)
        layout.addWidget(self.priority_button)

        layout.addStretch(1)

        self.details_button = QToolButton()
        self.details_button.setText("")
        self.details_button.setStyleSheet("background: transparent; border: none; color: "
                                          + styles.palette(self.theme)["muted"] + ";")
        self.details_button.setToolTip("Show the details of the selected download")
        self.details_button.clicked.connect(self._toggle_details)
        layout.addWidget(self.details_button)
        return bar

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
        for widget, icon in ((self.sort_combo, "sort"), (self.filter_combo, "filter")):
            picture = styles.make_icon(icon, colours["muted"])
            for index in range(widget.count()):
                widget.setItemIcon(index, picture)
        self.search_icon.setIcon(styles.make_icon("search", colours["muted"]))
        self.add_button.setIcon(styles.make_icon("plus", colours["accent_text"]))
        self.limiter_button.setIcon(styles.make_icon("snail", colours["muted"]))
        self._save_settings()

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
        """Bring back the downloads that were in the list when the app was closed."""
        meta = self.engine.load_meta()
        for record in self.engine.load_records():
            saved = meta.get(record.job_id)
            if saved:
                record.priority = saved.get("priority", record.priority)
                record.tags = list(saved.get("tags") or [])
                record.title = saved.get("title", record.title)
                record.start_after = float(saved.get("start_after") or 0.0)
            self.model.add_record(record)
        self.model.set_sort(self.sort_combo.currentText())
        self.model.set_filter(self.filter_combo.currentText())
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

    def _on_job_added(self, job_id: str) -> None:
        record = self.engine.record(job_id)
        if record is not None and self.model.record(job_id) is None:
            self.model.add_record(record)
        self._toggle_table_visibility()

    def _on_job_changed(self, job_id: str) -> None:
        self.model.update_record(job_id)
        record = self.model.record(job_id)
        if record is not None:
            self.engine.persist_meta(record)
            self.addons.fire("status_change", record)
        self._update_details()

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
        record = self.model.record(job_id)
        if record is not None:
            self.addons.fire("after_download", record)
        self._maybe_exit()

    def _on_job_error(self, job_id: str, message: str) -> None:
        self.model.update_record(job_id)
        self._on_engine_message(message)

    def _on_engine_message(self, message: str) -> None:
        self.message_label.setText(message)

    def _on_stats(self, down: float, up: float, active: int, total: int) -> None:
        self.down_speed_label.setText(f"▼ {fmt_speed(down)}")
        self.up_speed_label.setText(f"▲ {fmt_speed(up)}")
        colour = "#E8B84B" if (self.engine.options.slow_mode
                                or self.engine.options.rate_limit) else "#22C55E"
        self.down_speed_label.setStyleSheet(f"color: {colour};")
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
        colour = {"HIGH": "#22C55E", "MEDIUM": "#E0A93A", "LOW": "#EF5F5F"}.get(priority,
                                                                             "#22C55E")
        self.priority_button.setStyleSheet(f"color: {colour}; border-color: {colour};")
        self._update_details_button()

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
        self._save_settings()

    def _on_filter(self, text: str) -> None:
        self.model.set_filter(text)
        self._save_settings()

    def _on_selection_mode(self, text: str) -> None:
        self.selection_mode = text
        for mode, action in self.selection_actions.items():
            action.setChecked(mode == text)
        self.model.set_selection_mode(text)
        self._save_settings()

    def _on_search(self, text: str) -> None:
        self.model.set_search(text)
        self._toggle_table_visibility()

    def _toggle_select_all(self, checked: bool) -> None:
        if checked:
            self.table.selectAll()
        else:
            self.table.clearSelection()
        colours = styles.palette(self.theme)
        self.select_all_button.setIcon(styles.make_icon(
            "checkbox_checked" if checked else "checkbox", colours["accent"] if checked
            else colours["text"]))

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
        """The reference dialog: delete the files or only take them out of the list."""
        selected = self._selected_records()
        if not selected:
            return
        dialog = DeleteDownloadsDialog(self, selected)
        if dialog.exec() != DeleteDownloadsDialog.Accepted:
            return
        if dialog.delete_files:
            for record in selected:
                if record.root and Path(record.root).exists():
                    shutil.rmtree(record.root, ignore_errors=True)
        for record in selected:
            self.engine.remove_from_list(record, delete_files=False)
            self.model.remove_record(record.job_id)
        self.rebuild_tree()
        self._update_status_bar()

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
        """The right click menu of a row (position is inside the table)."""
        self._show_row_menu(self.table.viewport().mapToGlobal(position))

    def _on_actions_clicked(self, position: QPoint) -> None:
        """The three dot button of a row."""
        self._show_row_menu(self.table.viewport().mapToGlobal(position))

    def _build_row_menu(self, record: DownloadRecord) -> QMenu:
        """The context menu of one download, in the order of the reference."""
        menu = QMenu(self)

        # the download controls, the state decides what can be used
        running = record.status in (RUNNING, "downloading")
        paused = record.status == "paused"
        pause_action = menu.addAction("Pause")
        pause_action.setEnabled(running and not paused)
        pause_action.triggered.connect(lambda: self._pause_record(record))
        resume_action = menu.addAction("Resume")
        resume_action.setEnabled(paused or not running)
        resume_action.triggered.connect(lambda: self._resume_record(record))
        cancel_action = menu.addAction("Cancel")
        cancel_action.setEnabled(running or paused)
        cancel_action.triggered.connect(lambda: self._cancel_record(record))
        menu.addSeparator()

        open_action = menu.addAction("Open")
        open_action.setEnabled(bool(record.root))
        open_action.triggered.connect(lambda: self._open_record(record))

        show_action = menu.addAction("Show in folder")
        show_action.setEnabled(bool(record.root))
        show_action.triggered.connect(lambda: self._show_in_folder(record))

        priority_menu = menu.addMenu("Set priority")
        for priority in (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW):
            action = priority_menu.addAction(priority)
            action.triggered.connect(lambda _checked=False, value=priority:
                                     self.engine.set_priority(record, value))

        rename_action = menu.addAction("Rename file")
        rename_action.setEnabled(bool(record.root))
        rename_action.triggered.connect(lambda: self._rename_record(record))

        move_action = menu.addAction("Move to...")
        move_action.setEnabled(bool(record.root))
        move_action.triggered.connect(lambda: self._move_record(record))

        delete_action = menu.addAction("Delete file")
        delete_action.setEnabled(bool(record.root))
        delete_action.triggered.connect(lambda: self._delete_files(record))

        remove_action = menu.addAction("Remove from list")
        remove_action.triggered.connect(lambda: self._remove_from_list(record))
        menu.addSeparator()

        mirror = menu.addAction("Add mirror")
        mirror.triggered.connect(lambda: self._add_mirror(record))

        page_action = menu.addAction("Open download page")
        page_action.triggered.connect(lambda: open_url(record.url))

        copy_action = menu.addAction("Copy link")
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(record.url))

        update_action = menu.addAction("Check for update")
        update_action.triggered.connect(lambda: self._check_record_update(record))

        export_action = menu.addAction("Export selected downloads")
        export_action.triggered.connect(self._export_downloads)

        integrity_action = menu.addAction("File integrity")
        integrity_action.triggered.connect(lambda: self._show_integrity(record))
        menu.addSeparator()

        schedule_action = menu.addAction("Schedule")
        schedule_action.triggered.connect(lambda: self._schedule_record(record))

        change_action = menu.addAction("Change URL")
        change_action.triggered.connect(lambda: self._change_url(record))

        for name, kind in (("Convert to mp3", "mp3"), ("Convert to mp4", "mp4")):
            action = menu.addAction(name)
            action.triggered.connect(lambda _checked=False, value=kind:
                                     self._convert_record(record, value))

        virus = menu.addAction("Perform virus check")
        virus.setEnabled(False)
        virus.setToolTip("No virus scanner is available on this machine.")

        tag_menu = menu.addMenu("Add tag")
        tag_action = tag_menu.addAction("Tags...")
        tag_action.triggered.connect(lambda: self._edit_tags(record))

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
                                   history=self._destination_history())
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

    def _toggle_details(self) -> None:
        """The arrow in the status bar opens and closes the details card."""
        visible = self.details.isVisible()
        self.details.setVisible(not visible)
        self._update_details_button()

    def _update_details_button(self) -> None:
        """The button shows the name of the selected download and the arrow."""
        selected = self._selected_records()
        record = selected[0] if selected else (self.model.records()[0]
                                               if self.model.records() else None)
        arrow = "▼" if self.details.isVisible() else "▲"
        name = record.title if record is not None else ""
        self.details_button.setText(f"{name} {arrow}".strip())

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
        """The toolbar shows the selection actions, the way the reference does."""
        selected = self._selected_records()
        self.selection_bar.setVisible(bool(selected))
        if selected:
            total = sum(record.bytes_total for record in selected)
            self.selected_label_toolbar.setText(
                f"{len(selected)} selected · {fmt_size(total) if total else '0 B'}")
        for button in (self.resume_all_button, self.pause_all_button, self.remove_all_button):
            button.setEnabled(bool(selected))

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
        record.status = "cancelled"
        self._update_job_row(record)
        self._on_engine_message(f"{record.title} was cancelled")

    def _pause_record(self, record: DownloadRecord) -> None:
        """Pause a running download; the bytes that are already there are kept."""
        self.engine.pause(record)
        record.status = "paused"
        self._update_job_row(record)
        self._on_engine_message(f"{record.title} was paused")

    def _resume_record(self, record: DownloadRecord) -> None:
        """Continue a paused download, or start it again from the manifest."""
        self.engine.resume(record)
        record.status = "running"
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
        answer = QMessageBox.question(
            self, "Delete file",
            f"Delete the downloaded files of '{record.title}'?\n\n"
            "The entry stays in the list and can be downloaded again.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        if record.root and Path(record.root).exists():
            shutil.rmtree(record.root, ignore_errors=True)
        self._on_engine_message(f"Deleted the files of {record.title}")

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
        self.engine.persist_meta(record)
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

    def _toggle_table_visibility(self) -> None:
        self.table.setVisible(bool(self.model.records()))

    def _on_rows_changed(self, *args) -> None:
        self._toggle_table_visibility()
        self._update_status_bar()

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
            "verify_hashes": self.engine.options.verify_hashes,
            "auto_resume": self.engine.options.auto_resume,
            "sort": self.sort_combo.currentText(),
            "filter": self.filter_combo.currentText(),
        })
        save_settings(settings)

    def _on_settings_changed(self, settings: dict) -> None:
        self.settings.update(settings)
        self._save_settings()

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
        event.accept()
