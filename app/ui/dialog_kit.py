"""对话框骨架统一与长表单分区。

只规范呈现（边距、间距、控件最小高度、无障碍名称、表单标签关联），
不改动按钮位置顺序、确认流程与任何业务逻辑。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import CONTROL_SM, SPACE_LG, SPACE_MD, SPACE_SM, apply_role


def normalize(dialog: QDialog) -> None:
    """统一对话框的外观骨架；可重复调用，效果幂等。"""
    if not isinstance(dialog, QDialog):
        return
    root = dialog.layout()
    if root is not None:
        root.setContentsMargins(SPACE_LG, 14, SPACE_LG, SPACE_MD)
        root.setSpacing(10)
    for child in dialog.findChildren(QLayout):
        if child is not root and isinstance(child, (QVBoxLayout, QHBoxLayout, QFormLayout)):
            child.setSpacing(SPACE_SM)
    for button in dialog.findChildren(QPushButton):
        if button.minimumHeight() < CONTROL_SM:
            button.setMinimumHeight(CONTROL_SM)
        if not button.accessibleName() and button.text():
            button.setAccessibleName(button.text())
    # QFormLayout 的标签默认不是 buddy，补上无障碍名称让读屏能报出字段名
    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if label_item is None or field_item is None:
                continue
            label, field = label_item.widget(), field_item.widget()
            if (isinstance(label, QLabel) and isinstance(field, QWidget)
                    and not field.accessibleName()):
                field.setAccessibleName(label.text())


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    apply_role(label, "section-title")
    label.setAccessibleName(text)
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    apply_role(line, "section-line")
    return line


def add_section(layout: QLayout, title: str, index: int | None = None) -> None:
    """在纵向布局中插入「分隔线 + 分组标题」；只作用于 QVBoxLayout。"""
    if not isinstance(layout, QVBoxLayout):
        return
    if index is None:
        index = layout.count()
    layout.insertWidget(index, divider())
    layout.insertWidget(index + 1, section_title(title))


def section_bar(title: str) -> QWidget:
    """供非 QDialog 的扩展视图使用的独立分区条。"""
    container = QWidget()
    row = QVBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_SM // 2)
    row.addWidget(divider())
    row.addWidget(section_title(title))
    return container
