"""主窗口：路径选择、后台比较、状态汇总、会话与全局快捷键。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, QByteArray, QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core.compare import compare
from ..core.errors import Cancelled, DiffScopeError
from ..core.models import CompareMode, CompareOptions, CompareResult, DiffStatus
from ..core.file_formats import FormatRegistry
from ..core.i18n import tr
from ..core.services import Settings
from ..core.rules import CompareRules
from ..core.alignment import AlignmentConfig
from ..core.session import (
    Session,
    last_session_path,
    list_sessions,
    load_session,
    save_last_session,
    save_session,
    validate_session,
)
from .dialogs import (
    ConfirmActionsDialog,
    OptionsDialog,
    RecentSessionsDialog,
    ask_session_name,
    choose_session_path,
    confirm,
    show_about,
    show_error,
    show_info,
    show_warning,
)
from .file_view import FileCompareView
from .folder_view import FolderCompareView
from .theme import MUTED, STYLESHEET
from .widgets import SummaryBar


class CompareWorker(QObject):
    finished = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    progress = Signal(int, int, str)

    def __init__(self, left: str, right: str, options: CompareOptions,
                 left_vfs=None, right_vfs=None, alignment=None, rules=None):
        super().__init__()
        self.left = left
        self.right = right
        self.options = options
        self.left_vfs = left_vfs
        self.right_vfs = right_vfs
        self.alignment = alignment
        self.rules = rules
        self._cancel = False

    def stop(self) -> None:
        self._cancel = True

    @Slot()
    def run(self) -> None:
        try:
            result = compare(
                self.left,
                self.right,
                self.options,
                progress=lambda done, total, label: self.progress.emit(done, total, label),
                cancel=lambda: self._cancel,
                left_vfs=self.left_vfs,
                right_vfs=self.right_vfs,
                alignment=self.alignment,
                rules=self.rules,
            )
            self.finished.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:  # 所有异常都交给界面层给出可读提示
            self.failed.emit(exc)


class WelcomePanel(QWidget):
    open_recent = Signal(str, str)
    resume_requested = Signal()
    delete_recent = Signal(int)  # 参数：待删除条目的行号
    clear_recent = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 34px; font-weight: 700; color: #58b1f7;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("文件与文件夹对比 · 差异查看 · 合并同步")
        sub.setStyleSheet(f"font-size: 14px; color: {MUTED};")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(28)
        # 上次会话快捷恢复入口（存在上次会话时才显示）
        self.btn_resume = QPushButton()
        self.btn_resume.setProperty("accent", True)
        self.btn_resume.setMinimumWidth(420)
        self.btn_resume.setMinimumHeight(38)
        self.btn_resume.setToolTip("恢复上次关闭时的对比会话（含比较选项、过滤器与窗口位置）")
        self.btn_resume.clicked.connect(self.resume_requested.emit)
        self.btn_resume.setVisible(False)
        layout.addWidget(self.btn_resume, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(6)
        self.btn_folders = QPushButton("选择两个文件夹开始比较")
        self.btn_files = QPushButton("选择两个文件开始比较")
        self.btn_session = QPushButton("打开已保存的会话")
        self.btn_recent = QPushButton("最近的会话")
        for button in (self.btn_folders, self.btn_files):
            button.setProperty("accent", True)
            button.setMinimumWidth(260)
            button.setMinimumHeight(38)
        for button in (self.btn_session, self.btn_recent):
            button.setMinimumWidth(260)
            button.setMinimumHeight(34)
        for button in (self.btn_folders, self.btn_files, self.btn_session, self.btn_recent):
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(24)
        tip = QLabel(
            "也可以在上方工具栏直接输入左右路径后点击“开始比较”。\n"
            "F7 / F8 逐项跳转差异，Ctrl+F 查找，Ctrl+Shift+S 保存会话。"
        )
        tip.setStyleSheet(f"color: {MUTED};")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tip)
        self.recent_caption = QLabel("最近比较（点击重开 · 右键删除）")
        self.recent_caption.setStyleSheet(f"color: {MUTED};")
        self.recent_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_caption.setVisible(False)
        self.btn_clear_recent = QPushButton("清空全部")
        self.btn_clear_recent.setFlat(True)
        self.btn_clear_recent.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_recent.setStyleSheet(
            f"color: {MUTED}; border: none; padding: 0 4px;"
        )
        self.btn_clear_recent.setToolTip("删除全部最近比较记录")
        self.btn_clear_recent.setVisible(False)
        self.btn_clear_recent.clicked.connect(self.clear_recent.emit)
        cap_row = QHBoxLayout()
        cap_row.addStretch(1)
        cap_row.addWidget(self.recent_caption)
        cap_row.addWidget(self.btn_clear_recent)
        cap_row.addStretch(1)
        layout.addSpacing(12)
        layout.addLayout(cap_row)
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(140)
        self.recent_list.setVisible(False)
        self.recent_list.itemClicked.connect(self._emit_recent)
        self.recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self._recent_menu)
        layout.addWidget(self.recent_list)

    def set_recent(self, items: list) -> None:
        self.recent_list.clear()
        for entry in items:
            left, right = str(entry.get("left", "")), str(entry.get("right", ""))
            item = QListWidgetItem(f"{left}   ⇄   {right}")
            item.setData(Qt.ItemDataRole.UserRole, (left, right))
            item.setToolTip("点击重开该比较；右键可删除此条记录")
            self.recent_list.addItem(item)
        has = self.recent_list.count() > 0
        self.recent_list.setVisible(has)
        self.recent_caption.setVisible(has)
        self.btn_clear_recent.setVisible(has)

    def _recent_menu(self, pos) -> None:
        item = self.recent_list.itemAt(pos)
        menu = QMenu(self)
        act_delete = menu.addAction("删除此条记录") if item is not None else None
        act_clear = menu.addAction("清空全部记录")
        chosen = menu.exec(self.recent_list.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_delete and item is not None:
            self.delete_recent.emit(self.recent_list.row(item))
        elif chosen is act_clear:
            self.clear_recent.emit()

    def set_resume_session(self, left: str, right: str) -> None:
        """显示「恢复上次会话」按钮；路径过长时中间省略。"""

        def clip(p: str) -> str:
            return p if len(p) <= 44 else p[:22] + "…" + p[-20:]

        self.btn_resume.setText(f"恢复上次会话：{clip(left)}  ⇄  {clip(right)}")
        self.btn_resume.setVisible(True)

    def _emit_recent(self, item: QListWidgetItem) -> None:
        left, right = item.data(Qt.ItemDataRole.UserRole)
        self.open_recent.emit(left, right)


class MainWindow(QMainWindow):
    def __init__(self, argv: list[str] | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{__version__}  ·  文件与文件夹对比")
        self.resize(1440, 880)
        self.options = CompareOptions()
        self.settings = Settings()
        self.rules = CompareRules()
        self.alignment = AlignmentConfig()
        self.formats = FormatRegistry.load()
        self.result: CompareResult | None = None
        self._argv = list(argv or [])
        self._workspace_window = None
        self._extra_windows: list = []
        self._source_options: dict = {}
        self.session_name = "未命名会话"
        self._thread: QThread | None = None
        self._worker: CompareWorker | None = None
        self._switch_view = True
        self._restore_diff = 0
        self._restore_filters: dict = {}
        self._restore_geometry: str = ""
        self._restored_once = False
        self._build_ui()
        self._build_actions()
        self._apply_settings()
        self._apply_argv()

    # ---- 界面 -----------------------------------------------------
    def _build_ui(self) -> None:
        toolbar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        toolbar.addWidget(QLabel(tr("模式")))
        self.mode_box = QComboBox()
        self.mode_box.addItem("文件夹对比", CompareMode.FOLDERS.value)
        self.mode_box.addItem("文件对比", CompareMode.FILES.value)
        self.mode_box.setMinimumWidth(110)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        toolbar.addWidget(self.mode_box)
        toolbar.addSeparator()

        self.left_edit = QLineEdit()
        self.right_edit = QLineEdit()
        self.left_edit.setPlaceholderText("左侧文件或文件夹路径…")
        self.right_edit.setPlaceholderText("右侧文件或文件夹路径…")
        for edit in (self.left_edit, self.right_edit):
            edit.setMinimumWidth(260)
            edit.returnPressed.connect(self.start_compare)
            edit.textChanged.connect(lambda text, widget=edit: widget.setToolTip(text))
            edit.setAcceptDrops(True)
            edit.installEventFilter(self)
        self.btn_browse_left = QPushButton("浏览…")
        self.btn_browse_right = QPushButton("浏览…")
        self.btn_browse_left.clicked.connect(lambda: self._browse("left"))
        self.btn_browse_right.clicked.connect(lambda: self._browse("right"))
        toolbar.addWidget(QLabel(tr("左")))
        toolbar.addWidget(self.left_edit)
        toolbar.addWidget(self.btn_browse_left)
        toolbar.addWidget(QLabel(tr("右")))
        toolbar.addWidget(self.right_edit)
        toolbar.addWidget(self.btn_browse_right)

        self.btn_compare = QPushButton(tr("开始比较"))
        self.btn_compare.setProperty("accent", True)
        self.btn_compare.clicked.connect(lambda: self.start_compare())
        toolbar.addWidget(self.btn_compare)
        self.btn_refresh = QPushButton(tr("刷新"))
        self.btn_refresh.clicked.connect(lambda: self.start_compare())
        toolbar.addWidget(self.btn_refresh)
        self.btn_options = QPushButton(tr("比较选项"))
        self.btn_options.clicked.connect(self.open_options)
        toolbar.addWidget(self.btn_options)
        toolbar.addSeparator()
        self.btn_save_session = QPushButton(tr("保存会话"))
        self.btn_load_session = QPushButton(tr("会话…"))
        self.btn_save_session.clicked.connect(self.save_session_as)
        self.btn_load_session.clicked.connect(self.show_recent_sessions)
        toolbar.addWidget(self.btn_save_session)
        toolbar.addWidget(self.btn_load_session)

        self.stack = QStackedWidget()
        self.welcome = WelcomePanel()
        self.folder_view = FolderCompareView()
        self.file_view = FileCompareView()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.folder_view)
        self.stack.addWidget(self.file_view)
        self.setCentralWidget(self.stack)

        self.summary = SummaryBar()
        status = QStatusBar()
        status.setSizeGripEnabled(False)
        status.addWidget(self.summary)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(f"color: {MUTED};")
        status.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(130)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)
        self.setStatusBar(status)

        self.welcome.btn_folders.clicked.connect(lambda: self.choose_pair(CompareMode.FOLDERS))
        self.welcome.btn_files.clicked.connect(lambda: self.choose_pair(CompareMode.FILES))
        self.welcome.btn_session.clicked.connect(self.open_session_file)
        self.welcome.btn_recent.clicked.connect(self.show_recent_sessions)
        self.welcome.resume_requested.connect(self._resume_last_session)
        self.folder_view.open_pair.connect(self.open_text_pair)
        self.folder_view.drop_paths.connect(self._on_folder_drop)
        self.folder_view.refresh_requested.connect(lambda: self.start_compare(switch_view=False))
        self.folder_view.status_message.connect(self.set_status)
        self.folder_view.filter_changed.connect(self._on_folder_filter_changed)
        self.file_view.status_message.connect(self.set_status)
        self.file_view.back_requested.connect(self.back_to_folders)
        self.file_view.saved.connect(lambda: self.start_compare(switch_view=False))
        self.summary.filter_changed.connect(self.apply_status_filter)
        self.welcome.open_recent.connect(self._reopen_recent)
        self.welcome.delete_recent.connect(self._delete_recent)
        self.welcome.clear_recent.connect(self._clear_recent)
        self.welcome.set_recent(list(self.settings.get("recent_comparisons") or []))

    def _record_recent(self, left: str, right: str) -> None:
        items = [e for e in (self.settings.get("recent_comparisons") or [])
                 if not (e.get("left") == left and e.get("right") == right)]
        items.insert(0, {"left": left, "right": right})
        self.settings.set("recent_comparisons", items[:10])
        try:
            self.settings.save()
        except Exception:
            pass
        self.welcome.set_recent(items[:10])

    def _save_recent(self, items: list) -> None:
        self.settings.set("recent_comparisons", items)
        try:
            self.settings.save()
        except Exception:
            pass
        self.welcome.set_recent(items)

    def _delete_recent(self, row: int) -> None:
        items = list(self.settings.get("recent_comparisons") or [])
        if 0 <= row < len(items):
            items.pop(row)
            self._save_recent(items)
            self.set_status("已删除该条比较记录")

    def _clear_recent(self) -> None:
        items = list(self.settings.get("recent_comparisons") or [])
        if not items:
            return
        if not confirm(
            self,
            "清空比较记录",
            f"将删除全部 {len(items)} 条最近比较记录，该操作不可撤销。",
            danger=True,
        ):
            return
        self._save_recent([])
        self.set_status("已清空全部比较记录")

    def _reopen_recent(self, left: str, right: str) -> None:
        self.left_edit.setText(left)
        self.right_edit.setText(right)
        self.start_compare()

    def open_templates(self) -> None:
        from .dialogs_ext import TemplateDialog

        dialog = TemplateDialog(self, self.rules, self.options)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.applied is not None:
            self.rules, self.options = dialog.applied
            self.set_status("已应用比较模板（规则与选项已更新）")

    def open_git_diff(self) -> None:
        from .dialogs_ext import GitDiffDialog

        dialog = GitDiffDialog(self)
        dialog.compare_requested.connect(self._compare_git_pair)
        dialog.exec()

    def _compare_git_pair(self, left: str, right: str) -> None:
        self.left_edit.setText(left)
        self.right_edit.setText(right)
        self.start_compare()

    def open_sqlite_diff(self) -> None:
        from .dialogs_ext import SqliteDiffDialog

        SqliteDiffDialog(self).exec()

    def _build_actions(self) -> None:
        menubar = self.menuBar()
        file_menu: QMenu = menubar.addMenu(tr("文件"))
        ops_menu: QMenu = menubar.addMenu(tr("操作"))
        view_menu: QMenu = menubar.addMenu(tr("视图"))
        help_menu: QMenu = menubar.addMenu(tr("帮助"))

        def add(menu, text, shortcut, handler):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda checked=False, fn=handler: fn())
            menu.addAction(action)
            self.addAction(action)
            return action

        add(file_menu, tr("打开对比…"), "Ctrl+O", self.choose_pair_current)
        add(file_menu, tr("保存会话…"), "Ctrl+Shift+S", self.save_session_as)
        add(file_menu, tr("打开会话文件…"), "Ctrl+Shift+O", self.open_session_file)
        add(file_menu, tr("最近的会话…"), "", self.show_recent_sessions)
        file_menu.addSeparator()
        add(file_menu, tr("退出"), "Alt+F4", self.close)

        add(ops_menu, tr("重新比较"), "Ctrl+R", self.start_compare)
        add(ops_menu, tr("上一处差异"), "F7", self.previous_difference)
        add(ops_menu, tr("下一处差异"), "F8", self.next_difference)
        add(ops_menu, tr("保存修改"), "Ctrl+S", self.save_current)
        add(ops_menu, tr("查找"), "Ctrl+F", self.focus_search)
        add(ops_menu, tr("下一个匹配"), "F3", self.next_match)
        ops_menu.addSeparator()
        add(ops_menu, tr("合并当前差异 左→右"), "Ctrl+Right", lambda: self.merge_current("L2R"))
        add(ops_menu, tr("合并当前差异 右→左"), "Ctrl+Left", lambda: self.merge_current("R2L"))
        add(ops_menu, tr("选择性合并差异块…"), "", self.file_view.selective_merge)
        ops_menu.addSeparator()
        add(ops_menu, tr("从备份恢复…"), "", self.file_view.restore_backup)

        add(view_menu, tr("比较选项…"), "", self.open_options)
        add(view_menu, tr("展开全部"), "", self.folder_view.left.expandAll)
        add(view_menu, tr("折叠全部"), "", self.folder_view._collapse_all)
        add(view_menu, tr("返回目录对比"), "Esc", self.back_to_folders)
        add(help_menu, tr("关于"), "", lambda: show_about(self))
        add(help_menu, tr("帮助文档"), "", self.open_help)

        ext_menu: QMenu = menubar.addMenu(tr("扩展"))
        # 保住 Python 引用，避免 QMenu 包装对象被提前回收
        self._menus = {"file": file_menu, "ops": ops_menu, "view": view_menu,
                       "help": help_menu, "ext": ext_menu}
        add(ext_menu, tr("打开数据源…"), "", self.open_source_dialog)
        add(ext_menu, tr("创建文件系统快照…"), "", self.create_snapshot_dialog)
        ext_menu.addSeparator()
        add(ext_menu, tr("比较规则…"), "", self.open_rules)
        add(ext_menu, tr("比较模板…"), "", self.open_templates)
        add(ext_menu, tr("文件名对齐覆盖…"), "", self.open_alignment)
        add(ext_menu, tr("同步模式…"), "", self.open_sync_mode)
        add(ext_menu, tr("三向合并…"), "", self.open_merge3)
        add(ext_menu, tr("三向文件夹合并…"), "", self.open_folder_merge3)
        ext_menu.addSeparator()
        for label, kind in (("表格比较…", "table"), ("十六进制比较…", "hex"),
                            ("图片比较…", "image"), ("标签/版本比较…", "tags"),
                            ("注册表比较…", "registry")):
            add(ext_menu, tr(label), "", lambda value=kind: self.open_viewer(value))
        ext_menu.addSeparator()
        add(ext_menu, tr("Git 仓库改动比较…"), "", self.open_git_diff)
        add(ext_menu, tr("SQLite 数据比较…"), "", self.open_sqlite_diff)
        ext_menu.addSeparator()
        add(ext_menu, tr("生成报告…"), "", self.open_report)
        add(ext_menu, tr("脚本处理器…"), "", self.open_script)
        add(ext_menu, tr("标签页工作区"), "Ctrl+T", self.open_workspace)
        add(ext_menu, tr("独立文本编辑器"), "", self.open_standalone_editor)
        add(ext_menu, tr("比较剪贴板内容"), "", self.compare_clipboard)
        ext_menu.addSeparator()
        add(ext_menu, tr("查找与替换…"), "Ctrl+H", self.file_view.find_replace)
        add(ext_menu, tr("行详情"), "", self.file_view.line_detail)
        add(ext_menu, tr("导出差异清单…"), "", self.file_view.export_diff_list)
        add(ext_menu, tr("切换书签"), "Ctrl+B", self.file_view.toggle_bookmark)
        add(ext_menu, tr("下一个书签"), "", lambda: self.file_view.goto_bookmark(True))
        ext_menu.addSeparator()
        add(ext_menu, tr("文件格式…"), "", self.open_formats)
        ext_menu.addSeparator()
        add(ext_menu, tr("手动对齐当前行"), "", self.file_view.align_current_lines)
        add(ext_menu, tr("在当前位置断开对齐"), "", self.file_view.break_alignment)
        add(ext_menu, tr("清除手动对齐"), "", self.file_view.clear_alignments)
        ext_menu.addSeparator()
        add(ext_menu, tr("设置…"), "", self.open_settings)
        add(ext_menu, tr("切换浅色 / 深色主题"), "", self.toggle_theme)

    # ---- 状态与消息 -----------------------------------------------
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _show_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.btn_compare.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)
        if busy:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)

    def _on_progress(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        else:
            self.progress.setRange(0, 0)
        self.set_status(f"正在比较… {label}")

    # ---- 路径与比较 -----------------------------------------------
    def current_mode(self) -> CompareMode:
        return CompareMode(self.mode_box.currentData())

    def _on_mode_changed(self, *_args) -> None:
        self.left_edit.setPlaceholderText(
            "左侧文件夹路径…" if self.current_mode() is CompareMode.FOLDERS else "左侧文件路径…"
        )
        self.right_edit.setPlaceholderText(
            "右侧文件夹路径…" if self.current_mode() is CompareMode.FOLDERS else "右侧文件路径…"
        )

    def _browse(self, side: str) -> None:
        current = self.left_edit.text() if side == "left" else self.right_edit.text()
        start = current if Path(current).exists() else str(Path.home())
        if self.current_mode() is CompareMode.FOLDERS:
            path = QFileDialog.getExistingDirectory(
                self, f"选择{'左' if side == 'left' else '右'}侧文件夹", start
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, f"选择{'左' if side == 'left' else '右'}侧文件", start, "所有文件 (*.*)"
            )
        if path:
            (self.left_edit if side == "left" else self.right_edit).setText(path)

    def choose_pair_current(self) -> None:
        self.choose_pair(self.current_mode())

    def choose_pair(self, mode: CompareMode) -> None:
        """连续选择左右两个路径，选完即开始比较。"""
        self.mode_box.setCurrentIndex(0 if mode is CompareMode.FOLDERS else 1)
        if mode is CompareMode.FOLDERS:
            first = QFileDialog.getExistingDirectory(self, "选择左侧文件夹", str(Path.home()))
            second = QFileDialog.getExistingDirectory(self, "选择右侧文件夹", str(Path.home())) if first else ""
        else:
            first, _ = QFileDialog.getOpenFileName(self, "选择左侧文件", str(Path.home()), "所有文件 (*.*)")
            second = (
                QFileDialog.getOpenFileName(self, "选择右侧文件", str(Path.home()), "所有文件 (*.*)")[0]
                if first
                else ""
            )
        if not first or not second:
            self.set_status("已取消选择，未更改当前对比")
            return
        self.left_edit.setText(first)
        self.right_edit.setText(second)
        self.start_compare()

    def start_compare(self, switch_view: bool = True) -> None:
        left, right = self.left_edit.text().strip(), self.right_edit.text().strip()
        if not left or not right:
            show_warning(
                self,
                "路径不完整",
                "请同时填写左侧与右侧路径后再比较。",
                "可点击“浏览…”选择，也可以直接在输入框中粘贴完整路径。",
            )
            return
        if not self._check_unsaved():
            return
        if self._thread and self._thread.isRunning():
            self.set_status("上一次比较仍在进行，请稍候")
            return
        self._switch_view = switch_view
        self._show_busy(True)
        self.set_status("正在校验路径并比较…")
        try:
            left_vfs = self._make_vfs(left, "left")
            right_vfs = self._make_vfs(right, "right")
        except DiffScopeError as exc:
            show_error(self, exc, "打开数据源")
            self._show_busy(False)
            return
        self._worker = CompareWorker(
            left, right, self.options,
            left_vfs=left_vfs, right_vfs=right_vfs,
            alignment=self.alignment, rules=self.rules,
        )
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_compare_done)
        self._worker.failed.connect(self._on_compare_failed)
        self._worker.cancelled.connect(self._on_compare_cancelled)
        for signal in (self._worker.finished, self._worker.failed, self._worker.cancelled):
            signal.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self, *_args) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None
        self._show_busy(False)

    def _on_compare_done(self, result: CompareResult) -> None:
        self.result = result
        self._record_recent(str(result.left_root), str(result.right_root))
        self.summary.set_stats(result.stats)
        if self._switch_view:
            if result.mode is CompareMode.FOLDERS:
                self.folder_view.set_result(result)
                self.stack.setCurrentIndex(1)
            else:
                self.stack.setCurrentIndex(2)
                self.file_view.load_pair_async(
                    str(result.left_root), str(result.right_root), result.options, can_go_back=False,
                    done=lambda ok: None if ok else self.set_status("文件内容读取失败，请查看上方提示"),
                )
        elif result.mode is CompareMode.FOLDERS:
            self.folder_view.set_result(result)

        message = (
            f"完成：{result.stats.get('differences', 0)} 处差异 / "
            f"{result.stats.get('files', 0)} 个文件，用时 {result.elapsed_ms} ms"
        )
        if result.errors:
            message += f"，{len(result.errors)} 项读取问题"
        self.set_status(message)
        if result.errors:
            detail = "\n".join(result.errors[:12])
            if len(result.errors) > 12:
                detail += f"\n… 另有 {len(result.errors) - 12} 项"
            show_warning(
                self,
                "部分条目无法读取",
                f"有 {len(result.errors)} 个条目在比较时出现问题，已在列表中标记为“读取失败”。",
                detail,
            )
        if self._restore_filters and self._switch_view:
            self._apply_restore_filters()
        if self._restore_diff and self._switch_view:
            if result.mode is CompareMode.FOLDERS:
                self.folder_view.goto_difference(self._restore_diff)
            else:
                self.file_view.goto_block(self._restore_diff)
        self._restore_diff = 0
        self._restore_filters = {}

    def _on_compare_failed(self, exc: Exception) -> None:
        show_error(self, exc, "比较路径")
        self.set_status(f"比较失败：{getattr(exc, 'message', str(exc))}")

    def _on_compare_cancelled(self) -> None:
        self.set_status("比较已取消")

    def open_text_pair(self, left: str, right: str, rel: str) -> None:
        if not self._check_unsaved():
            return
        from .viewers import choose_viewer, create_viewer

        fmt = self.formats.match(rel or left) if rel or left else None
        try:
            data = Path(left).read_bytes() if Path(left).is_file() else b""
            kind = fmt.viewer if fmt and fmt.viewer != "auto" else choose_viewer(rel or left, data)
        except OSError:
            kind = "text"
        self.file_view.set_format(fmt)
        if kind != "text":
            viewer = create_viewer(kind, self)
            viewer.status_message.connect(self.set_status)
            right_data = Path(right).read_bytes() if Path(right).is_file() else b""
            viewer.set_data(data, right_data, left, right)
            self._show_widget_window(viewer, f"{viewer.viewer_name}：{rel}")
            self.set_status(f"已用 {viewer.viewer_name} 打开：{rel}")
            return
        self.stack.setCurrentIndex(2)
        self.set_status(f"正在加载 {rel} …")
        self.file_view.load_pair_async(
            left, right, self.options, rel_hint=rel, can_go_back=True,
            done=lambda ok: self._on_text_pair_opened(ok, rel),
        )

    def _on_text_pair_opened(self, ok: bool, rel: str) -> None:
        if ok:
            self.summary.set_stats(self._text_stats())
            self.set_status(f"已打开文本对比：{rel}")
        else:
            self.set_status("文件内容读取失败，请查看上方提示")
    def back_to_folders(self) -> None:
        if self.stack.currentIndex() != 2 or not self.file_view._can_go_back:
            return
        if not self._check_unsaved():
            return
        self.stack.setCurrentIndex(1)
        if self.result:
            self.summary.set_stats(self.result.stats)
        self.set_status("已返回目录对比")

    def _text_stats(self) -> dict[str, int]:
        stats = {s.value: 0 for s in DiffStatus}
        result = self.file_view.result
        if result:
            for row in result.rows:
                if row.op == "replace":
                    stats[DiffStatus.MODIFIED.value] += 1
                elif row.op == "delete":
                    stats[DiffStatus.LEFT_ONLY.value] += 1
                elif row.op == "insert":
                    stats[DiffStatus.RIGHT_ONLY.value] += 1
                else:
                    stats[DiffStatus.SAME.value] += 1
        stats["differences"] = len(self.file_view.blocks)
        stats["files"] = 2
        stats["folders"] = 0
        return stats

    # ---- 筛选与导航 -----------------------------------------------
    def apply_status_filter(self, key) -> None:
        if self.stack.currentIndex() == 1:
            text = self.folder_view.current_filter()[0]
            self.folder_view.set_filter_state(text, key)
        elif self.stack.currentIndex() == 2:
            self.file_view.set_status_filter(key)
            self.summary.set_active(key)
            count = len(self.file_view.visible_blocks())
            self.set_status(f"已筛选差异类型，当前可导航 {count} 处")
        else:
            self.set_status("请先开始一次对比")

    def _on_folder_filter_changed(self, _text, status_key) -> None:
        self.summary.set_active(status_key)

    def next_difference(self) -> None:
        if self.stack.currentIndex() == 1:
            self.folder_view.next_difference()
        elif self.stack.currentIndex() == 2:
            self.file_view.next_difference()
        else:
            self.set_status("请先开始一次对比")

    def previous_difference(self) -> None:
        if self.stack.currentIndex() == 1:
            self.folder_view.previous_difference()
        elif self.stack.currentIndex() == 2:
            self.file_view.previous_difference()
        else:
            self.set_status("请先开始一次对比")

    def focus_search(self) -> None:
        if self.stack.currentIndex() == 2:
            self.file_view.focus_search()
        elif self.stack.currentIndex() == 1:
            self.folder_view.search.setFocus()
            self.folder_view.search.selectAll()
        else:
            self.set_status("请先开始一次对比")

    def next_match(self) -> None:
        if self.stack.currentIndex() == 2:
            self.file_view.next_match()

    def merge_current(self, tag: str) -> None:
        if self.stack.currentIndex() != 2:
            self.set_status("合并仅在文本对比视图中可用")
            return
        from ..core.merge import MergeDirection

        direction = (
            MergeDirection.LEFT_TO_RIGHT if tag == "L2R" else MergeDirection.RIGHT_TO_LEFT
        )
        self.file_view.merge_current(direction)

    def save_current(self) -> None:
        if self.stack.currentIndex() == 2:
            self.file_view.save_modified()
        else:
            self.set_status("当前视图没有需要保存的文本内容")

    # ---- 选项与确认 -----------------------------------------------
    def open_options(self) -> None:
        dialog = OptionsDialog(self, self.options)
        if dialog.exec():
            self.options = dialog.values()
            self.set_status("比较选项已更新，点击“刷新”重新比较以生效")

    def _check_unsaved(self) -> bool:
        if self.stack.currentIndex() == 2 and self.file_view._dirty:
            return confirm(
                self,
                "存在未保存的修改",
                "当前文本有未保存的编辑或合并结果，离开该视图会丢失这些改动。是否继续？",
                "建议先执行“保存修改”（Ctrl+S）。",
                danger=True,
            )
        return True

    # ---- 会话 -----------------------------------------------------
    def _current_session(self) -> Session:
        folder_text, folder_status = self.folder_view.current_filter()
        file_query, file_case = self.file_view.current_filter()
        diff_index = (
            self.folder_view.current_difference_index()
            if self.stack.currentIndex() == 1
            else self.file_view.current_difference_index()
            if self.stack.currentIndex() == 2
            else 0
        )
        return Session(
            name=self.session_name,
            mode=self.current_mode().value,
            left=self.left_edit.text().strip(),
            right=self.right_edit.text().strip(),
            options=self.options,
            filters={
                "folder_text": folder_text,
                "folder_status": folder_status.value if isinstance(folder_status, DiffStatus) else folder_status,
                "file_query": file_query,
                "file_case": file_case,
            },
            current_diff=max(diff_index, 0),
            geometry={"main": bytes(self.saveGeometry()).hex()},
        )

    def save_session_as(self) -> None:
        name = ask_session_name(self, self.session_name)
        if not name:
            return
        try:
            path = save_session(self._current_session_for(name))
        except DiffScopeError as exc:
            show_error(self, exc, "保存会话")
            return
        self.session_name = name
        self.setWindowTitle(f"{APP_NAME}  v{__version__}  ·  {name}")
        self.set_status(f"会话已保存：{path}")

    def _current_session_for(self, name: str) -> Session:
        session = self._current_session()
        session.name = name
        return session

    def open_session_file(self) -> None:
        path = choose_session_path(self, save=False)
        if not path:
            return
        try:
            session = load_session(path)
        except DiffScopeError as exc:
            show_error(self, exc, "打开会话")
            return
        self.restore_session(session)

    def show_recent_sessions(self) -> None:
        sessions = list_sessions()
        dialog = RecentSessionsDialog(self, sessions)
        if dialog.exec() and dialog.selected:
            try:
                self.restore_session(load_session(dialog.selected))
            except DiffScopeError as exc:
                show_error(self, exc, "打开会话")

    def restore_session(self, session: Session) -> None:
        problems = validate_session(session)
        if problems:
            if not confirm(
                self,
                "会话中的路径不可用",
                "会话保存的路径当前不可访问，是否仍尝试加载？",
                "\n".join(problems[:6]),
                danger=True,
            ):
                return
        self.session_name = session.name
        self.setWindowTitle(f"{APP_NAME}  v{__version__}  ·  {session.name}")
        self.options = session.options
        self.mode_box.setCurrentIndex(0 if session.mode == CompareMode.FOLDERS.value else 1)
        self.left_edit.setText(session.left)
        self.right_edit.setText(session.right)
        self._restore_filters = session.filters or {}
        self._restore_diff = session.current_diff
        geometry = (session.geometry or {}).get("main")
        if geometry:
            self.restoreGeometry(QByteArray.fromHex(bytes.fromhex(geometry)))
        self.set_status(f"正在恢复会话：{session.name}")
        self.start_compare()

    def _apply_restore_filters(self) -> None:
        filters = self._restore_filters
        self.folder_view.set_filter_state(
            filters.get("folder_text", ""), filters.get("folder_status")
        )
        self.file_view.set_filter_state(filters.get("file_query", ""), filters.get("file_case", False))

    def showEvent(self, event) -> None:  # pragma: no cover - GUI 生命周期
        super().showEvent(event)
        if self._restored_once:
            return
        self._restored_once = True
        # 不再自动恢复并立即比较：主页提供一键恢复入口，由用户决定是否继续
        path = last_session_path()
        if path.exists():
            try:
                session = load_session(path)
                if not validate_session(session) and session.left and session.right:
                    self.welcome.set_resume_session(session.left, session.right)
                    self.set_status("就绪 · 可点击「恢复上次会话」继续，或选择新的比较")
                else:
                    self.set_status("就绪 · 上次会话的路径已失效，请重新选择路径")
            except DiffScopeError:
                self.set_status("就绪 · 上次会话无法读取，请重新选择路径")
        else:
            self.set_status("就绪 · 选择两个文件或文件夹开始比较")

    def _resume_last_session(self) -> None:
        path = last_session_path()
        if not path.exists():
            return
        try:
            session = load_session(path)
        except DiffScopeError as exc:
            show_error(self, exc, "恢复会话")
            return
        self.restore_session(session)

    # ---- 扩展功能 -------------------------------------------------
    def _apply_settings(self) -> None:
        from PySide6.QtGui import QFont

        from .theme import STYLESHEET, light_stylesheet
        from ..core import i18n

        i18n.set_language(str(self.settings.get("language")))
        self.file_view.max_text_bytes = int(self.settings.get("max_text_mb")) * 1024 * 1024
        self.file_view.formats_registry = self.formats
        app = QApplication.instance()
        if app:
            app.setStyleSheet(light_stylesheet() if self.settings.get("theme") == "light" else STYLESHEET)
            app.setFont(QFont(str(self.settings.get("font_family")),
                              int(self.settings.get("font_size"))))
        self.file_view.apply_settings(self.settings)
        self.setAcceptDrops(True)

    def _apply_argv(self) -> None:
        argv = self._argv
        for flag, count in (("--compare-with", 1), ("--compare", 2), ("--merge-gui", 4)):
            if flag in argv:
                index = argv.index(flag)
                values = argv[index + 1: index + 1 + count]
                if flag == "--compare-with" and values:
                    self.left_edit.setText(values[0])
                    self.set_status("已从命令行载入左侧路径，请选择右侧路径后比较")
                elif flag == "--compare" and len(values) == 2:
                    self.left_edit.setText(values[0])
                    self.right_edit.setText(values[1])
                    self.start_compare()
                elif flag == "--merge-gui":
                    self._pending_merge = values
        if "--workspace" in argv:
            self.open_workspace()

    def _make_vfs(self, spec: str, side: str):
        from ..core.vfs import open_vfs

        if "://" not in spec and not spec.lower().startswith(("zip:", "tar:", "snapshot:")):
            return None
        return open_vfs(spec, **self._source_options.get(side, {}))

    def dragEnterEvent(self, event) -> None:  # pragma: no cover - GUI
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.set_status("松开即导入：拖入 2 项 = 左右两侧，1 项 = 填入空侧")

    def dragMoveEvent(self, event) -> None:  # pragma: no cover - GUI
        # 必须接受 move 事件，否则释放鼠标不会触发 drop
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # pragma: no cover - GUI
        from .widgets import drop_paths as extract_paths

        paths = extract_paths(event)
        if not paths:
            return
        event.acceptProposedAction()
        self._handle_drop_paths(paths)

    def _handle_drop_paths(self, paths: list) -> None:
        if len(paths) >= 2:
            self._apply_dropped_pair(paths[0], paths[1])
            return
        path = paths[0]
        if Path(path).is_file():
            # 单个文件：直接进入文本对比界面。重活延到下一轮事件循环，
            # 让拖放事件立即返回、界面先切换，避免落点处的可感知卡顿。
            self.set_status(f"正在打开 {Path(path).name} …")
            QTimer.singleShot(0, lambda: self._open_single_dropped_file(path))
        else:
            target = self.left_edit if not self.left_edit.text().strip() else self.right_edit
            target.setText(path)
            self.set_status(f"已填入文件夹路径：{path}；再拖入另一个文件夹（或选择另一侧）即可比较")

    def _open_single_dropped_file(self, path: str) -> None:
        if not self._check_unsaved():
            return
        self.mode_box.setCurrentIndex(1)
        self.stack.setCurrentIndex(2)
        self.file_view.load_pair_async(
            path, None, self.options,
            rel_hint=Path(path).name, can_go_back=False,
            done=lambda ok: self._on_single_dropped_opened(ok, path),
        )

    def _on_single_dropped_opened(self, ok: bool, path: str) -> None:
        if ok:
            self.summary.set_stats(self._text_stats())
            self.set_status(
                f"已打开 {Path(path).name}；把第二个文件拖到右侧窗格即可开始对比"
            )
        else:
            self.set_status("文件内容读取失败，请查看上方提示")

    def _apply_dropped_pair(self, first: str, second: str) -> None:
        """拖入两项：按类型自动切换模式并开始比较。"""
        first_dir = Path(first).is_dir()
        second_dir = Path(second).is_dir()
        if first_dir != second_dir:
            show_warning(
                self, "拖入类型不一致",
                f"两项类型不同（{'文件夹' if first_dir else '文件'} 与 "
                f"{'文件夹' if second_dir else '文件'}），请拖入同为文件或同为文件夹的两项。",
            )
            return
        self.mode_box.setCurrentIndex(0 if first_dir else 1)
        self.left_edit.setText(first)
        self.right_edit.setText(second)
        self.start_compare()

    def eventFilter(self, obj, event) -> bool:  # pragma: no cover - GUI
        """路径输入框支持直接拖入文件/文件夹。"""
        if obj in (self.left_edit, self.right_edit):
            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.Drop and event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if url.isLocalFile():
                        obj.setText(os.path.normpath(url.toLocalFile()))
                        side = "左" if obj is self.left_edit else "右"
                        self.set_status(f"已拖入{side}侧路径：{url.toLocalFile()}")
                        break
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

    def _show_widget_window(self, widget, title: str) -> None:
        from PySide6.QtWidgets import QDialog

        dialog = QDialog(self)
        dialog.setWindowTitle(f"{APP_NAME} · {title}")
        dialog.resize(1100, 720)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(widget)
        dialog.show()
        self._extra_windows.append(dialog)

    def open_source_dialog(self) -> None:
        from .dialogs_ext import SourceDialog

        for side in ("left", "right"):
            dialog = SourceDialog(self, side)
            if not dialog.exec():
                break
            spec, options = dialog.values()
            if spec:
                (self.left_edit if side == "left" else self.right_edit).setText(spec)
                self._source_options[side] = options
        self.set_status("数据源已设置，点击“开始比较”连接并比较")

    def create_snapshot_dialog(self) -> None:
        from .dialogs_ext import SnapshotDialog
        from ..core.vfs import create_snapshot

        dialog = SnapshotDialog(self, self.left_edit.text().strip())
        if not dialog.exec():
            return
        root, output, excludes = dialog.values()
        if not root or not output:
            show_warning(self, "路径不完整", "请填写要快照的目录与输出文件。")
            return
        try:
            path = create_snapshot(root, output, excludes)
        except DiffScopeError as exc:
            show_error(self, exc, "创建快照")
            return
        show_info(self, "快照已创建", f"已保存：{path}\n可用作左侧或右侧数据源进行离线比较。")

    def open_viewer(self, kind: str) -> None:
        from .viewers import create_viewer

        left, _ = QFileDialog.getOpenFileName(self, "选择左侧文件")
        right, _ = QFileDialog.getOpenFileName(self, "选择右侧文件") if left else ("", "")
        if not left or not right:
            return
        viewer = create_viewer(kind, self)
        viewer.status_message.connect(self.set_status)
        viewer.set_data(Path(left).read_bytes(), Path(right).read_bytes(), left, right)
        self._show_widget_window(viewer, viewer.viewer_name)

    def open_rules(self) -> None:
        from .dialogs_ext import RulesDialog

        dialog = RulesDialog(self, self.rules)
        if dialog.exec():
            self.file_view.set_rules(self.rules)
            self.set_status(f"比较规则已更新（{len(self.rules.rules)} 条），点击“刷新”重新比较")

    def open_alignment(self) -> None:
        from .dialogs_ext import AlignmentDialog

        dialog = AlignmentDialog(self, self.alignment)
        if dialog.exec():
            self.alignment = dialog.values()
            self.set_status("对齐规则已更新，点击“刷新”重新比较")

    def open_sync_mode(self) -> None:
        from .dialogs_ext import SyncModeDialog
        from .extended_views import JobDialog
        from ..core.sync_modes import SyncMode, plan_mode

        if not self.result:
            show_info(self, "尚无比较结果", "请先执行一次文件夹比较。")
            return
        dialog = SyncModeDialog(self, SyncMode.MIRROR, self.folder_view.difference_count())
        if not dialog.exec():
            return
        mode, direction = dialog.values()
        plan = plan_mode(self.result, mode, direction, self.folder_view.selected_nodes() or None)
        if plan.empty:
            show_info(self, "无可同步操作", "当前模式下没有需要执行的操作。")
            return
        if not ConfirmActionsDialog.ask(
            self, "操作前确认 · 同步模式", f"模式：{mode.label}；共 {len(plan.actions)} 项操作。",
            plan.actions, plan.skipped, danger=True,
        ):
            return
        job = JobDialog(self, plan.actions)
        self._extra_windows.append(job)
        job.show()
        job.start()
        self.set_status(f"后台同步已启动：{len(plan.actions)} 项")

    def _on_folder_drop(self, side: str, paths: list) -> None:
        """目录树面板拖入：落点面板对应一侧，两项则两侧齐填并比较。"""
        if len(paths) >= 2:
            first, second = (paths[0], paths[1]) if side == "left" else (paths[1], paths[0])
            self._apply_dropped_pair(first, second)
            return
        target = self.left_edit if side == "left" else self.right_edit
        other = self.right_edit if side == "left" else self.left_edit
        target.setText(paths[0])
        if other.text().strip():
            self.start_compare()
        else:
            self.set_status(f"已填入{'左' if side == 'left' else '右'}侧：{paths[0]}，可再拖入或选择另一侧")

    def open_formats(self) -> None:
        from .dialogs_ext import FormatEditorDialog

        dialog = FormatEditorDialog(self, self.formats)
        if dialog.exec():
            self.set_status(f"文件格式规则已保存（{len(self.formats.formats)} 条）")

    def open_folder_merge3(self) -> None:
        from .extended_views import FolderMerge3View

        view = FolderMerge3View(self)
        view.status_message.connect(self.set_status)
        self._show_widget_window(view, "三向文件夹合并")

    def open_merge3(self) -> None:
        from .extended_views import Merge3View

        view = Merge3View(self)
        view.status_message.connect(self.set_status)
        paths = getattr(self, "_pending_merge", None)
        if paths:
            view.load_paths(paths)
        self._show_widget_window(view, "三向文本合并")

    def open_report(self) -> None:
        from .dialogs_ext import ReportDialog
        from ..core.report import save_linked_reports, save_report

        if not self.result:
            show_info(self, "尚无比较结果", "请先执行一次比较。")
            return
        dialog = ReportDialog(self)
        if not dialog.exec():
            return
        fmt, path, linked = dialog.values()
        if not path or not str(path):
            show_warning(self, "路径不完整", "请填写报告输出路径。")
            return
        try:
            save_report(self.result, path, fmt)
            if linked:
                save_linked_reports(self.result, path.parent / (path.stem + "_files"))
        except DiffScopeError as exc:
            show_error(self, exc, "生成报告")
            return
        if dialog.print_box.isChecked():
            try:
                from PySide6.QtPrintSupport import QPrintDialog, QPrinter
                from PySide6.QtWidgets import QTextBrowser

                from ..core.report import html_folder_report

                printer = QPrinter()
                if QPrintDialog(printer, self).exec():
                    browser = QTextBrowser()
                    browser.setHtml(html_folder_report(self.result))
                    browser.print_(printer)
                    self.set_status("报告已发送到打印机")
            except ImportError:
                show_warning(self, "打印不可用", "当前 PySide6 未包含 QtPrintSupport，已跳过打印。")
        show_info(self, "报告已生成", f"文件：{path}")

    def open_script(self) -> None:
        from .dialogs_ext import ScriptDialog

        dialog = ScriptDialog(self)
        dialog.exec()
        self.set_status(f"脚本执行完毕，退出码 {dialog.result_code}")

    def open_workspace(self) -> None:
        from .extended_views import WorkspaceWindow

        if self._workspace_window is None:
            self._workspace_window = WorkspaceWindow()
        self._workspace_window.show()
        self._workspace_window.raise_()
        self.set_status("已打开标签页工作区")

    def open_standalone_editor(self) -> None:
        from .extended_views import StandaloneEditor

        StandaloneEditor(self).exec()

    def compare_clipboard(self) -> None:
        import tempfile

        from ..core.fs_utils import write_text_atomic

        text = QApplication.clipboard().text()
        if not text:
            show_info(self, "剪贴板为空", "请先复制一段文本，再执行“比较剪贴板内容”。")
            return
        target = Path(tempfile.gettempdir()) / "diffscope-clipboard.txt"
        try:
            write_text_atomic(target, text, "utf-8", "\n")
        except DiffScopeError as exc:
            show_error(self, exc, "读取剪贴板")
            return
        current = str(self.file_view.left_path) if self.file_view.left_path else str(target)
        self.file_view.load_pair(current, str(target), self.options, rel_hint="剪贴板", can_go_back=True)
        self.stack.setCurrentIndex(2)
        self.set_status("已把剪贴板内容作为右侧进行比较")

    def open_settings(self) -> None:
        from .dialogs_ext import SettingsDialog

        dialog = SettingsDialog(self, self.settings)
        if dialog.exec():
            dialog.apply()
            self._apply_settings()
            self.set_status("设置已保存并应用")

    def open_help(self) -> None:
        from .dialogs_ext import HelpDialog

        HelpDialog(self).exec()

    def toggle_theme(self) -> None:
        self.settings.set("theme", "light" if self.settings.get("theme") == "dark" else "dark")
        self.settings.save()
        self._apply_settings()
        self.set_status(f"已切换到{'浅色' if self.settings.get('theme') == 'light' else '深色'}主题")

    def closeEvent(self, event) -> None:  # pragma: no cover - GUI 生命周期
        if not self._check_unsaved():
            event.ignore()
            return
        if self._worker:
            self._worker.stop()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        try:
            save_last_session(self._current_session())
        except DiffScopeError:
            pass
        super().closeEvent(event)


def _app_icon() -> QIcon | None:
    """应用图标：开发态取仓库 assets/，打包态取 PyInstaller 解包目录。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
    else:
        base = Path(__file__).resolve().parent.parent.parent / "assets"
    for name in ("icon_256.png", "icon_128.png", "icon_64.png"):
        path = base / name
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return None


def launch(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow(argv)
    window.show()
    return app.exec()
