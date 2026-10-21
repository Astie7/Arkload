"""Colours, stylesheet and vector icons for the ArkLoad window.

The palette follows the reference interface: a near-black background, a slightly lighter download
area, rounded controls in a dark grey, a cyan accent and a green "support" accent.  Everything is
drawn with QPainter, so the application has no image assets to ship.
"""
from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

DARK = {
    "bg": "#141415",
    "area": "#1A1A1B",
    "toolbar": "#222225",
    "input": "#222225",
    "border": "#333336",
    "accent": "#4CD3FF",
    "accent_text": "#06212B",
    "text": "#E4E4E7",
    "muted": "#8E8E93",
    "green": "#22C55E",
    "green_dark": "#15803D",
    "popup": "#252528",
    "popup_border": "#3A3A3D",
    "hover": "#2C2C30",
    "row_alt": "#1D1D1F",
    "selection": "#233240",
    "danger": "#F87171",
    "warning": "#FBBF24",
    "shadow": "#000000",
}

LIGHT = {
    "bg": "#F2F2F4",
    "area": "#FFFFFF",
    "toolbar": "#E9E9EC",
    "input": "#FFFFFF",
    "border": "#D5D5DA",
    "accent": "#0A84FF",
    "accent_text": "#FFFFFF",
    "text": "#1C1C1E",
    "muted": "#6E6E73",
    "green": "#16A34A",
    "green_dark": "#15803D",
    "popup": "#FFFFFF",
    "popup_border": "#C9C9CE",
    "hover": "#DEDEE2",
    "row_alt": "#F7F7F9",
    "selection": "#D6EBFA",
    "danger": "#D93025",
    "warning": "#B45309",
    "shadow": "#000000",
}

PALETTES = {"dark": DARK, "light": LIGHT}


def palette(name: str = "dark") -> dict:
    return dict(PALETTES.get(name, DARK))


def stylesheet(theme: str = "dark") -> str:
    """The application stylesheet for one theme."""
    p = palette(theme)
    return f"""
QWidget {{
    background-color: {p['bg']};
    color: {p['text']};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background-color: {p['bg']}; }}

QFrame#Toolbar {{ background-color: {p['toolbar']}; }}
QFrame#Toolbar QLabel, QFrame#StatusBar QLabel {{ background: transparent; }}
QFrame#StatusBar {{ background-color: {p['toolbar']}; }}
QFrame#DownloadArea {{ background-color: {p['area']}; border: 1px solid {p['border']};
                       border-radius: 10px; }}
QFrame#Sidebar {{ background-color: {p['bg']}; border-right: 1px solid {p['border']}; }}

QLabel#Title {{ font-size: 22px; font-weight: 600; }}
QLabel#Heading {{ font-size: 19px; font-weight: 600; }}
QLabel#Muted {{ color: {p['muted']}; }}
QLabel#Status {{ color: {p['muted']}; }}
QLabel#Support {{ color: {p['green']}; }}

QPushButton {{
    background-color: {p['input']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background-color: {p['hover']}; border-color: {p['accent']}; }}
QPushButton:pressed {{ background-color: {p['border']}; }}
QPushButton:disabled {{ color: {p['muted']}; border-color: {p['border']}; }}
QPushButton#Accent {{
    background-color: {p['accent']};
    color: {p['accent_text']};
    border: 1px solid {p['accent']};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 600;
}}
QPushButton#Accent:hover {{ background-color: #7BDCFF; border-color: #7BDCFF; }}
QPushButton#Support {{
    background-color: transparent;
    color: {p['green']};
    border: 1px solid {p['green_dark']};
    border-radius: 6px;
    padding: 5px 12px;
}}
QPushButton#Support:hover {{ background-color: {p['green_dark']}; color: #FFFFFF; }}
QPushButton#Priority {{
    background-color: transparent;
    color: {p['green']};
    border: 1px solid {p['green_dark']};
    border-radius: 6px;
    padding: 4px 12px;
    font-weight: 600;
}}
QPushButton#Priority:hover {{ background-color: {p['green_dark']}; color: #FFFFFF; }}
QLabel#Speed.TLabel {{ background: transparent; color: {p['muted']}; }}
QFrame#StatusBar[snail="true"] {{ background-color: #4A3A12; }}
QFrame#StatusBar[snail="true"] QLabel {{ color: #E8B84B; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {p['muted']};
    border: none;
    border-bottom: 1px solid transparent;
    padding: 4px 10px 4px 0;
    margin-right: 14px;
    font-size: 11px;
}}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 1px solid {p['accent']}; }}
QTreeWidget {{
    background-color: {p['bg']};
    border: none;
    alternate-background-color: {p['row_alt']};
    gridline-color: transparent;
    outline: none;
}}
QTreeWidget::item {{ padding: 3px 2px; }}
QHeaderView::section:only {{
    background-color: {p['bg']};
    color: {p['muted']};
    border: none;
    padding: 4px 6px;
    font-weight: 500;
}}
QPushButton#Danger {{
    background-color: {p['danger']};
    color: #FFFFFF;
    border: 1px solid {p['danger']};
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton#Danger:hover {{ background-color: #d94545; }}
QPushButton#Ghost {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {p['muted']};
    padding: 4px 8px;
}}
QPushButton#Ghost:hover {{ background-color: {p['hover']}; color: {p['text']}; }}
QPushButton#Tool {{
    background-color: {p['input']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 6px 12px;
    text-align: left;
}}
QPushButton#Tool:hover {{ background-color: {p['hover']}; }}
QPushButton#Tool::menu-indicator {{ image: none; width: 0; }}

QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px;
}}
QToolButton:hover {{ background-color: {p['hover']}; }}
QToolButton:pressed {{ background-color: {p['border']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit {{
    background-color: {p['input']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {p['accent']};
    selection-color: {p['accent_text']};
}}
QLineEdit:focus {{ border-color: {p['accent']}; }}
QLineEdit:disabled {{ color: {p['muted']}; }}

QComboBox {{
    background-color: {p['input']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {p['accent']}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{
    background-color: {p['popup']};
    border: 1px solid {p['popup_border']};
    selection-background-color: {p['selection']};
    selection-color: {p['text']};
    padding: 4px;
    outline: none;
}}

QTableView {{
    background-color: {p['area']};
    alternate-background-color: {p['row_alt']};
    border: none;
    gridline-color: transparent;
    selection-background-color: {p['selection']};
    selection-color: {p['text']};
    outline: none;
}}
QTableView::item {{ padding: 6px 4px; border: none; }}
QTableView::item:selected {{ background-color: {p['selection']}; }}

QHeaderView {{ background-color: {p['area']}; }}
QHeaderView::section {{
    background-color: {p['area']};
    color: {p['muted']};
    border: none;
    border-bottom: 1px solid {p['border']};
    padding: 7px 6px;
    font-weight: 500;
}}
QHeaderView::section:hover {{ color: {p['text']}; }}
QTableCornerButton::section {{ background-color: {p['area']}; border: none; }}

QMenu {{
    background-color: {p['popup']};
    border: 1px solid {p['popup_border']};
    padding: 6px;
    color: {p['text']};
}}
QMenu::item {{ padding: 7px 28px 7px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {p['selection']}; }}
QMenu::item:disabled {{ color: {p['muted']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 5px 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['border']}; border-radius: 5px; min-width: 30px; }}

QListWidget#SidebarList {{
    background-color: {p['bg']};
    border: none;
    outline: none;
    padding: 4px;
}}
QListWidget#SidebarList::item {{
    padding: 9px 12px;
    border-radius: 6px;
    color: {p['text']};
}}
QListWidget#SidebarList::item:hover {{ background-color: {p['hover']}; }}
QListWidget#SidebarList::item:selected {{ background-color: {p['selection']}; color: {p['text']}; }}

QCheckBox, QRadioButton {{ spacing: 8px; padding: 3px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid {p['border']}; border-radius: 4px; background: {p['input']}; }}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}
QRadioButton::indicator {{ border: 1px solid {p['border']}; border-radius: 9px; background: {p['input']}; }}
QRadioButton::indicator:checked {{ border: 5px solid {p['accent']}; background: {p['input']}; }}

QSpinBox, QDoubleSpinBox, QLineEdit {{
    background-color: {p['input']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 5px 8px;
    min-width: 90px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p['accent']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QProgressBar {{
    background-color: {p['border']};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background-color: {p['accent']}; border-radius: 3px; }}

QToolTip {{
    background-color: {p['popup']};
    color: {p['text']};
    border: 1px solid {p['popup_border']};
    padding: 4px;
}}
QStatusBar {{ background-color: {p['toolbar']}; color: {p['text']}; }}
QTextEdit, QPlainTextEdit {{ background-color: {p['input']}; border: 1px solid {p['border']};
                             border-radius: 6px; }}
"""


# --------------------------------------------------------------------------- icons

def _pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    return pixmap


def _pen(painter: QPainter, colour: str, width: float = 1.6) -> None:
    pen = QPen(QColor(colour))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)


def make_icon(name: str, colour: str = "#E4E4E7", size: int = 16) -> QIcon:
    """Draw one of the small vector icons used by the interface."""
    icon = QIcon()
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    s = size
    u = s / 16.0  # one unit is 16 px

    if name == "folder":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#4CD3FF"))
        path = QPainterPath()
        path.moveTo(1.5 * u, 4.0 * u)
        path.lineTo(6.0 * u, 4.0 * u)
        path.lineTo(7.5 * u, 5.6 * u)
        path.lineTo(14.5 * u, 5.6 * u)
        path.lineTo(14.5 * u, 12.5 * u)
        path.lineTo(1.5 * u, 12.5 * u)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "chevron":
        _pen(painter, colour, 1.7)
        painter.drawPolyline([QPointF(4 * u, 6.5 * u), QPointF(8 * u, 10.5 * u),
                              QPointF(12 * u, 6.5 * u)])
    elif name == "sort":
        _pen(painter, colour, 1.5)
        for index, width in enumerate((8.0, 5.5, 3.0)):
            y = (4.5 + index * 3.5) * u
            painter.drawLine(QPointF(3.5 * u, y), QPointF((3.5 + width) * u, y))
    elif name == "filter":
        _pen(painter, colour, 1.5)
        painter.drawPolyline([QPointF(2.5 * u, 3.5 * u), QPointF(13.5 * u, 3.5 * u),
                              QPointF(9.5 * u, 8.5 * u), QPointF(9.5 * u, 12.5 * u),
                              QPointF(6.5 * u, 10.5 * u), QPointF(6.5 * u, 8.5 * u),
                              QPointF(2.5 * u, 3.5 * u)])
    elif name == "search":
        _pen(painter, colour, 1.7)
        painter.drawEllipse(QPointF(7 * u, 7 * u), 4.0 * u, 4.0 * u)
        painter.drawLine(QPointF(10.2 * u, 10.2 * u), QPointF(13.5 * u, 13.5 * u))
    elif name == "menu":
        _pen(painter, colour, 1.7)
        for index in range(3):
            y = (4.0 + index * 4.0) * u
            painter.drawLine(QPointF(3.0 * u, y), QPointF(13.0 * u, y))
    elif name == "plus":
        _pen(painter, colour, 2.0)
        painter.drawLine(QPointF(8 * u, 3.5 * u), QPointF(8 * u, 12.5 * u))
        painter.drawLine(QPointF(3.5 * u, 8 * u), QPointF(12.5 * u, 8 * u))
    elif name == "dots":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        for y in (4.0, 8.0, 12.0):
            painter.drawEllipse(QPointF(8 * u, y * u), 1.3 * u, 1.3 * u)
    elif name == "file":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(4.0 * u, 2.5 * u)
        path.lineTo(9.5 * u, 2.5 * u)
        path.lineTo(12.0 * u, 5.0 * u)
        path.lineTo(12.0 * u, 13.5 * u)
        path.lineTo(4.0 * u, 13.5 * u)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(9.5 * u, 2.5 * u), QPointF(9.5 * u, 5.0 * u))
        painter.drawLine(QPointF(9.5 * u, 5.0 * u), QPointF(12.0 * u, 5.0 * u))
    elif name == "clipboard":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(4.0 * u, 3.0 * u, 8.0 * u, 11.0 * u), 1.5 * u, 1.5 * u)
        painter.drawRoundedRect(QRectF(6.0 * u, 1.5 * u, 4.0 * u, 2.5 * u), 1.0 * u, 1.0 * u)
    elif name == "checkbox":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(3.0 * u, 3.0 * u, 10.0 * u, 10.0 * u), 2.0 * u, 2.0 * u)
    elif name == "checkbox_checked":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(QRectF(3.0 * u, 3.0 * u, 10.0 * u, 10.0 * u), 2.0 * u, 2.0 * u)
        _pen(painter, "#141415", 1.8)
        painter.drawPolyline([QPointF(5.4 * u, 8.2 * u), QPointF(7.2 * u, 10.0 * u),
                              QPointF(10.8 * u, 6.0 * u)])
    elif name == "check":
        _pen(painter, colour, 2.0)
        painter.drawPolyline([QPointF(3.5 * u, 8.5 * u), QPointF(6.5 * u, 11.5 * u),
                              QPointF(12.5 * u, 4.5 * u)])
    elif name == "shield":
        _pen(painter, colour, 1.5)
        path = QPainterPath()
        path.moveTo(8 * u, 2.0 * u)
        path.lineTo(13.5 * u, 4.5 * u)
        path.lineTo(13.5 * u, 8.5 * u)
        path.cubicTo(13.5 * u, 11.5 * u, 11.0 * u, 13.0 * u, 8 * u, 14.0 * u)
        path.cubicTo(5.0 * u, 13.0 * u, 2.5 * u, 11.5 * u, 2.5 * u, 8.5 * u)
        path.lineTo(2.5 * u, 4.5 * u)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "snail":
        _pen(painter, colour, 1.5)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(9.0 * u, 9.0 * u), 3.2 * u, 3.2 * u)
        painter.drawArc(QRectF(7.0 * u, 7.0 * u, 4.0 * u, 4.0 * u), 30 * 16, 300 * 16)
        painter.drawPolyline([QPointF(3.0 * u, 12.5 * u), QPointF(4.0 * u, 11.0 * u),
                              QPointF(6.5 * u, 11.0 * u)])
    elif name == "down":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawPolygon([QPointF(8 * u, 11.5 * u), QPointF(12.5 * u, 6.0 * u),
                             QPointF(3.5 * u, 6.0 * u)])
    elif name == "up":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawPolygon([QPointF(8 * u, 4.5 * u), QPointF(12.5 * u, 10.0 * u),
                             QPointF(3.5 * u, 10.0 * u)])
    elif name == "trash":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(3.0 * u, 4.5 * u), QPointF(13.0 * u, 4.5 * u))
        path = QPainterPath()
        path.moveTo(4.5 * u, 4.5 * u)
        path.lineTo(5.5 * u, 13.5 * u)
        path.lineTo(10.5 * u, 13.5 * u)
        path.lineTo(11.5 * u, 4.5 * u)
        painter.drawPath(path)
        painter.drawLine(QPointF(6.2 * u, 2.5 * u), QPointF(9.8 * u, 2.5 * u))
        painter.drawLine(QPointF(6.5 * u, 6.5 * u), QPointF(6.8 * u, 11.5 * u))
        painter.drawLine(QPointF(9.5 * u, 6.5 * u), QPointF(9.2 * u, 11.5 * u))
    elif name == "heart":
        _pen(painter, colour, 1.5)
        path = QPainterPath()
        path.moveTo(8 * u, 13.5 * u)
        path.cubicTo(2.0 * u, 9.5 * u, 3.0 * u, 4.0 * u, 6.5 * u, 4.0 * u)
        path.cubicTo(7.6 * u, 4.0 * u, 8 * u, 5.0 * u, 8 * u, 5.0 * u)
        path.cubicTo(8 * u, 5.0 * u, 8.4 * u, 4.0 * u, 9.5 * u, 4.0 * u)
        path.cubicTo(13.0 * u, 4.0 * u, 14.0 * u, 9.5 * u, 8 * u, 13.5 * u)
        painter.drawPath(path)
    elif name == "clock":
        _pen(painter, colour, 1.5)
        painter.drawEllipse(QPointF(8 * u, 8 * u), 5.5 * u, 5.5 * u)
        painter.drawLine(QPointF(8 * u, 4.5 * u), QPointF(8 * u, 8 * u))
        painter.drawLine(QPointF(8 * u, 8 * u), QPointF(11 * u, 9.5 * u))
    elif name == "gear":
        _pen(painter, colour, 1.4)
        painter.drawEllipse(QPointF(8 * u, 8 * u), 3.0 * u, 3.0 * u)
        for index in range(6):
            angle = index * pi / 3
            painter.drawLine(QPointF(8 * u + 4.4 * u * cos(angle), 8 * u + 4.4 * u * sin(angle)),
                             QPointF(8 * u + 6.2 * u * cos(angle), 8 * u + 6.2 * u * sin(angle)))
    elif name == "pause":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(QRectF(5.0 * u, 3.5 * u, 2.2 * u, 9 * u), 1, 1)
        painter.drawRoundedRect(QRectF(8.8 * u, 3.5 * u, 2.2 * u, 9 * u), 1, 1)
    elif name == "play":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawPolygon([QPointF(5.5 * u, 3.5 * u), QPointF(12.5 * u, 8 * u),
                             QPointF(5.5 * u, 12.5 * u)])
    else:  # unknown name: a small dot, so the interface never shows a broken image
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QPointF(8 * u, 8 * u), 2.0 * u, 2.0 * u)

    painter.end()
    icon.addPixmap(pixmap)
    return icon


def app_icon(colour: str = "#4CD3FF", size: int = 64) -> QIcon:
    """The window icon: a rounded square with a download arrow."""
    icon = QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    u = size / 64.0
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawRoundedRect(QRectF(4 * u, 4 * u, 56 * u, 56 * u), 14 * u, 14 * u)
    painter.setBrush(QColor("#06212B"))
    painter.drawPolygon([QPointF(32 * u, 14 * u), QPointF(44 * u, 30 * u),
                         QPointF(36 * u, 30 * u), QPointF(36 * u, 40 * u),
                         QPointF(28 * u, 40 * u), QPointF(28 * u, 30 * u),
                         QPointF(20 * u, 30 * u)])
    painter.drawRoundedRect(QRectF(20 * u, 44 * u, 24 * u, 6 * u), 3 * u, 3 * u)
    painter.end()
    icon.addPixmap(pixmap)
    return icon


def icon_size(size: int = 16) -> QSize:
    return QSize(size, size)
