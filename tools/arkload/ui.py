"""The custom widgets of the ArkLoad interface.

They are the parts that the two references have and that no standard widget draws the way we
want them:

* :class:`TabStrip` - the tabs of JDownloader 2 (Downloads, LinkGrabber, History),
* :class:`DownloadRow` - the row of the list: the name, the facts under it, the progress bar
  and the percentage, the way Free Download Manager draws it,
* :class:`DropArea` - the area that accepts dropped links,
* :class:`OverviewBar` - the "Download Overview" of JDownloader 2 with the numbers of the list.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy, QTabBar, QVBoxLayout,
                               QWidget)

from . import styles


class TabStrip(QTabBar):
    """The tabs of JDownloader 2: the downloads, the link grabber and the history.

    Every tab carries the icon of its content, the way the reference does.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TabStrip")
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(False)
        self.setIconSize(self.iconSize())
        #: the name of the icon of every tab, so the theme can redraw them
        self._icons: list[str] = []
        self._colour = styles.palette("dark")["muted"]

    def add_tab(self, title: str, icon_name: str = "") -> int:
        """Add a tab, with its icon."""
        if icon_name:
            self._icons.append(icon_name)
            self.addTab(styles.make_icon(icon_name, self._colour, 14), title)
        else:
            self._icons.append("")
            self.addTab(title)
        return self.count() - 1

    def set_icon_colour(self, colour: str) -> None:
        """Refresh the icons after a theme change."""
        self._colour = colour
        for index, name in enumerate(self._icons):
            if name:
                self.setTabIcon(index, styles.make_icon(name, colour, 14))


class DownloadRow:
    """The numbers of one row of the list, shared by the table and the details card."""

    def __init__(self, record, colours: dict):
        self.record = record
        self.colours = colours

    @property
    def percent(self) -> int:
        return int(self.record.percent)

    @property
    def active(self) -> bool:
        return bool(self.record.active)

    @property
    def failed(self) -> bool:
        return self.record.status in ("failed", "incomplete")

    @property
    def complete(self) -> bool:
        return self.record.status == "complete"

    @property
    def paused(self) -> bool:
        return self.record.status in ("paused", "cancelled")

    def bar_colour(self) -> str:
        """The colour of the progress bar of this row."""
        if self.failed:
            return self.colours.get("danger", "#F04438")
        if self.complete:
            return self.colours.get("success", "#3DD68C")
        if self.paused:
            return self.colours.get("warning", "#F5A524")
        return self.colours.get("accent", "#3D9BFF")

    def facts(self) -> str:
        """The line under the name: how much is there, how much is left."""
        from .download_model import fmt_size, remaining_time

        record = self.record
        parts = []
        if record.bytes_total:
            parts.append(f"{fmt_size(record.bytes_done)} of {fmt_size(record.bytes_total)}")
        if record.total_files > 1:
            parts.append(f"{record.verified_files}/{record.total_files} files")
        left = remaining_time(record)
        if left:
            parts.append(left.replace("Remaining: ", ""))
        return "  ·  ".join(parts)


class DropArea(QWidget):
    """The area that accepts dropped links, the way Free Download Manager draws it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.colours = dict(styles.DARK)

    def set_colours(self, colours: dict) -> None:
        self.colours = dict(colours)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = self.colours.get("accent", "#3D9BFF")
        text = self.colours.get("text", "#E8ECF1")
        muted = self.colours.get("muted", "#8A929C")
        faint = self.colours.get("faint", "#5A626C")

        pen = QPen(QColor(self.colours.get("border_strong", "#2E353F")))
        pen.setStyle(Qt.DashLine)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 12, 12)

        centre = self.rect().center()
        # the glyph in a soft circle
        radius = 21
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.colours.get("surface_alt", "#181C22")))
        painter.drawEllipse(QPointF(centre.x(), centre.y() - 32), radius, radius)
        painter.setPen(QPen(QColor(accent), 1.8, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(centre.x(), centre.y() - 43), QPointF(centre.x(), centre.y() - 29))
        painter.drawPolyline([QPointF(centre.x() - 6, centre.y() - 35),
                              QPointF(centre.x(), centre.y() - 27),
                              QPointF(centre.x() + 6, centre.y() - 35)])
        painter.setPen(QPen(QColor(faint), 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(centre.x() - 12, centre.y() - 19),
                         QPointF(centre.x() + 12, centre.y() - 19))

        font = QFont(self.font())
        font.setPixelSize(15)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(text))
        title = "Drag-and-drop a link or a folder key"
        painter.drawText(QPointF(centre.x() - painter.fontMetrics().horizontalAdvance(title) // 2,
                                 centre.y() + 14), title)
        font.setPixelSize(12)
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor(muted))
        hint = "Folders, files and the links of any host"
        painter.drawText(QPointF(centre.x() - painter.fontMetrics().horizontalAdvance(hint) // 2,
                                 centre.y() + 36), hint)
        painter.end()


class OverviewBar(QFrame):
    """The "Download Overview" of JDownloader 2: the numbers of the whole list."""

    #: the facts of the overview, in the order of the reference
    FACTS = (("packages", "Packages"), ("speed", "Total speed"), ("loaded", "Bytes loaded"),
             ("eta", "ETA"), ("connections", "Open connections"), ("running", "Running downloads"))

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Overview")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 7, 16, 7)
        layout.setSpacing(26)
        self.values: dict = {}
        for key, name in self.FACTS:
            column = QVBoxLayout()
            column.setSpacing(1)
            title = QLabel(name)
            title.setObjectName("FactKey")
            value = QLabel("—")
            value.setObjectName("FactValue")
            column.addWidget(title)
            column.addWidget(value)
            layout.addLayout(column)
            self.values[key] = value
        layout.addStretch(1)

    def show_values(self, values: dict) -> None:
        """Show the numbers of the list."""
        for key, label in self.values.items():
            label.setText(str(values.get(key, "—")))
