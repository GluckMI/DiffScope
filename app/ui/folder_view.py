"""文件夹对比：共享模型的左右双树视图 + 筛选 / 导航 / 同步。"""

from __future__ import annotations

import os
from PySide6.QtCore import QAbstractItemModel, QItemSelectionModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.fs_utils import format_size, format_time
from ..core.merge import MergeDirection
from ..core.models import (
    STATUS_META,
    CompareNode,
    CompareResult,
    DiffStatus,
)
from ..core.sync import execute_sync, plan_sync
from .dialogs import ConfirmActionsDialog, SyncResultDialog, confirm, show_error, show_info
from . import effects
from .theme import STATUS_COLORS, apply_role, qcolor

COLUMNS = (
    "名称",
    "大小",
    "修改时间",
    "状态",
    "名称",
    "大小",
    "修改时间",
)
LEFT_COLS = (0, 1, 2)
RIGHT_COLS = (3, 4, 5, 6)


class CompareTreeModel(QAbstractItemModel):
    """一棵树同时承载左右两列组；两个 QTreeView 各显示其中一组列。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root: CompareNode | None = None
        self._visible: dict[int, list[CompareNode]] = {}
        self._text = ""
        self._status_key = None
        app = QApplication.instance()
        style = app.style() if app else None
        self._dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon) if style else QIcon()
        self._file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon) if style else QIcon()

    # ---- 过滤 -----------------------------------------------------
    def set_result(self, result: CompareResult) -> None:
        self.beginResetModel()
        self.root = result.root
        self._rebuild()
        self.endResetModel()

    def set_filter(self, text: str, status_key) -> None:
        self.beginResetModel()
        self._text = (text or "").strip().lower()
        self._status_key = status_key
        self._rebuild()
        self.endResetModel()

    @staticmethod
    def _normalize_status(key) -> object:
        if isinstance(key, DiffStatus):
            return key
        if isinstance(key, str) and key in {s.value for s in DiffStatus}:
            return DiffStatus(key)
        return key  # None 或 "differences"

    def _status_ok(self, node: CompareNode, key) -> bool:
        if key is None:
            return True
        if key == "differences":
            return node.is_difference()
        return node.status is key

    def _self_match(self, node: CompareNode, text: str, key) -> bool:
        if text and text not in node.rel.lower():
            return False
        return self._status_ok(node, key)

    def _rebuild(self) -> None:
        self._visible.clear()
        if self.root is None:
            return
        text = self._text
        key = self._normalize_status(self._status_key)

        def visit(node: CompareNode) -> bool:
            self_match = self._self_match(node, text, key)
            children = [child for child in node.children if visit(child)]
            self._visible[id(node)] = children if (text or key is not None) else node.children
            return self_match or bool(children)

        # 始终按完整可见性计算，父节点因后代匹配而保留
        for child in self.root.children:
            visit(child)
        if text or key is not None:
            self._visible[id(self.root)] = [
                child
                for child in self.root.children
                if self._self_match(child, text, key) or self._visible.get(id(child))
            ]
        else:
            self._visible[id(self.root)] = self.root.children

    # ---- Qt 模型接口 ----------------------------------------------
    def index(self, row: int, column: int = 0, parent: QModelIndex = QModelIndex()):
        if not self.root or column < 0 or column >= len(COLUMNS):
            return QModelIndex()
        node = self.root if not parent.isValid() else parent.internalPointer()
        children = self._visible.get(id(node), [])
        if 0 <= row < len(children):
            return self.createIndex(row, column, children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex = QModelIndex()):
        if not index.isValid() or not self.root:
            return QModelIndex()
        node: CompareNode = index.internalPointer()
        parent_node = node.parent
        if parent_node is None or parent_node is self.root:
            return QModelIndex()
        grand = parent_node.parent or self.root
        siblings = self._visible.get(id(grand), [])
        try:
            row = siblings.index(parent_node)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not self.root:
            return 0
        node = self.root if not parent.isValid() else parent.internalPointer()
        if parent.column() > 0:
            return 0
        return len(self._visible.get(id(node), []))

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node: CompareNode = index.internalPointer()
        col = index.column()
        status = node.status
        fg, bg = STATUS_COLORS[status][0], STATUS_COLORS[status][1]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(node, col)
        if role == Qt.ItemDataRole.ForegroundRole:
            return QBrush(QColor(fg) if col in (0, 3, 4) else qcolor("muted"))
        if role == Qt.ItemDataRole.BackgroundRole:
            return QBrush(QColor(bg)) if bg else None
        if role == Qt.ItemDataRole.DecorationRole and col in (0, 4):
            info = node.left if col == 0 else node.right
            if info and info.exists:
                return self._dir_icon if info.is_dir else self._file_icon
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (1, 5):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(node)
        return None

    def _display(self, node: CompareNode, col: int):
        if col == 3:
            label, symbol, _ = STATUS_META[node.status]
            return f"{symbol}  {label}"
        info = node.left if col in LEFT_COLS else node.right
        offset = col if col in LEFT_COLS else col - 4
        if not info or not info.exists:
            return "—" if offset else "（不存在）"
        if offset == 0:
            return node.name
        if offset == 1:
            return "<文件夹>" if info.is_dir else format_size(info.size)
        return format_time(info.mtime_ns)

    def _tooltip(self, node: CompareNode) -> str:
        label = STATUS_META[node.status][0]
        lines = [f"相对路径：{node.rel}", f"状态：{label}"]
        if node.note:
            lines.append(f"说明：{node.note}")
        for side, info in (("左侧", node.left), ("右侧", node.right)):
            if info and info.path:
                lines.append(f"{side}：{info.path}")
                if not info.is_dir:
                    lines.append(f"  大小 {format_size(info.size)}，修改时间 {format_time(info.mtime_ns)}")
        return "\n".join(lines)

    def headerData(self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def node_at(self, index: QModelIndex) -> CompareNode | None:
        return index.internalPointer() if index.isValid() else None

    def index_for_node(self, node: CompareNode, column: int = 0) -> QModelIndex:
        if not self.root or node is self.root:
            return QModelIndex()
        parent_node = node.parent or self.root
        siblings = self._visible.get(id(parent_node), [])
        try:
            row = siblings.index(node)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, column, node)

    def difference_nodes(self) -> list[CompareNode]:
        out: list[CompareNode] = []
        if not self.root:
            return out

        def visit(node: CompareNode) -> None:
            if node.rel and node.is_difference():
                out.append(node)
            for child in self._visible.get(id(node), []):
                visit(child)

        for child in self._visible.get(id(self.root), []):
            visit(child)
        return out


class FolderCompareView(QWidget):
    """左右并排的目录树面板。"""

    open_pair = Signal(str, str, str)
    drop_paths = Signal(str, list)   # side("left"/"right"), 本地路径列表
    refresh_requested = Signal()
    status_message = Signal(str)
    filter_changed = Signal(str, object)
    sync_finished = Signal()
    home_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: CompareResult | None = None
        self.setAcceptDrops(True)
        self.model = CompareTreeModel(self)
        self._syncing_scroll = False
        self._syncing_expand = False
        self._current_diff = -1
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.btn_home = QPushButton("← 主页")
        self.btn_home.setToolTip("返回欢迎主页（Alt+Home）")
        self.btn_home.setAccessibleName("返回欢迎主页")
        self.btn_home.clicked.connect(self.home_requested.emit)
        toolbar.addWidget(self.btn_home)

        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称或路径筛选（支持部分匹配）")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(230)
        self.search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search, 1)

        self.filter_box = QComboBox()
        self.filter_box.addItem("全部条目", None)
        self.filter_box.addItem("仅显示差异", "differences")
        for status in DiffStatus:
            self.filter_box.addItem(f"仅 {STATUS_META[status][0]}", status.value)
        self.filter_box.setMinimumWidth(130)
        self.filter_box.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_box)

        self.btn_prev = QPushButton("上一差异")
        self.btn_next = QPushButton("下一差异")
        self.btn_prev.clicked.connect(self.previous_difference)
        self.btn_next.clicked.connect(self.next_difference)
        toolbar.addWidget(self.btn_prev)
        toolbar.addWidget(self.btn_next)

        self.btn_expand = QPushButton("展开全部")
        self.btn_collapse = QPushButton("折叠")
        self.btn_expand.clicked.connect(lambda: self.left.expandAll())
        self.btn_collapse.clicked.connect(self._collapse_all)
        toolbar.addWidget(self.btn_expand)
        toolbar.addWidget(self.btn_collapse)

        self.btn_l2r = QPushButton("左 → 右 同步")
        self.btn_r2l = QPushButton("右 → 左 同步")
        self.btn_l2r.setProperty("accent", True)
        self.btn_l2r.clicked.connect(lambda: self.sync(MergeDirection.LEFT_TO_RIGHT))
        self.btn_r2l.clicked.connect(lambda: self.sync(MergeDirection.RIGHT_TO_LEFT))
        toolbar.addWidget(self.btn_l2r)
        toolbar.addWidget(self.btn_r2l)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.left = self._make_tree("left")
        self.right = self._make_tree("right")
        self.left_panel = self._make_panel("左侧", self.left, "left")
        self.right_panel = self._make_panel("右侧", self.right, "right")
        splitter.addWidget(self.left_panel)
        splitter.addWidget(self.right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 500])
        root.addWidget(splitter, 1)

        self.left.setModel(self.model)
        self.right.setModel(self.model)
        self.right.setSelectionModel(self.left.selectionModel())
        for column in RIGHT_COLS:
            self.left.setColumnHidden(column, True)
        for column in LEFT_COLS:
            self.right.setColumnHidden(column, True)
        self._setup_header(self.left, LEFT_COLS)
        self._setup_header(self.right, RIGHT_COLS)

        self.left.expanded.connect(lambda idx: self._mirror_expand(idx, True, self.right))
        self.left.collapsed.connect(lambda idx: self._mirror_expand(idx, False, self.right))
        self.right.expanded.connect(lambda idx: self._mirror_expand(idx, True, self.left))
        self.right.collapsed.connect(lambda idx: self._mirror_expand(idx, False, self.left))
        self.left.verticalScrollBar().valueChanged.connect(
            lambda value: self._mirror_scroll(value, self.right)
        )
        self.right.verticalScrollBar().valueChanged.connect(
            lambda value: self._mirror_scroll(value, self.left)
        )
        self.left.doubleClicked.connect(self._on_double_click)
        self.right.doubleClicked.connect(self._on_double_click)
        self.left.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.right.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.left.customContextMenuRequested.connect(lambda pos: self._context_menu(self.left, pos))
        self.right.customContextMenuRequested.connect(lambda pos: self._context_menu(self.right, pos))
        self.left.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self.hint = QLabel("提示：双击文件可打开文本对比；同步操作会先列出全部动作供确认。")
        apply_role(self.hint, "note")
        self.hint.setAccessibleName("目录对比操作提示")
        root.addWidget(self.hint)

    def _make_tree(self, side: str) -> QTreeView:
        tree = QTreeView()
        tree.setObjectName(f"tree_{side}")
        tree.setAlternatingRowColors(True)
        tree.setAccessibleName("左侧目录" if side == "left" else "右侧目录")
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tree.setUniformRowHeights(True)
        tree.setIndentation(16)
        tree.setAnimated(not effects.reduce_motion())
        tree.setRootIsDecorated(True)
        return tree

    def _make_panel(self, title: str, tree: QTreeView, side: str) -> QWidget:
        panel = QFrame()
        panel.setObjectName(f"panel_{side}")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName(f"path_{side}")
        apply_role(label, "path-label")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        setattr(self, f"path_label_{side}", label)
        layout.addWidget(label)
        layout.addWidget(tree, 1)
        return panel

    def _setup_header(self, tree: QTreeView, columns: tuple[int, ...]) -> None:
        """列宽按字体度量伸缩，高分屏 / 字号放大时不挤断文件名。用户仍可手动拖宽。"""
        header = tree.header()
        header.setMinimumSectionSize(72)
        for col in columns:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        unit = max(7, tree.fontMetrics().horizontalAdvance("0"))
        name_w, size_w, time_w = unit * 32, unit * 11, unit * 18
        if 0 in columns:
            for col, width in ((0, name_w), (1, size_w), (2, time_w)):
                tree.setColumnWidth(col, width)
        else:
            for col, width in ((3, size_w + unit * 4), (4, name_w), (5, size_w), (6, time_w)):
                tree.setColumnWidth(col, width)

    # ---- 数据装载 -------------------------------------------------
    def set_result(self, result: CompareResult) -> None:
        self.result = result
        self.model.set_result(result)
        self.path_label_left.setText(f"左侧：{result.left_root}")
        self.path_label_right.setText(f"右侧：{result.right_root}")
        self._current_diff = -1
        self.left.expandToDepth(0)

    def set_filter_state(self, text: str, status_key) -> None:
        block = self.search.blockSignals(True)
        self.search.setText(text or "")
        self.search.blockSignals(block)
        index = self.filter_box.findData(status_key if not isinstance(status_key, DiffStatus) else status_key.value)
        if index >= 0:
            self.filter_box.setCurrentIndex(index)
        self._apply_filter()

    def current_filter(self) -> tuple[str, object]:
        return self.search.text(), self.filter_box.currentData()

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
        self.drop_paths.emit(self.drop_side_at(pos), paths)

    def drop_side_at(self, pos) -> str:
        """落点在左面板 → left，右面板 → right，其余按中线判断。"""
        if self.left_panel.geometry().contains(pos):
            return "left"
        if self.right_panel.geometry().contains(pos):
            return "right"
        return "left" if pos.x() < self.width() / 2 else "right"

    def _apply_filter(self, *_args) -> None:
        self.model.set_filter(self.search.text(), self.filter_box.currentData())
        self._current_diff = -1
        self.filter_changed.emit(self.search.text(), self.filter_box.currentData())
        diffs = self.model.difference_nodes()
        self.status_message.emit(
            f"筛选后显示 {self.model.rowCount(QModelIndex())} 个顶层条目，其中差异 {len(diffs)} 项"
        )

    def _collapse_all(self) -> None:
        self.left.collapseAll()
        self.right.collapseAll()

    def _mirror_expand(self, index: QModelIndex, expand: bool, target: QTreeView) -> None:
        if self._syncing_expand:
            return
        self._syncing_expand = True
        if expand:
            target.expand(index)
        else:
            target.collapse(index)
        self._syncing_expand = False

    def _mirror_scroll(self, value: int, target: QTreeView) -> None:
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        target.verticalScrollBar().setValue(value)
        self._syncing_scroll = False

    def _on_selection_changed(self, *_args) -> None:
        diffs = self.model.difference_nodes()
        node = self.current_node()
        if node and node in diffs:
            self._current_diff = diffs.index(node)

    def current_node(self) -> CompareNode | None:
        indexes = self.left.selectionModel().selectedRows(0)
        return self.model.node_at(indexes[0]) if indexes else None

    def selected_nodes(self) -> list[CompareNode]:
        return [
            node
            for index in self.left.selectionModel().selectedRows(0)
            if (node := self.model.node_at(index)) is not None
        ]

    def _reveal_node(self, node: CompareNode) -> None:
        parent_node = node.parent
        chain: list[CompareNode] = []
        while parent_node is not None and parent_node is not self.model.root and parent_node.rel:
            chain.append(parent_node)
            parent_node = parent_node.parent
        for ancestor in reversed(chain):
            index = self.model.index_for_node(ancestor)
            if index.isValid():
                self.left.expand(index)
                self.right.expand(index)
        index = self.model.index_for_node(node)
        if index.isValid():
            self.left.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.left.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    # ---- 差异导航 -------------------------------------------------
    def difference_count(self) -> int:
        return len(self.model.difference_nodes())

    def current_difference_index(self) -> int:
        return self._current_diff

    def goto_difference(self, index: int) -> bool:
        diffs = self.model.difference_nodes()
        if not diffs or not (0 <= index < len(diffs)):
            return False
        self._current_diff = index
        self._reveal_node(diffs[index])
        self.status_message.emit(f"第 {index + 1} / {len(diffs)} 处差异：{diffs[index].rel}")
        return True

    def next_difference(self) -> None:
        self.goto_difference(self._current_diff + 1)

    def previous_difference(self) -> None:
        count = self.difference_count()
        self.goto_difference(self._current_diff - 1 if self._current_diff > 0 else count - 1)

    # ---- 打开 / 同步 ----------------------------------------------
    def _on_double_click(self, index: QModelIndex) -> None:
        node = self.model.node_at(index)
        if not node or node.is_dir or not self.result:
            return
        left = node.left.path if node.left and node.left.exists else self.result.left_root / node.rel
        right = node.right.path if node.right and node.right.exists else self.result.right_root / node.rel
        self.open_pair.emit(str(left), str(right), node.rel)

    def open_current(self) -> None:
        self._on_double_click(self.left.currentIndex())

    def _context_menu(self, tree: QTreeView, pos) -> None:
        index = tree.indexAt(pos)
        if not index.isValid():
            return
        node = self.model.node_at(index)
        if not node:
            return
        menu = QMenu(self)
        act_open = menu.addAction("打开文本对比")
        act_open.setEnabled(not node.is_dir)
        menu.addSeparator()
        act_l2r = menu.addAction("按此条目 左 → 右 同步")
        act_r2l = menu.addAction("按此条目 右 → 左 同步")
        action = menu.exec(tree.viewport().mapToGlobal(pos))
        if action is act_open:
            self._on_double_click(index)
        elif action is act_l2r:
            self.sync(MergeDirection.LEFT_TO_RIGHT, [node])
        elif action is act_r2l:
            self.sync(MergeDirection.RIGHT_TO_LEFT, [node])

    def sync(self, direction: MergeDirection, nodes: list[CompareNode] | None = None) -> None:
        if not self.result:
            return
        selected = nodes if nodes is not None else self.selected_nodes()
        if not selected:
            if not confirm(
                self,
                "确认同步范围",
                "当前未选中任何条目。是否对【全部差异】执行同步？",
                "将列出全部待执行操作，确认前可再次核对。",
                danger=True,
            ):
                return
            selected = [self.result.root]
        plan = plan_sync(self.result, selected, direction)
        if plan.empty:
            show_info(self, "无可同步操作", "所选条目没有可执行的变化（可能全部相同或读取失败）。")
            return
        intro = (
            f"方向：{direction.label}；共 {len(plan.actions)} 项操作，"
            f"跳过 {len(plan.skipped)} 项。请逐项核对后执行。"
        )
        if not ConfirmActionsDialog.ask(
            self, "操作前确认 · 目录同步", intro, plan.actions, plan.skipped, danger=True
        ):
            self.status_message.emit("目录同步已取消")
            return

        from PySide6.QtWidgets import QApplication as _QApp

        def progress(done: int, total: int, label: str) -> None:
            self.status_message.emit(f"同步中 {done}/{total}：{label}")
            _QApp.processEvents()

        try:
            report = execute_sync(plan, progress=progress)
        except Exception as exc:  # 不让同步中的意外异常导致界面退出
            show_error(self, exc, "执行目录同步")
            return
        SyncResultDialog(self, report, len(plan.actions)).exec()
        self.status_message.emit(
            f"同步完成：成功 {len(report.succeeded)} 项，失败 {len(report.failed)} 项"
        )
        self.sync_finished.emit()
        self.refresh_requested.emit()
