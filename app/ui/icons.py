"""运行时自绘线性图标：单色、1.5px 描边、随主题反色，不引入第三方资源。

所有图形定义在 24×24 的虚拟网格上，按需渲染为 16 / 20 / 24 逻辑像素，
并以 2 倍设备像素比绘制，保证 4K 与 125%–200% 缩放下依然清晰。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPainter, QPen, QPixmap

from .theme import qcolor

Grid = tuple[float, float]
Shape = tuple

SHAPES: dict[str, list[Shape]] = {
    "open": [
        ("poly", [(3, 6), (9, 6), (11, 8.5), (21, 8.5), (21, 19), (3, 19), (3, 6)]),
        ("line", (3, 19), (21, 19)),
    ],
    "file": [
        ("poly", [(6, 3), (15, 3), (18, 6), (18, 21), (6, 21), (6, 3)]),
        ("line", (15, 3), (15, 6)),
        ("line", (15, 6), (18, 6)),
        ("line", (9, 11), (15, 11)),
        ("line", (9, 15), (15, 15)),
    ],
    "compare": [
        ("rect", 2.5, 4, 8, 16),
        ("rect", 13.5, 4, 8, 16),
        ("line", (5, 9), (8, 9)),
        ("line", (5, 13), (8, 13)),
        ("line", (16, 9), (19, 9)),
        ("line", (16, 13), (19, 13)),
    ],
    "refresh": [
        ("arc", 4, 4, 16, 16, 45, 270),
        ("poly", [(17, 2), (21, 5), (16.5, 7)]),
    ],
    "merge_left": [
        ("line", (20, 12), (5, 12)),
        ("poly", [(10, 7), (4, 12), (10, 17)]),
        ("line", (20, 5), (20, 19)),
    ],
    "merge_right": [
        ("line", (4, 12), (19, 12)),
        ("poly", [(14, 7), (20, 12), (14, 17)]),
        ("line", (4, 5), (4, 19)),
    ],
    "sync": [
        ("arc", 3.5, 5, 17, 10, 15, 150),
        ("arc", 3.5, 9, 17, 10, 195, 150),
        ("poly", [(18, 5), (21, 8), (17, 9)]),
        ("poly", [(6, 19), (3, 16), (7, 15)]),
    ],
    "filter": [
        ("poly", [(3, 5), (21, 5), (14, 13), (14, 19), (10, 19), (10, 13), (3, 5)]),
    ],
    "find": [
        ("ellipse", 4, 4, 12, 12),
        ("line", (14, 14), (21, 21)),
    ],
    "bookmark": [
        ("poly", [(7, 3), (17, 3), (17, 21), (12, 17), (7, 21), (7, 3)]),
    ],
    "nav": [
        ("line", (8, 6), (20, 6)),
        ("line", (8, 12), (20, 12)),
        ("line", (8, 18), (20, 18)),
        ("rect", 3, 4.5, 3, 3),
        ("rect", 3, 10.5, 3, 3),
        ("rect", 3, 16.5, 3, 3),
    ],
    "save": [
        ("rect", 4, 4, 16, 16),
        ("rect", 8, 4, 8, 6),
        ("rect", 8, 14, 8, 6),
    ],
    "settings": [
        ("ellipse", 8, 8, 8, 8),
        ("ellipse", 5, 5, 14, 14),
    ]
    + [
        ("line",
         (12 + 8 * math.cos(i * math.pi / 4), 12 + 8 * math.sin(i * math.pi / 4)),
         (12 + 11 * math.cos(i * math.pi / 4), 12 + 11 * math.sin(i * math.pi / 4)))
        for i in range(8)
    ],
    "help": [
        ("ellipse", 4, 4, 16, 16),
        ("text", 8.5, 17, "?", 11),
    ],
    "table": [
        ("rect", 3, 5, 18, 14),
        ("line", (3, 10), (21, 10)),
        ("line", (3, 14.5), (21, 14.5)),
        ("line", (9, 5), (9, 19)),
        ("line", (15, 5), (15, 19)),
    ],
    "image": [
        ("rect", 3, 5, 18, 14),
        ("ellipse", 14.5, 8, 3, 3),
        ("poly", [(4, 17), (9, 12), (13, 16), (16, 13), (20, 17)]),
    ],
    "report": [
        ("poly", [(6, 3), (15, 3), (18, 6), (18, 21), (6, 21), (6, 3)]),
        ("line", (9, 10), (15, 10)),
        ("line", (9, 14), (15, 14)),
        ("line", (9, 18), (13, 18)),
    ],
    "session": [
        ("ellipse", 4, 4, 16, 16),
        ("poly", [(12, 7), (12, 12), (16, 14.5)]),
    ],
    "script": [
        ("rect", 3, 4, 18, 16),
        ("poly", [(10, 9), (7, 12), (10, 15)]),
        ("poly", [(14, 9), (17, 12), (14, 15)]),
    ],
    "git": [
        ("ellipse", 4, 3, 5, 5),
        ("ellipse", 4, 16, 5, 5),
        ("ellipse", 15, 9.5, 5, 5),
        ("line", (6.5, 8), (6.5, 16)),
        ("line", (6.5, 12), (15, 12)),
    ],
    "database": [
        ("ellipse", 4, 4, 16, 5),
        ("line", (4, 6.5), (4, 17.5)),
        ("line", (20, 6.5), (20, 17.5)),
        ("arc", 4, 12, 16, 5, 180, 180),
        ("arc", 4, 15, 16, 5, 180, 180),
    ],
    "back": [
        ("line", (20, 12), (6, 12)),
        ("poly", [(11, 7), (5, 12), (11, 17)]),
    ],
    "prev": [("poly", [(15, 6), (9, 12), (15, 18)])],
    "next": [("poly", [(9, 6), (15, 12), (9, 18)])],
    "clear": [
        ("rect", 5, 7, 14, 13),
        ("line", (3, 7), (21, 7)),
        ("line", (10, 4), (14, 4)),
        ("line", (10, 11), (10, 17)),
        ("line", (14, 11), (14, 17)),
    ],
    "warning": [
        ("poly", [(12, 3), (22, 20), (2, 20), (12, 3)]),
        ("line", (12, 9), (12, 15)),
        ("ellipse", 11, 17, 2, 2),
    ],
    "diff": [
        ("line", (3, 8), (21, 8)),
        ("poly", [(17, 4), (21, 8), (17, 12)]),
        ("line", (21, 16), (3, 16)),
        ("poly", [(7, 12), (3, 16), (7, 20)]),
    ],
    "theme": [
        ("ellipse", 4, 4, 16, 16),
        ("arc", 12, 4, 8, 16, 90, 180),
    ],
}

# 菜单动作按文案关键字匹配图标；中英文各给一份，切换语言后仍可命中。
ACTION_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("打开对比", "打开会话", "打开数据源", "Open", "open"), "open"),
    (("保存会话", "保存修改", "Save", "save"), "save"),
    (("最近的会话", "会话…", "Recent", "recent"), "session"),
    (("重新比较", "刷新", "Refresh", "refresh"), "refresh"),
    (("上一处差异", "上一个", "Previous", "prev"), "prev"),
    (("下一处差异", "下一个", "Next", "next"), "next"),
    (("合并当前差异 左→右", "Left"), "merge_right"),
    (("合并当前差异 右→左", "Right"), "merge_left"),
    (("选择性合并", "三向合并", "Merge", "merge"), "merge_right"),
    (("查找", "Find", "find"), "find"),
    (("查找与替换", "Replace", "replace"), "script"),
    (("书签", "Bookmark", "bookmark"), "bookmark"),
    (("比较选项", "设置", "Options", "Settings", "settings"), "settings"),
    (("展开全部", "折叠全部", "Expand", "Collapse", "nav"), "nav"),
    (("返回目录", "Back", "back"), "back"),
    (("关于", "帮助", "About", "Help", "help"), "help"),
    (("快照", "Snapshot", "snapshot"), "save"),
    (("比较规则", "比较模板", "对齐", "Rules", "Template", "Alignment"), "script"),
    (("同步模式", "Sync", "sync"), "sync"),
    (("表格比较", "Table", "table"), "table"),
    (("十六进制", "Hex", "hex"), "table"),
    (("图片比较", "Image", "image"), "image"),
    (("注册表", "Registry", "registry"), "table"),
    (("Git", "git"), "git"),
    (("SQLite", "sqlite"), "database"),
    (("生成报告", "导出差异", "Report", "report"), "report"),
    (("脚本处理器", "Script", "script"), "script"),
    (("标签页工作区", "Workspace", "workspace"), "table"),
    (("独立文本编辑器", "Editor", "editor"), "file"),
    (("剪贴板", "Clipboard", "clipboard"), "diff"),
    (("切换浅色", "主题", "Theme", "theme"), "theme"),
    (("文件格式", "Format", "format"), "file"),
)

_DEFAULT = "file"


def suggest(text: str) -> str:
    """按动作文案推断图标名。"""
    for hints, name in ACTION_HINTS:
        if any(hint in text for hint in hints):
            return name
    return _DEFAULT


def icon(name: str, size: int = 20, color_key: str = "text") -> QIcon:
    """生成单色线性图标；颜色在调用时取当前主题，切换主题后由 refresh_icons 重建。"""
    shapes = SHAPES.get(name, SHAPES[_DEFAULT])
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(scale)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 24, size / 24)
    pen = QPen(qcolor(color_key))
    pen.setWidthF(1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for shape in shapes:
        kind = shape[0]
        if kind == "line":
            painter.drawLine(shape[1][0], shape[1][1], shape[2][0], shape[2][1])
        elif kind == "poly":
            points = shape[1]
            for index in range(len(points) - 1):
                painter.drawLine(points[index][0], points[index][1],
                                 points[index + 1][0], points[index + 1][1])
        elif kind == "rect":
            painter.drawRect(QRectF(shape[1], shape[2], shape[3], shape[4]))
        elif kind == "ellipse":
            painter.drawEllipse(QRectF(shape[1], shape[2], shape[3], shape[4]))
        elif kind == "arc":
            painter.drawArc(QRectF(shape[1], shape[2], shape[3], shape[4]),
                            shape[5] * 16, shape[6] * 16)
        elif kind == "text":
            font = QFont()
            font.setPointSizeF(shape[4])
            painter.setFont(font)
            painter.drawText(QRectF(shape[1], shape[2] - shape[4], 12, shape[4] * 1.4),
                             Qt.AlignmentFlag.AlignCenter, shape[3])
    painter.end()
    return QIcon(pixmap)


_BINDINGS: list[tuple[object, str, int, str]] = []


def bind(widget, name: str, size: int = 18, color_key: str = "text") -> None:
    """给控件绑定图标并登记，主题切换时可统一重建。"""
    widget.setIcon(icon(name, size, color_key))
    widget.setIconSize(QSize(size, size))
    _BINDINGS.append((widget, name, size, color_key))


def bind_action(action, name: str | None = None, size: int = 18) -> None:
    text = action.text()
    action.setIcon(icon(name or suggest(text), size, "muted"))
    _BINDINGS.append((action, name or suggest(text), size, "muted"))


def decorate_actions(window) -> None:
    """为主窗口所有菜单动作补齐图标。"""
    from PySide6.QtGui import QAction

    for action in window.findChildren(QAction):
        if action.text() and not action.isSeparator():
            bind_action(action)


def refresh_icons() -> None:
    """主题切换后按当前色板重建全部已绑定图标。"""
    alive: list[tuple[object, str, int, str]] = []
    for target, name, size, color_key in _BINDINGS:
        try:
            if hasattr(target, "setIcon"):
                target.setIcon(icon(name, size, color_key))
                alive.append((target, name, size, color_key))
        except RuntimeError:
            continue
    _BINDINGS[:] = alive
