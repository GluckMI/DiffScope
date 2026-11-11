"""扩展视图：缩略图、书签、查找替换、行详情、独立编辑器、三向合并、后台任务、标签页工作区。"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QFont, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.compare import compare
from ..core.errors import DiffScopeError
from ..core.fs_utils import read_text, write_text_atomic
from ..core.merge3 import merge3_text
from ..core.models import CompareMode, CompareOptions, CompareResult, STATUS_META, DiffStatus
from ..core.services import TaskController
from ..core.sync import SyncPlan, execute_sync
from ..core.workspace import Workspace, WorkspaceTab, load_workspace, save_workspace
from .dialogs import show_error, show_info
from .file_view import FileCompareView
from .folder_view import FolderCompareView
from .main_window import CompareWorker
from .dialog_kit import section_bar
from .theme import FONT_BASE, STATUS_COLORS, apply_role, default_mono_font
from .viewers import ViewerWidget, choose_viewer, create_viewer

MONO = QFont(default_mono_font(), FONT_BASE)


# ---------------------------------------------------------------- 缩略图 / 书签
from .widgets import Minimap  # 缩略图组件统一放在 widgets，避免循环依赖


class Bookmarks:
    """行号书签集合，支持在书签之间导航。"""

    def __init__(self):
        self.rows: set[int] = set()

    def toggle(self, row: int) -> bool:
        if row in self.rows:
            self.rows.discard(row)
            return False
        self.rows.add(row)
        return True

    def next(self, current: int) -> int | None:
        later = sorted(row for row in self.rows if row > current)
        return later[0] if later else (min(self.rows) if self.rows else None)

    def previous(self, current: int) -> int | None:
        earlier = sorted((row for row in self.rows if row < current), reverse=True)
        return earlier[0] if earlier else (max(self.rows) if self.rows else None)


# ---------------------------------------------------------------- 查找替换 / 行详情 / 独立编辑器
class FindReplaceDialog(QDialog):
    def __init__(self, parent, side: str = "left"):
        super().__init__(parent)
        self.setWindowTitle("查找与替换")
        self.side = side
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.find = QLineEdit()
        self.replace = QLineEdit()
        self.regex = QCheckBox("正则表达式")
        self.case = QCheckBox("区分大小写")
        self.scope = QComboBox()
        self.scope.addItems(["左侧", "右侧", "两侧"])
        self.scope.setCurrentIndex(0 if side == "left" else 1)
        for label, widget in (("查找", self.find), ("替换为", self.replace), ("作用范围", self.scope)):
            form.addRow(label, widget)
        layout.addLayout(form)
        layout.addWidget(self.regex)
        layout.addWidget(self.case)
        row = QHBoxLayout()
        find_btn = QPushButton("查找全部")
        replace_btn = QPushButton("替换全部")
        replace_btn.setProperty("accent", True)
        find_btn.clicked.connect(lambda: self.done(1))
        replace_btn.clicked.connect(lambda: self.done(2))
        row.addWidget(find_btn)
        row.addWidget(replace_btn)
        row.addStretch(1)
        layout.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)
        self.action = 0

    def show_dialog(self) -> int:
        self.action = self.exec()
        return self.action

    def pattern(self) -> re.Pattern | str:
        text = self.find.text()
        flags = 0 if self.case.isChecked() else re.IGNORECASE
        return re.compile(text, flags) if self.regex.isChecked() else text

    def target(self) -> str:
        return "both" if self.scope.currentIndex() == 2 else (
            "left" if self.scope.currentIndex() == 0 else "right")

    def apply(self, text: str) -> tuple[str, int]:
        pattern, replacement = self.pattern(), self.replace.text()
        if isinstance(pattern, re.Pattern):
            return pattern.subn(replacement, text)
        if not pattern:
            return text, 0
        return text.replace(pattern, replacement), text.count(pattern)


class LineDetailDialog(QDialog):
    """行详情：文本 / 十六进制 / 字符对齐。"""

    def __init__(self, parent, left: str, right: str):
        super().__init__(parent)
        self.setWindowTitle("行详情")
        self.resize(680, 360)
        layout = QVBoxLayout(self)
        table = QTableWidget(3, 3)
        table.setHorizontalHeaderLabels(["视图", "左侧", "右侧"])
        table.setVerticalHeaderLabels(["文本", "十六进制", "字符对齐"])
        for col, value in ((1, left), (2, right)):
            table.setItem(0, col, QTableWidgetItem(repr(value)))
            table.setItem(1, col, QTableWidgetItem(" ".join(f"{ord(ch):02X}" for ch in value)))
            table.setItem(2, col, QTableWidgetItem(" ".join(value)))
        table.setFont(MONO)
        layout.addWidget(table, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)


class StandaloneEditor(QDialog):
    """独立文本编辑器（可另存为、从剪贴板比较）。"""

    def __init__(self, parent, path: str = "", text: str = ""):
        super().__init__(parent)
        self.path = Path(path) if path else None
        self.setWindowTitle(f"文本编辑器 - {path or '未命名'}")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setFont(MONO)
        self.editor.setPlainText(text or (read_text(self.path).text if self.path and self.path.is_file() else ""))
        layout.addWidget(self.editor, 1)
        row = QHBoxLayout()
        open_btn = QPushButton("打开…")
        save_btn = QPushButton("保存")
        clipboard_btn = QPushButton("与剪贴板比较")
        open_btn.clicked.connect(self._open)
        save_btn.clicked.connect(self._save)
        clipboard_btn.clicked.connect(self._from_clipboard)
        row.addWidget(open_btn)
        row.addWidget(save_btn)
        row.addWidget(clipboard_btn)
        row.addStretch(1)
        layout.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)
        self.clipboard_text = ""

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开文本文件")
        if path:
            self.path = Path(path)
            self.setWindowTitle(f"文本编辑器 - {path}")
            self.editor.setPlainText(read_text(self.path).text)

    def _save(self) -> None:
        if not self.path:
            chosen = QFileDialog.getSaveFileName(self, "保存", "", "文本文件 (*.txt)")[0]
            if not chosen:
                return
            self.path = Path(chosen)
        path = self.path
        try:
            write_text_atomic(path, self.editor.toPlainText(), "utf-8", "\n")
        except DiffScopeError as exc:
            show_error(self, exc, "保存文本")
            return
        self.path = path
        show_info(self, "已保存", f"文件已写入：{path}")

    def _from_clipboard(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.clipboard_text = QApplication.clipboard().text()
        self.accept()


# ---------------------------------------------------------------- 三向合并
class Merge3View(QWidget):
    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self.paths: list[str] = []
        layout = QVBoxLayout(self)
        layout.addWidget(section_bar("输入与输出路径"))
        bar = QHBoxLayout()
        self.base_edit = QLineEdit()
        self.left_edit = QLineEdit()
        self.right_edit = QLineEdit()
        self.output_edit = QLineEdit()
        for label, widget in (("祖先", self.base_edit), ("左侧", self.left_edit),
                              ("右侧", self.right_edit), ("输出", self.output_edit)):
            bar.addWidget(QLabel(label))
            bar.addWidget(widget)
        load = QPushButton("加载并合并")
        load.setProperty("accent", True)
        load.clicked.connect(self.load_paths)
        bar.addWidget(load)
        layout.addLayout(bar)
        layout.addWidget(section_bar("三向窗格"))
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.panes: list[QPlainTextEdit] = []
        for title in ("共同祖先", "左侧", "右侧", "合并输出"):
            container = QWidget()
            box = QVBoxLayout(container)
            box.addWidget(QLabel(title))
            editor = QPlainTextEdit()
            editor.setFont(MONO)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            if title != "合并输出":
                editor.setReadOnly(True)
            box.addWidget(editor, 1)
            splitter.addWidget(container)
            self.panes.append(editor)
        layout.addWidget(splitter, 1)
        layout.addWidget(section_bar("冲突处理"))
        row = QHBoxLayout()
        self.conflicts = QListWidget()
        self.conflicts.setMaximumHeight(120)
        layout.addWidget(self.conflicts)
        for label, choice in (("取左侧", "left"), ("取右侧", "right"),
                              ("取祖先", "base"), ("保留双方", "both")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=choice: self._resolve(value))
            row.addWidget(button)
        self.favor = QComboBox()
        self.favor.addItems(["(不自动解决)", "left", "right", "base"])
        save = QPushButton("保存输出")
        save.clicked.connect(self.save_output)
        row.addWidget(self.favor)
        row.addWidget(save)
        layout.addLayout(row)

    def load_paths(self, paths: list[str] | None = None) -> None:
        if paths:
            self.base_edit.setText(paths[0])
            self.left_edit.setText(paths[1])
            self.right_edit.setText(paths[2])
            if len(paths) > 3:
                self.output_edit.setText(paths[3])
        specs = [self.base_edit.text(), self.left_edit.text(), self.right_edit.text()]
        if not all(specs):
            self.status_message.emit("请填写祖先、左侧、右侧三个文件路径")
            return
        try:
            texts = [read_text(Path(item)).text for item in specs]
            self.result = merge3_text(*texts, favor=self.favor.currentText()
                                      if self.favor.currentIndex() > 0 else "")
        except DiffScopeError as exc:
            show_error(self, exc, "三向合并")
            return
        for editor, text in zip(self.panes[:3], texts):
            editor.setPlainText(text)
        self.panes[3].setPlainText(self.result.output_text)
        self._refresh_conflicts()
        self.status_message.emit(
            f"三向合并：自动合并 {self.result.auto_merged} 处，冲突 {len(self.result.conflicts)} 处"
        )

    def _refresh_conflicts(self) -> None:
        self.conflicts.clear()
        if not self.result:
            return
        for index, chunk in enumerate(self.result.chunks):
            if chunk.is_conflict:
                self.conflicts.addItem(f"冲突 {index + 1}: {' / '.join(chunk.left[:2])} ↔ {' / '.join(chunk.right[:2])}")

    def _resolve(self, choice: str) -> None:
        if not self.result:
            return
        self.result.resolve_all(choice)
        self.panes[3].setPlainText(self.result.output_text)
        self._refresh_conflicts()
        self.status_message.emit(f"已按「{choice}」解决全部冲突")

    def save_output(self) -> None:
        if not self.result:
            return
        target = self.output_edit.text().strip()
        if not target:
            target = QFileDialog.getSaveFileName(self, "保存合并结果", "", "所有文件 (*.*)")[0]
        if not target:
            return
        try:
            write_text_atomic(Path(target), self.panes[3].toPlainText(), "utf-8", "\n")
        except DiffScopeError as exc:
            show_error(self, exc, "保存合并结果")
            return
        self.status_message.emit(f"合并结果已保存：{target}")


# ---------------------------------------------------------------- 后台任务
class _JobWorker(QObject):
    finished = Signal(object)
    progress = Signal(int, int, str)

    def __init__(self, actions, controller: TaskController):
        super().__init__()
        self.actions = actions
        self.controller = controller

    @Slot()
    def run(self) -> None:
        report = execute_sync(
            SyncPlan(actions=self.actions),
            progress=lambda done, total, label: self.progress.emit(done, total, label),
            cancel=lambda: self.controller.cancelled,
        )
        self.finished.emit(report)


class JobDialog(QDialog):
    """后台执行同步：进度、暂停/继续、取消。"""

    def __init__(self, parent, actions):
        super().__init__(parent)
        self.setWindowTitle("后台同步")
        self.resize(520, 200)
        self.actions = actions
        self.controller = TaskController()
        self.report = None
        layout = QVBoxLayout(self)
        self.label = QLabel("准备执行…")
        self.bar = QProgressBar()
        self.pause = QPushButton("暂停")
        self.pause.clicked.connect(self._toggle_pause)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self._cancel)
        row = QHBoxLayout()
        row.addWidget(self.pause)
        row.addWidget(cancel)
        row.addStretch(1)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addLayout(row)
        self._paused = False
        self._thread = QThread(self)
        self._worker = _JobWorker(actions, self.controller)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self.bar.setRange(0, len(actions) or 1)

    def start(self) -> None:
        self._thread.start()

    def _on_progress(self, done: int, total: int, label: str) -> None:
        self.controller.wait_if_paused()
        self.bar.setValue(done)
        self.label.setText(f"{done}/{total}：{label}")

    def _on_finished(self, report) -> None:
        self.report = report
        self.label.setText(f"完成：成功 {len(report.succeeded)} 项，失败 {len(report.failed)} 项")
        self.pause.setEnabled(False)

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self.controller.pause()
            self.pause.setText("继续")
        else:
            self.controller.resume()
            self.pause.setText("暂停")

    def _cancel(self) -> None:
        self.controller.cancel()
        self.label.setText("正在取消…")

    def closeEvent(self, event) -> None:  # pragma: no cover
        self.controller.cancel()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        super().closeEvent(event)


# ---------------------------------------------------------------- 标签页工作区
class SessionPane(QWidget):
    """工作区中的一个比较标签页。"""

    status_message = Signal(str)

    def __init__(self, parent=None, tab: WorkspaceTab | None = None):
        super().__init__(parent)
        self.tab = tab or WorkspaceTab()
        self.result: CompareResult | None = None
        self._thread: QThread | None = None
        self._worker: CompareWorker | None = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.left_edit = QLineEdit(self.tab.left)
        self.right_edit = QLineEdit(self.tab.right)
        self.left_edit.setPlaceholderText("左侧路径 / 数据源")
        self.right_edit.setPlaceholderText("右侧路径 / 数据源")
        self.mode = QComboBox()
        self.mode.addItems(["文件夹", "文件"])
        self.mode.setCurrentIndex(0 if self.tab.mode == "folders" else 1)
        run = QPushButton("比较")
        run.setProperty("accent", True)
        run.clicked.connect(self.start_compare)
        for widget in (self.left_edit, self.right_edit, self.mode, run):
            bar.addWidget(widget)
        layout.addLayout(bar)
        self.stack = QStackedWidget()
        self.folder_view = FolderCompareView()
        self.file_view = FileCompareView()
        self.viewer: ViewerWidget | None = None
        self.stack.addWidget(self.folder_view)
        self.stack.addWidget(self.file_view)
        layout.addWidget(self.stack, 1)
        self.folder_view.status_message.connect(self.status_message.emit)
        self.file_view.status_message.connect(self.status_message.emit)
        self.folder_view.open_pair.connect(self.open_text_pair)

    def start_compare(self) -> None:
        from ..core.vfs import open_vfs

        left, right = self.left_edit.text().strip(), self.right_edit.text().strip()
        if not left or not right:
            self.status_message.emit("请填写左右路径")
            return
        if self._thread and self._thread.isRunning():
            return
        try:
            left_vfs = open_vfs(left)
            right_vfs = open_vfs(right)
        except DiffScopeError as exc:
            show_error(self, exc, "打开数据源")
            return
        self._worker = CompareWorker(left, right, self.tab.options,
                                     left_vfs=left_vfs, right_vfs=right_vfs)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(lambda exc: show_error(self, exc, "比较"))
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_done(self, result: CompareResult) -> None:
        self.result = result
        if result.mode is CompareMode.FOLDERS:
            self.folder_view.set_result(result)
            self.stack.setCurrentIndex(0)
        else:
            self.file_view.load_pair(str(result.left_root), str(result.right_root), result.options)
            self.stack.setCurrentIndex(1)
        self.status_message.emit(f"完成：{result.stats.get('differences', 0)} 处差异")

    def open_text_pair(self, left: str, right: str, rel: str) -> None:
        from pathlib import Path

        try:
            data = Path(left).read_bytes() if left and Path(left).is_file() else b""
            kind = choose_viewer(rel or left, data, self.tab.viewer)
        except OSError:
            kind = "text"
        if kind == "text":
            self.file_view.load_pair(left, right, self.tab.options, rel_hint=rel, can_go_back=True)
            self.stack.setCurrentIndex(1)
        else:
            if self.viewer:
                self.stack.removeWidget(self.viewer)
                self.viewer.deleteLater()
            self.viewer = create_viewer(kind, self)
            self.viewer.status_message.connect(self.status_message.emit)
            self.stack.addWidget(self.viewer)
            self.stack.setCurrentWidget(self.viewer)
            right_data = Path(right).read_bytes() if right and Path(right).is_file() else b""
            self.viewer.set_data(data, right_data, left, right)

    def to_tab(self) -> WorkspaceTab:
        return WorkspaceTab(
            name=self.tab.name, mode="folders" if self.mode.currentIndex() == 0 else "files",
            left=self.left_edit.text(), right=self.right_edit.text(),
            viewer=self.tab.viewer, options=self.tab.options, filters=self.tab.filters,
        )


class WorkspaceWindow(QMainWindow):
    """标签页工作区：多个比较会话同时打开，整体保存/恢复。"""

    status_message = Signal(str)

    def __init__(self, workspace: Workspace | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DiffScope · 工作区")
        self.resize(1400, 860)
        self.workspace = workspace or Workspace()
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("就绪")
        self.status_message.connect(self.statusBar().showMessage)
        toolbar = self.addToolBar("工作区")
        for label, handler in (("新建标签", self.add_tab), ("保存工作区", self.save),
                               ("加载工作区", self.load), ("关闭标签", lambda: self.close_tab(self.tabs.currentIndex()))):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, fn=handler: fn())
            toolbar.addAction(action)
        action = QAction("新建标签 (Ctrl+T)", self)
        action.setShortcut(QKeySequence("Ctrl+T"))
        action.triggered.connect(lambda: self.add_tab())
        self.addAction(action)
        if not self.workspace.tabs:
            self.add_tab()
        else:
            for tab in self.workspace.tabs:
                self.add_tab(tab)
            self.tabs.setCurrentIndex(min(self.workspace.active, self.tabs.count() - 1))

    def add_tab(self, tab: WorkspaceTab | None = None) -> SessionPane:
        pane = SessionPane(self, tab)
        pane.status_message.connect(self.status_message.emit)
        index = self.tabs.addTab(pane, (tab.name if tab else f"会话 {self.tabs.count() + 1}"))
        self.tabs.setCurrentIndex(index)
        return pane

    def close_tab(self, index: int) -> None:
        if self.tabs.count() <= 1:
            return
        self.tabs.removeTab(index)

    def current(self) -> SessionPane | None:
        return self.tabs.currentWidget()

    def save(self) -> None:
        self.workspace.tabs = [self.tabs.widget(i).to_tab() for i in range(self.tabs.count())]
        self.workspace.active = self.tabs.currentIndex()
        path, _ = QFileDialog.getSaveFileName(self, "保存工作区", "", "工作区 (*.json)")
        if path:
            save_workspace(self.workspace, Path(path))
            self.status_message.emit(f"工作区已保存：{path}")

    def load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开工作区", "", "工作区 (*.json)")
        if not path:
            return
        try:
            self.workspace = load_workspace(Path(path))
        except DiffScopeError as exc:
            show_error(self, exc, "打开工作区")
            return
        while self.tabs.count():
            self.tabs.removeTab(0)
        for tab in self.workspace.tabs:
            self.add_tab(tab)
        self.status_message.emit(f"已加载工作区：{self.workspace.name}（{self.tabs.count()} 个标签）")

class FolderMerge3View(QWidget):
    """三向文件夹合并：祖先 + 左 + 右 → 输出目录。"""

    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(section_bar("输入与输出路径"))
        bar = QHBoxLayout()
        self.base_edit = QLineEdit()
        self.left_edit = QLineEdit()
        self.right_edit = QLineEdit()
        self.output_edit = QLineEdit()
        for label, widget in (("祖先", self.base_edit), ("左侧", self.left_edit),
                              ("右侧", self.right_edit), ("输出", self.output_edit)):
            bar.addWidget(QLabel(label))
            bar.addWidget(widget, 1)
        layout.addLayout(bar)
        layout.addWidget(section_bar("冲突策略与执行"))
        bar2 = QHBoxLayout()
        self.favor = QComboBox()
        self.favor.addItems(["(不自动解决冲突)", "left", "right", "base"])
        run = QPushButton("执行合并")
        run.setProperty("accent", True)
        run.clicked.connect(self.run_merge)
        bar2.addWidget(QLabel("冲突策略"))
        bar2.addWidget(self.favor)
        bar2.addWidget(run)
        bar2.addStretch(1)
        layout.addLayout(bar2)
        self.conflicts = QListWidget()
        self.conflicts.setMaximumHeight(120)
        layout.addWidget(self.conflicts)
        self.hint = QLabel(
            "说明：以共同祖先为基准合并左右两个目录的变更到输出目录；"
            "双方一致变更自动合并，冲突按策略处理，执行前会列出完整操作清单。"
        )
        self.hint.setWordWrap(True)
        apply_role(self.hint, "note")
        layout.addWidget(self.hint)

    def run_merge(self) -> None:
        from pathlib import Path

        from PySide6.QtWidgets import QApplication

        from ..core.compare import compare
        from ..core.errors import DiffScopeError
        from ..core.merge3 import merge3_folders
        from ..core.sync import SyncPlan, execute_sync
        from .dialogs import ConfirmActionsDialog, SyncResultDialog, show_info

        base, left, right = self.base_edit.text().strip(), self.left_edit.text().strip(), self.right_edit.text().strip()
        output = self.output_edit.text().strip()
        if not all([base, left, right, output]):
            show_info(self, "路径不完整", "请填写祖先、左侧、右侧与输出四个目录。")
            return
        self.status_message.emit("正在比较 祖先↔左侧 …")
        QApplication.processEvents()
        try:
            diff_left = compare(base, left)
            self.status_message.emit("正在比较 祖先↔右侧 …")
            QApplication.processEvents()
            diff_right = compare(base, right)
            actions, conflicts = merge3_folders(
                diff_left, diff_right, Path(output),
                favor=self.favor.currentText() if self.favor.currentIndex() > 0 else "",
            )
        except DiffScopeError as exc:
            from .dialogs import show_error

            show_error(self, exc, "三向文件夹合并")
            return
        self.conflicts.clear()
        for rel in conflicts:
            self.conflicts.addItem(f"冲突：{rel}")
        if not actions:
            show_info(self, "无需合并", "左右两侧相对祖先没有需要写入输出的变更。")
            return
        favor_label = self.favor.currentText() if self.favor.currentIndex() > 0 else "未处理冲突"
        intro = (f"冲突 {len(conflicts)} 处（{favor_label}）；共 {len(actions)} 项输出操作。"
                 + ("未自动解决的冲突未包含在操作中。" if conflicts and self.favor.currentIndex() == 0 else ""))
        if not ConfirmActionsDialog.ask(self, "操作前确认 · 三向文件夹合并", intro, actions, danger=True):
            self.status_message.emit("三向文件夹合并已取消")
            return

        def progress(done, total, label):
            self.status_message.emit(f"合并中 {done}/{total}：{label}")
            QApplication.processEvents()

        report = execute_sync(SyncPlan(actions=actions), progress=progress)
        SyncResultDialog(self, report, len(actions)).exec()
        self.status_message.emit(
            f"三向文件夹合并完成：成功 {len(report.succeeded)} 项，冲突 {len(conflicts)} 处"
        )

