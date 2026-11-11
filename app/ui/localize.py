"""界面文案的运行时本地化：窗口显示时统一翻译控件文本。

无需在每个控件构造处逐个包裹翻译调用；语言切换后新建的对话框也会自动生效。
文件路径、用户数据与动态数值不会被改写。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QTabWidget,
    QTableWidget,
    QTreeWidget,
    QWidget,
)

from ..core.i18n import current_language, tr_ui
from . import dialog_kit, effects

# 只有真正像路径 / 文件名 / URL 的文本才跳过翻译；
# 「标签/版本比较」「F7 / F8」「差异 0 / 0」这类含斜杠的界面文案仍需翻译。
_PATH_MARK = re.compile(r"(?:^[A-Za-z]:[\\/]|:\\/\\/|://|[\\/]{2,}|\\\\[\w.-]+\\|\\)")
_EXT_MARK = re.compile(r"\.[A-Za-z0-9]{1,6}\s*$")


def _looks_like_data(text: str) -> bool:
    return bool(_PATH_MARK.search(text) or _EXT_MARK.search(text))


def _safe(text: str) -> str:
    if not text or _looks_like_data(text):
        return text
    # 图例 chip 等控件会带前后空格；查词时去掉，回填时保留原有留白
    stripped = text.strip()
    translated = tr_ui(stripped)
    if translated == stripped:
        return text
    return text.replace(stripped, translated, 1)


def translate_tree(root: QWidget) -> None:
    """翻译一个窗口内所有静态控件文案（含菜单动作与表头）。"""
    if current_language() == "zh":
        return
    root.setWindowTitle(_safe(root.windowTitle()))
    for action in root.findChildren(QAction):
        if action.text() and not action.isSeparator():
            action.setText(_safe(action.text()))
        if action.toolTip():
            action.setToolTip(_safe(action.toolTip()))
    for menu in root.findChildren(QMenu):
        menu.setTitle(_safe(menu.title()))
    for widget in root.findChildren(QWidget):
        if isinstance(widget, (QLabel, QAbstractButton)):
            if widget.text():
                widget.setText(_safe(widget.text()))
            if widget.toolTip():
                widget.setToolTip(_safe(widget.toolTip()))
        elif isinstance(widget, QGroupBox):
            widget.setTitle(_safe(widget.title()))
        elif isinstance(widget, QLineEdit):
            widget.setPlaceholderText(_safe(widget.placeholderText()))
        elif isinstance(widget, QComboBox):
            for index in range(widget.count()):
                widget.setItemText(index, _safe(widget.itemText(index)))
        elif isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                widget.setTabText(index, _safe(widget.tabText(index)))
        elif isinstance(widget, (QTableWidget, QTreeWidget)):
            header = (widget.horizontalHeader() if isinstance(widget, QTableWidget)
                      else widget.header())
            if isinstance(header, QHeaderView):
                model = header.model()
                if model is not None:
                    for index in range(header.count()):
                        value = model.headerData(index, header.orientation())
                        if isinstance(value, str):
                            model.setHeaderData(index, header.orientation(), _safe(value))
        if widget.windowTitle():
            widget.setWindowTitle(_safe(widget.windowTitle()))
        if widget.statusTip():
            widget.setStatusTip(_safe(widget.statusTip()))


class Localizer(QObject):
    """应用级事件过滤器：顶层窗口显示时自动翻译一次。"""

    _busy = False

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 回调签名
        if (Localizer._busy or event.type() != QEvent.Type.Show
                or not isinstance(obj, QWidget) or not obj.isWindow()):
            return False
        Localizer._busy = True
        try:
            # 译文无中文，重复调用是幂等的
            translate_tree(obj)
            if isinstance(obj, QDialog):
                dialog_kit.normalize(obj)
                effects.fade_in(obj)
        finally:
            Localizer._busy = False
        return False


def install(app) -> Localizer:
    localizer = Localizer(app)
    app.installEventFilter(localizer)
    # 保持引用，避免 Python 包装对象被回收
    app.setProperty("diffscope_localizer", localizer)
    return localizer
