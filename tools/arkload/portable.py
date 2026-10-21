"""Building a portable copy of the application.

A portable copy keeps its settings and its download manifests next to itself, so it can be
carried on a USB stick.  The folder looks like this::

    ArkLoad/
      ArkLoad.cmd              <- double click this
      app/
        portable.marker    <- tells the application to keep its data here
        tools/arkload/...      <- the application itself
      data/                <- settings and manifests appear here on first start
"""
from __future__ import annotations

import shutil
from pathlib import Path

LAUNCHER = """@echo off
rem Portable ArkLoad. The data stays in the data folder next to this file.
setlocal
cd /d "%~dp0"
where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "app\\tools\\arkload\\main.py" %*
    exit /b 0
)
where pyw >nul 2>&1
if not errorlevel 1 (
    start "" pyw "app\\tools\\arkload\\main.py" %*
    exit /b 0
)
python "app\\tools\\arkload\\main.py" %*
if errorlevel 1 pause
"""

MODULES = ("mediafire_dl.py", "mediafire_resume.py", "mediafire_session.py", "__init__.py")


def create_portable(target: Path | str, source: Path | None = None) -> Path:
    """Copy the application into ``target`` and make it portable. Returns the folder."""
    target = Path(target)
    source = Path(source) if source else Path(__file__).resolve().parent.parent
    app = target / "app"
    package = app / "tools" / "arkload"
    package.mkdir(parents=True, exist_ok=True)
    (app / "tools").mkdir(parents=True, exist_ok=True)

    for name in MODULES:
        origin = source / name
        if origin.is_file():
            shutil.copy2(origin, app / "tools" / name)
    for item in (source / "arkload").iterdir():
        if item.suffix == ".py":
            shutil.copy2(item, package / item.name)
    (app / "tools" / "__init__.py").write_text(
        (source / "__init__.py").read_text(encoding="utf-8"), encoding="utf-8")
    (app / "portable.marker").write_text("portable\n", encoding="utf-8")
    (target / "ArkLoad.cmd").write_text(LAUNCHER, encoding="utf-8")
    (target / "data").mkdir(parents=True, exist_ok=True)
    (target / "README.txt").write_text(
        "ArkLoad - portable version\n\n"
        "Double click ArkLoad.cmd to start it. The settings and the download list are kept in the "
        "data folder next to this file.\n", encoding="utf-8")
    return target
