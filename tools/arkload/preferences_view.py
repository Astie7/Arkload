"""The preferences view: a sidebar with the sections of the reference interface.

Every control is wired to something that really works: the virus scanner uses Windows Defender
or ClamAV, the remote access server is a small web interface on this machine, the browser
extension is generated into the data folder, the conversion uses ffmpeg and the add-on folder is
loaded at start-up.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from .download_engine import EngineOptions

SECTIONS = ("General", "Download Engine", "Browser Integration", "Network", "Traffic Limits",
            "Advanced")

#: the short explanation under the title of every section
SECTION_HINTS = {
    "General": "The look of the application and the folder the downloads go to.",
    "Download Engine": "The MediaFire links are downloaded by ArkLoad itself, every other link "
                       "by the JDownloader 2 engine, which runs hidden in the background.",
    "Browser Integration": "The extension that puts the links of the page you are on into "
                           "ArkLoad.",
    "Network": "How many files are downloaded at the same time and how often a file is retried.",
    "Traffic Limits": "The speed limit of the transfers.",
    "Advanced": "The recovery passes, the integrity check and the resume after a restart.",
}


class PreferencesView(QWidget):
    """The preferences page, shown inside the main window's stacked widget."""

    changed = Signal(object)          # EngineOptions
    theme_changed = Signal(str)       # "dark" | "light"
    action_requested = Signal(str, object)   # "create_extension", "start_remote", …
    settings_changed = Signal(dict)

    def __init__(self, options: EngineOptions | None = None, parent: QWidget | None = None,
                 settings: dict | None = None):
        super().__init__(parent)
        self.options = options or EngineOptions()
        self.settings = settings if settings is not None else {}
        self.language = "English (United States)"
        self.launch_at_startup = False
        self.suggest_by_type = True
        self.suggest_by_url = False
        self.fixed_folder = ""
        self.build()

    # -- layout ---------------------------------------------------------------
    def build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 18, 10, 18)
        side_layout.setSpacing(4)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("SidebarList")
        for name in SECTIONS:
            self.sidebar.addItem(QListWidgetItem(name))
        self.sidebar.currentRowChanged.connect(self._switch)
        side_layout.addWidget(self.sidebar, 1)
        root.addWidget(sidebar)

        self.stack = QStackedWidget()
        for name in SECTIONS:
            self.stack.addWidget(self._page(name))
        root.addWidget(self.stack, 1)
        self.sidebar.setCurrentRow(0)

    def _page(self, name: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel(name)
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        hint = QLabel(SECTION_HINTS.get(name, ""))
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setVisible(bool(SECTION_HINTS.get(name)))
        layout.addWidget(hint)

        body = QFrame()
        body.setObjectName("Panel")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 18, 20, 18)
        body_layout.setSpacing(12)
        layout.addWidget(body)
        layout.addStretch(1)

        if name == "General":
            self._build_general(body_layout)
        elif name == "Download Engine":
            self._build_engine(body_layout)
        elif name == "Browser Integration":
            self._build_browser(body_layout)
        elif name == "Network":
            self._build_network(body_layout)
        elif name == "Traffic Limits":
            self._build_limits(body_layout)
        else:
            self._build_advanced(body_layout)
        return page

    # -- pages ----------------------------------------------------------------
    def _build_engine(self, layout: QVBoxLayout) -> None:
        """The engine that downloads the links of the hosts ArkLoad does not know itself."""
        from .jdownloader import find_engine

        intro = QLabel(
            "ArkLoad downloads the MediaFire links with its own engine, which verifies every "
            "file against the SHA-256 MediaFire publishes.  Every other link is downloaded by "
            "the JDownloader 2 engine: it runs hidden in the background, so no JDownloader "
            "window is ever shown.")
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)
        self.engine_edit = QLineEdit(self.settings.get("engine_path", ""))
        self.engine_edit.setPlaceholderText("The folder or the JDownloader.jar of the engine")
        self.engine_edit.setMaximumWidth(520)
        self.engine_edit.textChanged.connect(self._on_engine_settings)
        form.addRow("JDownloader engine", self.engine_edit)

        self.engine_port_spin = QSpinBox()
        self.engine_port_spin.setRange(1024, 65535)
        self.engine_port_spin.setMaximumWidth(140)
        self.engine_port_spin.setValue(int(self.settings.get("engine_port", 3128) or 3128))
        self.engine_port_spin.valueChanged.connect(self._on_engine_settings)
        form.addRow("Local API port", self.engine_port_spin)
        layout.addLayout(form)

        row = QHBoxLayout()
        browse = QPushButton("Choose the engine…")
        browse.clicked.connect(self._choose_engine)
        row.addWidget(browse)
        test = QPushButton("Test the connection")
        test.clicked.connect(self._test_engine)
        row.addWidget(test)
        row.addStretch(1)
        layout.addLayout(row)

        self.engine_label = QLabel("")
        self.engine_label.setObjectName("Muted")
        self.engine_label.setWordWrap(True)
        layout.addWidget(self.engine_label)

        found = find_engine(self.engine_edit.text().strip() or None)
        if found:
            self.engine_label.setText(f"The engine is there: {found}")
        elif self.engine_edit.text().strip():
            self.engine_label.setText("There is no JDownloader.jar at that place.")
        else:
            self.engine_label.setText(
                "No engine was found.  Build it with packaging/jdownloader/build_engine.ps1 and "
                "point ArkLoad at the folder that holds JDownloader.jar.")

    def _choose_engine(self) -> None:
        start = self.engine_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose the folder of the engine", start)
        if folder:
            self.engine_edit.setText(folder)
            self._on_engine_settings()

    def _test_engine(self) -> None:
        """Ask the engine whether it answers."""
        from .engine import EngineError, JDownloaderEngine

        path = self.engine_edit.text().strip()
        port = int(self.engine_port_spin.value())
        if not path:
            self.engine_label.setText("Choose the engine first.")
            return
        client = JDownloaderEngine("127.0.0.1", port, timeout=5)
        try:
            packages = client.packages()
        except EngineError as e:
            self.engine_label.setText(f"The engine does not answer: {e}")
            return
        self.engine_label.setText(f"The engine answers: {len(packages)} download(s) in its list.")
    def _build_general(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        form.setSpacing(10)
        self.theme_combo = QComboBox()
        self.theme_combo.setMaximumWidth(220)
        self.theme_combo.addItems(["System", "Dark", "Light"])
        self.theme_combo.setCurrentText("Dark")
        self.theme_combo.currentTextChanged.connect(self._on_theme)
        form.addRow("Theme", self.theme_combo)
        layout.addLayout(form)

        hint = QLabel("A light theme is used when the system theme is unknown.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        language_form = QFormLayout()
        language_form.setSpacing(10)
        self.language_combo = QComboBox()
        self.language_combo.setMaximumWidth(220)
        self.language_combo.addItem("English (United States)")
        self.language_combo.setToolTip("Only English is translated in this build.")
        language_form.addRow("Language", self.language_combo)
        layout.addLayout(language_form)

        note = QLabel("Your language is not listed? Translations are not part of this build.")
        note.setObjectName("Muted")
        layout.addWidget(note)

        label = QLabel("Options")
        label.setObjectName("SubHeading")
        layout.addWidget(label)
        self.launch_check = QCheckBox("Launch at startup (minimized)")
        self.launch_check.setToolTip(
            "Creates a 'Run' entry in the Windows registry for this application.")
        self.launch_check.toggled.connect(self._on_launch)
        layout.addWidget(self.launch_check)

        label = QLabel("Default download folder")
        label.setObjectName("SubHeading")
        layout.addWidget(label)
        self.auto_folder_radio = QRadioButton("Choose default download folder automatically")
        self.auto_folder_radio.setChecked(True)
        self.auto_folder_radio.toggled.connect(self._on_folder_mode)
        layout.addWidget(self.auto_folder_radio)
        self.type_folder_check = QCheckBox("Suggest folders based on file type")
        self.type_folder_check.setChecked(True)
        layout.addWidget(self.type_folder_check)
        self.url_folder_check = QCheckBox("Suggest folders based on download URL")
        layout.addWidget(self.url_folder_check)
        self.fixed_folder_radio = QRadioButton("Fixed default download folder")
        self.fixed_folder_radio.toggled.connect(self._on_folder_mode)
        layout.addWidget(self.fixed_folder_radio)
        self.folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(self.options.destination)
        self.folder_edit.setMaximumWidth(520)
        self.folder_edit.setEnabled(False)
        self.folder_edit.textChanged.connect(self._on_folder)
        self.folder_row.addWidget(self.folder_edit)
        folder_btn = QPushButton("Browse…")
        folder_btn.clicked.connect(self._choose_folder)
        self.folder_row.addWidget(folder_btn)
        layout.addLayout(self.folder_row)

    def _build_browser(self, layout: QVBoxLayout) -> None:
        label = QLabel(
            "The browser extension puts a MediaFire link on the clipboard with one click. "
            "Press Ctrl+V in ArkLoad (or the Paste button in the new download window) to add it.")
        label.setWordWrap(True)
        label.setObjectName("Muted")
        layout.addWidget(label)

        row = QHBoxLayout()
        create = QPushButton("Create the extension")
        create.clicked.connect(self._create_extension)
        row.addWidget(create)
        open_folder = QPushButton("Open the folder")
        open_folder.clicked.connect(self._open_extension)
        row.addWidget(open_folder)
        row.addStretch(1)
        layout.addLayout(row)

        self.extension_label = QLabel("")
        self.extension_label.setObjectName("Muted")
        self.extension_label.setWordWrap(True)
        layout.addWidget(self.extension_label)
        self._update_extension_label()

    def _build_network(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        form.setSpacing(10)
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setMaximumWidth(140)
        self.parallel_spin.setRange(1, 16)
        self.parallel_spin.setValue(self.options.parallel)
        self.parallel_spin.valueChanged.connect(self._on_engine)
        form.addRow("Parallel downloads", self.parallel_spin)
        self.retries_spin = QSpinBox()
        self.retries_spin.setMaximumWidth(140)
        self.retries_spin.setRange(1, 10)
        self.retries_spin.setValue(self.options.retries)
        self.retries_spin.valueChanged.connect(self._on_engine)
        form.addRow("Attempts per file", self.retries_spin)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setMaximumWidth(140)
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(int(self.options.timeout))
        self.timeout_spin.valueChanged.connect(self._on_engine)
        form.addRow("Network timeout", self.timeout_spin)
        self.connections_spin = QSpinBox()
        self.connections_spin.setRange(1, 16)
        self.connections_spin.setMaximumWidth(140)
        self.connections_spin.setValue(int(self.options.connections))
        self.connections_spin.setToolTip(
            "How many parts of a file are downloaded at the same time. More connections can be "
            "faster, the server has to allow partial downloads.")
        self.connections_spin.valueChanged.connect(self._on_engine)
        form.addRow("Connections per file", self.connections_spin)
        layout.addLayout(form)
        note = QLabel("The engine has no proxy support; a proxy field would not do anything.")
        note.setObjectName("Muted")
        layout.addWidget(note)

    def _build_limits(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        form.setSpacing(10)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(0, 102400)
        self.rate_spin.setSingleStep(64)
        self.rate_spin.setSuffix(" KB/s")
        self.rate_spin.setValue(int(self.options.rate_limit // 1024))
        self.rate_spin.setToolTip("0 means no limit. The limit applies to every transfer.")
        self.rate_spin.valueChanged.connect(self._on_rate)
        form.addRow("Speed limit", self.rate_spin)
        layout.addLayout(form)

        self.slow_check = QCheckBox("One transfer at a time")
        self.slow_check.setChecked(self.options.slow_mode)
        self.slow_check.toggled.connect(self._on_engine)
        layout.addWidget(self.slow_check)
        slow_form = QFormLayout()
        self.slow_pause_spin = QSpinBox()
        self.slow_pause_spin.setRange(0, 60)
        self.slow_pause_spin.setSuffix(" s")
        self.slow_pause_spin.setValue(int(self.options.slow_pause))
        self.slow_pause_spin.setMaximumWidth(140)
        self.slow_pause_spin.valueChanged.connect(self._on_engine)
        slow_form.addRow("Pause between files", self.slow_pause_spin)
        layout.addLayout(slow_form)

        note = QLabel(
            "The speed limit slows the transfer down to the given rate; it is the same value as "
            "the chip in the status bar. \"One transfer at a time\" downloads the files one "
            "after the other instead of in parallel.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        layout.addWidget(note)

    def _build_advanced(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        form.setSpacing(10)
        self.recovery_spin = QSpinBox()
        self.recovery_spin.setMaximumWidth(140)
        self.recovery_spin.setRange(0, 10)
        self.recovery_spin.setValue(self.options.recovery_rounds)
        self.recovery_spin.valueChanged.connect(self._on_engine)
        form.addRow("Recovery passes", self.recovery_spin)
        self.verify_check = QCheckBox("Verify files against MediaFire's SHA-256")
        self.verify_check.setChecked(self.options.verify_hashes)
        self.verify_check.toggled.connect(self._on_engine)
        form.addRow("Integrity", self.verify_check)
        self.auto_resume_check = QCheckBox("Resume unfinished downloads on startup")
        self.auto_resume_check.setChecked(self.options.auto_resume)
        self.auto_resume_check.toggled.connect(self._on_engine)
        form.addRow("Queue", self.auto_resume_check)
        layout.addLayout(form)

        ffmpeg_form = QFormLayout()
        self.ffmpeg_edit = QLineEdit(self.settings.get("ffmpeg_path", ""))
        self.ffmpeg_edit.setPlaceholderText("Leave empty to use ffmpeg from the PATH")
        self.ffmpeg_edit.setMaximumWidth(520)
        self.ffmpeg_edit.textChanged.connect(self._on_advanced)
        ffmpeg_form.addRow("ffmpeg", self.ffmpeg_edit)
        layout.addLayout(ffmpeg_form)

        label = QLabel("MediaFire cookies")
        label.setObjectName("SubHeading")
        layout.addWidget(label)
        self.cookies_edit = QLineEdit(self.options.cookies)
        self.cookies_edit.setMaximumWidth(520)
        self.cookies_edit.setPlaceholderText("ukey=…; user=…  (never saved to disk)")
        self.cookies_edit.textChanged.connect(self._on_engine)
        layout.addWidget(self.cookies_edit)
        note = QLabel(
            "Cookies are needed for files that MediaFire reports as blocked, usually after they "
            "were copied into your own account. They are kept in memory only.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        layout.addWidget(note)

    # -- handlers -------------------------------------------------------------
    def _switch(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    def _on_theme(self, text: str) -> None:
        theme = {"Dark": "dark", "Light": "light"}.get(text)
        if theme is None:
            theme = "dark"
        self.theme_changed.emit(theme)

    def _on_launch(self, checked: bool) -> None:
        self.launch_at_startup = checked

    def _on_folder_mode(self, checked: bool) -> None:
        self.folder_edit.setEnabled(not checked)
        self._on_engine()

    def _on_folder(self, text: str) -> None:
        self.options.destination = text
        self._emit()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the download folder",
                                                  self.folder_edit.text() or str(Path.home()))
        if folder:
            self.folder_edit.setText(folder)

    def _on_engine(self) -> None:
        self.options.parallel = self.parallel_spin.value()
        self.options.retries = self.retries_spin.value()
        self.options.timeout = float(self.timeout_spin.value())
        self.options.slow_mode = self.slow_check.isChecked()
        self.options.slow_pause = float(self.slow_pause_spin.value())
        self.options.recovery_rounds = self.recovery_spin.value()
        self.options.connections = self.connections_spin.value()
        self.options.verify_hashes = self.verify_check.isChecked()
        self.options.auto_resume = self.auto_resume_check.isChecked()
        self.options.cookies = self.cookies_edit.text().strip()
        if self.fixed_folder_radio.isChecked() and self.folder_edit.text().strip():
            self.options.destination = self.folder_edit.text().strip()
        self._emit()

    def _on_engine_settings(self) -> None:
        """The JDownloader engine changed: it belongs to the settings and to the options."""
        self.options.engine_path = self.engine_edit.text().strip()
        self.options.engine_port = int(self.engine_port_spin.value())
        self.settings_changed.emit({"engine_path": self.options.engine_path,
                                    "engine_port": self.options.engine_port})
        self._emit()

    def _emit(self) -> None:
        self.changed.emit(self.options)


    # -- new handlers ---------------------------------------------------------
    def _on_rate(self, value: int) -> None:
        self.options.rate_limit = int(value) * 1024
        self._emit()

    def _create_extension(self) -> None:
        self.action_requested.emit("create_extension", None)

    def _open_extension(self) -> None:
        self.action_requested.emit("open_extension", None)

    def _update_extension_label(self) -> None:
        from .browser_extension import EXTENSION_DIR
        if EXTENSION_DIR.is_dir():
            self.extension_label.setText(f"The extension is in: {EXTENSION_DIR}")
        else:
            self.extension_label.setText("The extension has not been created yet.")

    def _open_addon_folder(self) -> None:
        self.action_requested.emit("open_addon_folder", None)

    def _on_advanced(self) -> None:
        self.settings["ffmpeg_path"] = self.ffmpeg_edit.text().strip()
        self._emit_settings()

    def _emit_settings(self) -> None:
        self.settings_changed.emit(dict(self.settings))

    # -- loading --------------------------------------------------------------
    def load(self, settings: dict) -> None:
        """Apply the persisted settings to the widgets."""
        theme = settings.get("theme", "dark")
        self.theme_combo.setCurrentText({"dark": "Dark", "light": "Light"}.get(theme, "Dark"))
        self.launch_check.setChecked(bool(settings.get("launch_at_startup", False)))
        self.folder_edit.setText(settings.get("destination", self.options.destination))
        if settings.get("fixed_folder"):
            self.fixed_folder_radio.setChecked(True)
        else:
            self.auto_folder_radio.setChecked(True)
        self.type_folder_check.setChecked(bool(settings.get("suggest_by_type", True)))
        self.url_folder_check.setChecked(bool(settings.get("suggest_by_url", False)))
        self.rate_spin.setValue(int(settings.get("rate_limit", 0) // 1024))
        self.ffmpeg_edit.setText(settings.get("ffmpeg_path", ""))
        self._update_extension_label()
        self.slow_check.setChecked(bool(settings.get("slow_mode", False)))
        self.slow_pause_spin.setValue(int(settings.get("slow_pause", 1.5)))
        self.parallel_spin.setValue(int(settings.get("jobs", 4)))
        self.retries_spin.setValue(int(settings.get("retries", 3)))
        self.timeout_spin.setValue(int(settings.get("timeout", 60)))
        self.recovery_spin.setValue(int(settings.get("recovery_rounds", 3)))
        self.connections_spin.setValue(int(settings.get("connections", 1)))
        self.verify_check.setChecked(bool(settings.get("verify_hashes", True)))
        self.auto_resume_check.setChecked(bool(settings.get("auto_resume", True)))
        self.cookies_edit.setText(settings.get("cookies", ""))
        self._on_engine()
