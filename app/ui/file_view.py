"""文本对比：左右分栏、差异高亮、逐项导航、查找、合并与保存。"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import DiffScopeError
from ..core.fs_utils import read_text, write_text_atomic
from ..core.merge import MergeDirection, merge_all, merge_block, rebuild
from ..core.models import CompareOptions, DiffStatus, TextDiffResult
from ..core.text_diff import AlignmentPoint, TextAlignments, compute_text_diff, diff_blocks
from .dialogs import confirm, show_error, show_info
from .theme import BORDER, MUTED, PANEL, STATUS_COLORS
from .widgets import AlignmentGutter, DiffTextEdit, Minimap


class TextPairWorker(QObject):
    """后台加载文本对：文件读取/格式转换 + 差异计算，避免大文件阻塞界面。"""

    finished = Signal(object)
    failed = Signal(object)
    stage = Signal(str)  # 阶段进度：读取左/右侧 → 计算差异，让加载过程可见

    def __init__(self, view, left, right, options, seq, rel_hint="", done=None):
        super().__init__()
        self.view = view  # 只调用其纯计算方法 _prepare_pair，不触碰控件
        self.left = left
        self.right = right
        self.options = options
        self.seq = seq
        self.rel_hint = rel_hint
        self.done = done

    @Slot()
    def run(self) -> None:
        # 阶段进度桥接：worker 线程内通过信号发回界面线程
        self.view._prepare_stage = lambda msg: self.stage.emit(msg)
        try:
            data = self.view._prepare_pair(self.left, self.right, self.options)
            self.finished.emit((self, data))
        except Exception as exc:  # 后台线程兜底，异常交给界面线程展示
            self.failed.emit((self, exc))
        finally:
            self.view._prepare_stage = None


class FileCompareView(QWidget):
    """两个对齐的文本窗格，支持编辑、合并与回写。"""

    status_message = Signal(str)
    saved = Signal()
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.left_path: Path | None = None
        self.right_path: Path | None = None
        self.options = CompareOptions()
        self.result: TextDiffResult | None = None
        self.blocks: list[list[int]] = []
        self.current_block = -1
        self._dirty: set[str] = set()
        self._matches = 0
        self._match_index = 0
        self._syncing_scroll = False
        self._case_sensitive = False
        self._status_filter = None
        # BC 式显示过滤：全部 / 差别 / 相同。仅改变显示与导航，不改动数据与保存内容。
        self._display_filter = "all"
        self._display_map: list[int] | None = None    # 显示行 → 原始行
        self._display_positions: dict[int, int] = {}  # 原始行 → 显示行
        self.rules = None
        self.alignments = TextAlignments()
        self.active_format = None
        self.formats_registry = None
        self.max_text_bytes = 64 * 1024 * 1024
        self.settings = None
        self.minimap = None
        self.bookmarks: set[int] = set()
        self._rel_hint = ""
        self._can_go_back = False
        self._load_seq = 0            # 异步加载序号：结果回来时比对，旧结果直接丢弃
        self._async_thread: QThread | None = None
        self._async_worker = None
        self.setAcceptDrops(True)
        self._build_ui()

    # ---- 界面 -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        bar1 = QHBoxLayout()
        bar1.setSpacing(6)
        self.btn_back = QPushButton("← 返回目录")
        self.btn_back.clicked.connect(lambda: self.back_requested.emit())
        self.btn_back.setVisible(False)
        bar1.addWidget(self.btn_back)

        self.btn_prev = QPushButton("上一差异 (F7)")
        self.btn_next = QPushButton("下一差异 (F8)")
        self.btn_prev.clicked.connect(self.previous_difference)
        self.btn_next.clicked.connect(self.next_difference)
        bar1.addWidget(self.btn_prev)
        bar1.addWidget(self.btn_next)
        self.counter = QLabel("差异 0 / 0")
        self.counter.setStyleSheet(f"color: {MUTED}; padding: 0 6px;")
        bar1.addWidget(self.counter)
        bar1.addSpacing(10)

        # 显示过滤（BC 式）：全部(*) / 差别(≠) / 相同(=)
        self.show_all_btn = QPushButton("全部 *")
        self.show_diff_btn = QPushButton("差别 ≠")
        self.show_same_btn = QPushButton("相同 =")
        for btn in (self.show_all_btn, self.show_diff_btn, self.show_same_btn):
            btn.setCheckable(True)
            btn.setToolTip("显示过滤：只影响显示与差异导航，不改数据；过滤状态下编辑/合并/保存暂停")
        self.show_diff_btn.setStyleSheet(f"color: #e06c60;")
        self.show_all_btn.setChecked(True)
        self.show_group = QButtonGroup(self)
        self.show_group.setExclusive(True)
        self.show_group.addButton(self.show_all_btn)
        self.show_group.addButton(self.show_diff_btn)
        self.show_group.addButton(self.show_same_btn)
        self.show_all_btn.clicked.connect(lambda: self.set_display_filter("all"))
        self.show_diff_btn.clicked.connect(lambda: self.set_display_filter("differences"))
        self.show_same_btn.clicked.connect(lambda: self.set_display_filter("same"))
        bar1.addWidget(self.show_all_btn)
        bar1.addWidget(self.show_diff_btn)
        bar1.addWidget(self.show_same_btn)
        bar1.addSpacing(10)

        self.btn_merge_l2r = QPushButton("合并当前块 →")
        self.btn_merge_r2l = QPushButton("← 合并当前块")
        self.btn_merge_l2r.setToolTip("用左侧当前差异块覆盖右侧对应内容")
        self.btn_merge_r2l.setToolTip("用右侧当前差异块覆盖左侧对应内容")
        self.btn_merge_l2r.clicked.connect(lambda: self.merge_current(MergeDirection.LEFT_TO_RIGHT))
        self.btn_merge_r2l.clicked.connect(lambda: self.merge_current(MergeDirection.RIGHT_TO_LEFT))
        bar1.addWidget(self.btn_merge_r2l)
        bar1.addWidget(self.btn_merge_l2r)
        self.btn_all_l2r = QPushButton("全部 左→右")
        self.btn_all_r2l = QPushButton("全部 右→左")
        self.btn_all_l2r.clicked.connect(lambda: self.merge_all(MergeDirection.LEFT_TO_RIGHT))
        self.btn_all_r2l.clicked.connect(lambda: self.merge_all(MergeDirection.RIGHT_TO_LEFT))
        bar1.addWidget(self.btn_all_r2l)
        bar1.addWidget(self.btn_all_l2r)
        bar1.addStretch(1)
        root.addLayout(bar1)

        bar2 = QHBoxLayout()
        bar2.setSpacing(6)
        self.query = QLineEdit()
        self.query.setPlaceholderText("查找文本（两侧同步高亮）")
        self.query.setClearButtonEnabled(True)
        self.query.setMaximumWidth(320)
        self.query.textChanged.connect(self._find)
        self.query.returnPressed.connect(self.next_match)
        bar2.addWidget(self.query)
        self.btn_find_prev = QPushButton("上一个")
        self.btn_find_next = QPushButton("下一个 (F3)")
        self.btn_find_prev.clicked.connect(self.previous_match)
        self.btn_find_next.clicked.connect(self.next_match)
        bar2.addWidget(self.btn_find_prev)
        bar2.addWidget(self.btn_find_next)
        self.case_box = QCheckBox("区分大小写")
        self.case_box.stateChanged.connect(self._find)
        bar2.addWidget(self.case_box)
        self.match_label = QLabel("")
        self.match_label.setStyleSheet(f"color: {MUTED};")
        bar2.addWidget(self.match_label)
        bar2.addStretch(1)
        self.btn_recompare = QPushButton("重新比较")
        self.btn_recompare.clicked.connect(self.recompare)
        self.btn_save_left = QPushButton("保存左侧")
        self.btn_save_right = QPushButton("保存右侧")
        self.btn_save_left.clicked.connect(lambda: self.save_side("left"))
        self.btn_save_right.clicked.connect(lambda: self.save_side("right"))
        bar2.addWidget(self.btn_recompare)
        bar2.addWidget(self.btn_save_left)
        bar2.addWidget(self.btn_save_right)
        root.addLayout(bar2)

        self.error_banner = QLabel()
        self.error_banner.setWordWrap(True)
        self.error_banner.setVisible(False)
        self.error_banner.setStyleSheet(
            "background: #421d22; color: #f58282; border: 1px solid #722c33;"
            "border-radius: 4px; padding: 8px;"
        )
        root.addWidget(self.error_banner)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.left_edit = DiffTextEdit("left")
        self.right_edit = DiffTextEdit("right")
        self.left_edit.edited.connect(lambda: self._mark_dirty("left"))
        self.right_edit.edited.connect(lambda: self._mark_dirty("right"))
        self.left_panel = self._make_panel("左侧文件", self.left_edit, "left")
        self.right_panel = self._make_panel("右侧文件", self.right_edit, "right")
        splitter.addWidget(self.left_panel)
        self.alignment_gutter = AlignmentGutter(self.left_edit, self.right_edit)
        self.alignment_gutter.link_requested.connect(lambda l, r: self._add_alignment_link(l, r))
        self.alignment_gutter.break_requested.connect(lambda l, r: self._add_alignment_break(l, r))
        self.alignment_gutter.remove_requested.connect(self._remove_alignment_link)
        self.alignment_gutter.status_hint.connect(self.status_message.emit)
        splitter.addWidget(self.alignment_gutter)
        splitter.addWidget(self.right_panel)
        self.minimap_widget = Minimap()
        splitter.addWidget(self.minimap_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(3, 0)
        # 连线栏与缩略图不可折叠、不被挤到 0 宽
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(3, False)
        splitter.setSizes([600, 30, 600, 26])
        root.addWidget(splitter, 1)
        self.set_minimap(self.minimap_widget)

        self.left_edit.verticalScrollBar().valueChanged.connect(
            lambda value: self._mirror_scroll(value, self.right_edit)
        )
        self.right_edit.verticalScrollBar().valueChanged.connect(
            lambda value: self._mirror_scroll(value, self.left_edit)
        )

        legend = QHBoxLayout()
        legend.setSpacing(10)
        for text, color in (
            ("修改内容", STATUS_COLORS[DiffStatus.MODIFIED][1]),
            ("仅左侧有", STATUS_COLORS[DiffStatus.LEFT_ONLY][1]),
            ("仅右侧有", STATUS_COLORS[DiffStatus.RIGHT_ONLY][1]),
        ):
            chip = QLabel(f"  {text}  ")
            chip.setStyleSheet(f"background: {color}; color: #e6e8ee; border-radius: 3px; padding: 2px 6px;")
            legend.addWidget(chip)
        note = QLabel("灰色空行表示该侧缺少对应内容；文本可直接编辑，保存前会再次确认。")
        note.setStyleSheet(f"color: {MUTED};")
        legend.addWidget(note)
        legend.addStretch(1)
        root.addLayout(legend)

        # 底部行内容栏：显示光标所在行在两侧的对应内容（各一行）
        info = QFrame()
        info.setStyleSheet(f"background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(8, 4, 8, 4)
        info_layout.setSpacing(2)
        self.line_info_left = QLabel("左：—")
        self.line_info_right = QLabel("右：—")
        for label in (self.line_info_left, self.line_info_right):
            label.setStyleSheet("color: #e3e6ec; border: none;")
            info_layout.addWidget(label)
        root.addWidget(info)
        self.line_info_frame = info

        # 以最近移动过光标的一侧定位当前行；goto_row / 加载后移动光标也会触发刷新
        self._active_editor = self.left_edit
        self.left_edit.cursorPositionChanged.connect(
            lambda: self._update_line_info(self.left_edit))
        self.right_edit.cursorPositionChanged.connect(
            lambda: self._update_line_info(self.right_edit))

    def _make_panel(self, title: str, editor: DiffTextEdit, side: str) -> QWidget:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setStyleSheet(
            f"color: {MUTED}; background: {PANEL}; border: 1px solid {BORDER};"
            "border-radius: 4px; padding: 6px 8px;"
        )
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        setattr(self, f"path_label_{side}", label)
        layout.addWidget(label)
        layout.addWidget(editor, 1)
        return panel

    # ---- 装载 -----------------------------------------------------
    def load_pair(
        self,
        left: str | Path | None,
        right: str | Path | None,
        options: CompareOptions | None = None,
        rel_hint: str = "",
        can_go_back: bool = False,
    ) -> bool:
        """同步加载（供测试与内部使用）；界面路径请用 load_pair_async。"""
        self._load_seq += 1  # 使仍在进行的后台加载结果失效
        self.options = options or CompareOptions()
        self.left_path = Path(left) if left else None
        self.right_path = Path(right) if right else None
        self._rel_hint = rel_hint
        self._can_go_back = can_go_back
        self.btn_back.setVisible(can_go_back)
        try:
            left_doc, right_doc, result = self._prepare_pair(left, right, self.options)
        except DiffScopeError as exc:
            self._show_load_error(exc)
            return False
        self._apply_loaded_pair(left_doc, right_doc, result, rel_hint)
        return True

    def load_pair_async(
        self,
        left: str | Path | None,
        right: str | Path | None,
        options: CompareOptions | None = None,
        rel_hint: str = "",
        can_go_back: bool = False,
        done=None,
    ) -> None:
        """后台线程加载：立即返回，完成后在界面线程应用结果。done(ok) 可选回调。

        注意：worker 信号必须连接到本控件（QObject）的绑定方法，Qt 才会走 queued
        connection 把回调调度回界面线程；连接 lambda 会在 worker 线程直接执行。
        rel_hint / done 存在 worker 上，随结果一起带回来。
        """
        self._load_seq += 1
        seq = self._load_seq
        self.options = options or CompareOptions()
        self.left_path = Path(left) if left else None
        self.right_path = Path(right) if right else None
        self._rel_hint = rel_hint
        self._can_go_back = can_go_back
        self.btn_back.setVisible(can_go_back)
        name = Path(left).name if left else (Path(right).name if right else "文件")
        self.status_message.emit(f"正在加载 {name} …")
        worker = TextPairWorker(self, left, right, self.options, seq, rel_hint, done)
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.stage.connect(self.status_message.emit)
        worker.finished.connect(self._on_worker_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        # 必须持有 worker 的 Python 引用，否则局部变量离开作用域后 QObject 被 GC，信号无法返回
        self._async_worker = worker
        self._async_thread = thread
        thread.start()

    def _on_worker_finished(self, payload) -> None:
        worker, data = payload
        self._async_thread = None
        self._async_worker = None
        if worker.seq != self._load_seq:  # 期间发起了新的加载，丢弃旧结果
            if worker.done:
                worker.done(False)
            return
        left_doc, right_doc, result = data
        self._apply_loaded_pair(left_doc, right_doc, result, worker.rel_hint)
        if worker.done:
            worker.done(True)

    def _on_worker_failed(self, payload) -> None:
        worker, exc = payload
        self._async_thread = None
        self._async_worker = None
        if worker.seq != self._load_seq:
            if worker.done:
                worker.done(False)
            return
        if isinstance(exc, DiffScopeError):
            self._show_load_error(exc)
        else:
            show_error(self, exc, "打开文本对比")
            self.status_message.emit(f"文本加载失败：{getattr(exc, 'message', exc)}")
        if worker.done:
            worker.done(False)

    _prepare_stage = None  # 由 load_pair_async 临时注入的进度回调

    def _prepare_pair(self, left, right, options):
        """读取/转换两侧文档并计算差异。可在后台线程运行：只读配置，不触碰控件。"""
        stage = self._prepare_stage
        left_path = Path(left) if left else None
        right_path = Path(right) if right else None
        if stage:
            stage("正在读取文件内容 …")
        if left_path and left_path.suffix.lower() in (".patch", ".diff") and left_path.is_file():
            from ..core.formats import parse_patch

            left_text, right_text, _ = parse_patch(
                left_path.read_bytes().decode("utf-8", "replace")
            )
            left_enc = right_enc = "utf-8"
            left_doc = right_doc = None
        else:
            left_doc = self._load_doc(left_path)
            right_doc = self._load_doc(right_path)
            left_text = left_doc.text if left_doc else ""
            right_text = right_doc.text if right_doc else ""
            left_enc = left_doc.encoding if left_doc else (right_doc.encoding if right_doc else "utf-8")
            right_enc = right_doc.encoding if right_doc else (left_doc.encoding if left_doc else "utf-8")
        if stage:
            stage(f"正在计算差异（共 {len(left_text.splitlines()) + len(right_text.splitlines())} 行）…")
        result = compute_text_diff(left_text, right_text, options, left_enc, right_enc,
                                   self._effective_rules(), self._alignments_snapshot())
        return left_doc, right_doc, result

    def _alignments_snapshot(self) -> TextAlignments:
        return TextAlignments(
            links=list(self.alignments.links),
            breaks=list(self.alignments.breaks),
            style=self.alignments.style,
        )

    def _apply_loaded_pair(self, left_doc, right_doc, result, rel_hint: str) -> None:
        self._dirty.clear()
        self.error_banner.setVisible(False)
        self._apply_result(result, reset_counter=True)
        self._update_path_labels(left_doc, right_doc)
        label = rel_hint or (self.left_path.name if self.left_path else self.right_path.name if self.right_path else "")
        state = "两侧内容一致" if result.identical else f"发现 {len(self.blocks)} 处差异"
        self.status_message.emit(f"文本对比：{label} — {state}")
        self._update_line_info()



    def _effective_rules(self):
        """全局规则 + 当前文件格式规则的合并。"""
        from ..core.rules import CompareRules

        fmt = self.active_format
        if fmt and fmt.rules and fmt.rules.rules:
            return CompareRules(
                rules=list(self.rules.rules) + list(fmt.rules.rules) if self.rules else list(fmt.rules.rules),
                language=fmt.rules.language or (self.rules.language if self.rules else ""),
                delimiter=fmt.rules.delimiter,
            )
        return self.rules

    def _load_doc(self, path):
        """比较前转换：文件格式转换器 → PDF/Word/RTF/HTML → 原编码读取。"""
        if path is None or not path.is_file():
            return None
        from ..core.fs_utils import TextDocument
        from ..core.formats import to_text

        fmt = self.active_format or (self.formats_registry.match(path.name) if self.formats_registry else None)
        if fmt and fmt.converter:
            from ..core.file_formats import run_converter

            text = run_converter(fmt.converter, path)
            return TextDocument(path, text, "utf-8", "\n", len(text))
        if path.suffix.lower() in (".pdf", ".docx", ".dotx", ".rtf", ".doc", ".html", ".htm", ".xml", ".dfm"):
            text = to_text(path.read_bytes(), path.name)
            return TextDocument(path, text, "utf-8", "\n", len(text))
        return read_text(path, max_bytes=self.max_text_bytes)

    def _show_load_error(self, exc: DiffScopeError) -> None:
        text = exc.user_message().replace("\n", "  ")
        self.error_banner.setText(f"[{exc.title}] {text}")
        self.error_banner.setVisible(True)
        self.left_edit.setPlainText("")
        self.right_edit.setPlainText("")
        self.blocks = []
        self.current_block = -1
        self.counter.setText("差异 0 / 0")
        self.line_info_left.setText("左：—")
        self.line_info_right.setText("右：—")
        self.status_message.emit(f"文本读取失败：{exc.message}")
        show_error(self, exc, "打开文本对比")

    def _update_path_labels(self, left_doc, right_doc) -> None:
        for side, path, doc in (
            ("left", self.left_path, left_doc),
            ("right", self.right_path, right_doc),
        ):
            label: QLabel = getattr(self, f"path_label_{side}")
            if doc:
                info = f"编码 {doc.encoding}，{self._newline_name(doc.newline)}，{len(doc.text.splitlines())} 行"
                label.setText(f"{'左侧' if side == 'left' else '右侧'}：{path}    [{info}]")
                label.setToolTip(str(path))
            else:
                label.setText(f"{'左侧' if side == 'left' else '右侧'}：（文件不存在，保存时可新建）")
                label.setToolTip("该侧没有对应文件")

    @staticmethod
    def _newline_name(value: str) -> str:
        return {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}.get(value, "LF")

    def _apply_result(self, result: TextDiffResult, reset_counter: bool = False) -> None:
        self.result = result
        self.blocks = diff_blocks(result)
        if self._display_filter != "all":
            self._display_filter = "all"
            self._display_map = None
            self._display_positions = {}
            for editor in (self.left_edit, self.right_edit):
                editor.setReadOnly(False)
            self._set_filter_actions_enabled(True)
            self.show_all_btn.setChecked(True)
        if reset_counter:
            self.current_block = -1
        # 先更新书签与语言数据（不触发全文扫描），让 set_result 的自动高亮一次到位
        syntax_enabled = bool(self.settings is None or self.settings.get("syntax_highlight"))
        fmt_language = self.active_format.language if self.active_format and self.active_format.language else ""
        for editor, path in ((self.left_edit, self.left_path), (self.right_edit, self.right_path)):
            editor.set_bookmarks(self.bookmarks)
            editor.set_language(fmt_language or (path.name if path else ""), syntax_enabled, defer=True)
        self.left_edit.set_result(result)
        self.right_edit.set_result(result)
        self.left_edit.set_query(self.query.text(), self.case_box.isChecked())
        self.right_edit.set_query(self.query.text(), self.case_box.isChecked())
        self._sync_matches()
        if self.minimap is not None:
            self.minimap.set_rows(result.rows)
        if self.blocks:
            target = 0 if reset_counter else min(max(self.current_block, 0), len(self.blocks) - 1)
            self.goto_block(target)
        else:
            self.current_block = -1
            self.left_edit.set_current_block(set())
            self.right_edit.set_current_block(set())
            self.counter.setText("差异 0 / 0")
        self._refresh_save_buttons()

    # ---- 差异导航 -------------------------------------------------
    def difference_count(self) -> int:
        return len(self.blocks)

    def current_difference_index(self) -> int:
        return self.current_block

    def goto_block(self, index: int) -> bool:
        if not self.blocks or not (0 <= index < len(self.blocks)):
            return False
        self.current_block = index
        rows = set(self.blocks[index])
        if self._display_filter != "all":
            # 编辑器按文档块号定位高亮，过滤模式下需换算成显示位置
            rows = {self._display_positions[r] for r in rows if r in self._display_positions}
        self.left_edit.set_current_block(rows)
        self.right_edit.set_current_block(rows)
        first = self.blocks[index][0]
        if self._display_filter != "all":
            first = self._display_positions.get(first)
            if first is None:
                return False
        self.left_edit.goto_row(first)
        self.right_edit.goto_row(first)
        self.counter.setText(f"差异 {index + 1} / {len(self.blocks)}")
        if self.minimap is not None:
            self.minimap.set_current(first)
        if self.result:
            row = self.result.rows[first]
            self.status_message.emit(
                f"第 {index + 1} / {len(self.blocks)} 处差异：左 {row.left_no or '—'} / 右 {row.right_no or '—'} 行"
            )
        return True

    # ---- 状态筛选导航 ---------------------------------------------
    def _expected_status(self) -> DiffStatus | None:
        key = self._status_filter
        if key is None or key == "differences":
            return None
        if isinstance(key, DiffStatus):
            return key
        if isinstance(key, str) and key in {s.value for s in DiffStatus}:
            return DiffStatus(key)
        return None

    def _block_matches(self, block_index: int) -> bool:
        expected = self._expected_status()
        if expected is None:
            return True
        if not self.result:
            return False
        mapping = {
            "replace": DiffStatus.MODIFIED,
            "delete": DiffStatus.LEFT_ONLY,
            "insert": DiffStatus.RIGHT_ONLY,
            "equal": DiffStatus.SAME,
        }
        return any(mapping.get(self.result.rows[i].op) is expected for i in self.blocks[block_index])

    def visible_blocks(self) -> list[int]:
        return [i for i in range(len(self.blocks))
                if self._block_matches(i) and self._block_displayed(i)]

    def _block_displayed(self, block_index: int) -> bool:
        if self._display_filter == "all":
            return True
        positions = self._display_positions
        return any(row in positions for row in self.blocks[block_index])

    # ---- 显示过滤（全部 / 差别 / 相同） -----------------------------
    def set_display_filter(self, mode: str) -> None:
        if mode not in ("all", "differences", "same") or mode == self._display_filter:
            return
        self._display_filter = mode
        self._apply_display_filter()

    def _apply_display_filter(self) -> None:
        if not self.result:
            return
        readonly = self._display_filter != "all"
        if not readonly:
            self._display_map = None
            self._display_positions = {}
            rows = self.result.rows
        else:
            if self._display_filter == "differences":
                keep: set[int] = set()
                context = 3  # 每个差异段两侧保留的上下文行数
                for block in self.blocks:
                    start = max(0, block[0] - context)
                    end = min(len(self.result.rows) - 1, block[-1] + context)
                    keep.update(range(start, end + 1))
            else:  # same：只显示相同段
                keep = {row.index for row in self.result.rows if row.op == "equal"}
            rows = [row for row in self.result.rows if row.index in keep]
            self._display_map = [row.index for row in rows]
            self._display_positions = {orig: i for i, orig in enumerate(self._display_map)}
        display_result = copy.copy(self.result)
        display_result.rows = rows
        # 过滤状态只读：编辑器文本 ≠ 原文，禁止编辑/合并/保存/重新比较，避免把过滤内容写回
        for editor in (self.left_edit, self.right_edit):
            editor.setReadOnly(readonly)
        self._set_filter_actions_enabled(not readonly)
        self.left_edit.set_result(display_result)
        self.right_edit.set_result(display_result)
        links, breaks = (self.alignments.links, self.alignments.breaks) if not readonly else ([], [])
        self.alignment_gutter.set_state(links, breaks)
        if self.minimap is not None:
            self.minimap.set_rows(rows)
        # 保持当前差异块可见
        if self.blocks and self.current_block >= 0 and self._block_displayed(self.current_block):
            self.goto_block(self.current_block)
        hint = {
            "all": "显示过滤：全部行",
            "differences": f"显示过滤：仅差异段（含 3 行上下文），共 {len(rows)} 行；只读，切回「全部」可编辑",
            "same": f"显示过滤：仅相同段，共 {len(rows)} 行；只读，切回「全部」可编辑",
        }[self._display_filter]
        self.status_message.emit(hint)

    # ---- 底部行内容栏 ----------------------------------------------
    def _update_line_info(self, editor: DiffTextEdit | None = None) -> None:
        """显示光标所在行两侧的对应内容；以最近移动过光标的一侧定位。

        过滤模式下 editor.rows 是显示行，DiffLine 上的行号/文本仍是原始数据，天然兼容。
        """
        if editor is None:
            editor = self._active_editor
        else:
            self._active_editor = editor
        row_no = editor.current_row()
        rows = editor.rows
        if not rows or not (0 <= row_no < len(rows)):
            self.line_info_left.setText("左：—")
            self.line_info_right.setText("右：—")
            return
        row = rows[row_no]
        self.line_info_left.setText(f"左 {row.left_no or '—'}：{self._clip(row.left_text)}")
        self.line_info_right.setText(f"右 {row.right_no or '—'}：{self._clip(row.right_text)}")

    @staticmethod
    def _clip(text: str | None) -> str:
        if text is None:
            return "（该侧没有对应内容）"
        text = text.expandtabs(4)
        return text if len(text) <= 200 else text[:200] + " …"

    def _set_filter_actions_enabled(self, enabled: bool) -> None:
        for btn in (self.btn_merge_l2r, self.btn_merge_r2l, self.btn_all_l2r,
                    self.btn_all_r2l, self.btn_recompare, self.btn_save_left, self.btn_save_right):
            btn.setEnabled(enabled)

    def set_status_filter(self, key) -> None:
        self._status_filter = key
        visible = self.visible_blocks()
        if visible:
            self.goto_block(visible[0])
        else:
            self.status_message.emit("当前筛选条件下没有匹配的差异")

    def next_difference(self) -> None:
        visible = self.visible_blocks()
        if not visible:
            self.status_message.emit("当前筛选条件下没有差异")
            return
        if self.current_block in visible:
            target = visible[(visible.index(self.current_block) + 1) % len(visible)]
        else:
            target = next((i for i in visible if i > self.current_block), visible[0])
        self.goto_block(target)

    def previous_difference(self) -> None:
        visible = self.visible_blocks()
        if not visible:
            self.status_message.emit("当前筛选条件下没有差异")
            return
        if self.current_block in visible:
            target = visible[(visible.index(self.current_block) - 1) % len(visible)]
        else:
            target = next((i for i in reversed(visible) if i < self.current_block), visible[-1])
        self.goto_block(target)

    # ---- 查找 -----------------------------------------------------
    def _find(self, *_args) -> None:
        query = self.query.text()
        case = self.case_box.isChecked()
        left_count = self.left_edit.set_query(query, case)
        right_count = self.right_edit.set_query(query, case)
        self._sync_matches()
        if query:
            self.match_label.setText(f"匹配：左 {left_count} / 右 {right_count}")
        else:
            self.match_label.setText("")

    def _sync_matches(self) -> None:
        self._matches = max(len(self.left_edit._matches), len(self.right_edit._matches))
        self._match_index = 0

    def next_match(self) -> None:
        if not self._matches:
            self.status_message.emit("没有匹配的文本")
            return
        self._match_index = (self._match_index + 1) % self._matches
        self.left_edit.goto_match(self._match_index if self.left_edit._matches else 0)
        self.right_edit.goto_match(self._match_index if self.right_edit._matches else 0)

    def previous_match(self) -> None:
        if not self._matches:
            self.status_message.emit("没有匹配的文本")
            return
        self._match_index = (self._match_index - 1) % self._matches
        self.left_edit.goto_match(self._match_index if self.left_edit._matches else 0)
        self.right_edit.goto_match(self._match_index if self.right_edit._matches else 0)

    def focus_search(self) -> None:
        self.query.setFocus()
        self.query.selectAll()

    # ---- 合并 / 重比较 --------------------------------------------
    def merge_current(self, direction: MergeDirection) -> None:
        if not self.result or not self.blocks or self.current_block < 0:
            self.status_message.emit("当前没有可合并的差异")
            return
        row_index = self.blocks[self.current_block][0]
        detail = f"第 {self.current_block + 1} / {len(self.blocks)} 处差异（显示行 {row_index + 1}）"
        if not confirm(
            self,
            "确认合并差异块",
            f"将以【{direction.label}】方式合并 {detail}。",
            "目标侧对应内容会被源侧覆盖，其余内容不变。合并后仍需执行“保存”才会写入磁盘。",
            danger=True,
        ):
            self.status_message.emit("合并已取消")
            return
        previous = len(self.blocks)
        new_result = merge_block(self.result, row_index, direction)
        self._dirty.add(direction.target)
        self._apply_result(new_result)
        self.status_message.emit(
            f"已合并 1 处差异，剩余 {len(self.blocks)} 处（原 {previous} 处）；请保存目标侧文件"
        )

    def merge_all(self, direction: MergeDirection) -> None:
        if not self.result or self.result.identical:
            show_info(self, "无需合并", "两侧内容已一致，没有可合并的差异。")
            return
        if not confirm(
            self,
            "确认整文件合并",
            f"将执行【{direction.label}】整文件覆盖：目标侧全部内容会被源侧替换。",
            "该操作影响整个文件；执行后仍需点击“保存”才会写入磁盘。",
            danger=True,
        ):
            self.status_message.emit("整文件合并已取消")
            return
        new_result = merge_all(self.result, direction)
        self._dirty.add(direction.target)
        self._apply_result(new_result, reset_counter=True)
        self.status_message.emit("整文件合并完成，两侧内容已一致；请保存目标侧文件")

    def recompare(self) -> None:
        if not self.result:
            return
        new_result = rebuild(self.result, self.left_edit.text(), self.right_edit.text())
        self._apply_result(new_result, reset_counter=True)
        self.status_message.emit("已按当前编辑内容重新比较")

    # ---- 保存 -----------------------------------------------------
    def _mark_dirty(self, side: str) -> None:
        self._dirty.add(side)
        self._refresh_save_buttons()

    def _refresh_save_buttons(self) -> None:
        self.btn_save_left.setText("保存左侧 *" if "left" in self._dirty else "保存左侧")
        self.btn_save_right.setText("保存右侧 *" if "right" in self._dirty else "保存右侧")
        self.btn_save_left.setProperty("accent", "left" in self._dirty)
        self.btn_save_right.setProperty("accent", "right" in self._dirty)
        self.btn_save_left.style().polish(self.btn_save_left)
        self.btn_save_right.style().polish(self.btn_save_right)

    def side_path(self, side: str) -> Path | None:
        return self.left_path if side == "left" else self.right_path

    def save_side(self, side: str) -> bool:
        if not self.result:
            return False
        path = self.side_path(side)
        if path is None:
            show_info(self, "无法保存", "该侧未指定文件路径，请在目录对比中选择具体文件后重试。")
            return False
        text = self.left_edit.text() if side == "left" else self.right_edit.text()
        encoding = self.result.left_encoding if side == "left" else self.result.right_encoding
        newline = self.result.left_newline if side == "left" else self.result.right_newline
        action = "覆盖原文件" if path.exists() else "新建文件"
        if not confirm(
            self,
            "确认保存",
            f"即将{action}：\n{path}\n\n编码 {encoding}，换行 {self._newline_name(newline)}。",
            "原文件内容会被替换，此操作不可撤销。",
            danger=True,
        ):
            self.status_message.emit("保存已取消")
            return False
        try:
            if self.settings and self.settings.get("auto_backup") and path.exists():
                from ..core.services import backup_file

                backup_file(path, int(self.settings.get("backup_keep")))
            write_text_atomic(path, text, encoding, newline)
        except DiffScopeError as exc:
            show_error(self, exc, "保存文件")
            return False
        self._dirty.discard(side)
        self._refresh_save_buttons()
        self.status_message.emit(f"已保存：{path}")
        self.saved.emit()
        return True

    def save_modified(self) -> None:
        if not self._dirty:
            self.status_message.emit("当前没有未保存的修改")
            return
        for side in ("left", "right"):
            if side in self._dirty:
                if not self.save_side(side):
                    break

    # ---- 辅助 -----------------------------------------------------
    # ---- 拖拽导入 -------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # pragma: no cover - GUI
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # pragma: no cover - GUI
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # pragma: no cover - GUI
        from .widgets import drop_paths as extract_paths

        paths = extract_paths(event)
        if not paths:
            return
        event.acceptProposedAction()
        self._handle_drop(paths, event.position().toPoint())

    def _handle_drop(self, paths: list, pos) -> None:
        if self.left_panel.geometry().contains(pos):
            side = "left"
        elif self.right_panel.geometry().contains(pos):
            side = "right"
        else:
            side = "left" if pos.x() < self.width() / 2 else "right"
        if len(paths) >= 2:
            first, second = paths[0], paths[1]
        else:
            path = paths[0]
            other = self.left_path if side == "right" else self.right_path
            if other is None:
                # 另一侧还没有文件：先单侧打开，提示拖第二个文件到对面
                left = path if side == "left" else None
                right = path if side == "right" else None
                self.status_message.emit(f"正在打开 {Path(path).name} …")
                QTimer.singleShot(0, lambda: self._load_dropped(
                    left, right, f"已打开 {path}；把第二个文件拖到{'右' if side == 'left' else '左'}侧窗格即可对比"))
                return
            first = path if side == "left" else str(other)
            second = str(other) if side == "left" else path
        self.status_message.emit(f"正在打开 {Path(first).name} …")
        QTimer.singleShot(0, lambda: self._load_dropped(first, second, f"已拖入：{first} ↔ {second}"))

    def _load_dropped(self, first, second, done_message: str) -> None:
        self.load_pair_async(
            first, second, self.options,
            rel_hint=Path(first).name if first else (Path(second).name if second else ""),
            can_go_back=self._can_go_back,
            done=lambda ok: self.status_message.emit(done_message) if ok else None,
        )

    def _mirror_scroll(self, value: int, target: DiffTextEdit) -> None:
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        target.verticalScrollBar().setValue(value)
        self._syncing_scroll = False

    # ---- 扩展能力 -------------------------------------------------
    def apply_settings(self, settings) -> None:
        """应用字体、自动换行、语法高亮、行号、缩略图设置。"""
        from PySide6.QtGui import QFont

        self.settings = settings
        family = str(settings.get("editor_font_family"))
        size = int(settings.get("editor_font_size"))
        for editor in (self.left_edit, self.right_edit):
            editor.setFont(QFont(family, size))
            editor.set_word_wrap(bool(settings.get("word_wrap")))
            editor.set_line_numbers_visible(bool(settings.get("show_line_numbers")))
        for label in (self.line_info_left, self.line_info_right):
            label.setFont(QFont(family, size))
        if self.result:
            self._apply_result(self.result)

    def set_rules(self, rules) -> None:
        self.rules = rules
        if self.result:
            from ..core.text_diff import compute_text_diff

            self._apply_result(compute_text_diff(
                self.left_edit.text(), self.right_edit.text(), self.options,
                self.result.left_encoding, self.result.right_encoding, rules,
            ))

    def set_minimap(self, minimap) -> None:
        self.minimap = minimap
        if minimap is not None:
            minimap.row_clicked.connect(self.goto_visible_row)
            if self.result:
                minimap.set_rows(self.result.rows)

    def goto_visible_row(self, row: int) -> None:
        self.left_edit.goto_row(row)
        self.right_edit.goto_row(row)
        if self.minimap is not None:
            self.minimap.set_current(row)

    def find_replace(self) -> None:
        from .extended_views import FindReplaceDialog

        dialog = FindReplaceDialog(self)
        action = dialog.show_dialog()
        if action not in (1, 2):
            return
        target = dialog.target()
        for side in (("left", "right") if target == "both" else (target,)):
            editor = self.left_edit if side == "left" else self.right_edit
            if action == 1:
                self.query.setText(dialog.find.text())
                self._find()
                self.status_message.emit(f"查找：{dialog.find.text()}")
            else:
                updated, count = dialog.apply(editor.text())
                if count:
                    editor.setPlainText(updated)
                self.status_message.emit(f"{side} 侧替换 {count} 处")
        if action == 2:
            self.recompare()

    def line_detail(self) -> None:
        from .extended_views import LineDetailDialog

        index = self.left_edit.current_row()
        # 用编辑器当前行集（过滤模式下与文档块对应），不能用原始 result.rows
        rows = self.left_edit.rows or []
        left = rows[index].left_text if 0 <= index < len(rows) and rows[index].left_text else ""
        right = rows[index].right_text if 0 <= index < len(rows) and rows[index].right_text else ""
        LineDetailDialog(self, left, right).exec()

    def _original_row(self, editor, display_row: int) -> int:
        """显示行号 → 原始行号（全部模式下恒等）。"""
        rows = editor.rows
        if 0 <= display_row < len(rows):
            return rows[display_row].index
        return display_row

    def toggle_bookmark(self) -> None:
        row = self._original_row(self.left_edit, self.left_edit.current_row())
        if row in self.bookmarks:
            self.bookmarks.discard(row)
        else:
            self.bookmarks.add(row)
        for editor in (self.left_edit, self.right_edit):
            editor.set_bookmarks(self.bookmarks)
        self.status_message.emit(f"书签：{len(self.bookmarks)} 个（行 {row + 1}）")

    def goto_bookmark(self, forward: bool = True) -> None:
        if not self.bookmarks:
            self.status_message.emit("当前没有书签")
            return
        current = self.left_edit.current_row()
        candidates = sorted(row for row in self.bookmarks if (row > current if forward else row < current))
        target = candidates[0] if candidates else (min(self.bookmarks) if forward else max(self.bookmarks))
        self.goto_visible_row(target)

    def current_filter(self) -> tuple[str, object]:
        return self.query.text(), self.case_box.isChecked()

    # ---- 手动对齐 / 文件格式 ---------------------------------------
    def align_current_lines(self) -> None:
        if not self.result:
            self.status_message.emit("请先打开文本对比")
            return
        left = self.left_edit.current_row() + 1
        right = self.right_edit.current_row() + 1
        if not (0 < left <= self.left_edit.document().blockCount()
                and 0 < right <= self.right_edit.document().blockCount()):
            self.status_message.emit("当前行号超出范围，无法对齐")
            return
        self.alignments.links.append(AlignmentPoint(left, right))
        self._apply_alignments(f"已把左第 {left} 行与右第 {right} 行手动对齐")

    def break_alignment(self) -> None:
        if not self.result:
            self.status_message.emit("请先打开文本对比")
            return
        left = self.left_edit.current_row() + 1
        right = self.right_edit.current_row() + 1
        self.alignments.breaks.append(AlignmentPoint(left, right))
        self._apply_alignments(f"已在左第 {left} 行 / 右第 {right} 行处断开对齐")

    def clear_alignments(self) -> None:
        self.alignments.clear()
        self._apply_alignments("已清除全部手动对齐设置")

    def _add_alignment_link(self, left: int, right: int) -> None:
        left = self._original_row(self.left_edit, left)
        right = self._original_row(self.right_edit, right)
        self.alignments.links.append(AlignmentPoint(left + 1, right + 1))
        self._apply_alignments(f"已手动对齐：左第 {left + 1} 行 ↔ 右第 {right + 1} 行")

    def _add_alignment_break(self, row: int, _second: int = 0) -> None:
        row = self._original_row(self.left_edit, row)
        self.alignments.breaks.append(AlignmentPoint(row + 1, row + 1))
        self._apply_alignments(f"已在左第 {row + 1} 行处断开对齐")

    def _remove_alignment_link(self, left: int, _second: int = 0) -> None:
        left = self._original_row(self.left_edit, left)
        self.alignments.links = [p for p in self.alignments.links if p.left != left + 1]
        if self.result:
            from ..core.text_diff import compute_text_diff

            self._apply_result(compute_text_diff(
                self.left_edit.text(), self.right_edit.text(), self.options,
                self.result.left_encoding, self.result.right_encoding,
                self.result.rules, self.alignments,
            ))
        self.alignment_gutter.set_state(self.alignments.links, self.alignments.breaks)
        self.status_message.emit(f"已删除左第 {left + 1} 行的连线")

    def _apply_alignments(self, message: str) -> None:
        if not self.result:
            return
        from ..core.text_diff import compute_text_diff

        result = compute_text_diff(
            self.left_edit.text(), self.right_edit.text(), self.options,
            self.result.left_encoding, self.result.right_encoding,
            self.result.rules, self.alignments,
        )
        self._apply_result(result)
        self.alignment_gutter.set_state(self.alignments.links, self.alignments.breaks)
        self.status_message.emit(
            f"{message}（链接 {len(self.alignments.links)} 处 / 断开 {len(self.alignments.breaks)} 处）"
        )

    def set_format(self, fmt) -> None:
        self.active_format = fmt
        if self.result:
            from ..core.text_diff import compute_text_diff

            self._apply_result(compute_text_diff(
                self.left_edit.text(), self.right_edit.text(), self.options,
                self.result.left_encoding, self.result.right_encoding,
                self._effective_rules(), self.alignments,
            ))

    def set_filter_state(self, text: str, case_sensitive: bool) -> None:
        block = self.query.blockSignals(True)
        self.query.setText(text or "")
        self.query.blockSignals(block)
        self.case_box.setChecked(bool(case_sensitive))
        self._find()
