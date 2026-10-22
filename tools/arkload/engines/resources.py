"""Where the bundled engines live.

ArkLoad ships its own engines: the JDownloader 2 engine (with the Java runtime it needs) and
aria2.  The user does not have to install or to point at anything - the application finds its
own resources.

There are three places, and the first one that holds the file wins:

1. the **application data** (``%LOCALAPPDATA%\\ArkLoad``) - where the installer puts them, so a
   user can replace an engine without reinstalling the program,
2. the **program folder** - where a portable copy keeps them, right next to the executable,
3. the **source tree** - where the development build finds the engine that was built from the
   official sources.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: the name of the folder the engines live in
ENGINE_DIR = "engines"

#: the name of the JDownloader engine inside that folder
JDOWNLOADER_DIR = "jdownloader"

#: the name of the aria2 engine inside that folder
ARIA2_DIR = "aria2"

#: the file the aria2 engine is started from
ARIA2_EXE = "aria2c.exe"


def app_data_dir() -> Path:
    """The folder the data of the application lives in."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "ArkLoad"
    return Path.home() / ".arkload"


def program_dir() -> Path:
    """The folder the program runs from."""
    if getattr(sys, "frozen", False):
        # the PyInstaller one file build unpacks itself into the temporary folder
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def source_dir() -> Path:
    """The root of the source tree."""
    return Path(__file__).resolve().parent.parent.parent.parent


def engine_roots() -> list[Path]:
    """The folders the engines can be in, most specific first."""
    roots = [
        app_data_dir() / ENGINE_DIR,
        program_dir() / ENGINE_DIR,
        source_dir() / "packaging" / ENGINE_DIR,
    ]
    # a development build may also point somewhere else
    override = os.environ.get("ARKLOAD_ENGINES")
    if override:
        roots.insert(0, Path(override))
    return roots


def jdownloader_dir() -> Path | None:
    """The folder of the bundled JDownloader engine (with the jar and the plugins)."""
    for root in engine_roots():
        candidate = root / JDOWNLOADER_DIR
        if (candidate / "JDownloader.jar").is_file():
            return candidate
    return None


def jdownloader_jar() -> Path | None:
    """The jar the JDownloader engine is started from."""
    folder = jdownloader_dir()
    return (folder / "JDownloader.jar") if folder else None


def bundled_java() -> Path | None:
    """The Java runtime that belongs to the bundled engine."""
    folder = jdownloader_dir()
    if not folder:
        return None
    for candidate in (folder / "jre" / "bin" / "java.exe",
                      folder / "jre" / "bin" / "java"):
        if candidate.is_file():
            return candidate
    return None


def aria2_exe() -> Path | None:
    """The aria2 engine."""
    for root in engine_roots():
        candidate = root / ARIA2_DIR / ARIA2_EXE
        if candidate.is_file():
            return candidate
    return None


def java_command() -> str:
    """The java command: the bundled runtime first, then the one of the system.

    The bundled runtime is what the installer ships, so a clean Windows system works without a
    Java installation.
    """
    bundled = bundled_java()
    if bundled:
        return str(bundled)
    override = os.environ.get("ARKLOAD_JAVA")
    if override and Path(override).exists():
        return override
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    import shutil
    return shutil.which("java") or "java"


def engine_state_dir(name: str) -> Path:
    """The folder an engine keeps its own files in (its configuration, its logs, its data)."""
    folder = app_data_dir() / "engine-state" / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder
