"""深色主题：颜色常量与全局样式表。"""

from __future__ import annotations

from PySide6.QtGui import QColor

from ..core.models import DiffStatus

BG = "#14161b"
PANEL = "#1b1e24"
PANEL_ALT = "#22252d"
BORDER = "#333844"
TEXT = "#e3e6ec"
MUTED = "#8f96a4"
ACCENT = "#3d9df2"
ACCENT_HOVER = "#5cb1f7"
DANGER = "#ef6666"

STATUS_COLORS: dict[DiffStatus, tuple[str, str, str]] = {
    # (文字色, 行背景色, 徽章背景色)
    DiffStatus.SAME: ("#8f95a2", "", "#2e313a"),
    DiffStatus.MODIFIED: ("#f0ba5e", "#3f2f14", "#63471b"),
    DiffStatus.LEFT_ONLY: ("#63c2f8", "#153450", "#1a4e75"),
    DiffStatus.RIGHT_ONLY: ("#c9a6f7", "#2e2049", "#513680"),
    DiffStatus.TYPE_CONFLICT: ("#f59884", "#45201c", "#73322b"),
    DiffStatus.FOLDER_DIFF: ("#e0b063", "#362a15", "#54401e"),
    DiffStatus.ERROR: ("#f58282", "#421d22", "#722c33"),
}

LINE_COLORS = {
    "equal": ("", ""),
    "replace": ("#3f2f14", "#3f2f14"),
    "delete": ("#153450", "#24272f"),
    "insert": ("#24272f", "#2e2049"),
    "cosmetic": ("#2b2d34", "#2b2d34"),
}
INLINE_COLORS = {
    "replace": "#96691c",
    "delete": "#2f74a8",
    "insert": "#6f4ba5",
}
CURRENT_BLOCK = "#7d6626"
PLACEHOLDER = "#8f95a2"
LINE_NUMBER_BG = "#1d2026"
LINE_NUMBER_FG = "#767d8c"

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
QPushButton:hover {{ background: #2e323c; border-color: #4a4f5c; }}
QPushButton:pressed {{ background: #383d49; }}
QPushButton:disabled {{ color: #6a7080; background: {PANEL}; }}
QPushButton[accent="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600; }}
QPushButton[accent="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[accent="true"]:disabled {{ background: #2c4a72; border-color: #2c4a72; color: #8b94a8; }}
QPushButton[danger="true"] {{ color: #f0a0a0; }}
QPushButton:checked {{ background: #2c4a72; border-color: {ACCENT}; }}

QTreeView, QTableView {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 4px;
    gridline-color: #2e313a; alternate-background-color: #262932;
    selection-background-color: #2c5386; selection-color: #ffffff; outline: none;
}}
QTreeView::item {{ padding: 3px 2px; border: none; }}
QTreeView::item:hover {{ background: #303848; }}
QTreeView::item:selected {{ background: #2c5386; }}
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
QScrollBar::handle {{ background: #444a57; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: #5b6272; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid #565d6b; border-radius: 3px; background: {PANEL_ALT};
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
    TEXT: "#20242c", MUTED: "#5c6573", ACCENT: "#1e7cd8", ACCENT_HOVER: "#3590e8",
    "#14161b": "#f4f6f9", "#1d2026": "#ffffff", "#1b1e24": "#ffffff",
    "#22252d": "#fbfcfe", "#303848": "#e9eef7", "#2c5386": "#b8d0f5",
    "#2e313a": "#e3e7ee", "#444a57": "#b6bdc8", "#5b6272": "#a2aab6",
    "#2e323c": "#eef1f6", "#383d49": "#e5e9f0",
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
