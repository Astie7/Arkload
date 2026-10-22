"""Where the preferences and the download state live.

The file is shared with the Tkinter window (``~/.mediafire-dl-gui.json``), so both front ends
keep the same destination folder, theme and download options.  A portable copy of the application
keeps its data next to itself, and the packaged Windows build keeps it in the user's application
data folder - never inside the installation directory.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: the name of the application (also the name of the data folder)
APP_ID = "ArkLoad"
APP_NAME = "ArkLoad"
APP_VERSION = "1.1.0"

SETTINGS_PATH = Path.home() / ".mediafire-dl-gui.json"
LOG_PATH = Path.home() / ".mediafire-dl-gui.log"
PORTABLE_MARKER = "portable.marker"


def is_frozen() -> bool:
    """True when the application runs from a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def is_portable() -> bool:
    """True when the application runs from a portable copy."""
    if os.environ.get("MEDIAFIRE_DL_PORTABLE"):
        return True
    return (Path(__file__).resolve().parent.parent / PORTABLE_MARKER).is_file()


def data_dir() -> Path:
    """The folder for the settings, the manifests and the add-ons.

    * a portable copy keeps it next to itself
    * the packaged Windows build uses ``%LOCALAPPDATA%\\ArkLoad``
    * the source version uses ``~/.mediafire-dl``
    """
    override = os.environ.get("MEDIAFIRE_DL_DATA_DIR")
    if override:
        return Path(override)
    if is_portable():
        return Path(__file__).resolve().parent.parent.parent / "data"
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_ID
    return Path.home() / ".mediafire-dl"


def state_dir() -> Path:
    """The folder with the download manifests."""
    return data_dir() / "jobs"


def addons_dir() -> Path:
    return data_dir() / "addons"


def log_path() -> Path:
    return data_dir() / "arkload.log"


def settings_path() -> Path:
    """Honour MEDIAFIRE_GUI_SETTINGS so tests can use their own file."""
    override = os.environ.get("MEDIAFIRE_GUI_SETTINGS")
    if override:
        return Path(override)
    if is_portable():
        return data_dir() / "gui-settings.json"
    if is_frozen():
        return data_dir() / "gui-settings.json"
    return SETTINGS_PATH


def load_settings(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict, path: Path | None = None) -> bool:
    try:
        target = path or settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def default_destination() -> str:
    downloads = Path.home() / "Downloads"
    return str(downloads if downloads.is_dir() else Path.home())


def write_log(text: str) -> None:
    try:
        target = log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError:
        pass
