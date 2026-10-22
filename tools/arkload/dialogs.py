"""The dialogs of the application: Add download, rename, move, tags, schedule, about."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QTimeEdit, QToolButton,
                               QVBoxLayout, QWidget)

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
    """The 'Add download' window: a link and a button, the way the reference shows it.

    The folder of the download comes from the preferences; a download that has to go somewhere
    else is moved afterwards ("Move to..." in the menu of the row).
    """

    def __init__(self, parent: QWidget | None = None, destination: str = "", url: str = "",
                 history: list[str] | None = None, resolver=None):
        super().__init__(parent)
        self.setWindowTitle("Add download")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.selected_file: str = ""
        self.accepted_url: str = ""
        self.accepted: dict | None = None
        self.destination = destination
        #: tells which engine runs a link (``url -> "arkload" | "jdownloader"``)
        self.resolver = resolver

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        # the head of the dialog
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("Add download")
        title.setObjectName("Heading")
        head.addWidget(title)
        head.addStretch(1)
        close_button = QToolButton()
        close_button.setText("✕")
        close_button.setFixedSize(20, 20)
        close_button.setToolTip("Close")
        close_button.clicked.connect(self.reject)
        head.addWidget(close_button)
        layout.addLayout(head)

        self.hint_label = QLabel("Enter a link or choose a file with links")
        self.hint_label.setObjectName("Muted")
        layout.addWidget(self.hint_label)

        # the link: the field and the button share one row
        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://www.mediafire.com/folder/abc123")
        self.url_edit.setClearButtonEnabled(True)
        browse = QPushButton("Browse...")
        browse.setIcon(make_icon("folder", "#8A929C"))
        browse.setToolTip("Choose a file with the links")
        browse.clicked.connect(self._choose_file)
        layout.addWidget(self._input_row(self.url_edit, browse))

        self.engine_label = QLabel("")
        self.engine_label.setObjectName("Muted")
        self.engine_label.setWordWrap(True)
        layout.addWidget(self.engine_label)

        # the buttons
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok_button = QPushButton("OK")
        self.ok_button.setObjectName("Accent")
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self._accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        self.url_edit.setFocus()
        self.url_edit.textChanged.connect(self._validate)

    # -- the layout ------------------------------------------------------------
    @staticmethod
    def _input_row(field, button) -> QWidget:
        """A field and its button in one bordered row."""
        row = QFrame()
        row.setObjectName("InputRow")
        box = QHBoxLayout(row)
        box.setContentsMargins(10, 0, 5, 0)
        box.setSpacing(6)
        box.addWidget(field, 1)
        button.setObjectName("Flat")
        box.addWidget(button, 0)
        return row

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
            self.engine_label.setText("ArkLoad downloads this link itself and checks the "
                                      "files against the hash of the source.")
        elif engine == "jdownloader":
            self.engine_label.setText("The JDownloader engine downloads this link.")
        else:
            self.engine_label.setText("")

    def _choose_file(self) -> None:
        """Choose a file that holds the links."""
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a file with links", "", "All files (*)")
        if not path:
            return
        if is_torrent(path):
            QMessageBox.warning(self, "Add download",
                                "ArkLoad does not download torrents.")
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            QMessageBox.warning(self, "Add download", f"The file cannot be read:\n\n{e}")
            return
        self.url_edit.setText(text.strip())
        self._validate()

    def _accept(self) -> None:
        text = self.url_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "Add download", "Enter a link to download.")
            return
        if is_torrent(text):
            QMessageBox.warning(self, "Add download",
                                "ArkLoad does not download torrents.")
            return
        if not (text.startswith("http://") or text.startswith("https://")):
            # a bare MediaFire folder key is allowed, everything else has to be a link
            try:
                dl.parse_source(text)
            except Exception as e:  # noqa: BLE001 - show the reason to the user
                QMessageBox.warning(self, "Add download",
                                    f"That link cannot be used:\n\n{e}")
                return
        self.accepted_url = text
        self.accepted = {"url": text, "dest": self.destination, "source": None, "name": ""}
        self.accept()


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
        self.setMinimumWidth(470)
        self.delete_files = False
        downloads = list(downloads or [])
        count = len(downloads)
        plural = "" if count == 1 else "s"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        head = QVBoxLayout()
        head.setSpacing(3)
        title = QLabel(f"Delete selected downloads ({count})")
        title.setObjectName("Heading")
        head.addWidget(title)
        question = QLabel(
            f"Are you sure you want to delete the selected download{plural}? "
            f"Choose what happens with its file{plural}:")
        question.setObjectName("Muted")
        question.setWordWrap(True)
        head.addWidget(question)
        layout.addLayout(head)

        listing_header = QLabel("Paths of the download" + plural)
        listing_header.setObjectName("SubHeading")
        layout.addWidget(listing_header)

        listing = QPlainTextEdit()
        listing.setObjectName("DeleteListing")
        listing.setReadOnly(True)
        listing.setFrameShape(QFrame.NoFrame)
        listing.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        listing.setFixedHeight(96 if count > 1 else 56)
        for record in downloads:
            listing.appendPlainText(str(record.root or record.destination))
        layout.addWidget(listing)

        stays = "s" if count == 1 else ""        # the file stays / the files stay
        # the files stay on the disk unless the user chooses "Delete files"
        choice_hint = QLabel(
            f"<b>Remove from list</b> only takes the download{plural} out of the list — "
            f"the file{plural} stay{stays} on the disk. <b>Delete files</b> removes "
            + ("it" if count == 1 else "them") + " from the disk as well.")
        choice_hint.setWordWrap(True)
        layout.addWidget(choice_hint)

        # the choice and the buttons share the last row, so the dialog ends in one line
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setToolTip("Close the dialog; nothing changes")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.remove_button = QPushButton("REMOVE FROM LIST")
        self.remove_button.setToolTip(
            "Take the download out of the list; the files stay on the disk")
        self.remove_button.clicked.connect(self._remove)
        footer.addWidget(self.remove_button)
        self.delete_button = QPushButton("DELETE FILES")
        self.delete_button.setObjectName("Danger")
        self.delete_button.setToolTip(
            "Delete the files from the disk and take the download out of the list")
        self.delete_button.clicked.connect(self._delete)
        footer.addWidget(self.delete_button)
        layout.addLayout(footer)

    def _remove(self) -> None:
        self.delete_files = False
        self.accept()

    def _delete(self) -> None:
        self.delete_files = True
        self.accept()

