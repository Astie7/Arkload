"""Entry point of the ArkLoad download manager.

    python tools/arkload/main.py
    python -m tools.arkload.main
    dist\\ArkLoadDownloader\\ArkLoadDownloader.exe

The packaged build runs this file: it sets the application name and the icon, and it shows a
message box instead of a silent crash when something goes wrong during the start-up.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

if __package__ in (None, ""):  # direct script run
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

try:
    from .arkload_window import APP_NAME, ArkLoadMainWindow
    from . import settings as settings_store
    from . import styles
except ImportError:  # pragma: no cover - direct script run
    from tools.arkload.arkload_window import APP_NAME, ArkLoadMainWindow
    from tools.arkload import settings as settings_store
    from tools.arkload import styles


def resource_path(name: str) -> Path:
    """A file that sits next to the executable (or inside the PyInstaller bundle)."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = Path(__file__).resolve().parent.parent.parent
    return Path(base) / name


def application_icon() -> QIcon:
    """The icon of the application: the drawn one, or the .ico of the build."""
    icon_file = resource_path("assets/arkload.ico")
    if icon_file.is_file():
        return QIcon(str(icon_file))
    return styles.app_icon(styles.palette("dark")["accent"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ArkLoadDownloader", description=APP_NAME)
    parser.add_argument("--state-dir", help="folder for the download manifests")
    parser.add_argument("--settings", help="path of the settings file")
    parser.add_argument("--add", action="append", metavar="URL", default=[],
                        help="add a MediaFire link and download it (can be used several times)")
    parser.add_argument("--destination", help="folder for the downloads of --add")
    parser.add_argument("--headless", action="store_true",
                        help="do not show the window (for scripts and the browser extension)")
    parser.add_argument("--exit-when-done", action="store_true",
                        help="close the application when every download has finished")
    args = parser.parse_args(argv)

    if args.settings:
        os.environ["MEDIAFIRE_GUI_SETTINGS"] = args.settings

    QApplication.setAttribute(Qt.AA_DontShowIconsInMenus, False)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(settings_store.APP_VERSION)
    app.setOrganizationName("ArkLoad")
    app.setWindowIcon(application_icon())

    try:
        window = ArkLoadMainWindow(state_dir=args.state_dir)
        window.setWindowIcon(application_icon())
        for url in args.add:
            window.add_download_url(url, args.destination)
        if args.exit_when_done:
            window.exit_when_done = True
        if not args.headless:
            window.show()
    except Exception:  # noqa: BLE001 - a start-up error must be visible
        detail = traceback.format_exc()
        settings_store.write_log(detail)
        try:
            QMessageBox.critical(None, APP_NAME,
                                 "ArkLoad could not start.\n\n"
                                 f"{detail.strip().splitlines()[-1]}\n\n"
                                 f"Details: {settings_store.log_path()}")
        except Exception:  # noqa: BLE001 - no GUI at all
            print(detail, file=sys.stderr)
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
