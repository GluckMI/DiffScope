"""通用界面组件：差异文本编辑器、行号栏、差异高亮器、状态汇总条。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QWidget,
)

from ..core.models import STATUS_META, DiffLine, DiffStatus, TextDiffResult
from .theme import (
    CURRENT_BLOCK,
    FONT_BASE,
    INLINE_COLORS,
    LINE_COLORS,
    STATUS_COLORS,
    SYNTAX_FG,
    apply_role,
    c,
    default_mono_font,
    mono_font_stack,
    qcolor,
    status_key,
)

MONO_FONT = QFont(default_mono_font(), FONT_BASE)
MONO_FONT.setStyleHint(QFont.StyleHint.Monospace)

# 差异行的形状/文字冗余标识：不依赖颜色即可区分状态
ROW_MARK = {"replace": "≠", "delete": "<", "insert": ">"}


def set_mono_font(family: str, size_pt: int) -> None:
    """缩放时同步更新行号栏与占位行字体，避免只放大正文导致数字裁切。"""
    MONO_FONT.setFamily(family or default_mono_font())
    MONO_FONT.setPointSize(max(6, int(size_pt)))


def line_number_width(size_pt: int) -> int:
    """行号栏宽度按字号计算：预留状态符号 + 6 位行号 + 书签/手动标记。"""
    digits = max(6, 4 + (int(size_pt) - 10) // 6)
    return max(64, 24 + int(size_pt) * digits // 2 + 20)

KEYWORDS: dict[str, set[str]] = {
    "python": {"def", "class", "if", "else", "elif", "for", "while", "return", "import", "from",
               "as", "try", "except", "finally", "with", "lambda", "None", "True", "False",
               "pass", "yield", "async", "await", "in", "is", "not", "and", "or", "raise", "global"},
    "csharp": {"public", "private", "protected", "class", "namespace", "using", "void", "int",
               "string", "bool", "var", "new", "return", "if", "else", "for", "foreach", "while",
               "static", "async", "await", "try", "catch", "null", "true", "false"},
    "java": {"public", "private", "class", "interface", "package", "import", "void", "int",
             "String", "boolean", "new", "return", "if", "else", "for", "while", "static", "null"},
    "javascript": {"function", "const", "let", "var", "return", "if", "else", "for", "while",
                   "class", "import", "export", "async", "await", "null", "true", "false", "new"},
    "sql": {"SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "CREATE", "TABLE",
            "JOIN", "ON", "AND", "OR", "NULL", "ORDER", "BY", "GROUP", "VALUES"},
    "cpp": {"include", "define", "class", "struct", "namespace", "template", "int", "void",
            "const", "auto", "return", "if", "else", "for", "while", "new", "delete", "nullptr"},
    "go": {"func", "package", "import", "var", "const", "type", "struct", "interface", "map",
           "chan", "go", "defer", "if", "else", "for", "range", "return", "switch", "case",
           "default", "nil", "true", "false", "string", "int", "int64", "error", "byte"},
    "rust": {"fn", "let", "mut", "pub", "struct", "enum", "impl", "trait", "use", "mod", "match",
             "if", "else", "loop", "while", "for", "in", "return", "self", "crate", "Some",
             "None", "Ok", "Err", "i32", "u32", "usize", "str", "String", "bool", "true", "false"},
    "json": {"true", "false", "null"},
    "yaml": {"true", "false", "null", "yes", "no", "on", "off"},
    "markdown": set(),
}
TOKEN_RE = __import__("re").compile(r'#[^\n]*|//[^\n]*|"[^"\n]*"|\'[^\'\n]*\'|\b\w+\b')


def language_from_name(name: str) -> str:
    from pathlib import Path

    suffix = Path(name).suffix.lower()
    return {
        ".py": "python", ".pyw": "python", ".cs": "csharp", ".java": "java",
        ".js": "javascript", ".ts": "javascript", ".jsx": "javascript", ".tsx": "javascript",
        ".sql": "sql", ".c": "cpp", ".h": "cpp", ".cpp": "cpp", ".hpp": "cpp",
        ".cc": "cpp", ".swift": "cpp", ".go": "go", ".rs": "rust",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".markdown": "markdown",
    }.get(suffix, "")


def _mix(first: str, second: str, ratio: float) -> QColor:
    a, b = QColor(first), QColor(second)
    return QColor(
        round(a.red() * (1 - ratio) + b.red() * ratio),
        round(a.green() * (1 - ratio) + b.green() * ratio),
        round(a.blue() * (1 - ratio) + b.blue() * ratio),
    )


def _row_background(row: DiffLine, side: str) -> str:
    if row.is_diff:
        return LINE_COLORS[row.op][0 if side == "left" else 1]
    if row.cosmetic:
        return LINE_COLORS["cosmetic"][0]
    return ""


class DiffHighlighter(QSyntaxHighlighter):
    """整行配色 + 行内字符级差异 + 当前差异块 + 查找匹配。"""

    def __init__(self, document):
        super().__init__(document)
        self.rows: list[DiffLine] = []
        self.side = "left"
        self.matches: dict[int, list[tuple[int, int]]] = {}
        self.language = ""
        self.syntax_enabled = True

    def set_rows(self, rows: list[DiffLine], side: str) -> None:
        self.rows = rows
        self.side = side
        self.rehighlight()

    def assign_rows(self, rows: list[DiffLine], side: str) -> None:
        """只更新数据不触发全文重绘；配合 setPlainText 的自动高亮，全文只扫一遍。"""
        self.rows = rows
        self.side = side

    def set_language(self, language: str, enabled: bool = True, rehighlight: bool = True) -> None:
        self.language = language or ""
        self.syntax_enabled = enabled
        if rehighlight:
            self.rehighlight()

    def update_matches(self, matches: list[tuple[int, int, int]]) -> None:
        changed = set(self.matches) | {block for block, _, _ in matches}
        self.matches = {}
        for block, start, length in matches:
            self.matches.setdefault(block, []).append((start, length))
        # 逐块重绘在少量差异时更快；匹配行很多时（如每行都命中）全文重绘反而快一个数量级
        if len(changed) > 400:
            self.rehighlight()
        else:
            self._rehighlight(changed)

    def _rehighlight(self, blocks: set[int]) -> None:
        document = self.document()
        for number in blocks:
            block = document.findBlockByNumber(number)
            if block.isValid():
                self.rehighlightBlock(block)

    def highlightBlock(self, text: str) -> None:
        index = self.currentBlock().blockNumber()
        row = self.rows[index] if 0 <= index < len(self.rows) else None
        if not text:
            return
        if row:
            spans = row.left_spans if self.side == "left" else row.right_spans
            deep = INLINE_COLORS.get(row.op)
            if spans and deep:
                span_fmt = QTextCharFormat()
                span_fmt.setBackground(QColor(deep))
                # 行内差异同时给下划线，色觉障碍下仍可识别
                span_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                for start, end in spans:
                    if end > start:
                        self.setFormat(start, end - start, span_fmt)
            if row.cosmetic and not spans:
                faint = QTextCharFormat()
                faint.setBackground(qcolor("cosmetic_bg"))
                self.setFormat(0, len(text), faint)
        if self.language and self.syntax_enabled:
            self._apply_syntax(text, _row_background(row, self.side) if row else "")
        for start, length in self.matches.get(index, []):
            match_fmt = QTextCharFormat()
            match_fmt.setBackground(qcolor("find_bg"))
            match_fmt.setForeground(qcolor("find_fg"))
            match_fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
            self.setFormat(start, length, match_fmt)

    def _apply_syntax(self, text: str, background: str) -> None:
        """语法着色只改前景色，同时保留差异行背景。"""
        keywords = KEYWORDS.get(self.language, set())
        for match in TOKEN_RE.finditer(text):
            token = match.group(0)
            if token.startswith(("#", "//")):
                color = SYNTAX_FG["comment"]
            elif token[:1] in "\"'":
                color = SYNTAX_FG["string"]
            elif token.isdigit():
                color = SYNTAX_FG["number"]
            elif token in keywords or token.upper() in keywords:
                color = SYNTAX_FG["keyword"]
            else:
                continue
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if background:
                fmt.setBackground(QColor(background))
            self.setFormat(match.start(), len(token), fmt)


class LineNumberArea(QWidget):
    def __init__(self, editor: "DiffTextEdit"):
        super().__init__(editor)
        self.editor = editor
        self.setFixedWidth(line_number_width(10))

    def sizeHint(self) -> QSize:  # pragma: no cover - GUI
        return QSize(self.width(), 0)

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI 绘制
        painter = QPainter(self)
        painter.fillRect(event.rect(), qcolor("line_number_bg"))
        # 与卡片组件一致的右侧分隔线，消除自绘区域与全局样式的割裂
        edge = painter.pen()
        edge.setColor(qcolor("border"))
        edge.setWidth(1)
        painter.setPen(edge)
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        edit = self.editor
        block = edit.firstVisibleBlock()
        number = block.blockNumber()
        rect = edit.blockBoundingGeometry(block).translated(edit.contentOffset())
        top = rect.top()
        bottom = top + rect.height()
        rows = edit.rows
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                row = rows[number] if 0 <= number < len(rows) else None
                mark = ""
                if row is not None and row.is_diff:
                    status = _status_for_row(row, edit.side)
                    fg = STATUS_COLORS[status][0]
                    painter.fillRect(QRectF(0, top, 3, bottom - top), QColor(fg))
                    mark = ROW_MARK.get(row.op, "≠")
                    painter.setPen(QColor(fg))
                elif row is not None and row.cosmetic:
                    painter.fillRect(QRectF(0, top, 3, bottom - top), qcolor("cosmetic_mark"))
                label = ""
                if row is not None:
                    value = row.left_no if edit.side == "left" else row.right_no
                    label = str(value) if value is not None else "–"
                if row is not None and row.manual:
                    painter.fillRect(QRectF(self.width() - 14, top + 2, 5, bottom - top - 4), qcolor("link"))
                if number in edit._bookmarks:
                    painter.fillRect(QRectF(self.width() - 8, top + 2, 5, bottom - top - 4), qcolor("accent"))
                painter.setFont(MONO_FONT)
                if mark:
                    painter.drawText(
                        QRectF(4, top, 14, bottom - top),
                        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                        mark,
                    )
                painter.setPen(qcolor("line_number_fg"))
                painter.drawText(
                    QRectF(18, top, self.width() - 22, bottom - top),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
            block = block.next()
            top = bottom
            rect = edit.blockBoundingGeometry(block).translated(edit.contentOffset())
            bottom = top + rect.height()
            number += 1
        painter.end()


def _status_for_row(row: DiffLine, side: str) -> DiffStatus:
    if row.op == "delete":
        return DiffStatus.LEFT_ONLY
    if row.op == "insert":
        return DiffStatus.RIGHT_ONLY
    if row.op == "replace":
        return DiffStatus.MODIFIED
    if row.cosmetic:
        return DiffStatus.FOLDER_DIFF
    return DiffStatus.SAME


class DiffTextEdit(QPlainTextEdit):
    """单侧文本窗格：整行背景、行内高亮、行号栏、占位行提示、查找高亮。"""

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self.side = side
        self.rows: list[DiffLine] = []
        self._placeholder: set[int] = set()
        self._current: set[int] = set()
        self._bookmarks: set[int] = set()
        self._matches: list[tuple[int, int, int]] = []  # (block, start, length)
        self._loading = False
        self.setFont(MONO_FONT)
        # 文件拖放由上层对比视图统一接管（编辑器默认会拦截 URL 拖放）
        self.setAcceptDrops(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(32)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setStyleSheet(
            f'QPlainTextEdit {{ background: {c("editor_bg")}; color: {c("text")}; }}'
        )
        self.number_area = LineNumberArea(self)
        self.highlighter = DiffHighlighter(self.document())
        self.updateRequest.connect(self._on_update_request)
        self.setViewportMargins(self.number_area.width(), 0, 0, 0)
        self.setReadOnly(False)
        self.textChanged.connect(self._on_text_changed)

    # Ctrl+滚轮缩放：只发信号，由上层对比视图统一调整两侧字号并记忆
    zoom_requested = Signal(int)

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = 1 if event.angleDelta().y() > 0 else -1
            self.zoom_requested.emit(delta)
            event.accept()
            return
        super().wheelEvent(event)

    # ---- 内容装载 -------------------------------------------------
    def set_result(self, result: TextDiffResult) -> None:
        self._loading = True
        self.rows = result.rows
        self._current = set()
        self._matches = []
        self.highlighter.matches = {}
        lines: list[str] = []
        self._placeholder.clear()
        for position, row in enumerate(result.rows):
            text = row.left_text if self.side == "left" else row.right_text
            if text is None:
                # 占位集合按文档块号记录（过滤模式下 row.index 是原始行号，不能直接用）
                self._placeholder.add(position)
                lines.append("")
            else:
                lines.append(text)
        # 末尾换行用一个额外的空文本块表示，保证 toPlainText() 能无损还原
        raw = result.left_raw if self.side == "left" else result.right_raw
        if lines and raw.endswith(("\n", "\r")):
            lines.append("")
        # 先写入行数据，setPlainText 触发的自动高亮即为唯一一次全文扫描
        self.highlighter.assign_rows(result.rows, self.side)
        self.setPlainText("\n".join(lines))
        self._refresh_selections()
        self._loading = False
        self.moveCursor(QTextCursor.MoveOperation.Start)
        self.number_area.update()

    def text(self) -> str:
        return self.toPlainText()

    def apply_editor_style(self, family: str, size_pt: int) -> None:
        """编辑器自身样式表显式声明字体（全局样式表会覆盖 setFont）。

        字号缩放同时作用于正文、行号栏、连线栏与占位行，避免放大后数字裁切。
        """
        size = max(6, int(size_pt))
        set_mono_font(family, size)
        self.setFont(QFont(family or default_mono_font(), size))
        self.setStyleSheet(
            f'QPlainTextEdit {{ background: {c("editor_bg")}; color: {c("text")};'
            f' font-family: "{family or default_mono_font()}", '
            f'{mono_font_stack(family or default_mono_font())};'
            f' font-size: {size}pt; }}'
        )
        self.setTabStopDistance(QFontMetrics(MONO_FONT).horizontalAdvance(" ") * 4)
        width = line_number_width(size)
        self.number_area.setFixedWidth(width)
        self.setViewportMargins(width if self.number_area.isVisible() else 0, 0, 0, 0)
        self.number_area.update()

    def set_word_wrap(self, enabled: bool) -> None:        self.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth if enabled else QPlainTextEdit.LineWrapMode.NoWrap
        )

    def set_line_numbers_visible(self, visible: bool) -> None:
        self.number_area.setVisible(visible)
        self.setViewportMargins(self.number_area.width() if visible else 0, 0, 0, 0)

    def set_language(self, name: str, enabled: bool = True, defer: bool = False) -> None:
        # defer=True 只更新语言数据，全文扫描交给随后的 set_result/setPlainText
        # rehighlight 会触发 textChanged，需要按加载态屏蔽，避免误标"已修改"
        self._loading = True
        self.highlighter.set_language(language_from_name(name), enabled, rehighlight=not defer)
        self._loading = False

    def set_bookmarks(self, rows: set[int]) -> None:
        self._bookmarks = set(rows)
        self.number_area.update()

    def current_row(self) -> int:
        return self.textCursor().blockNumber()

    def _on_text_changed(self) -> None:
        if not self._loading:
            self.edited.emit()

    edited = Signal()

    # ---- 视觉状态 -------------------------------------------------
    def set_current_block(self, rows: set[int]) -> None:
        self._current = set(rows)
        self._refresh_selections()

    def set_query(self, query: str, case_sensitive: bool = False) -> int:
        self._matches.clear()
        if query:
            find_flags = QTextDocument.FindFlag(0)
            if case_sensitive:
                find_flags |= QTextDocument.FindFlag.FindCaseSensitively
            cursor = QTextCursor(self.document())
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            while True:
                found = self.document().find(query, cursor, find_flags)
                if found.isNull():
                    break
                start = found.selectionStart() - found.block().position()
                self._matches.append((found.blockNumber(), start, len(query)))
                cursor = QTextCursor(self.document())
                cursor.setPosition(found.selectionEnd())
                if len(self._matches) > 3000:
                    break
        self._loading = True  # 匹配高亮的 rehighlight 同样会触发 textChanged
        self.highlighter.update_matches(self._matches)
        self._loading = False
        self._refresh_selections()
        return len(self._matches)

    def _base_selection(self, cursor: QTextCursor, color: QColor) -> QTextEdit.ExtraSelection:
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cursor
        sel.format.setBackground(color)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        return sel

    def _refresh_selections(self) -> None:
        """整行背景：连续同色的行合并为一个跨块选区（FullWidthSelection 会逐行铺满）。

        万行文件单侧全是差异时从上万个选区降到个位数，构建与重绘开销同步下降。
        """
        selections: list[QTextEdit.ExtraSelection] = []
        doc = self.document()
        group_start: QTextBlock | None = None
        group_color: QColor | None = None
        group_end: QTextBlock | None = None

        def flush() -> None:
            nonlocal group_start, group_color, group_end
            if group_start is not None and group_color is not None:
                cursor = QTextCursor(doc)
                cursor.setPosition(group_start.position())
                if group_end is not group_start:
                    cursor.setPosition(
                        group_end.position() + max(group_end.length() - 1, 0),
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                selections.append(self._base_selection(cursor, group_color))
            group_start = group_color = group_end = None

        # self.rows 与文档块一一对应（过滤模式下是子集），必须按枚举位置找块；
        # row.index 是原始行号，在过滤视图里会定位到错误的块（曾导致差异行无高亮）
        for position, row in enumerate(self.rows):
            if position >= doc.blockCount():
                continue
            background = _row_background(row, self.side)
            if not background:
                flush()
                continue
            color = _mix(background, str(CURRENT_BLOCK), 0.30) if position in self._current else QColor(background)
            block = doc.findBlockByNumber(position)
            if group_start is None:
                group_start, group_color, group_end = block, color, block
            elif color == group_color and block.blockNumber() == group_end.blockNumber() + 1:
                group_end = block
            else:
                flush()
                group_start, group_color, group_end = block, color, block
        flush()
        self.setExtraSelections(selections)

    def goto_row(self, row_index: int) -> None:
        if not (0 <= row_index < self.document().blockCount()):
            return
        block = self.document().findBlockByNumber(row_index)
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()
        self.number_area.update()

    def goto_match(self, index: int) -> None:
        if not self._matches:
            return
        block_no, start, length = self._matches[index % len(self._matches)]
        block = self.document().findBlockByNumber(block_no)
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + start)
        cursor.setPosition(block.position() + start + length, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.centerCursor()

    # ---- 布局 -----------------------------------------------------
    def resizeEvent(self, event) -> None:  # pragma: no cover - GUI
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.number_area.setGeometry(
            rect.left(), rect.top(), self.number_area.width(), rect.height()
        )

    def _on_update_request(self, rect, delta_y) -> None:  # pragma: no cover - GUI
        if delta_y:
            self.number_area.scroll(0, delta_y)
        else:
            self.number_area.update(0, rect.y(), self.number_area.width(), rect.height())

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI 绘制
        super().paintEvent(event)
        if not self._placeholder:
            return
        painter = QPainter(self.viewport())
        painter.setFont(MONO_FONT)
        painter.setPen(qcolor("placeholder"))
        for index in self._placeholder:
            block = self.document().findBlockByNumber(index)
            if not block.isValid():
                continue
            rect = self.blockBoundingGeometry(block).translated(self.contentOffset())
            if rect.bottom() < event.rect().top() or rect.top() > event.rect().bottom():
                continue
            painter.drawText(
                QRectF(rect.left() + 10, rect.top(), rect.width() - 20, rect.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "（此侧无对应内容）",
            )
        painter.end()


class ShortcutHints(QWidget):
    """分组键帽式快捷键提示：键名用键帽组件，说明用次要文字。"""

    def __init__(self, groups: list[tuple[tuple[str, ...], str]], parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addStretch(1)
        for index, (keys, description) in enumerate(groups):
            if index:
                separator = QLabel("·")
                apply_role(separator, "note")
                row.addWidget(separator)
            for key in keys:
                cap = QLabel(key)
                apply_role(cap, "keycap")
                cap.setAccessibleName(f"快捷键 {key}")
                row.addWidget(cap)
            label = QLabel(description)
            apply_role(label, "note")
            row.addWidget(label)
        row.addStretch(1)


class SummaryBar(QWidget):
    """可点击的状态汇总 / 筛选条。"""

    filter_changed = Signal(object)  # DiffStatus | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("summaryBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        title = QLabel("状态汇总")
        apply_role(title, "muted")
        layout.addWidget(title)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[object, QPushButton] = {}
        self._ids: dict[int, object] = {}
        self._add_button("全部", None)
        for status in (
            DiffStatus.MODIFIED,
            DiffStatus.LEFT_ONLY,
            DiffStatus.RIGHT_ONLY,
            DiffStatus.FOLDER_DIFF,
            DiffStatus.ERROR,
            DiffStatus.SAME,
        ):
            self._add_button(STATUS_META[status][0], status)
        layout.addStretch(1)
        self.buttons[None].setChecked(True)
        self.group.idClicked.connect(self._on_clicked)

    def _add_button(self, text: str, key) -> None:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setMinimumWidth(72)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        apply_role(button, "status-button", status_key(key) if key is not None else "same")
        button.setAccessibleName(f"筛选：{text}")
        ident = len(self.buttons)
        self.group.addButton(button, ident)
        self._ids[ident] = key
        self.buttons[key] = button
        self.layout().addWidget(button)

    def _on_clicked(self, ident: int) -> None:  # pragma: no cover - GUI
        self.filter_changed.emit(self._ids.get(ident))

    def set_stats(self, stats: dict[str, int] | None) -> None:
        stats = stats or {}
        total = sum(stats.get(s.value, 0) for s in STATUS_META)
        self.buttons[None].setText(f"全部 {total}")
        for status in STATUS_META:
            button = self.buttons.get(status)
            if button is None:
                continue
            count = stats.get(status.value, 0)
            button.setText(f"{STATUS_META[status][0]} {count}")
            button.setAccessibleName(f"筛选：{STATUS_META[status][0]}，共 {count} 项")

    def set_active(self, key) -> None:
        button = self.buttons.get(key)
        if button:
            block = self.group.blockSignals(True)
            button.setChecked(True)
            self.group.blockSignals(block)

class Minimap(QWidget):
    """差异分布缩略图：点击跳转到对应行，当前差异块高亮。"""

    row_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(28)
        self.setToolTip("差异分布：点击跳转")
        self.setAccessibleName("差异分布缩略图，点击跳转到对应行")
        self._rows: list = []
        self._total = 1
        self._current = -1

    def apply_scale(self, size_pt: int) -> None:
        """高分屏 / 字号放大时同步加宽，避免细条难以命中。"""
        self.setFixedWidth(max(26, min(44, int(size_pt) * 2 + 8)))

    def set_rows(self, rows: list) -> None:
        self._rows = rows
        self._total = max(1, len(rows))
        self.update()

    def set_current(self, row: int) -> None:
        self._current = row
        self.update()

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI 绘制
        painter = QPainter(self)
        painter.fillRect(event.rect(), qcolor("line_number_bg"))
        edge = painter.pen()
        edge.setColor(qcolor("border"))
        edge.setWidth(1)
        painter.setPen(edge)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        height = max(2, self.height() - 4)
        # 与文档一致按枚举位置定位（过滤模式下 row.index 是原始行号，比例会错位）
        for position, row in enumerate(self._rows):
            if not row.is_diff:
                continue
            y = 2 + int(position / self._total * height)
            status = (DiffStatus.MODIFIED if row.op == "replace" else
                      DiffStatus.LEFT_ONLY if row.op == "delete" else DiffStatus.RIGHT_ONLY)
            painter.fillRect(2, y, self.width() - 4, 3, QColor(STATUS_COLORS[status][0]))
        if 0 <= self._current < self._total:
            y = 2 + int(self._current / self._total * height)
            painter.fillRect(0, max(0, y - 1), self.width(), 3, qcolor("find_bg"))
        painter.end()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI
        height = max(2, self.height() - 4)
        ratio = (event.position().y() - 2) / height
        self.row_clicked.emit(max(0, min(self._total - 1, int(ratio * self._total))))

class AlignmentGutter(QWidget):
    """两个文本窗格之间的对齐连线栏。

    - 按住鼠标从一侧拖到另一侧：创建手动对齐连线
    - 同侧按下拖动释放：创建断开对齐点
    - 右键点击连线附近：删除该连线
    """

    link_requested = Signal(int, int)    # 0 基左侧行、右侧行
    break_requested = Signal(int, int)
    remove_requested = Signal(int, int)  # 删除指定左侧行上的连线
    status_hint = Signal(str)

    def __init__(self, left_edit, right_edit, parent=None):
        super().__init__(parent)
        self.left_edit = left_edit
        self.right_edit = right_edit
        self.links: list[tuple[int, int]] = []      # 0 基行号
        self.breaks: list[tuple[int, int]] = []
        self.setFixedWidth(34)
        self.setToolTip("对齐连线栏：从一侧拖到另一侧建立手动对齐，同侧拖动建立断点，右键删除连线")
        self.setAccessibleName("手动对齐连线栏")
        self.setMouseTracking(True)
        self._drag = None                            # (side, row)
        self._hover = None                           # (side, row)

    def apply_scale(self, size_pt: int) -> None:
        """连线栏宽度随字号调整，保持拖拽命中区域可用。"""
        self.setFixedWidth(max(30, min(52, int(size_pt) * 2 + 14)))

    # ---- 坐标换算 -------------------------------------------------
    def _row_global_y(self, editor, row: int) -> float | None:
        block = editor.document().findBlockByNumber(row)
        if not block.isValid():
            return None
        rect = editor.blockBoundingGeometry(block).translated(editor.contentOffset())
        return float(editor.viewport().mapToGlobal(rect.center().toPoint()).y())

    def _row_at(self, editor: QPlainTextEdit, global_y: float) -> int | None:
        block = editor.firstVisibleBlock()
        best, best_dist = None, None
        while block.isValid():
            rect = editor.blockBoundingGeometry(block).translated(editor.contentOffset())
            if rect.top() > editor.viewport().height() + 50:
                break
            gy = float(editor.viewport().mapToGlobal(rect.center().toPoint()).y())
            dist = abs(gy - global_y)
            if best_dist is None or dist < best_dist:
                best, best_dist = block.blockNumber(), dist
            block = block.next()
        return best

    def _pick(self, event) -> tuple[str, int]:
        side = "left" if event.position().x() < self.width() / 2 else "right"
        editor = self.left_edit if side == "left" else self.right_edit
        row = self._row_at(editor, event.globalPosition().y())
        if row is None:
            row = editor.current_row()
        return side, row

    # ---- 交互 -----------------------------------------------------
    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI
        if event.button() == Qt.MouseButton.RightButton:
            self._remove_nearest(event)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        side, row = self._pick(event)
        self._drag = (side, row)
        self.grabMouse()
        self.status_hint.emit(f"对齐拖拽：起点 {side} 第 {row + 1} 行，拖到另一侧释放")

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI
        if self._drag:
            self._hover = self._pick(event)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI
        if not self._drag:
            return
        self.releaseMouse()
        side2, row2 = self._pick(event)
        side1, row1 = self._drag
        self._drag = self._hover = None
        if side1 != side2:
            left, right = (row1, row2) if side1 == "left" else (row2, row1)
            self.link_requested.emit(left, right)
        else:
            self.break_requested.emit(row1, row1)
        self.update()

    def _remove_nearest(self, event) -> None:  # pragma: no cover - GUI
        y = event.globalPosition().y()
        best, best_dist = None, None
        for index, (left_row, right_row) in enumerate(self.links):
            y1 = self._row_global_y(self.left_edit, left_row)
            y2 = self._row_global_y(self.right_edit, right_row)
            points = [value for value in (y1, y2) if value is not None]
            if points:
                dist = min(abs(value - y) for value in points)
                if best_dist is None or dist < best_dist:
                    best, best_dist = index, dist
        if best is not None and best_dist < 24:
            left_row, _ = self.links.pop(best)
            self.remove_requested.emit(left_row, left_row)
        self.update()

    # ---- 数据 -----------------------------------------------------
    def set_state(self, links: list, breaks: list) -> None:
        self.links = [(p.left - 1, p.right - 1) for p in links]
        self.breaks = [(p.left - 1, p.right - 1) for p in breaks]
        self.update()

    # ---- 绘制 -----------------------------------------------------
    def paintEvent(self, event) -> None:  # pragma: no cover - GUI 绘制
        painter = QPainter(self)
        painter.fillRect(event.rect(), qcolor("line_number_bg"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 与卡片组件一致的细边框，消除自绘区域与全局样式的割裂感
        edge = painter.pen()
        edge.setColor(qcolor("border"))
        edge.setWidth(1)
        painter.setPen(edge)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        def y_local(editor, row) -> float | None:
            value = self._row_global_y(editor, row)
            if value is None:
                return None
            return float(self.mapFromGlobal(QPoint(0, int(value))).y())

        for left_row, right_row in self.links:
            y1, y2 = y_local(self.left_edit, left_row), y_local(self.right_edit, right_row)
            if y1 is None or y2 is None:
                continue
            pen = painter.pen()
            pen.setColor(qcolor("link"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(2, int(y1), self.width() - 2, int(y2))
        for left_row, _right_row in self.breaks:
            y1 = y_local(self.left_edit, left_row)
            if y1 is None:
                continue
            pen = painter.pen()
            pen.setColor(qcolor("break"))
            painter.setPen(pen)
            painter.drawLine(2, int(y1), self.width() - 2, int(y1))
        if self._drag:
            side, row = self._drag
            editor = self.left_edit if side == "left" else self.right_edit
            y1 = y_local(editor, row)
            y2 = None
            if self._hover:
                hover_side, hover_row = self._hover
                y2 = y_local(self.left_edit if hover_side == "left" else self.right_edit, hover_row)
            if y1 is not None and y2 is not None:
                pen = painter.pen()
                pen.setColor(qcolor("find_bg"))
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(2, int(y1), self.width() - 2, int(y2))
        painter.end()


def drop_paths(event) -> list[str]:
    """从拖放事件提取本地路径（已规范化）；无本地文件返回空列表。"""
    import os

    try:
        mime = event.mimeData()
    except RuntimeError:
        return []
    if not mime.hasUrls():
        return []
    return [os.path.normpath(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]

