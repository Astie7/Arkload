"""Download records, states and the table model.

The model is deliberately independent from the widgets: the window renders it, the engine
updates it, and the tests can drive it without a running Qt application.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt

try:  # package import
    from .. import mediafire_resume as rs
    from .core import Task
except ImportError:  # direct script run
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mediafire_resume as rs  # type: ignore[no-redef]
    from tools.arkload.core import Task  # type: ignore[no-redef]

#: job level states (mirrors mediafire_resume, plus the "paused" state of the UI)
QUEUED = rs.JOB_QUEUED
RUNNING = rs.JOB_RUNNING
PAUSED = rs.JOB_PAUSED
INTERRUPTED = rs.JOB_INTERRUPTED
CANCELLED = rs.JOB_CANCELLED
COMPLETE = rs.JOB_COMPLETE
INCOMPLETE = rs.JOB_INCOMPLETE
FAILED = rs.JOB_FAILED

#: file level states
FILE_PENDING = rs.FILE_PENDING
FILE_DOWNLOADING = rs.FILE_DOWNLOADING
FILE_COMPLETE = rs.FILE_COMPLETE
FILE_INCOMPLETE = rs.FILE_INCOMPLETE
FILE_MISSING = rs.FILE_MISSING
FILE_VERIFY_FAILED = rs.FILE_VERIFY_FAILED
FILE_FAILED = rs.FILE_FAILED

PRIORITIES = ("HIGH", "MEDIUM", "LOW")
PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW = "HIGH", "MEDIUM", "LOW"

#: the speed limiter presets shown in the status bar (bytes per second)
RATE_PRESETS = (("OFF", 0), ("100 KB/s", 100 * 1024), ("250 KB/s", 250 * 1024),
                ("500 KB/s", 500 * 1024), ("1 MB/s", 1024 * 1024),
                ("2 MB/s", 2 * 1024 * 1024), ("5 MB/s", 5 * 1024 * 1024),
                ("10 MB/s", 10 * 1024 * 1024))

SORT_MODES = ("Newest", "Old", "A-Z", "Z-A", "By size: large", "By size: small",
              "By status: ascending", "By status: descending")
FILTER_MODES = ("All files", "Missing Files", "Active", "Completed", "MediaFire folders",
                "Video", "Music")
SELECTION_MODES = ("All", "Downloads only", "Uploads only", "Completed", "Stopped")


def fmt_size(value: int | None) -> str:
    """Human readable size, the way the reference shows it (323 KB)."""
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.0f} {unit}" if size >= 100 else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def fmt_speed(value: float) -> str:
    """Transfer speed, the way the reference shows it (0 B/s, 1.2 MB/s)."""
    value = max(0.0, float(value))
    if value < 1:
        return "0 B/s"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            return f"{value:.0f} {unit}" if value >= 100 or unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB/s"


def fmt_rate(value: int) -> str:
    """The speed limit as it is shown in the status bar."""
    if not value:
        return "OFF"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.0f} MB/s"
    return f"{value // 1024} KB/s"


def fmt_duration(seconds: float) -> str:
    """A short duration, the way the reference shows the remaining time."""
    total = int(max(0.0, seconds))
    if total < 10:
        return "<10s"
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def remaining_time(record) -> str:
    """How long the download still needs, the way the reference shows it."""
    if not record.active or record.speed_down <= 0 or not record.bytes_total:
        return ""
    left = max(0, record.bytes_total - record.bytes_done)
    if left <= 0:
        return ""
    return f"Remaining: {fmt_duration(left / record.speed_down)}"


def fmt_added(timestamp: float) -> str:
    """'Today 3:51 AM' / 'Yesterday 22:04' / 'Tue 18:20' / '12 Oct 2025'."""
    moment = datetime.fromtimestamp(timestamp)
    now = datetime.now()
    if moment.date() == now.date():
        return f"Today {moment.strftime('%I:%M %p').lstrip('0')}"
    if moment.date() == now.date() - timedelta(days=1):
        return f"Yesterday {moment.strftime('%I:%M %p').lstrip('0')}"
    if (now.date() - moment.date()).days < 7:
        return moment.strftime("%a %H:%M")
    return moment.strftime("%d %b %Y")


#: the record of a download is the task of the core (one object, one state)
DownloadRecord = Task


class DownloadTableModel(QAbstractTableModel):
    """A flat table of downloads, with the sort and filter modes of the reference toolbar."""

    COLUMNS = ("#", "Name", "Size", "Status", "Download", "Upload", "Added", "")
    COLUMN_INDEX, COLUMN_NAME, COLUMN_SIZE, COLUMN_STATUS = 0, 1, 2, 3
    COLUMN_DOWN, COLUMN_UP, COLUMN_ADDED, COLUMN_ACTIONS = 4, 5, 6, 7

    #: the data the status delegate needs to draw the progress bar
    STATUS_ROLE = Qt.UserRole + 1
    #: tells the name delegate that the row can be expanded
    EXPANDER_ROLE = Qt.UserRole + 2

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._records: list[DownloadRecord] = []
        self._visible: list[DownloadRecord] = []
        self.sort_mode = "Newest"
        self.filter_mode = "All files"
        self.selection_mode = "All"
        self.category = "All downloads"
        self.search_text = ""
        #: optional callable ``record -> QIcon`` used for the file/folder icon
        self.icon_provider = None

    # -- data -----------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt naming)
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.COLUMNS[section]
        return section + 1

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._visible[index.row()]
        column = index.column()
        if role == Qt.DisplayRole:
            if column == self.COLUMN_INDEX:
                return index.row() + 1
            if column == self.COLUMN_NAME:
                return record.title
            if column == self.COLUMN_SIZE:
                return fmt_size(record.bytes_total or None)
            if column == self.COLUMN_STATUS:
                return record.status_text()
            if column == self.COLUMN_DOWN:
                return fmt_speed(record.speed_down)
            if column == self.COLUMN_UP:
                return fmt_speed(record.speed_up)
            if column == self.COLUMN_ADDED:
                remaining = remaining_time(record)
                return remaining if remaining else fmt_added(record.added)
            return ""
        if role == Qt.TextAlignmentRole:
            if column in (self.COLUMN_INDEX, self.COLUMN_SIZE, self.COLUMN_DOWN,
                          self.COLUMN_UP):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if column == self.COLUMN_STATUS:
                return int(Qt.AlignLeft | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return self._tooltip(record)
        if role == Qt.DecorationRole and column == self.COLUMN_NAME and self.icon_provider:
            return self.icon_provider(record)
        if role == Qt.ForegroundRole and column == self.COLUMN_STATUS:
            return self._status_colour(record)
        if role == Qt.UserRole:
            return record.job_id
        if role == self.EXPANDER_ROLE and column == self.COLUMN_NAME:
            return bool(record.files)
        if role == self.STATUS_ROLE and column == self.COLUMN_STATUS:
            return {
                "percent": record.percent,
                "text": record.status_text(),
                "priority": record.priority,
                "active": record.active,
                "done": record.bytes_done,
                "size": record.bytes_total,
                "failed": record.status in ("failed", "incomplete"),
            }
        return None

    @staticmethod
    def _status_colour(record: DownloadRecord):
        """The status column is coloured like the reference (cyan while running, green when done)."""
        from PySide6.QtGui import QColor
        if record.status == COMPLETE:
            return QColor("#4CD3FF")
        if record.status in (RUNNING, "downloading"):
            return QColor("#4CD3FF")
        if record.status in (INCOMPLETE, FAILED):
            return QColor("#F87171")
        if record.status in ("cancelled", "interrupted", "paused"):
            return QColor("#FBBF24")
        return None

    def _tooltip(self, record: DownloadRecord) -> str:
        lines = [f"<b>{record.title}</b>", record.url, f"Destination: {record.destination}"]
        if record.total_files:
            lines.append(f"Files: {record.verified_files}/{record.total_files} verified")
            broken = [item for item in record.files if item.status != FILE_COMPLETE]
            for item in broken[:12]:
                lines.append(f"• {item.path}: {item.status.replace('_', ' ')}")
            if len(broken) > 12:
                lines.append(f"… and {len(broken) - 12} more")
        if record.tags:
            lines.append("Tags: " + ", ".join(record.tags))
        return "<br>".join(lines)

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    # -- content --------------------------------------------------------------
    def records(self) -> list[DownloadRecord]:
        return list(self._records)

    def visible_records(self) -> list[DownloadRecord]:
        return list(self._visible)

    def record(self, job_id: str) -> DownloadRecord | None:
        for record in self._records:
            if record.job_id == job_id:
                return record
        return None

    def record_at(self, row: int) -> DownloadRecord | None:
        if 0 <= row < len(self._visible):
            return self._visible[row]
        return None

    def add_record(self, record: DownloadRecord) -> None:
        self._records.append(record)
        self.refresh()

    def remove_record(self, job_id: str) -> bool:
        for index, record in enumerate(self._records):
            if record.job_id == job_id:
                del self._records[index]
                self.refresh()
                return True
        return False

    def clear(self) -> None:
        self.beginResetModel()
        self._records.clear()
        self._visible.clear()
        self.endResetModel()

    def update_record(self, job_id: str) -> None:
        """Tell the view that one record changed."""
        for row, record in enumerate(self._visible):
            if record.job_id == job_id:
                index = self.index(row, 0)
                self.dataChanged.emit(index, self.index(row, self.columnCount() - 1))
                return

    def refresh(self) -> None:
        """Re-apply the search, the filter and the sort order."""
        self.beginResetModel()
        self._visible = [record for record in self._records
                         if record.matches_search(self.search_text)
                         and record.matches_filter(self.filter_mode)
                         and record.matches_selection(self.selection_mode)
                         and record.matches_category(self.category)]
        self._visible.sort(key=self._sort_key)
        self.endResetModel()

    def _sort_key(self, record: DownloadRecord):
        mode = self.sort_mode
        if mode == "Newest":
            return -record.added
        if mode == "Old":
            return record.added
        if mode == "A-Z":
            return record.title.casefold()
        if mode == "Z-A":
            return tuple(-ord(ch) for ch in record.title.casefold())
        if mode == "By size: large":
            return -record.bytes_total
        if mode == "By size: small":
            return record.bytes_total
        if mode == "By status: ascending":
            return (record.status_key(), record.title.casefold())
        if mode == "By status: descending":
            return (-record.status_key(), record.title.casefold())
        return -record.added

    # -- settings -------------------------------------------------------------
    def set_search(self, text: str) -> None:
        self.search_text = text.strip()
        self.refresh()

    def set_filter(self, mode: str) -> None:
        self.filter_mode = mode
        self.refresh()

    def set_selection_mode(self, mode: str) -> None:
        self.selection_mode = mode
        self.refresh()

    def set_category(self, category: str) -> None:
        """The category of the sidebar."""
        self.category = category
        self.refresh()

    def count(self, category: str) -> int:
        """How many downloads a category holds."""
        return sum(1 for record in self._records if record.matches_category(category))

    def set_sort(self, mode: str) -> None:
        self.sort_mode = mode
        self.refresh()


def records_from_manifests(manifests: Iterable[rs.Job]) -> list[DownloadRecord]:
    """Build table records from the persisted manifests."""
    records: list[DownloadRecord] = []
    for job in manifests:
        record = DownloadRecord.from_dict({
            "job_id": job.job_id, "url": job.source_url, "title": job.title or job.source_key,
            "destination": job.destination, "source_kind": job.source_kind,
            "source_key": job.source_key, "root": job.root, "status": job.status,
        })
        record.files = list(job.files)
        record.bytes_total = job.total_bytes
        record.bytes_done = job.verified_bytes
        records.append(record)
    return records
