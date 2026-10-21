"""Create the application icon (arkload.ico) from the vector icon of the interface.

    python packaging/make_icon.py

Windows wants a real .ico file for the executable and the installer.  Qt can paint the icon
but cannot write .ico files, so the sizes are painted as PNG and packed into an ICO container
by hand (Vista and later accept PNG inside an .ico).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QSize  # noqa: E402
from PySide6.QtGui import QIcon, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tools.arkload import styles  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
TARGET = ROOT / "packaging" / "assets" / "arkload.ico"


def png_bytes(icon: QIcon, size: int) -> bytes:
    image = icon.pixmap(QSize(size, size)).toImage().convertToFormat(QImage.Format_ARGB32)
    storage = QByteArray()          # the buffer keeps a reference to it
    buffer = QBuffer(storage)
    buffer.open(QBuffer.ReadWrite)
    image.save(buffer, "PNG")
    data = bytes(storage)
    buffer.close()
    return data


def write_ico(images: list[tuple[int, bytes]], target: Path) -> None:
    """Pack the PNG images into an ICO file."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    payload = b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(data), offset)
        payload += data
        offset += len(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + entries + payload)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    colours = styles.palette("dark")
    icon = styles.app_icon(colours["accent"], 256)
    images = [(size, png_bytes(icon, size)) for size in SIZES]
    write_ico(images, TARGET)
    print(f"wrote {TARGET} ({TARGET.stat().st_size} bytes, sizes: "
          f"{', '.join(str(size) for size in SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
