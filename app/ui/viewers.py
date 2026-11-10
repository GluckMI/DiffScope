"""专用查看器：表格、十六进制、图片、标签/版本信息、注册表、报告预览。"""

from __future__ import annotations

import io
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core import binary as binary_mod
from ..core import formats as formats_mod
from ..core import media as media_mod
from ..core.errors import DiffScopeError
from ..core.models import STATUS_META, DiffStatus
from .theme import MUTED, STATUS_COLORS

MONO = QFont("Consolas", 10)
MONO.setStyleHint(QFont.StyleHint.Monospace)


def _color(value: str) -> QColor:
    return QColor(value)


class ViewerWidget(QWidget):
    """所有查看器的公共接口。"""

    status_message = Signal(str)
    viewer_name = "通用"

    def set_data(self, left: bytes, right: bytes, left_name: str = "", right_name: str = "") -> None:
        raise NotImplementedError

    def _error(self, exc: Exception) -> None:
        message = getattr(exc, "user_message", lambda: str(exc))()
        self.status_message.emit(message)
        from .dialogs import show_error

        show_error(self, exc, f"{self.viewer_name} 加载")

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {MUTED}; padding: 3px;")
        label.setWordWrap(True)
        return label

    def _wrap(self, title: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label(title))
        layout.addWidget(widget, 1)
        return container


# ---------------------------------------------------------------- 表格
class TableViewer(ViewerWidget):
    viewer_name = "表格比较"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.left_data: formats_mod.TableData | None = None
        self.right_data: formats_mod.TableData | None = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.sheet = QComboBox()
        self.sheet.currentIndexChanged.connect(self._refresh)
        self.keys = QLineEdit()
        self.keys.setPlaceholderText("关键列（1,2… 逗号分隔）")
        self.ignore = QLineEdit()
        self.ignore.setPlaceholderText("忽略列（1,2…）")
        self.order = QLineEdit()
        self.order.setPlaceholderText("列顺序（1,2,3…）")
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0, 1000000)
        self.tolerance.setDecimals(3)
        self.tolerance.setPrefix("容差 ")
        self.sort_box = QCheckBox("比较前排序")
        for widget in (self.sheet, self.keys, self.ignore, self.order, self.tolerance):
            bar.addWidget(widget)
        bar.addWidget(self.sort_box)
        apply_btn = QPushButton("应用")
        apply_btn.clicked.connect(self._refresh)
        bar.addWidget(apply_btn)
        layout.addLayout(bar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_table = self._make_table()
        self.right_table = self._make_table()
        splitter.addWidget(self._wrap("左侧", self.left_table))
        splitter.addWidget(self._wrap("右侧", self.right_table))
        layout.addWidget(splitter, 1)

    def _make_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        return table

    def _wrap(self, title: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label(title))
        layout.addWidget(widget, 1)
        return container

    def set_data(self, left: bytes, right: bytes, left_name: str = "", right_name: str = "") -> None:
        try:
            self.left_data = formats_mod.load_table(left, left_name)
            self.right_data = formats_mod.load_table(right, right_name)
        except DiffScopeError as exc:
            self._error(exc)
            return
        names = sorted(set(self.left_data.sheets) | set(self.right_data.sheets))
        self.sheet.blockSignals(True)
        self.sheet.clear()
        self.sheet.addItems(names or ["(默认)"])
        self.sheet.blockSignals(False)
        self._refresh()

    @staticmethod
    def _indices(text: str) -> list[int]:
        out = []
        for part in text.replace("，", ",").split(","):
            part = part.strip()
            if part.isdigit() and int(part) > 0:
                out.append(int(part) - 1)
        return out

    def _selected(self, data: formats_mod.TableData) -> formats_mod.TableData:
        name = self.sheet.currentText()
        return data.sheets.get(name, data)

    def _refresh(self) -> None:
        if not self.left_data or not self.right_data:
            return
        options = formats_mod.TableOptions(
            key_columns=self._indices(self.keys.text()),
            ignore_columns=self._indices(self.ignore.text()),
            column_order=self._indices(self.order.text()),
            tolerance=self.tolerance.value(),
            sort_before_compare=self.sort_box.isChecked(),
        )
        left, right = self._selected(self.left_data), self._selected(self.right_data)
        try:
            diff = formats_mod.compare_tables(left, right, options)
        except DiffScopeError as exc:
            self._error(exc)
            return
        headers = diff.headers or [f"列 {i + 1}" for i in range(max(left.width, right.width))]
        for table in (self.left_table, self.right_table):
            table.setRowCount(len(diff.rows))
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
        for index, row in enumerate(diff.rows):
            for col, header in enumerate(headers):
                for table, values in ((self.left_table, row.left), (self.right_table, row.right)):
                    item = QTableWidgetItem(values[col] if col < len(values) else "")
                    if col in row.changed:
                        item.setBackground(_color(STATUS_COLORS[DiffStatus.MODIFIED][1]))
                        item.setForeground(_color(STATUS_COLORS[DiffStatus.MODIFIED][0]))
                    elif row.op in ("delete", "insert"):
                        status = DiffStatus.LEFT_ONLY if row.op == "delete" else DiffStatus.RIGHT_ONLY
                        item.setBackground(_color(STATUS_COLORS[status][1]))
                    table.setItem(index, col, item)
        self.status_message.emit(
            f"表格比较：{len(diff.rows)} 行，{len(diff.diff_indexes)} 处差异，工作表 {self.sheet.count()} 个"
        )


# ---------------------------------------------------------------- 十六进制
class HexViewer(ViewerWidget):
    viewer_name = "十六进制比较"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.left_bytes = b""
        self.right_bytes = b""
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.width_box = QComboBox()
        self.width_box.addItems(["8", "16", "32", "64"])
        self.width_box.setCurrentText("16")
        self.align_box = QComboBox()
        self.align_box.addItems(["quick", "exact"])
        self.only_diff = QCheckBox("仅显示差异")
        self.find = QLineEdit()
        self.find.setPlaceholderText("查找十六进制，如 48 65 6C 6C 6F")
        self.find.returnPressed.connect(self._find)
        self.replace = QLineEdit()
        self.replace.setPlaceholderText("替换为十六进制")
        apply_btn = QPushButton("替换全部（左侧）")
        apply_btn.clicked.connect(self._replace)
        for widget in (self.width_box, self.align_box, self.only_diff, self.find, self.replace, apply_btn):
            bar.addWidget(widget)
        self.width_box.currentIndexChanged.connect(self._refresh)
        self.align_box.currentIndexChanged.connect(self._refresh)
        self.only_diff.stateChanged.connect(self._refresh)
        layout.addLayout(bar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_text = self._editor()
        self.right_text = self._editor()
        splitter.addWidget(self._wrap("左侧", self.left_text))
        splitter.addWidget(self._wrap("右侧", self.right_text))
        layout.addWidget(splitter, 1)
        self.edit_offset = QLineEdit()
        self.edit_offset.setPlaceholderText("内联编辑：偏移(十六进制) + 新字节，如 00000010:41 42")
        self.edit_offset.returnPressed.connect(self._inline_edit)
        layout.addWidget(self.edit_offset)

    def _editor(self) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setFont(MONO)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return editor

    def set_data(self, left: bytes, right: bytes, left_name: str = "", right_name: str = "") -> None:
        self.left_bytes, self.right_bytes = left, right
        self._refresh()

    def _refresh(self) -> None:
        try:
            result = binary_mod.compare_hex(
                self.left_bytes, self.right_bytes,
                int(self.width_box.currentText()), self.align_box.currentText(),
            )
        except DiffScopeError as exc:
            self._error(exc)
            return
        rows = [row for row in result.rows if row.is_diff] if self.only_diff.isChecked() else result.rows
        left_lines, right_lines = [], []
        for row in rows:
            a, b, _ = binary_mod.format_hex_row(row)
            left_lines.append(a)
            right_lines.append(b)
        self.left_text.setPlainText("\n".join(left_lines))
        self.right_text.setPlainText("\n".join(right_lines))
        self._highlight(result, rows)
        self.status_message.emit(
            f"十六进制：{len(result.diff_offsets)} 个字节不同 / {result.left_size} : {result.right_size} 字节"
        )

    def _highlight(self, result, rows) -> None:
        for editor in (self.left_text, self.right_text):
            selections = []
            for index, row in enumerate(rows):
                if not row.is_diff:
                    continue
                block = editor.document().findBlockByNumber(index)
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.MoveOperation.EndOfLine, QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setBackground(_color(STATUS_COLORS[DiffStatus.MODIFIED][1]))
                from PySide6.QtWidgets import QTextEdit

                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format = fmt
                selections.append(selection)
            editor.setExtraSelections(selections)

    def _find(self) -> None:
        try:
            pattern = binary_mod.parse_hex(self.find.text())
        except DiffScopeError as exc:
            self._error(exc)
            return
        offsets = binary_mod.find_hex(self.left_bytes, pattern)
        self.status_message.emit(f"找到 {len(offsets)} 处：{', '.join(f'{o:X}' for o in offsets[:10])}")

    def _replace(self) -> None:
        try:
            pattern = binary_mod.parse_hex(self.find.text())
            replacement = binary_mod.parse_hex(self.replace.text())
        except DiffScopeError as exc:
            self._error(exc)
            return
        self.left_bytes, count = binary_mod.replace_hex(self.left_bytes, pattern, replacement)
        self._refresh()
        self.status_message.emit(f"已替换 {count} 处（仅内存，保存后写入文件）")

    def _inline_edit(self) -> None:
        text = self.edit_offset.text().strip()
        if ":" not in text:
            self.status_message.emit("格式：偏移:字节，如 00000010:41 42")
            return
        offset_text, _, hex_text = text.partition(":")
        try:
            offset = int(offset_text.strip(), 16)
            self.left_bytes = binary_mod.patch_hex(self.left_bytes, offset, binary_mod.parse_hex(hex_text))
        except (ValueError, DiffScopeError) as exc:
            self._error(exc)
            return
        self._refresh()
        self.status_message.emit(f"已在偏移 {offset:X} 写入 {len(hex_text.split())} 字节")


# ---------------------------------------------------------------- 图片
class ImageViewer(ViewerWidget):
    viewer_name = "图片比较"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.left_bytes = b""
        self.right_bytes = b""
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.tolerance = QComboBox()
        self.tolerance.addItems(["0", "2", "5", "10", "20", "40"])
        self.scale = QComboBox()
        self.scale.addItems(["0.25", "0.5", "1.0", "2.0"])
        self.scale.setCurrentText("1.0")
        self.transform = QComboBox()
        self.transform.addItems(["none", "flip_h", "flip_v", "rotate_90", "rotate_180"])
        self.mode = QComboBox()
        self.mode.addItems(["difference", "blend"])
        for label, widget in (("RGB 容差", self.tolerance), ("缩放", self.scale),
                              ("变换（右侧）", self.transform), ("模式", self.mode)):
            bar.addWidget(QLabel(label))
            bar.addWidget(widget)
        refresh = QPushButton("重新比较")
        refresh.clicked.connect(self._refresh)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        row = QHBoxLayout()
        self.views: list[QLabel] = []
        for title in ("左侧", "右侧", "差异/叠加"):
            container = QWidget()
            box = QVBoxLayout(container)
            box.addWidget(self._label(title))
            label = QLabel("(无图片)")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumSize(200, 160)
            label.setStyleSheet("background: #1d2026; border: 1px solid #333844;")
            box.addWidget(label, 1)
            row.addWidget(container, 1)
            self.views.append(label)
        layout.addLayout(row, 1)

    def set_data(self, left: bytes, right: bytes, left_name: str = "", right_name: str = "") -> None:
        self.left_bytes, self.right_bytes = left, right
        self._refresh()

    def _refresh(self) -> None:
        try:
            result = media_mod.compare_images(
                self.left_bytes, self.right_bytes,
                tolerance=int(self.tolerance.currentText()),
                scale=float(self.scale.currentText()),
                transform_right=self.transform.currentText(),
                mode=self.mode.currentText(),
            )
            for label, data in zip(self.views[:2], (self.left_bytes, self.right_bytes)):
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                label.setPixmap(pixmap.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                label.setScaledContents(False)
            diff = QPixmap()
            diff.loadFromData(result.diff_image)
            self.views[2].setPixmap(diff.scaled(self.views[2].size(), Qt.AspectRatioMode.KeepAspectRatio))
        except DiffScopeError as exc:
            self._error(exc)
            return
        similarity = (f"，感知相似度 {result.similarity * 100:.1f}%" if result.similarity is not None else "")
        self.status_message.emit(
            f"图片比较：{result.width}×{result.height}，差异 {result.diff_pixels} 像素 "
            f"({result.ratio * 100:.2f}%)，容差 {result.tolerance}{similarity}"
        )


# ---------------------------------------------------------------- 标签 / 版本 / 注册表
class KeyValueViewer(ViewerWidget):
    """标签、exe 版本信息与注册表差异共用三列表格。"""

    viewer_name = "键值比较"
    kind = "tags"

    def __init__(self, parent=None, kind: str = "tags"):
        super().__init__(parent)
        self.kind = kind
        self.viewer_name = {"tags": "标签/版本比较", "registry": "注册表比较"}.get(kind, "键值比较")
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["字段 / 路径", "左侧", "右侧", "状态"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        layout.addWidget(self.table, 1)

    def set_data(self, left: bytes, right: bytes, left_name: str = "", right_name: str = "") -> None:
        try:
            if self.kind == "registry":
                left_map = binary_mod.parse_reg_file(left) if left_name.lower().endswith(".reg") else binary_mod.registry_snapshot(left_name)
                right_map = binary_mod.parse_reg_file(right) if right_name.lower().endswith(".reg") else binary_mod.registry_snapshot(right_name)
            elif left_name.lower().endswith((".exe", ".dll", ".sys", ".ocx")):
                left_map, right_map = media_mod.exe_version(left, left_name), media_mod.exe_version(right, right_name)
            else:
                left_map, right_map = media_mod.read_tags(left, left_name), media_mod.read_tags(right, right_name)
        except DiffScopeError as exc:
            self._error(exc)
            return
        keys = sorted(set(left_map) | set(right_map))
        self.table.setRowCount(len(keys))
        differences = 0
        for index, key in enumerate(keys):
            a, b = left_map.get(key, ""), right_map.get(key, "")
            status = "相同" if a == b else ("仅左侧" if not b else "仅右侧" if not a else "已修改")
            if a != b:
                differences += 1
            for col, value in enumerate((key, a, b, status)):
                item = QTableWidgetItem(str(value))
                if a != b and col in (1, 2, 3):
                    item.setBackground(_color(STATUS_COLORS[DiffStatus.MODIFIED][1]))
                self.table.setItem(index, col, item)
        self.table.resizeColumnsToContents()
        self.status_message.emit(f"{self.viewer_name}：{len(keys)} 项，{differences} 处差异")


class ReportViewer(ViewerWidget):
    viewer_name = "报告预览"

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        layout.addWidget(self.browser, 1)

    def set_data(self, left: bytes, right: bytes, left_name: str = "", right_name: str = "") -> None:
        self.browser.setHtml((left or right).decode("utf-8", "replace"))
        self.status_message.emit("报告预览已加载")


# ---------------------------------------------------------------- 路由
VIEWER_BY_SUFFIX = {
    **{s: "table" for s in (".csv", ".tsv", ".xlsx", ".xlsm", ".html", ".htm")},
    **{s: "image" for s in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")},
    **{s: "tags" for s in (".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav",
                           ".exe", ".dll", ".sys", ".ocx")},
    ".reg": "registry",
}


def choose_viewer(name: str, data: bytes, explicit: str = "auto") -> str:
    """按扩展名与内容嗅探选择查看器。"""
    if explicit != "auto":
        return explicit
    suffix = Path(name).suffix.lower()
    if suffix in VIEWER_BY_SUFFIX:
        return VIEWER_BY_SUFFIX[suffix]
    if suffix in (".pdf", ".docx", ".dotx", ".rtf", ".doc", ".patch", ".diff"):
        return "text"
    if b"\x00" in data[:8192]:
        return "hex"
    return "text"


def create_viewer(kind: str, parent=None) -> ViewerWidget:
    return {
        "table": lambda: TableViewer(parent),
        "hex": lambda: HexViewer(parent),
        "image": lambda: ImageViewer(parent),
        "tags": lambda: KeyValueViewer(parent, "tags"),
        "registry": lambda: KeyValueViewer(parent, "registry"),
        "report": lambda: ReportViewer(parent),
    }.get(kind, lambda: HexViewer(parent))()
