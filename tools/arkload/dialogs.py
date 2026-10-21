"""The dialogs of the application: Add download, rename, move, tags, schedule, about."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QTimeEdit, QVBoxLayout, QWidget)

try:  # package import
    from .. import mediafire_dl as dl
except ImportError:  # direct script run
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mediafire_dl as dl  # type: ignore[no-redef]

from .styles import make_icon

TORRENT_SUFFIXES = (".torrent",)


def is_torrent(path: str) -> bool:
    return str(path).lower().endswith(TORRENT_SUFFIXES)


class AddDownloadDialog(QDialog):
    """The 'New download' window: the link, where it goes, the name and the schedule."""

    def __init__(self, parent: QWidget | None = None, destination: str = "", url: str = "",
                 history: list[str] | None = None, resolver=None):
        super().__init__(parent)
        self.setWindowTitle("New download")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.selected_file: str = ""
        self.accepted_url: str = ""
        self.accepted: dict | None = None
        #: tells which engine runs a link (``url -> "ArkLoad" | "JDownloader"``)
        self.resolver = resolver

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("New download")
        title.setObjectName("Heading")
        layout.addWidget(title)

        label = QLabel("Link")
        label.setObjectName("FieldLabel")
        label.setMaximumWidth(400)
        layout.addWidget(label)
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://www.mediafire.com/folder/abc123")
        self.url_edit.setClearButtonEnabled(True)
        url_row.addWidget(self.url_edit, 1)
        paste = QPushButton("Paste")
        paste.setIcon(make_icon("clipboard", "#8E8E93"))
        paste.clicked.connect(self._paste)
        url_row.addWidget(paste)
        layout.addLayout(url_row)

        hint = QLabel("A MediaFire folder, a MediaFire file, a bare folder key such as "
                      "dpqhf3ughd4j5 - or any other link, which the JDownloader engine "
                      "downloads.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.engine_label = QLabel("")
        self.engine_label.setObjectName("Muted")
        layout.addWidget(self.engine_label)

        label = QLabel("Save to")
        label.setObjectName("FieldLabel")
        label.setMaximumWidth(400)
        layout.addWidget(label)
        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self.destination_combo = QComboBox()
        self.destination_combo.setEditable(True)
        folders = list(history or [])
        if destination and destination not in folders:
            folders.insert(0, destination)
        self.destination_combo.addItems(folders)
        self.destination_combo.setCurrentText(destination)
        self.destination_combo.setMinimumWidth(360)
        save_row.addWidget(self.destination_combo, 1)
        folder_btn = QPushButton()
        folder_btn.setIcon(make_icon("folder", "#8E8E93"))
        folder_btn.setToolTip("Choose a folder")
        folder_btn.setFixedSize(34, 30)
        folder_btn.clicked.connect(self._choose_folder)
        save_row.addWidget(folder_btn)
        layout.addLayout(save_row)

        label = QLabel("File name")
        label.setObjectName("FieldLabel")
        label.setMaximumWidth(400)
        layout.addWidget(label)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("The name of the folder that is created")
        layout.addWidget(self.name_edit)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.schedule_check = QCheckBox("Scheduler")
        self.schedule_check.setToolTip("Start the download later")
        self.schedule_check.toggled.connect(self._toggle_schedule)
        bottom.addWidget(self.schedule_check)
        self.schedule_time = QTimeEdit(QTime.currentTime().addSecs(3600))
        self.schedule_time.setDisplayFormat("HH:mm")
        self.schedule_time.setVisible(False)
        bottom.addWidget(self.schedule_time)
        bottom.addStretch(1)
        self.space_label = QLabel("")
        self.space_label.setObjectName("Muted")
        bottom.addWidget(self.space_label)
        layout.addLayout(bottom)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok_button = QPushButton("DOWNLOAD")
        self.ok_button.setObjectName("Accent")
        self.ok_button.clicked.connect(self._accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        self.url_edit.setFocus()
        self.url_edit.textChanged.connect(self._validate)
        self.destination_combo.currentTextChanged.connect(self._update_space)
        self._update_space()

    # -- helpers --------------------------------------------------------------
    def _validate(self) -> None:
        text = self.url_edit.text().strip()
        self.ok_button.setEnabled(bool(text) and not is_torrent(text))
        self._show_engine(text)

    def _show_engine(self, text: str) -> None:
        """Tell the user which engine will run the link."""
        if not text or self.resolver is None:
            self.engine_label.setText("")
            return
        try:
            engine = self.resolver(text)
        except Exception:  # noqa: BLE001 - the label is a hint, never a blocker
            engine = ""
        if engine == "arkload":
            self.engine_label.setText("This link is downloaded by ArkLoad and verified "
                                      "against MediaFire's SHA-256.")
        elif engine == "jdownloader":
            self.engine_label.setText("This link is downloaded by the JDownloader engine.")
        else:
            self.engine_label.setText("")

    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)
            self._validate()

    def _choose_folder(self) -> None:
        current = self.destination_combo.currentText().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose the download folder", current)
        if folder:
            self.destination_combo.setCurrentText(folder)
            self._update_space()

    def _toggle_schedule(self, checked: bool) -> None:
        self.schedule_time.setVisible(checked)

    def _update_space(self) -> None:
        """Show how much room is left on the destination drive."""
        import shutil
        folder = self.destination_combo.currentText().strip() or str(Path.home())
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            free = None
        if free is None:
            self.space_label.setText("")
            return
        self.space_label.setText(f"Disk space: {free / (1024 ** 3):.0f} GB free")

    def _accept(self) -> None:
        text = self.url_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "New download",
                                "Enter a link to download.")
            return
        if is_torrent(text):
            QMessageBox.warning(
                self, "New download",
                "ArkLoad does not download torrents.")
            return
        if not (text.startswith("http://") or text.startswith("https://")):
            # a bare MediaFire folder key is allowed, everything else has to be a link
            try:
                dl.parse_source(text)
            except Exception as e:  # noqa: BLE001 - show the reason to the user
                QMessageBox.warning(self, "New download",
                                    f"That link cannot be used:\n\n{e}")
                return
        destination = self.destination_combo.currentText().strip()
        if not destination:
            QMessageBox.warning(self, "New download", "Choose a folder for the download.")
            return
        try:
            Path(destination).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "New download", f"The folder cannot be used:\n\n{e}")
            return
        self.accepted_url = text
        self.accepted = {
            "url": text,
            "dest": str(Path(destination).expanduser()),
            "source": None,
            "name": self.name_edit.text().strip(),
            "schedule": self._schedule_time(),
        }
        self.accept()

    def _schedule_time(self) -> float:
        """The chosen start time as a unix timestamp (0 when the scheduler is off)."""
        if not self.schedule_check.isChecked():
            return 0.0
        from datetime import datetime, timedelta
        chosen = self.schedule_time.time().toPython()
        now = datetime.now()
        target = now.replace(hour=chosen.hour, minute=chosen.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()


class RenameDialog(QDialog):
    """Rename the downloaded folder or file."""

    def __init__(self, parent: QWidget | None = None, current: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Rename file")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(QLabel("New name"))
        self.name_edit = QLineEdit(current)
        layout.addWidget(self.name_edit)
        buttons = QDialogButtonBox()
        buttons.addButton("Rename", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name_edit.selectAll()
        self.name_edit.setFocus()

    def value(self) -> str:
        return self.name_edit.text().strip()


class MoveDialog(QDialog):
    """Move a finished download to another folder."""

    def __init__(self, parent: QWidget | None = None, current: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Move to…")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Move the download to"))
        self.folder_edit = QLineEdit(current)
        layout.addWidget(self.folder_edit)
        buttons = QDialogButtonBox()
        buttons.addButton("Move", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.folder_edit.text().strip()


class TagsDialog(QDialog):
    """Attach tags to a download (they are stored in the manifest)."""

    def __init__(self, parent: QWidget | None = None, tags: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add tag")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Tags, separated by commas"))
        self.tags_edit = QLineEdit(", ".join(tags or []))
        layout.addWidget(self.tags_edit)
        buttons = QDialogButtonBox()
        buttons.addButton("Save", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> list[str]:
        return [part.strip() for part in self.tags_edit.text().split(",") if part.strip()]


class ScheduleDialog(QDialog):
    """Start a download at a given time."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Schedule")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Start the download at"))
        self.time_edit = QTimeEdit(QTime.currentTime().addSecs(3600))
        self.time_edit.setDisplayFormat("HH:mm")
        layout.addWidget(self.time_edit)
        buttons = QDialogButtonBox()
        buttons.addButton("Schedule", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def start_time(self) -> float:
        """The chosen time as a unix timestamp (today, or tomorrow if it already passed)."""
        from datetime import datetime, timedelta
        chosen = self.time_edit.time().toPython()
        now = datetime.now()
        target = now.replace(hour=chosen.hour, minute=chosen.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()


class TextReportDialog(QDialog):
    """Show a text report (file integrity, errors, …)."""

    def __init__(self, parent: QWidget | None = None, title: str = "Report", text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 12)
        from PySide6.QtWidgets import QPlainTextEdit
        self.view = QPlainTextEdit(text)
        self.view.setReadOnly(True)
        layout.addWidget(self.view)
        buttons = QDialogButtonBox()
        buttons.addButton("Close", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    """The 'About' window."""

    def __init__(self, parent: QWidget | None = None, version: str = "1.0"):
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)
        title = QLabel("ArkLoad")
        title.setObjectName("Heading")
        layout.addWidget(title)
        text = QLabel(
            f"Version {version}\n\n"
            "A download manager for MediaFire folders and files.\n"
            "Downloads resume after a crash, missing and corrupt files are detected, and every "
            "file is verified against MediaFire's own SHA-256.\n\n"
            "Built with PySide6. Every download is verified against MediaFire's own SHA-256.")
        text.setWordWrap(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox()
        buttons.addButton("Close", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

class MirrorDialog(QDialog):
    """Add an alternative source for a download."""

    def __init__(self, parent: QWidget | None = None, current: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add mirror")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(QLabel("A mirror is a second MediaFire link with the same files. "
                                "It is used when the first source fails."))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.mediafire.com/folder/…")
        layout.addWidget(self.url_edit)
        if current:
            existing = QLabel("Current mirrors: " + ", ".join(current))
            existing.setObjectName("Muted")
            existing.setWordWrap(True)
            layout.addWidget(existing)
        buttons = QDialogButtonBox()
        buttons.addButton("Add mirror", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.url_edit.setFocus()

    def value(self) -> str:
        return self.url_edit.text().strip()


class ConvertDialog(QDialog):
    """Convert a finished download to mp3 or mp4 with ffmpeg."""

    def __init__(self, parent: QWidget | None = None, source: str = "", kind: str = "mp3"):
        super().__init__(parent)
        self.setWindowTitle(f"Convert to {kind}")
        self.setMinimumWidth(520)
        self.kind = kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(QLabel("The converted file is written next to the original."))
        self.source_edit = QLineEdit(source)
        self.source_edit.setReadOnly(True)
        layout.addWidget(self.source_edit)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.target_edit = QLineEdit(str(Path(source).with_suffix(f".{kind}")) if source else "")
        row.addWidget(self.target_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton("Convert", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "Save the converted file",
                                                    self.target_edit.text(),
                                                    f"{self.kind.upper()} files (*.{self.kind})")
        if path:
            self.target_edit.setText(path)

    def values(self) -> tuple[str, str]:
        return self.source_edit.text().strip(), self.target_edit.text().strip()


class AddonsDialog(QDialog):
    """The add-on manager: what is installed, what is enabled and what it does."""

    def __init__(self, parent: QWidget | None = None, manager=None):
        super().__init__(parent)
        self.setWindowTitle("Add-ons")
        self.resize(640, 440)
        self.manager = manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 12)
        layout.setSpacing(10)

        header = QLabel("Add-ons")
        header.setObjectName("Heading")
        layout.addWidget(header)

        note = QLabel("Add-ons are folders in %s. Each one has an addon.json and a Python "
                      "file with a setup() function." % (getattr(manager, "folder", "")))
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.items = {}
        if manager is not None:
            for addon in manager.discover():
                label = f"{addon.name}  {addon.version}\n{addon.description or 'No description'}"
                item = QListWidgetItem(label)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if addon.enabled else Qt.Unchecked)
                if addon.error:
                    item.setToolTip(addon.error)
                self.list.addItem(item)
                self.items[item] = addon

        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton("Apply", QDialogButtonBox.AcceptRole)
        buttons.addButton("Close", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        if self.manager is None:
            return
        for item, addon in self.items.items():
            addon.enabled = item.checkState() == Qt.Checked
            self.manager.reload(addon)
        self.accept()


class RemoteAccessDialog(QDialog):
    """Shows the address of the remote access page and the token."""

    def __init__(self, parent: QWidget | None = None, url: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Remote access")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Open this address on any device of the network to watch and "
                                "control the downloads."))
        self.url_edit = QLineEdit(url)
        self.url_edit.setReadOnly(True)
        layout.addWidget(self.url_edit)
        buttons = QDialogButtonBox()
        copy = buttons.addButton("Copy address", QDialogButtonBox.ActionRole)
        copy.clicked.connect(self._copy)
        buttons.addButton("Close", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.url_edit.text())

class DeleteDownloadsDialog(QDialog):
    """Delete the files or only take the downloads out of the list (like the reference)."""

    def __init__(self, parent: QWidget | None = None, downloads=None):
        super().__init__(parent)
        self.setWindowTitle("Delete selected downloads")
        self.setMinimumWidth(460)
        self.delete_files = False
        downloads = list(downloads or [])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"Delete selected downloads ({len(downloads)})")
        title.setObjectName("Heading")
        layout.addWidget(title)

        listing = QPlainTextEdit()
        listing.setReadOnly(True)
        listing.setMaximumHeight(140)
        for record in downloads:
            listing.appendPlainText(str(record.root or record.destination))
        layout.addWidget(listing)

        self.remember_check = QCheckBox("Remember my choice")
        self.remember_check.setToolTip(
            "The next deletions use the same choice without asking again")
        layout.addWidget(self.remember_check)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.remove_button = QPushButton("REMOVE FROM LIST")
        self.remove_button.clicked.connect(self._remove)
        buttons.addWidget(self.remove_button)
        self.delete_button = QPushButton("DELETE FILES")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self._delete)
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)

    def _remove(self) -> None:
        self.delete_files = False
        self.accept()

    def _delete(self) -> None:
        self.delete_files = True
        self.accept()

