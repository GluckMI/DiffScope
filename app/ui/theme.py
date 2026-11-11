"""主题：深 / 浅两套语义色板、动态令牌与全局样式表。

界面只读取语义令牌，不写死色值；切换主题时刷新全局样式表并重新抛光控件。
保留旧常量名，供既有模块以 `from .theme import ...` 的方式继续导入。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

from ..core.models import DiffStatus

# ---------------------------------------------------------------- 色板
# 差异色相锚点固定：修改=橙、仅左=蓝、仅右=紫、含差异目录=暗橙、错误=红。
# 两套色板只调整明度 / 饱和度，保证 Beyond Compare 用户的辨识习惯不变。

DARK: dict = {
    "bg": "#14161b",
    "panel": "#1b1e24",
    "panel_alt": "#22252d",
    "border": "#333844",
    "border_hover": "#4a4f5c",
    "text": "#e3e6ec",
    "muted": "#9aa3b2",
    "brand": "#58b1f7",
    "accent": "#3d9df2",
    "accent_hover": "#5cb1f7",
    "accent_button": "#3d9df2",
    "accent_text": "#10141b",
    "danger": "#f58282",
    "danger_button": "#a32d2d",
    "danger_text": "#ffffff",
    "editor_bg": "#1d2026",
    "line_number_bg": "#1d2026",
    "line_number_fg": "#a8b1c0",
    "placeholder": "#9aa3b2",
    "current_block": "#7d6626",
    "find_bg": "#f0c674",
    "find_fg": "#14161b",
    "link": "#b18bea",
    "break": "#e6a445",
    "cosmetic_bg": "#3b3e47",
    "cosmetic_mark": "#9aa063",
    "selection_bg": "#2c5386",
    "selection_text": "#ffffff",
    "alternate_bg": "#262932",
    "gridline": "#2e313a",
    "scroll_handle": "#545b69",
    "scroll_hover": "#6b7383",
    "badge_text": "#ffffff",
    "status": {
        "same": ("#a8b1c0", "", "#2e313a", ""),
        "modified": ("#f0ba5e", "#3f2f14", "#63471b", "#7a5314"),
        "left_only": ("#63c2f8", "#153450", "#1a4e75", "#1c5a86"),
        "right_only": ("#c9a6f7", "#2e2049", "#513680", "#553882"),
        "type_conflict": ("#f59884", "#45201c", "#73322b", "#8a3a2c"),
        "folder_diff": ("#e0b063", "#362a15", "#54401e", "#6e5220"),
        "error": ("#f58282", "#421d22", "#722c33", "#8a343c"),
    },
    "line": {
        "equal": ("", ""),
        "replace": ("#3f2f14", "#3f2f14"),
        "delete": ("#153450", "#24272f"),
        "insert": ("#24272f", "#2e2049"),
        "cosmetic": ("#2b2d34", "#2b2d34"),
    },
    "inline": {"replace": "#7a5314", "delete": "#1c5a86", "insert": "#553882"},
    "syntax": {
        "keyword": "#d7a6e0",
        "comment": "#8fbf74",
        "string": "#e8a98f",
        "number": "#c5dfa8",
    },
}

LIGHT: dict = {
    "bg": "#f4f6f9",
    "panel": "#ffffff",
    "panel_alt": "#fbfcfe",
    "border": "#d7dce5",
    "border_hover": "#aab4c4",
    "text": "#20242c",
    "muted": "#5c6573",
    "brand": "#145da8",
    "accent": "#1e7cd8",
    "accent_hover": "#3590e8",
    "accent_button": "#145da8",
    "accent_text": "#ffffff",
    "danger": "#b3261e",
    "danger_button": "#a32d2d",
    "danger_text": "#ffffff",
    "editor_bg": "#ffffff",
    "line_number_bg": "#f2f4f8",
    "line_number_fg": "#5c6573",
    "placeholder": "#5c6573",
    "current_block": "#e8c56a",
    "find_bg": "#f0c674",
    "find_fg": "#20242c",
    "link": "#7a4fb0",
    "break": "#a8671a",
    "cosmetic_bg": "#eef0f4",
    "cosmetic_mark": "#8a7a3c",
    "selection_bg": "#b8d0f5",
    "selection_text": "#20242c",
    "alternate_bg": "#f7f9fc",
    "gridline": "#e3e7ee",
    "scroll_handle": "#b6bdc8",
    "scroll_hover": "#8f98a8",
    "badge_text": "#20242c",
    "status": {
        "same": ("#5c6573", "", "#e3e7ee", ""),
        "modified": ("#8a5a00", "#fff2dc", "#f7dca8", "#f6d08a"),
        "left_only": ("#0b5f9e", "#e3f1fd", "#b9ddf8", "#a9d3f5"),
        "right_only": ("#6a3aa8", "#f2e9fd", "#ddc9f7", "#d6bdf2"),
        "type_conflict": ("#a13b22", "#fdeee9", "#f4c9b8", "#efb79c"),
        "folder_diff": ("#7a5200", "#fff6e5", "#f0d8a8", "#edcf92"),
        "error": ("#b3261e", "#fdeceb", "#f4c7c3", "#eaa6a0"),
    },
    "line": {
        "equal": ("", ""),
        "replace": ("#fff2dc", "#fff2dc"),
        "delete": ("#e3f1fd", "#f0f4f8"),
        "insert": ("#f0f4f8", "#f2e9fd"),
        "cosmetic": ("#eef0f4", "#eef0f4"),
    },
    "inline": {"replace": "#f6d08a", "delete": "#a9d3f5", "insert": "#d6bdf2"},
    "syntax": {
        "keyword": "#6b2680",
        "comment": "#2f6620",
        "string": "#963a20",
        "number": "#255a17",
    },
}

_STATUS_KEYS: dict[DiffStatus, str] = {
    DiffStatus.SAME: "same",
    DiffStatus.MODIFIED: "modified",
    DiffStatus.LEFT_ONLY: "left_only",
    DiffStatus.RIGHT_ONLY: "right_only",
    DiffStatus.TYPE_CONFLICT: "type_conflict",
    DiffStatus.FOLDER_DIFF: "folder_diff",
    DiffStatus.ERROR: "error",
}

_STATUS_DEFAULTS = {
    key: (tuple(DARK["status"][key]), tuple(LIGHT["status"][key]))
    for key in DARK["status"]
}


def set_custom_colors(colors: dict | None) -> None:
    """用户自定义差异徽章色；文字色与行背景仍由主题托管，避免对比度失控。"""
    import re

    values = colors if isinstance(colors, dict) else {}
    for key, (dark_default, light_default) in _STATUS_DEFAULTS.items():
        chosen = str(values.get(key) or "")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", chosen):
            chosen = ""
        DARK["status"][key] = tuple(dark_default[:2]) + (chosen or dark_default[2],) + tuple(dark_default[3:])
        LIGHT["status"][key] = tuple(light_default[:2]) + (chosen or light_default[2],) + tuple(light_default[3:])


def custom_color_keys() -> tuple[str, ...]:
    return tuple(key for key in DARK["status"] if key not in ("same", "type_conflict"))


_ACTIVE = "dark"


def set_theme(name: str) -> str:
    """设置主题；system 按操作系统偏好解析为 dark / light。返回解析后的档位。"""
    global _ACTIVE
    value = str(name or "dark").lower()
    if value == "system":
        value = system_mode()
    if value not in ("dark", "light"):
        value = "dark"
    _ACTIVE = value
    return value


def system_mode() -> str:
    """读取系统明暗偏好；无法判定时回退到深色。"""
    try:
        if sys.platform == "win32":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            finally:
                winreg.CloseKey(key)
            return "light" if int(value) else "dark"
        if sys.platform == "darwin":
            import subprocess

            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            return "dark" if "Dark" in out.stdout else "light"
    except Exception:
        pass
    return "dark"


def theme_name() -> str:
    return _ACTIVE


def palette() -> dict:
    return DARK if _ACTIVE == "dark" else LIGHT


def c(key: str) -> str:
    """取当前主题的语义色值。"""
    return palette()[key]


def qcolor(value: str) -> QColor:
    """语义键或十六进制色值 → QColor（保留旧调用签名）。"""
    current = palette()
    return QColor(current.get(value, value))


def status_colors() -> dict[DiffStatus, tuple[str, str, str, str]]:
    return {status: tuple(palette()["status"][key]) for status, key in _STATUS_KEYS.items()}


def status_fg(status: DiffStatus) -> QColor:
    return QColor(status_colors()[status][0])


def status_bg(status: DiffStatus) -> QColor:
    value = status_colors()[status][1]
    return QColor(value) if value else QColor(0, 0, 0, 0)


def status_key(status: DiffStatus) -> str:
    return _STATUS_KEYS.get(status, "same")


# ---------------------------------------------------------------- 动态令牌
class _Token:
    """字符串型动态色值：在 f-string / str() 求值时读取当前主题。"""

    __slots__ = ("key",)

    def __init__(self, key: str) -> None:
        self.key = key

    def __str__(self) -> str:
        return c(self.key)

    def __format__(self, spec: str) -> str:
        return format(str(self), spec)

    def __repr__(self) -> str:
        return repr(str(self))

    def __eq__(self, other) -> bool:
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))

    def __add__(self, other):
        return str(self) + str(other)

    def __radd__(self, other):
        return str(other) + str(self)


class _View(Mapping):
    """按当前主题解析的分段色表（STATUS / LINE / INLINE / SYNTAX）。"""

    __slots__ = ("section",)

    def __init__(self, section: str) -> None:
        self.section = section

    def _data(self):
        data = palette()[self.section]
        if self.section == "status":
            return {status: tuple(data[key]) for status, key in _STATUS_KEYS.items()}
        return data

    def __getitem__(self, key):
        return self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self) -> int:
        return len(self._data())


BG = _Token("bg")
PANEL = _Token("panel")
PANEL_ALT = _Token("panel_alt")
BORDER = _Token("border")
TEXT = _Token("text")
MUTED = _Token("muted")
ACCENT = _Token("accent")
ACCENT_HOVER = _Token("accent_hover")
DANGER = _Token("danger")
BRAND = _Token("brand")
CURRENT_BLOCK = _Token("current_block")
PLACEHOLDER = _Token("placeholder")
LINE_NUMBER_BG = _Token("line_number_bg")
LINE_NUMBER_FG = _Token("line_number_fg")
STATUS_COLORS = _View("status")
LINE_COLORS = _View("line")
INLINE_COLORS = _View("inline")
SYNTAX_FG = _View("syntax")

# ---------------------------------------------------------------- 排版令牌
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
RADIUS_CARD = 6
RADIUS_CONTROL = 4
CONTROL_SM = 28
CONTROL_MD = 32
CONTROL_LG = 40

# 字阶：以界面默认 9 号为基准，其余角色按同一比例同步下调
FONT_BASE = 9
FONT_NOTE = 9
FONT_SECTION = 10
FONT_HELP_BODY = 10
FONT_SUBTITLE = 11
FONT_HELP_H3 = 11
FONT_HELP_H2 = 13
FONT_HEADING = 14
FONT_HERO = 26


def is_macos() -> bool:
    return sys.platform == "darwin"


def ui_font_stack() -> str:
    if is_macos():
        return '"PingFang SC", "SF Pro Text", "Helvetica Neue", sans-serif'
    return '"Microsoft YaHei UI", "Segoe UI", "Noto Sans CJK SC", sans-serif'


def mono_font_stack(exclude: str = "") -> str:
    """等宽字体回退链；排除用户已指定的首选字体，避免重复声明。"""
    fonts = (["SF Mono", "Menlo", "PingFang SC"] if is_macos()
             else ["Cascadia Mono", "Consolas", "Microsoft YaHei UI"])
    fonts = [name for name in fonts if name.lower() != exclude.strip().lower()]
    return ", ".join(f'"{name}"' for name in fonts) + ", monospace"


def default_ui_font() -> str:
    return "PingFang SC" if is_macos() else "Microsoft YaHei UI"


def default_mono_font() -> str:
    return "SF Mono" if is_macos() else "Consolas"


# ---------------------------------------------------------------- 样式表
def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                for v in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _on_badge(background: str) -> str:
    """自定义徽章色后自动选择高对比文字色，保证 AA。"""
    return "#ffffff" if _luminance(background) < 0.35 else "#10141b"


def _status_rules(p: dict) -> str:
    """按状态生成徽章 / 图例 / 汇总按钮的 QSS 规则。"""
    lines: list[str] = []
    for key, (fg, _row, badge, _inline) in p["status"].items():
        ink = _on_badge(badge)
        lines.append(
            f'*[dsStatus="{key}"] {{ border: 1px solid {badge}; color: {fg}; }}'
        )
        lines.append(
            f'QPushButton[dsStatus="{key}"]:checked {{ '
            f'background: {badge}; color: {ink}; border-color: {badge}; }}'
        )
        lines.append(
            f'*[dsRole="chip"][dsStatus="{key}"] {{ '
            f'background: {badge}; color: {ink}; border: none; }}'
        )
    return "\n".join(lines)


def _build_stylesheet(p: dict) -> str:
    radius = f"{RADIUS_CARD}px"
    radius_small = f"{RADIUS_CONTROL}px"
    return f"""
* {{ font-family: {ui_font_stack()}; }}
QWidget {{ color: {p['text']}; background: {p['bg']}; }}
QMainWindow, QDialog {{ background: {p['bg']}; }}
QToolBar {{
    background: {p['panel']}; border: none; border-bottom: 1px solid {p['border']};
    padding: {SPACE_XS}px {SPACE_SM}px; spacing: {SPACE_SM}px;
}}
QToolBar QLabel {{ background: transparent; color: {p['muted']}; padding: 0 {SPACE_XS}px; }}
QMenuBar {{ background: {p['panel']}; border-bottom: 1px solid {p['border']}; }}
QMenuBar::item {{ padding: 5px {SPACE_MD}px; background: transparent; border-radius: {radius_small}; }}
QMenuBar::item:selected {{ background: {p['accent_button']}; color: {p['accent_text']}; }}
QMenu {{ background: {p['panel_alt']}; border: 1px solid {p['border']}; padding: {SPACE_XS}px; }}
QMenu::item {{ padding: 6px 22px; border-radius: {radius_small}; }}
QMenu::item:selected {{ background: {p['accent_button']}; color: {p['accent_text']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: {SPACE_XS}px {SPACE_SM}px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit,
QTableWidget, QListWidget, QTreeWidget {{
    background: {p['panel_alt']}; border: 1px solid {p['border']}; border-radius: {radius_small};
    color: {p['text']}; padding: {SPACE_XS}px 6px;
    selection-background-color: {p['selection_bg']}; selection-color: {p['selection_text']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus,
QTextEdit:focus, QTableWidget:focus, QListWidget:focus, QTreeView:focus {{
    border: 2px solid {p['accent']};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {p['muted']}; background: {p['panel']}; }}
QPlainTextEdit[readOnly="true"], QTextEdit[readOnly="true"] {{ background: {p['panel']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {p['panel_alt']}; border: 1px solid {p['border']};
    selection-background-color: {p['selection_bg']}; color: {p['text']}; outline: none;
}}

QPushButton {{
    background: {p['panel_alt']}; border: 1px solid {p['border']}; border-radius: {radius_small};
    color: {p['text']}; padding: 5px {SPACE_MD}px; min-height: {CONTROL_SM - 8}px;
}}
QPushButton:hover {{ background: {p['panel']}; border-color: {p['border_hover']}; }}
QPushButton:pressed {{ background: {p['gridline']}; }}
QPushButton:focus {{ border: 2px solid {p['accent']}; }}
QPushButton:disabled {{ color: {p['muted']}; background: {p['panel']}; }}
QPushButton[accent="true"] {{
    background: {p['accent_button']}; border-color: {p['accent_button']};
    color: {p['accent_text']}; font-weight: 600;
}}
QPushButton[accent="true"]:hover {{ background: {p['accent_hover']}; border-color: {p['accent_hover']}; }}
QPushButton[accent="true"]:disabled {{ background: {p['panel']}; border-color: {p['border']}; color: {p['muted']}; }}
QPushButton[danger="true"] {{ color: {p['danger']}; }}
QPushButton:checked {{ background: {p['selection_bg']}; border-color: {p['accent']}; color: {p['selection_text']}; }}

QTreeView, QTableView {{
    background: {p['panel_alt']}; border: 1px solid {p['border']}; border-radius: {radius_small};
    gridline-color: {p['gridline']}; alternate-background-color: {p['alternate_bg']};
    selection-background-color: {p['selection_bg']}; selection-color: {p['selection_text']}; outline: none;
}}
QTreeView::item, QTableView::item {{ padding: 6px {SPACE_XS}px; border: none; }}
QTreeView::item:hover, QTableView::item:hover {{ background: {p['alternate_bg']}; }}
QTreeView::item:selected, QTableView::item:selected {{ background: {p['selection_bg']}; }}
QHeaderView::section {{
    background: {p['panel']}; color: {p['muted']}; border: none;
    border-right: 1px solid {p['border']}; border-bottom: 1px solid {p['border']};
    padding: {SPACE_SM}px {SPACE_SM}px; font-weight: 600;
}}
QHeaderView::section:hover {{ color: {p['text']}; }}
QTreeView::branch {{ background: {p['panel_alt']}; }}
QTableWidget {{ gridline-color: {p['border']}; }}
QTableWidget::item {{ padding: {SPACE_XS}px 6px; }}
QListWidget::item {{ padding: 6px {SPACE_SM}px; border-radius: {radius_small}; }}
QListWidget::item:selected {{ background: {p['selection_bg']}; color: {p['selection_text']}; }}

QSplitter::handle {{ background: {p['border']}; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}
QSplitter::handle:hover {{ background: {p['accent']}; }}

QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: {radius_small}; background: {p['panel']}; }}
QTabBar::tab {{
    background: {p['panel']}; color: {p['muted']}; border: 1px solid {p['border']};
    padding: 6px {SPACE_MD}px; min-width: 72px;
}}
QTabBar::tab:selected {{ background: {p['panel_alt']}; color: {p['text']}; border-bottom-color: {p['accent']}; }}
QTabBar::tab:hover {{ color: {p['text']}; }}

QStatusBar {{ background: {p['panel']}; border-top: 1px solid {p['border']}; color: {p['muted']}; }}
QStatusBar QLabel {{ background: transparent; }}
QProgressBar {{
    background: {p['panel_alt']}; border: 1px solid {p['border']}; border-radius: {radius_small};
    height: {SPACE_SM}px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 2px; }}
QScrollBar:vertical {{ background: {p['panel']}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {p['panel']}; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {p['scroll_handle']}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {p['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCheckBox, QRadioButton {{ background: transparent; spacing: {SPACE_SM}px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {p['border_hover']};
    border-radius: {radius_small}; background: {p['panel_alt']};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p['accent_button']}; border-color: {p['accent_button']};
}}
QGroupBox {{ border: 1px solid {p['border']}; border-radius: {radius}; margin-top: {SPACE_MD}px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {p['muted']}; }}
QMessageBox {{ background: {p['bg']}; }}
QToolTip {{ background: {p['panel_alt']}; color: {p['text']}; border: 1px solid {p['border']}; padding: {SPACE_XS}px; }}
QFrame[role="card"] {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: {radius}; }}
QFrame[role="divider"] {{ background: {p['border']}; }}

*[dsRole="hero"] {{ color: {p['brand']}; font-size: {FONT_HERO}px; font-weight: 600; background: transparent; }}
*[dsRole="subtitle"] {{ color: {p['muted']}; font-size: {FONT_SUBTITLE}px; background: transparent; }}
*[dsRole="muted"] {{ color: {p['muted']}; background: transparent; }}
*[dsRole="note"] {{ color: {p['muted']}; background: transparent; font-size: {FONT_NOTE}px; }}
*[dsRole="counter"] {{ color: {p['muted']}; background: transparent; padding: 0 6px; }}
*[dsRole="danger-text"] {{ color: {p['danger']}; background: transparent; }}
*[dsRole="info-text"] {{ color: {p['text']}; border: none; background: transparent; }}
*[dsRole="panel-card"] {{
    background: {p['panel']}; border: 1px solid {p['border']}; border-radius: {radius};
}}
*[dsRole="path-label"] {{
    color: {p['muted']}; background: {p['panel']}; border: 1px solid {p['border']};
    border-radius: {radius}; padding: 6px {SPACE_SM}px;
}}
*[dsRole="error-banner"] {{
    background: {p['status']['error'][1]}; color: {p['status']['error'][0]};
    border: 1px solid {p['status']['error'][2]}; border-radius: {radius}; padding: {SPACE_SM}px {SPACE_MD}px;
}}
*[dsRole="warning-note"] {{
    background: {p['status']['modified'][1]}; color: {p['status']['modified'][0]};
    border-radius: {radius}; padding: 6px {SPACE_SM}px;
}}
*[dsRole="img-surface"] {{
    background: {p['editor_bg']}; border: 1px solid {p['border']}; border-radius: {radius};
}}
*[dsRole="chip"] {{ border-radius: {radius_small}; padding: 2px {SPACE_SM}px; }}
*[dsEmphasis="true"] {{ border: 2px solid {p['accent']}; border-radius: {radius}; }}
*[dsRole="keycap"] {{
    background: {p['panel_alt']}; color: {p['text']}; border: 1px solid {p['border']};
    border-bottom-width: 2px; border-radius: {radius_small}; padding: 2px 8px; font-size: {FONT_NOTE}px;
}}
QPushButton[dsRole="resume-action"] {{
    background: transparent; color: {p['accent']};
    border: 1px solid {p['accent']}; border-radius: {radius_small}; font-weight: 600;
}}
QPushButton[dsRole="resume-action"]:hover {{ background: {p['panel_alt']}; }}
QPushButton[dsRole="resume-action"]:pressed {{ background: {p['gridline']}; }}
QPushButton[dsRole="resume-action"]:disabled {{ color: {p['muted']}; border-color: {p['border']}; }}
*[dsRole="section-title"] {{ color: {p['muted']}; font-size: {FONT_SECTION}px; font-weight: 600; }}
*[dsRole="section-line"] {{ background: {p['border']}; }}
*[dsRole="info-strip"] {{
    background: {p['panel_alt']}; color: {p['muted']}; border: 1px solid {p['border']};
    border-radius: {radius_small}; padding: 3px {SPACE_SM}px;
}}
*[dsRole="link-button"] {{ color: {p['muted']}; border: none; background: transparent; padding: 0 {SPACE_XS}px; }}
*[dsRole="primary-button"] {{
    background: {p['accent_button']}; color: {p['accent_text']};
    border: 1px solid {p['accent_button']}; border-radius: {radius_small}; padding: 5px {SPACE_LG}px;
}}
*[dsRole="danger-button"] {{
    background: {p['danger_button']}; color: {p['danger_text']};
    border: 1px solid {p['danger_button']}; border-radius: {radius_small}; padding: 5px {SPACE_LG}px;
}}
{_status_rules(p)}
"""


def stylesheet() -> str:
    """生成当前主题的全局样式表。"""
    return _build_stylesheet(palette())


def light_stylesheet() -> str:
    """浅色样式表（不改变当前主题，保留旧接口）。"""
    return _build_stylesheet(LIGHT)


# 测试与 launch() 仍以深色为默认，保持向后兼容
STYLESHEET = _build_stylesheet(DARK)


def apply_role(widget: QWidget, role: str, status: str | None = None) -> QWidget:
    """用语义角色取代内联色值；主题切换时由全局样式表统一重上色。"""
    widget.setProperty("dsRole", role)
    if status is not None:
        widget.setProperty("dsStatus", status)
    try:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
    except (AttributeError, RuntimeError):
        pass
    return widget


def refresh(app: QApplication | None = None) -> None:
    """主题切换后重新应用样式并刷新控件绘制。

    只遍历「当前可见的顶层窗口及其子树」：`QApplication.allWidgets()` 会保留
    历史上创建过的全部控件（含已关闭的对话框），全量遍历会随窗口数量累积成
    O(N²)；隐藏控件在下次 show 时会被 Qt 重新 polish，无需在此处理。
    """
    target = app or QApplication.instance()
    if target is None:
        return
    target.setStyleSheet(stylesheet())
    try:
        roots = [w for w in target.allWidgets() if w.isVisible() and w.isWindow()]
    except (AttributeError, RuntimeError):
        return
    for root in roots:
        for widget in [root, *root.findChildren(QWidget)]:
            try:
                style = widget.style()
                style.unpolish(widget)
                style.polish(widget)
                widget.update()
            except (AttributeError, RuntimeError):
                continue
