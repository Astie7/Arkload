# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of the ArkLoad desktop application.

    pyinstaller packaging/arkload.spec                  -> dist/ArkLoad/  (folder build)
    ARKLOAD_ONEFILE=1 pyinstaller packaging/arkload.spec  -> dist/ArkLoad.exe (single file)

The user data (settings, download manifests, add-ons) is never written into the build: the
application uses %LOCALAPPDATA%\\ArkLoad for it.
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent   # the repository root
ASSETS = ROOT / "packaging" / "assets"
ICON = str(ASSETS / "arkload.ico")
VERSION_FILE = str(ROOT / "packaging" / "version_info.txt")
ONEFILE = os.environ.get("ARKLOAD_ONEFILE", "").strip() == "1"

# the application only needs PySide6 and the standard library
EXCLUDES = [
    "tkinter", "test", "unittest", "pydoc_data", "doctest",
    "PyQt5", "PyQt6", "matplotlib", "numpy", "PIL", "setuptools", "pip",
    "sphinx", "notebook", "IPython",
]

a = Analysis(
    [str(ROOT / "packaging" / "arkload_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ASSETS), "assets")],
    hiddenimports=[
        "tools",
        "tools.arkload",
        "tools.arkload.main",
        "tools.arkload.arkload_window",
        "tools.arkload.download_engine",
        "tools.arkload.download_model",
        "tools.arkload.jdownloader",
        "tools.arkload.engine",
        "tools.arkload.hosts",
        "tools.arkload.preferences_view",
        "tools.arkload.dialogs",
        "tools.arkload.styles",
        "tools.arkload.settings",
        "tools.arkload.addons",
        "tools.arkload.browser_extension",
        "tools.arkload.converter",
        "tools.arkload.portable",
        "tools.mediafire_dl",
        "tools.mediafire_resume",
        "tools.mediafire_session",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

common = dict(
    name="ArkLoad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # a windowed application: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=VERSION_FILE,
)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        **common,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **common,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="ArkLoad",
    )
