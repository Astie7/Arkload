"""Build ArkLoad with Nuitka (standalone).

    python -m nuitka --standalone --enable-plugin=pyside6 --output-dir=build packaging/arkload_launcher.py

The result is build/arkload_launcher.dist/ArkLoad.exe (renamed to build/ArkLoad.dist/ArkLoad.exe).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "packaging" / "assets"
LAUNCHER = ROOT / "packaging" / "arkload_launcher.py"
OUTPUT_DIR = ROOT / "build"


def main() -> int:
    if not LAUNCHER.is_file():
        print("the launcher is missing:", LAUNCHER)
        return 1
    # the MSVC compiler of Visual Studio 2022
    vcvars = Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat")
    if not vcvars.is_file():
        print("the C compiler is missing: Visual Studio 2022 Community is not installed")
        return 1

    # the assets that the application reads at run time
    data_args = [f"--include-data-dir={ASSETS}=assets"]

    command = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--include-package=tools",
        "--include-package=tools.arkload",
        "--include-package=tools.arkload.engines",
        *data_args,
        "--assume-yes-for-downloads",
        "--output-dir=" + str(OUTPUT_DIR),
        "--output-filename=ArkLoad.exe",
        str(LAUNCHER),
    ]
    print("the command:", " ".join(command))

    env = os.environ.copy()
    # Nuitka finds the MSVC compiler through the environment
    setup = subprocess.run(f'call "{vcvars}" && set', shell=True, capture_output=True, text=True)
    for line in setup.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value

    result = subprocess.run(command, cwd=str(ROOT), env=env)
    if result.returncode != 0:
        print("the build failed, the code is", result.returncode)
        return result.returncode

    # Nuitka writes the build into build/arkload_launcher.dist
    produced = OUTPUT_DIR / "arkload_launcher.dist"
    final = OUTPUT_DIR / "ArkLoad"
    if produced.is_dir():
        if final.exists():
            shutil.rmtree(final)
        produced.rename(final)
        print("the build is in", final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
