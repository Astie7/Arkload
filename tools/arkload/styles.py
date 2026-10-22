"""The design system of ArkLoad: colours, stylesheet and vector icons.

The interface follows two references:

* **Free Download Manager** gives the frame: one bar at the top with the filters on the left
  and the main action, the search and the menu on the right, a table with the columns
  ``# Name Size Status Download Upload Added``, an area that accepts dropped links and a slim
  bar at the bottom with the limiter, the speeds and the priority.
* **JDownloader 2** gives the power: the tabs (Downloads, LinkGrabber, History), the link
  grabber with its own input, and the "Download Overview" with the numbers of the whole list.

The dark theme is the one of the reference: a near black window, a slightly lighter bar, one
blue accent and a green for what is running well.  Everything is drawn with QPainter, so the
application ships no images.
"""
from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

#: the dark theme
DARK = {
    # the surfaces, from the darkest to the lightest
    "bg": "#0C0E11",
    "surface": "#12151A",
    "surface_alt": "#181C22",
    "bar": "#101317",
    "elevated": "#1F242B",
    "input": "#171B21",
    # the lines
    "border": "#232932",
    "border_soft": "#1A1F26",
    "border_strong": "#2E353F",
    # the colours
    "accent": "#3D9BFF",
    "accent_hover": "#5AAEFF",
    "accent_soft": "#12283D",
    "accent_text": "#03131F",
    "text": "#E8ECF1",
    "muted": "#8A929C",
    "faint": "#5A626C",
    "success": "#3DD68C",
    "success_soft": "#12291E",
    "warning": "#F5A524",
    "danger": "#F04438",
    "danger_soft": "#2A1513",
    "row_alt": "#0F1216",
    "selection": "#15293A",
    "popup": "#181C22",
    "popup_border": "#2E353F",
    "hover": "#1B2027",
    "shadow": "#000000",
}

#: the light theme
LIGHT = {
    "bg": "#F0F2F5",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F8FA",
    "bar": "#FFFFFF",
    "elevated": "#FFFFFF",
    "input": "#FFFFFF",
    "border": "#E1E5EA",
    "border_soft": "#EDEFF2",
    "border_strong": "#CBD2D9",
    "accent": "#1A73E8",
    "accent_hover": "#4285F4",
    "accent_soft": "#E3F0FE",
    "accent_text": "#FFFFFF",
    "text": "#14171C",
    "muted": "#6A737D",
    "faint": "#98A0A8",
    "success": "#12A150",
    "success_soft": "#E3F6EB",
    "warning": "#B7791F",
    "danger": "#D93025",
    "danger_soft": "#FBE7E5",
    "row_alt": "#FAFBFC",
    "selection": "#E3F0FE",
    "popup": "#FFFFFF",
    "popup_border": "#D7DCE1",
    "hover": "#ECEFF3",
    "shadow": "#000000",
}

PALETTES = {"dark": DARK, "light": LIGHT}


def palette(name: str = "dark") -> dict:
    """The colours of one theme."""
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
QLabel {{ background: transparent; }}
QFrame#Panel QLabel, QFrame#Toolbar QLabel, QFrame#ToolStrip QLabel,
QFrame#StatusBar QLabel, QFrame#Overview QLabel, QFrame#DetailsCard QLabel {{
    background: transparent;
}}
QToolTip {{
    background-color: {p['elevated']};
    color: {p['text']};
    border: 1px solid {p['border_strong']};
    padding: 5px 9px;
    border-radius: 6px;
}}

/* -- the surfaces ---------------------------------------------------------- */
QFrame#Toolbar, QFrame#ToolStrip {{ background-color: {p['bar']}; }}
QFrame#Toolbar {{ border-bottom: 1px solid {p['border_soft']}; }}
QFrame#ToolStrip {{ border-bottom: 1px solid {p['border_soft']}; }}
QFrame#Toolbar QLabel, QFrame#ToolStrip QLabel,
QFrame#StatusBar QLabel, QFrame#Overview QLabel,
QFrame#DetailsCard QLabel {{ background: transparent; }}
QFrame#StatusBar {{
    background-color: {p['bar']};
    border-top: 1px solid {p['border_soft']};
}}
QFrame#Panel {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 10px;
}}
QFrame#DetailsCard {{
    background-color: {p['surface']};
    border-top: 1px solid {p['border_soft']};
}}
QFrame#Overview {{
    background-color: {p['surface_alt']};
    border-top: 1px solid {p['border_soft']};
}}
QFrame#Sidebar {{
    background-color: {p['bar']};
    border-right: 1px solid {p['border_soft']};
}}

/* -- the texts ------------------------------------------------------------- */
QLabel#Title {{ font-size: 20px; font-weight: 600; }}
QLabel#Heading {{ font-size: 17px; font-weight: 600; }}
QLabel#SubHeading {{ font-size: 12px; font-weight: 600; color: {p['muted']}; }}
QLabel#Muted {{ color: {p['muted']}; }}
QLabel#Faint {{ color: {p['faint']}; }}
QLabel#FactKey {{ color: {p['faint']}; font-size: 11px; }}
QLabel#FactValue {{ color: {p['text']}; font-size: 12px; font-weight: 600; }}
QLabel#Link {{ color: {p['accent']}; }}

/* -- the buttons ----------------------------------------------------------- */
QPushButton {{
    background-color: {p['input']};
    color: {p['text']};
    border: 1px solid {p['border_strong']};
    border-radius: 7px;
    padding: 6px 13px;
}}
QPushButton:hover {{ background-color: {p['hover']}; border-color: {p['accent']}; }}
QPushButton:pressed {{ background-color: {p['border']}; }}
QPushButton:disabled {{ color: {p['faint']}; border-color: {p['border']}; }}
QPushButton#Accent {{
    background-color: {p['accent']};
    color: {p['accent_text']};
    border: 1px solid {p['accent']};
    border-radius: 7px;
    padding: 7px 16px;
    font-weight: 600;
}}
QPushButton#Accent:hover {{ background-color: {p['accent_hover']};
                            border-color: {p['accent_hover']}; }}
QPushButton#AccentSmall {{
    background-color: {p['accent']};
    color: {p['accent_text']};
    border: 1px solid {p['accent']};
    border-radius: 7px;
    padding: 5px 12px;
    font-weight: 600;
}}
QPushButton#AccentSmall:hover {{ background-color: {p['accent_hover']};
                                border-color: {p['accent_hover']}; }}
QPushButton#Accent:disabled {{ background-color: {p['border_strong']};
                               color: {p['faint']}; border-color: {p['border_strong']}; }}
QFrame#InputRow {{
    background-color: {p['input']};
    border: 1px solid {p['border_strong']};
    border-radius: 8px;
}}
QFrame#InputRow QLineEdit, QFrame#InputRow QComboBox {{
    background: transparent;
    border: none;
    padding: 7px 2px;
}}
QFrame#InputRow QLineEdit:focus, QFrame#InputRow QComboBox:focus {{ border: none; }}
QPushButton#Flat {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {p['muted']};
    padding: 5px 10px;
}}
QPushButton#Flat:hover {{ background-color: {p['hover']}; color: {p['text']}; }}
QPushButton#Ghost {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {p['muted']};
    padding: 5px 9px;
    border-radius: 7px;
}}
QPushButton#Ghost:hover {{ background-color: {p['hover']}; color: {p['text']}; }}
QPushButton#Tool {{
    background-color: {p['input']};
    border: 1px solid {p['border']};
    border-radius: 7px;
    padding: 6px 12px;
    text-align: left;
}}
QPushButton#Tool:hover {{ background-color: {p['hover']}; }}
QPushButton#Tool::menu-indicator {{ image: none; width: 0; }}
QPushButton#Priority {{
    background-color: transparent;
    color: {p['success']};
    border: 1px solid {p['success']};
    border-radius: 7px;
    padding: 3px 12px;
    font-weight: 600;
}}
QPushButton#Priority:hover {{ background-color: {p['success_soft']}; }}
QPushButton#Danger {{
    background-color: transparent;
    color: {p['danger']};
    border: 1px solid {p['border_strong']};
    border-radius: 7px;
    padding: 6px 13px;
    font-weight: 600;
}}
QPushButton#Danger:hover {{ background-color: {p['danger']}; color: #FFFFFF;
                            border-color: {p['danger']}; }}

/* the path listing of the delete dialog */
QPlainTextEdit#DeleteListing {{
    background-color: {p['elevated']};
    color: {p['text']};
    border: 1px solid {p['border_soft']};
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 12px;
    selection-background-color: {p['selection']};
}}

QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px;
}}
QToolButton:hover {{ background-color: {p['hover']}; }}
QToolButton:pressed {{ background-color: {p['border']}; }}
QToolButton:disabled {{ color: {p['faint']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#Limiter {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 8px 3px 6px;
    color: {p['muted']};
    font-weight: 600;
}}
QToolButton#Limiter:hover {{
    background-color: {p['hover']};
    border-color: {p['border']};
    color: {p['text']};
}}
QToolButton#Chip {{
    background-color: {p['input']};
    border: 1px solid {p['border']};
    border-radius: 13px;
    padding: 4px 12px 4px 10px;
    color: {p['muted']};
}}
QToolButton#Chip:hover {{ border-color: {p['accent']}; color: {p['text']}; }}
QFrame#Separator {{
    background-color: {p['border_soft']};
    max-width: 1px; min-width: 1px;
    border: none;
}}
QToolButton#Pill {{
    background-color: {p['input']};
    border: 1px solid {p['border']};
    border-radius: 7px;
    padding: 5px 11px;
    color: {p['text']};
    font-size: 12px;
}}
QToolButton#Pill:hover {{ border-color: {p['accent']}; }}
QToolButton#Pill::menu-indicator {{ image: none; width: 0; }}

/* -- the inputs ------------------------------------------------------------ */
QLineEdit {{
    background-color: {p['input']};
    border: 1px solid {p['border_strong']};
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: {p['accent']};
    selection-color: {p['accent_text']};
}}
QLineEdit:focus {{ border-color: {p['accent']}; }}
QLineEdit:disabled {{ color: {p['muted']}; }}

QComboBox {{
    background-color: {p['input']};
    border: 1px solid {p['border_strong']};
    border-radius: 7px;
    padding: 6px 9px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {p['accent']}; }}
QComboBox:focus {{ border-color: {p['accent']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {p['popup']};
    border: 1px solid {p['popup_border']};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {p['accent']};
    selection-color: {p['accent_text']};
    outline: none;
}}

QSpinBox, QTimeEdit, QDateTimeEdit {{
    background-color: {p['input']};
    border: 1px solid {p['border_strong']};
    border-radius: 7px;
    padding: 6px 9px;
    min-height: 18px;
}}
QSpinBox:focus {{ border-color: {p['accent']}; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {p['border_strong']};
    border-radius: 4px;
    background: {p['input']};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {p['accent']};
    border-color: {p['accent']};
}}

QListWidget#SidebarList {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListWidget#SidebarList::item {{
    padding: 9px 12px;
    border-radius: 7px;
    color: {p['muted']};
    margin: 1px 4px;
}}
QListWidget#SidebarList::item:hover {{ background-color: {p['hover']}; color: {p['text']}; }}
QListWidget#SidebarList::item:selected {{
    background-color: {p['accent_soft']};
    color: {p['accent']};
    font-weight: 600;
}}

/* -- the tabs (JDownloader 2) ---------------------------------------------- */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {p['muted']};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 4px 10px 4px 0;
    margin-right: 14px;
    font-size: 11px;
}}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 1px solid {p['accent']}; }}
QTabBar#TabStrip {{ background: transparent; }}
QTabBar#TabStrip::tab {{
    background: transparent;
    color: {p['muted']};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 2px 8px 2px;
    margin-right: 20px;
    font-size: 13px;
}}
QTabBar#TabStrip::tab:hover {{ color: {p['text']}; }}
QTabBar#TabStrip::tab:selected {{
    color: {p['text']};
    border-bottom: 2px solid {p['accent']};
    font-weight: 600;
}}

/* -- the tables (Free Download Manager) ------------------------------------ */
QTableView, QTableView#GrabberTable {{
    background-color: transparent;
    alternate-background-color: {p['row_alt']};
    border: none;
    gridline-color: transparent;
    outline: none;
    selection-background-color: {p['selection']};
    selection-color: {p['text']};
}}
QTableView::item, QTableView#GrabberTable::item {{ padding: 3px 6px; }}
QTableView::item:hover, QTableView#GrabberTable::item:hover {{
    background-color: {p['hover']};
}}
QTableView::item:selected, QTableView::item:selected:!active,
QTableView#GrabberTable::item:selected, QTableView#GrabberTable::item:selected:!active {{
    background-color: {p['selection']};
    color: {p['text']};
}}
QTableView::item:selected:active, QTableView#GrabberTable::item:selected:active {{
    background-color: {p['selection']};
    color: {p['text']};
}}
QHeaderView {{ background-color: transparent; }}
QHeaderView::section {{
    background-color: transparent;
    color: {p['faint']};
    border: none;
    border-bottom: 1px solid {p['border_soft']};
    padding: 7px 6px 8px 6px;
    font-size: 11px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {p['muted']}; }}
QTableCornerButton::section {{ background-color: transparent; border: none; }}

QTreeWidget {{
    background-color: transparent;
    border: none;
    alternate-background-color: {p['row_alt']};
    gridline-color: transparent;
    outline: none;
}}
QTreeWidget::item {{ padding: 4px 2px; }}

/* -- the progress ---------------------------------------------------------- */
QProgressBar {{
    background-color: {p['border']};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {p['accent']};
    border-radius: 4px;
}}

/* -- the scroll bars ------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {p['border_strong']};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {p['border_strong']};
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -- the menus ------------------------------------------------------------- */
QMenu {{
    background-color: {p['popup']};
    border: 1px solid {p['popup_border']};
    border-radius: 9px;
    padding: 5px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 5px; color: {p['text']}; }}
QMenu::item:selected {{ background-color: {p['accent_soft']}; color: {p['accent']}; }}
QMenu::item:disabled {{ color: {p['faint']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
"""


def _pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    return pixmap


def _pen(painter: QPainter, colour: str, width: float = 1.5) -> None:
    pen = QPen(QColor(colour))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)


def make_icon(name: str, colour: str = "#E8ECF1", size: int = 16) -> QIcon:
    """Draw one of the small vector icons used by the interface."""
    icon = QIcon()
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    s = size
    u = s / 16.0  # one unit is 16 px

    if name == "folder":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3D9BFF"))
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
    elif name == "chevron_up":
        _pen(painter, colour, 1.7)
        painter.drawPolyline([QPointF(4 * u, 9.5 * u), QPointF(8 * u, 5.5 * u),
                              QPointF(12 * u, 9.5 * u)])
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
        _pen(painter, "#0C0E11", 2.0)
        painter.drawPolyline([QPointF(5.8 * u, 8.2 * u), QPointF(7.4 * u, 10.0 * u),
                              QPointF(10.4 * u, 6.0 * u)])
    elif name == "check":
        _pen(painter, colour, 2.0)
        painter.drawPolyline([QPointF(3.5 * u, 8.5 * u), QPointF(6.5 * u, 11.5 * u),
                              QPointF(12.5 * u, 4.5 * u)])
    elif name == "shield":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline([QPointF(8 * u, 2.0 * u), QPointF(13.0 * u, 4.0 * u),
                              QPointF(13.0 * u, 8.0 * u), QPointF(8 * u, 14.0 * u),
                              QPointF(3.0 * u, 8.0 * u), QPointF(3.0 * u, 4.0 * u),
                              QPointF(8 * u, 2.0 * u)])
    elif name == "snail":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(9.5 * u, 9.5 * u), 4.0 * u, 4.0 * u)
        painter.drawLine(QPointF(2.5 * u, 12.5 * u), QPointF(13.5 * u, 12.5 * u))
        painter.drawLine(QPointF(4.5 * u, 12.5 * u), QPointF(6.5 * u, 7.5 * u))
        painter.drawLine(QPointF(6.5 * u, 7.5 * u), QPointF(8.5 * u, 12.5 * u))
    elif name == "down":
        _pen(painter, colour, 1.8)
        painter.drawLine(QPointF(8 * u, 3.0 * u), QPointF(8 * u, 11.5 * u))
        painter.drawPolyline([QPointF(4.5 * u, 8.0 * u), QPointF(8 * u, 11.8 * u),
                              QPointF(11.5 * u, 8.0 * u)])
    elif name == "up":
        _pen(painter, colour, 1.8)
        painter.drawLine(QPointF(8 * u, 12.0 * u), QPointF(8 * u, 3.5 * u))
        painter.drawPolyline([QPointF(4.5 * u, 7.0 * u), QPointF(8 * u, 3.2 * u),
                              QPointF(11.5 * u, 7.0 * u)])
    elif name == "trash":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(3.0 * u, 4.5 * u), QPointF(13.0 * u, 4.5 * u))
        painter.drawPolyline([QPointF(4.5 * u, 4.5 * u), QPointF(5.4 * u, 13.5 * u),
                              QPointF(10.6 * u, 13.5 * u), QPointF(11.5 * u, 4.5 * u)])
        painter.drawPolyline([QPointF(6.2 * u, 4.5 * u), QPointF(6.2 * u, 2.5 * u),
                              QPointF(9.8 * u, 2.5 * u), QPointF(9.8 * u, 4.5 * u)])
    elif name == "heart":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        path = QPainterPath()
        path.moveTo(8 * u, 13.0 * u)
        path.cubicTo(1.5 * u, 8.5 * u, 3.5 * u, 3.0 * u, 8 * u, 6.5 * u)
        path.cubicTo(12.5 * u, 3.0 * u, 14.5 * u, 8.5 * u, 8 * u, 13.0 * u)
        painter.drawPath(path)
    elif name == "clock":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(8 * u, 8 * u), 5.5 * u, 5.5 * u)
        painter.drawPolyline([QPointF(8 * u, 4.5 * u), QPointF(8 * u, 8.5 * u),
                              QPointF(11.0 * u, 10.0 * u)])
    elif name == "gear":
        _pen(painter, colour, 1.4)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(8 * u, 8 * u), 2.8 * u, 2.8 * u)
        for index in range(8):
            angle = index * pi / 4
            painter.drawLine(QPointF(8 * u + cos(angle) * 4.0 * u, 8 * u + sin(angle) * 4.0 * u),
                             QPointF(8 * u + cos(angle) * 6.2 * u, 8 * u + sin(angle) * 6.2 * u))
    elif name == "pause":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(QRectF(4.5 * u, 3.0 * u, 2.6 * u, 10.0 * u), 1.0 * u, 1.0 * u)
        painter.drawRoundedRect(QRectF(8.9 * u, 3.0 * u, 2.6 * u, 10.0 * u), 1.0 * u, 1.0 * u)
    elif name == "stop":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(QRectF(4.0 * u, 4.0 * u, 8.0 * u, 8.0 * u), 1.2 * u, 1.2 * u)
    elif name == "play":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        path = QPainterPath()
        path.moveTo(5.0 * u, 3.0 * u)
        path.lineTo(13.0 * u, 8.0 * u)
        path.lineTo(5.0 * u, 13.0 * u)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "download":
        _pen(painter, colour, 1.6)
        painter.drawLine(QPointF(8 * u, 2.5 * u), QPointF(8 * u, 9.5 * u))
        painter.drawPolyline([QPointF(4.8 * u, 6.5 * u), QPointF(8 * u, 9.8 * u),
                              QPointF(11.2 * u, 6.5 * u)])
        painter.drawPolyline([QPointF(3.0 * u, 12.0 * u), QPointF(13.0 * u, 12.0 * u)])
    elif name == "link":
        _pen(painter, colour, 1.6)
        painter.drawPolyline([QPointF(6.6 * u, 9.4 * u), QPointF(4.8 * u, 11.2 * u),
                              QPointF(7.6 * u, 14.0 * u), QPointF(9.4 * u, 12.2 * u)])
        painter.drawPolyline([QPointF(9.4 * u, 6.6 * u), QPointF(11.2 * u, 4.8 * u),
                              QPointF(8.4 * u, 2.0 * u), QPointF(6.6 * u, 4.8 * u)])
        painter.drawLine(QPointF(5.8 * u, 10.2 * u), QPointF(10.2 * u, 5.8 * u))
    elif name == "history":
        _pen(painter, colour, 1.5)
        painter.drawArc(QRectF(2.5 * u, 3.0 * u, 11.0 * u, 11.0 * u), 40 * 16, 280 * 16)
        painter.drawPolyline([QPointF(2.2 * u, 6.4 * u), QPointF(2.6 * u, 10.2 * u),
                              QPointF(6.4 * u, 9.8 * u)])
        painter.drawPolyline([QPointF(8 * u, 5.0 * u), QPointF(8 * u, 8.4 * u),
                              QPointF(10.6 * u, 9.8 * u)])
    elif name == "bolt":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        path = QPainterPath()
        path.moveTo(9.0 * u, 1.5 * u)
        path.lineTo(4.0 * u, 9.0 * u)
        path.lineTo(7.4 * u, 9.0 * u)
        path.lineTo(6.6 * u, 14.5 * u)
        path.lineTo(12.0 * u, 6.8 * u)
        path.lineTo(8.4 * u, 6.8 * u)
        path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    icon.addPixmap(pixmap)
    return icon


def app_icon(colour: str = "#3D9BFF", size: int = 64) -> QIcon:
    """The icon of the application: a rounded tile with an arrow into a tray."""
    icon = QIcon()
    pixmap = _pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    u = size / 16.0
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawRoundedRect(QRectF(0.8 * u, 0.8 * u, 14.4 * u, 14.4 * u), 3.6 * u, 3.6 * u)
    _pen(painter, "#03131F", 1.9 * u)
    painter.drawLine(QPointF(8 * u, 3.4 * u), QPointF(8 * u, 9.4 * u))
    painter.drawPolyline([QPointF(5.0 * u, 6.6 * u), QPointF(8 * u, 9.7 * u),
                          QPointF(11.0 * u, 6.6 * u)])
    painter.drawPolyline([QPointF(3.8 * u, 12.2 * u), QPointF(12.2 * u, 12.2 * u)])
    painter.end()
    icon.addPixmap(pixmap)
    return icon


def icon_size(size: int = 16) -> QSize:
    return QSize(size, size)
