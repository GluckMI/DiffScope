"""深色主题：颜色常量与全局样式表。"""

from __future__ import annotations

from PySide6.QtGui import QColor

from ..core.models import DiffStatus

BG = "#1b1d23"
PANEL = "#22242b"
PANEL_ALT = "#282b33"
BORDER = "#363a45"
TEXT = "#dfe2e8"
MUTED = "#9096a3"
ACCENT = "#4d8cf0"
ACCENT_HOVER = "#619bff"
DANGER = "#e65757"

STATUS_COLORS: dict[DiffStatus, tuple[str, str, str]] = {
    # (文字色, 行背景色, 徽章背景色)
    DiffStatus.SAME: ("#8b919e", "", "#2c2f37"),
    DiffStatus.MODIFIED: ("#e8b35a", "#3a2c17", "#5a421d"),
    DiffStatus.LEFT_ONLY: ("#74bdf5", "#183348", "#1d4a6b"),
    DiffStatus.RIGHT_ONLY: ("#c4a2f2", "#2c2042", "#493270"),
    DiffStatus.TYPE_CONFLICT: ("#f29784", "#421f1c", "#6b302a"),
    DiffStatus.FOLDER_DIFF: ("#dcae62", "#332917", "#4d3c1d"),
    DiffStatus.ERROR: ("#f08080", "#3f1d21", "#6a2a30"),
}

LINE_COLORS = {
    "equal": ("", ""),
    "replace": ("#3a2c17", "#3a2c17"),
    "delete": ("#183348", "#23262d"),
    "insert": ("#23262d", "#2c2042"),
    "cosmetic": ("#2a2b31", "#2a2b31"),
}
INLINE_COLORS = {
    "replace": "#8a6320",
    "delete": "#2a6a94",
    "insert": "#63438f",
}
CURRENT_BLOCK = "#6d5a25"
PLACEHOLDER = "#8b919e"
LINE_NUMBER_BG = "#202229"
LINE_NUMBER_FG = "#747b89"

STYLESHEET = f"""
* {{ font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif; font-size: 12px; }}
QWidget {{ color: {TEXT}; background: {BG}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolBar {{
    background: {PANEL}; border: none; border-bottom: 1px solid {BORDER};
    padding: 4px; spacing: 4px;
}}
QToolBar QLabel {{ background: transparent; color: {MUTED}; padding: 0 3px; }}
QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 5px 12px; background: transparent; }}
QMenuBar::item:selected {{ background: {ACCENT}; border-radius: 3px; }}
QMenu {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QTableWidget {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 4px;
    color: {TEXT}; padding: 4px 6px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {MUTED}; background: {PANEL}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    color: {TEXT}; outline: none;
}}

QPushButton {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 4px;
    color: {TEXT}; padding: 5px 12px; min-height: 20px;
}}
QPushButton:hover {{ background: #31353f; border-color: #4a4f5c; }}
QPushButton:pressed {{ background: #3a3f4b; }}
QPushButton:disabled {{ color: #686d79; background: {PANEL}; }}
QPushButton[accent="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600; }}
QPushButton[accent="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[accent="true"]:disabled {{ background: #35415a; border-color: #35415a; color: #8b94a8; }}
QPushButton[danger="true"] {{ color: #f0a0a0; }}
QPushButton:checked {{ background: #35415a; border-color: {ACCENT}; }}

QTreeView, QTableView {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 4px;
    gridline-color: #2f323b; alternate-background-color: #252830;
    selection-background-color: #35507a; selection-color: #ffffff; outline: none;
}}
QTreeView::item {{ padding: 3px 2px; border: none; }}
QTreeView::item:hover {{ background: #303747; }}
QTreeView::item:selected {{ background: #35507a; }}
QHeaderView::section {{
    background: {PANEL}; color: {MUTED}; border: none;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; font-weight: 600;
}}
QHeaderView::section:hover {{ color: {TEXT}; }}
QTreeView::branch {{ background: {PANEL_ALT}; }}
QTreeView::branch:has-children:!has-siblings:closed {{ image: none; border: none; }}
QTableWidget {{ gridline-color: {BORDER}; }}
QTableWidget::item {{ padding: 4px 6px; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QSplitter::handle:hover {{ background: {ACCENT}; }}

QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ background: transparent; }}
QProgressBar {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 3px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QScrollBar:vertical {{ background: {PANEL}; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 11px; margin: 0; }}
QScrollBar::handle {{ background: #464b57; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: #5a6070; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid #555b68; border-radius: 3px; background: {PANEL_ALT};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 5px; margin-top: 12px; padding: 10px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {MUTED}; }}
QMessageBox {{ background: {BG}; }}
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QFrame[role="card"] {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame[role="divider"] {{ background: {BORDER}; }}
"""


LIGHT_MAP = {
    BG: "#f4f6f9", PANEL: "#ffffff", PANEL_ALT: "#fbfcfe", BORDER: "#d7dce5",
    TEXT: "#20242c", MUTED: "#5c6573", ACCENT: "#3568c8", ACCENT_HOVER: "#4a7ce0",
    "#1b1d23": "#f4f6f9", "#202229": "#ffffff", "#22242b": "#ffffff",
    "#282b33": "#fbfcfe", "#303747": "#e9eef7", "#35507a": "#b8d0f5",
    "#2f323b": "#e3e7ee", "#464b57": "#b6bdc8", "#5a6070": "#a2aab6",
    "#31353f": "#eef1f6", "#3a3f4b": "#e5e9f0",
}


def light_stylesheet() -> str:
    """基于深色样式表生成浅色主题（按颜色常量替换）。"""
    result = STYLESHEET
    for old, new in sorted(LIGHT_MAP.items(), key=lambda item: -len(item[0])):
        result = result.replace(old, new)
    return result


def qcolor(hex_value: str) -> QColor:
    return QColor(hex_value)


def status_fg(status: DiffStatus) -> QColor:
    return QColor(STATUS_COLORS[status][0])


def status_bg(status: DiffStatus) -> QColor:
    value = STATUS_COLORS[status][1]
    return QColor(value) if value else QColor(0, 0, 0, 0)
