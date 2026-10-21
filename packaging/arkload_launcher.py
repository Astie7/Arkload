"""The script PyInstaller freezes into the executable.

It keeps the imports static so that the packer finds the whole application, and it works both
from the source tree and from the PyInstaller bundle.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # inside the PyInstaller bundle the modules are collected next to the executable
    sys.path.insert(0, str(Path(sys._MEIPASS)))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.arkload.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
