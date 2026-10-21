"""Media conversion with ffmpeg.

The converter uses a real ``ffmpeg`` binary.  When ffmpeg is not installed the module says so
instead of pretending to convert anything.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ConversionResult:
    ok: bool = False
    output: str = ""
    path: str = ""
    message: str = ""

    def __post_init(self) -> None:
        if not self.message:
            self.message = "Converted." if self.ok else "The conversion failed."


def find_ffmpeg(custom: str = "") -> str | None:
    """The ffmpeg binary to use: a configured path or the one on PATH."""
    if custom and custom.strip():
        path = Path(custom.strip())
        if path.is_file():
            return str(path)
    found = shutil.which("ffmpeg")
    if found:
        return found
    # a few places where ffmpeg usually ends up on Windows
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ShareX" / "ffmpeg.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "ffmpeg.exe",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def ffmpeg_available(custom: str = "") -> bool:
    return find_ffmpeg(custom) is not None


def convert(source: Path | str, target: Path | str, kind: str = "mp3",
            custom: str = "") -> ConversionResult:
    """Convert a media file to mp3 or mp4 with ffmpeg."""
    source = Path(source)
    target = Path(target)
    ffmpeg = find_ffmpeg(custom)
    if not ffmpeg:
        return ConversionResult(
            message="ffmpeg was not found. Install it (https://ffmpeg.org) and set the path in "
                    "Preferences → Advanced, then try again.")
    if not source.is_file():
        return ConversionResult(message=f"{source} does not exist.")
    if kind == "mp3":
        command = [ffmpeg, "-y", "-i", str(source), "-vn", "-ar", "44100", "-ac", "2",
                   "-b:a", "192k", str(target)]
    else:
        command = [ffmpeg, "-y", "-i", str(source), "-c:v", "libx264", "-preset", "fast",
                   "-c:a", "aac", "-b:a", "192k", str(target)]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as e:
        return ConversionResult(message=f"ffmpeg failed: {e}")
    if finished.returncode != 0:
        tail = (finished.stderr or "").strip().splitlines()[-6:]
        return ConversionResult(output="\n".join(tail),
                                message="ffmpeg could not convert the file.")
    return ConversionResult(ok=True, path=str(target), output=finished.stderr or "")


def can_convert(name: str) -> bool:
    """Only files ffmpeg can read are worth offering."""
    return Path(name).suffix.lower() in (
        ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".wma", ".aac",
        ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v")
