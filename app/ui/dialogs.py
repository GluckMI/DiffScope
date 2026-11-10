"""对话框：错误提示、操作前确认、执行报告、比较选项、会话管理。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import APP_NAME, __version__
from ..core.errors import DiffScopeError, map_os_error
from ..core.models import CompareOptions, SyncAction, SyncReport
from ..core.session import Session, session_dir
from ..core.i18n import tr
from .theme import ACCENT, DANGER, MUTED


def _message(parent, icon, title: str, text: str, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(icon)
    box.setText(text)
    if detail:
        box.setDetailedText(detail)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText(tr("知道了"))
    box.exec()


def show_error(parent, exc: Exception, context: str = "") -> None:
    if isinstance(exc, DiffScopeError):
        title, text = exc.title, exc.user_message()
        detail = context
    elif isinstance(exc, OSError):
        mapped = map_os_error(context or ".", exc, "访问")
        title, text, detail = mapped.title, mapped.user_message(), context
    else:
        title = "操作失败"
        text = f"发生未预期的错误：{exc}"
        detail = f"{type(exc).__name__}: {exc}" + (f"\n场景：{context}" if context else "")
    _message(parent, QMessageBox.Icon.Critical, title, text, detail)


def show_info(parent, title: str, text: str, detail: str = "") -> None:
    _message(parent, QMessageBox.Icon.Information, title, text, detail)


def show_warning(parent, title: str, text: str, detail: str = "") -> None:
    _message(parent, QMessageBox.Icon.Warning, title, text, detail)


def confirm(parent, title: str, text: str, detail: str = "", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText(tr("确认执行"))
    box.button(QMessageBox.StandardButton.No).setText(tr("取消"))
    if danger:
        box.button(QMessageBox.StandardButton.Yes).setStyleSheet(
            f"background: {DANGER}; color: white; padding: 5px 14px; border-radius: 4px;"
        )
    return box.exec() == QMessageBox.StandardButton.Yes


class ConfirmActionsDialog(QDialog):
    """同步 / 批量合并前的操作清单，必须显式确认。"""

    def __init__(
        self,
        parent,
        title: str,
        intro: str,
        actions: list[SyncAction],
        skipped: list[str] | None = None,
        danger: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(880, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        head = QLabel(intro)
        head.setWordWrap(True)
        head.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(head)

        table = QTableWidget(len(actions), 3, self)
        table.setHorizontalHeaderLabels(["操作", "相对路径", "说明"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        labels = {"mkdir": "创建目录", "copy": "复制覆盖", "remove": "删除"}
        for row, action in enumerate(actions):
            kind = labels.get(action.kind, action.kind)
            color = "#f0a0a0" if action.kind == "remove" else ("#f0ba5e" if action.kind == "copy" else MUTED)
            item = QTableWidgetItem(kind)
            item.setForeground(Qt.GlobalColor.white)
            item.setData(Qt.ItemDataRole.ForegroundRole, _color(color))
            table.setItem(row, 0, item)
            table.setItem(row, 1, QTableWidgetItem(action.rel))
            table.setItem(row, 2, QTableWidgetItem(action.note or ""))
        layout.addWidget(table, 1)

        if skipped:
            note = QLabel("已跳过的条目：" + "；".join(skipped[:5]) + (" …" if len(skipped) > 5 else ""))
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {DANGER};")
            layout.addWidget(note)

        if danger:
            warn = QLabel("注意：删除操作优先移入系统回收站，回收站不可用时转存至备份目录；复制操作会覆盖目标文件。")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #f0ba5e; padding: 6px 8px; background: #3f2f14; border-radius: 4px;")
            layout.addWidget(warn)

        buttons = QDialogButtonBox(self)
        ok = buttons.addButton(tr("确认执行"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(tr("取消"), QDialogButtonBox.ButtonRole.RejectRole)
        preview = buttons.addButton(tr("导出预演报告…"), QDialogButtonBox.ButtonRole.ActionRole)
        preview.setToolTip("把将执行的操作清单导出为 CSV（先导出审阅，再决定是否执行）")
        preview.clicked.connect(self._export_preview)
        ok.setStyleSheet(f"background: {ACCENT}; color: white; padding: 5px 16px;")
        if danger:
            ok.setStyleSheet(f"background: {DANGER}; color: white; padding: 5px 16px;")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._preview_note = QLabel("")
        self._preview_note.setStyleSheet(f"color: {MUTED};")
        self._preview_note.setWordWrap(True)
        layout.addWidget(self._preview_note)
        layout.addWidget(buttons)
        self._cancel = cancel
        self._actions = actions

    def _export_preview(self) -> None:
        import csv

        path, _ = QFileDialog.getSaveFileName(self, "导出预演报告", "sync-preview", "CSV (*.csv)")
        if not path:
            return
        labels = {"mkdir": "创建目录", "copy": "复制覆盖", "remove": "删除"}
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["操作", "相对路径", "说明"])
                for action in self._actions:
                    writer.writerow([labels.get(action.kind, action.kind), action.rel, action.note or ""])
        except OSError as exc:
            self._preview_note.setText(f"导出失败：{exc}")
            self._preview_note.setStyleSheet(f"color: {DANGER};")
            return
        self._preview_note.setText(f"预演报告已保存：{path}")

    @staticmethod
    def ask(parent, title: str, intro: str, actions, skipped=None, danger=True) -> bool:
        dialog = ConfirmActionsDialog(parent, title, intro, actions, skipped, danger)
        return dialog.exec() == QDialog.DialogCode.Accepted


def _color(value: str):
    from PySide6.QtGui import QColor

    return QColor(value)


class SyncResultDialog(QDialog):
    def __init__(self, parent, report: SyncReport, total: int):
        super().__init__(parent)
        self.setWindowTitle(tr("同步结果"))
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"共 {total} 项操作，成功 {len(report.succeeded)} 项，失败 {len(report.failed)} 项"
            + ("，已取消" if report.cancelled else "")
        )
        summary.setStyleSheet("font-size: 13px; font-weight: 600; padding: 6px;")
        layout.addWidget(summary)

        table = QTableWidget(len(report.failed), 2, self)
        table.setHorizontalHeaderLabels(["相对路径", "失败原因"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, (action, reason) in enumerate(report.failed):
            table.setItem(row, 0, QTableWidgetItem(action.rel))
            table.setItem(row, 1, QTableWidgetItem(reason))
        layout.addWidget(table, 1)

        if not report.failed:
            tip = QLabel("全部操作已完成。建议执行一次刷新，确认两侧状态。")
            tip.setStyleSheet(f"color: {MUTED};")
            layout.addWidget(tip)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("关闭"))
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class OptionsDialog(QDialog):
    def __init__(self, parent, options: CompareOptions):
        super().__init__(parent)
        self.setWindowTitle(tr("比较选项"))
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.content = QCheckBox("比较文件内容（关闭时仅按大小与修改时间判断）")
        self.content.setChecked(options.compare_content)
        self.case = QCheckBox("忽略大小写")
        self.case.setChecked(options.ignore_case)
        self.space = QCheckBox("忽略空白字符差异")
        self.space.setChecked(options.ignore_whitespace)
        self.symlink = QCheckBox("展开符号链接目录（可能导致循环，慎用）")
        self.symlink.setChecked(options.follow_symlinks)
        for widget in (self.content, self.case, self.space, self.symlink):
            form.addRow(widget)

        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0, 3600)
        self.tolerance.setDecimals(1)
        self.tolerance.setSuffix(" 秒")
        self.tolerance.setValue(options.timestamp_tolerance)
        form.addRow("时间戳容差（未比较内容时）", self.tolerance)

        self.excludes = QLineEdit(", ".join(options.excludes))
        self.excludes.setPlaceholderText("如：*.tmp, .git, __pycache__")
        form.addRow("排除规则（逗号分隔）", self.excludes)
        layout.addLayout(form)

        tip = QLabel("提示：忽略大小写 / 空白会同时作用于文本对比与目录内容判定；修改后需重新比较。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {MUTED}; padding: 6px;")
        layout.addWidget(tip)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> CompareOptions:
        patterns = tuple(p.strip() for p in self.excludes.text().replace("，", ",").split(",") if p.strip())
        return CompareOptions(
            compare_content=self.content.isChecked(),
            ignore_case=self.case.isChecked(),
            ignore_whitespace=self.space.isChecked(),
            timestamp_tolerance=self.tolerance.value(),
            excludes=patterns,
            follow_symlinks=self.symlink.isChecked(),
        )


def ask_session_name(parent, default: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, "保存对比会话", "会话名称：", QLineEdit.EchoMode.Normal, default)
    return text.strip() if ok and text.strip() else None


def choose_session_path(parent, save: bool = False) -> Path | None:
    folder = str(session_dir())
    if save:
        path, _ = QFileDialog.getSaveFileName(parent, "保存会话", folder, "DiffScope 会话 (*.json)")
    else:
        path, _ = QFileDialog.getOpenFileName(parent, "打开会话", folder, "DiffScope 会话 (*.json)")
    return Path(path) if path else None


class RecentSessionsDialog(QDialog):
    def __init__(self, parent, sessions: list[tuple[Path, Session]]):
        super().__init__(parent)
        self.setWindowTitle(tr("最近的会话"))
        self.resize(560, 320)
        self.selected: Path | None = None
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self._paths: list[Path] = []
        for path, session in sessions:
            label = f"{session.name}    [{session.mode}]"
            detail = f"    {session.left}   ↔   {session.right}    ({session.saved_at})"
            self.list.addItem(label + "\n" + detail)
            self._paths.append(path)
        if not sessions:
            self.list.addItem("（暂无已保存的会话）")
        self.list.itemDoubleClicked.connect(self._accept_current)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        open_btn = QPushButton(tr("打开所选会话"))
        open_btn.setProperty("accent", True)
        open_btn.clicked.connect(self._accept_current)
        close_btn = QPushButton(tr("取消"))
        close_btn.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(open_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _accept_current(self) -> None:
        index = self.list.currentRow()
        if 0 <= index < len(self._paths):
            self.selected = self._paths[index]
            self.accept()


def show_about(parent) -> None:
    text = (
        f"{APP_NAME}  v{__version__}\n\n"
        "文件 / 文件夹对比、差异查看与合并同步工具。\n\n"
        "快捷键：\n"
        "  Ctrl+O      打开对比\n"
        "  Ctrl+R      重新比较\n"
        "  Ctrl+S      保存当前侧文件\n"
        "  Ctrl+Shift+S  保存会话      Ctrl+Shift+O  打开会话\n"
        "  F7 / F8     上一处 / 下一处差异\n"
        "  Ctrl+F      查找            F3  下一个匹配\n"
        "  Ctrl+← / →  合并当前差异块（左→右 / 右→左）\n"
    )
    _message(parent, QMessageBox.Icon.Information, f"关于 {APP_NAME}", text)
