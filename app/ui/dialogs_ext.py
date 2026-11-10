"""扩展对话框：设置、比较规则、对齐覆盖、同步模式、脚本、数据源、报告、帮助。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.alignment import AlignmentConfig, AlignmentRule
from ..core.merge import MergeDirection
from ..core import git_diff, sqlite_diff
from ..core.rules import CompareRules, IgnoreRule, COMMENT_PRESETS
from ..core.services import Settings, check_update, install_shell_integration, vcs_config_commands
from ..core.session import delete_template, list_templates, load_template, save_template
from ..core.sync_modes import SyncMode
from ..core.i18n import tr
from .theme import MUTED


def _buttons(parent) -> QDialogButtonBox:
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent
    )
    box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定"))
    box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
    box.accepted.connect(parent.accept)
    box.rejected.connect(parent.reject)
    return box


class SettingsDialog(QDialog):
    def __init__(self, parent, settings: Settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(tr("设置"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._appearance(), "外观")
        tabs.addTab(self._behavior(), "比较与文件")
        tabs.addTab(self._shortcuts(), "快捷键")
        tabs.addTab(self._integration(), "集成")
        layout.addWidget(tabs, 1)
        layout.addWidget(_buttons(self))

    def _appearance(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(self.settings.get("theme"))
        self.language = QComboBox()
        self.language.addItems(["zh", "en"])
        self.language.setCurrentText(str(self.settings.get("language")))
        form.addRow("界面语言（重启生效）", self.language)
        self.font = QLineEdit(self.settings.get("font_family"))
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 24)
        self.font_size.setValue(int(self.settings.get("font_size")))
        self.editor_font = QLineEdit(self.settings.get("editor_font_family"))
        self.editor_size = QSpinBox()
        self.editor_size.setRange(8, 24)
        self.editor_size.setValue(int(self.settings.get("editor_font_size")))
        self.wrap = QCheckBox("文本自动换行")
        self.wrap.setChecked(bool(self.settings.get("word_wrap")))
        self.syntax = QCheckBox("语法高亮")
        self.syntax.setChecked(bool(self.settings.get("syntax_highlight")))
        self.minimap = QCheckBox("显示缩略图概览")
        self.minimap.setChecked(bool(self.settings.get("show_minimap")))
        self.lines = QCheckBox("显示行号")
        self.lines.setChecked(bool(self.settings.get("show_line_numbers")))
        for label, widget in (("主题", self.theme), ("界面字体", self.font), ("界面字号", self.font_size),
                              ("编辑器字体", self.editor_font), ("编辑器字号", self.editor_size)):
            form.addRow(label, widget)
        for widget in (self.wrap, self.syntax, self.minimap, self.lines):
            form.addRow(widget)
        return page

    def _behavior(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.backup = QCheckBox("保存前自动备份")
        self.backup.setChecked(bool(self.settings.get("auto_backup")))
        self.auto_recompare = QCheckBox("编辑后自动重新比较（停止输入 0.8 秒后刷新差异）")
        self.auto_recompare.setChecked(bool(self.settings.get("auto_recompare", True)))
        self.backup_keep = QSpinBox()
        self.backup_keep.setRange(1, 100)
        self.backup_keep.setValue(int(self.settings.get("backup_keep")))
        self.content = QCheckBox("默认比较文件内容")
        self.content.setChecked(bool(self.settings.get("default_compare_content")))
        self.expand = QCheckBox("比较后展开到差异目录")
        self.expand.setChecked(bool(self.settings.get("expand_to_differences")))
        self.external = QLineEdit(str(self.settings.get("external_tool")))
        self.external.setPlaceholderText("第三方比较工具路径（处理不支持的文件）")
        self.updates = QCheckBox("启动时检查更新")
        self.updates.setChecked(bool(self.settings.get("check_updates")))
        self.update_url = QLineEdit(str(self.settings.get("update_url")))
        self.update_url.setPlaceholderText("版本信息 JSON 地址（含 version 字段）")
        self.max_text = QSpinBox()
        self.max_text.setRange(1, 1024)
        self.max_text.setSuffix(" MB")
        self.max_text.setValue(int(self.settings.get("max_text_mb")))
        self.max_nodes = QSpinBox()
        self.max_nodes.setRange(1000, 5_000_000)
        self.max_nodes.setSingleStep(10000)
        self.max_nodes.setValue(int(self.settings.get("max_nodes")))
        for label, widget in (("保留备份数", self.backup_keep), ("外部工具", self.external),
                              ("更新源", self.update_url), ("单文件文本上限", self.max_text),
                              ("目录条目上限", self.max_nodes)):
            form.addRow(label, widget)
        for widget in (self.backup, self.auto_recompare, self.content, self.expand, self.updates):
            form.addRow(widget)
        return page

    def _shortcuts(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.shortcut_edits: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for name, value in self.settings.get("shortcuts").items():
            edit = QLineEdit(str(value))
            form.addRow(name, edit)
            self.shortcut_edits[name] = edit
        layout.addLayout(form)
        layout.addWidget(QLabel("重启应用后快捷键生效。"))
        return page

    def _integration(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.shell = QCheckBox("安装 Windows 右键菜单集成")
        self.shell.setChecked(bool(self.settings.get("shell_integration")))
        layout.addWidget(self.shell)
        git_commands = vcs_config_commands(Path(__file__).resolve().parents[2] / "run.bat")
        browser = QTextBrowser()
        browser.setPlainText(
            "Git 外部 diff / merge 配置命令：\n" + "\n".join(git_commands["git"])
            + "\n\nSVN 配置：\n" + "\n".join(git_commands["svn"])
        )
        layout.addWidget(browser, 1)
        self.update_label = QLabel("")
        check = QPushButton(tr("检查更新"))
        check.clicked.connect(lambda: self.update_label.setText(check_update()))
        layout.addWidget(check)
        layout.addWidget(self.update_label)
        return page

    def apply(self) -> Settings:
        data = self.settings.data
        data.update({
            "theme": self.theme.currentText(),
            "font_family": self.font.text(),
            "font_size": self.font_size.value(),
            "editor_font_family": self.editor_font.text(),
            "editor_font_size": self.editor_size.value(),
            "word_wrap": self.wrap.isChecked(),
            "syntax_highlight": self.syntax.isChecked(),
            "show_minimap": self.minimap.isChecked(),
            "show_line_numbers": self.lines.isChecked(),
            "auto_backup": self.backup.isChecked(),
            "auto_recompare": self.auto_recompare.isChecked(),
            "backup_keep": self.backup_keep.value(),
            "default_compare_content": self.content.isChecked(),
            "expand_to_differences": self.expand.isChecked(),
            "external_tool": self.external.text(),
            "check_updates": self.updates.isChecked(),
            "language": self.language.currentText(),
            "update_url": self.update_url.text(),
            "max_text_mb": self.max_text.value(),
            "max_nodes": self.max_nodes.value(),
            "shortcuts": {name: edit.text() for name, edit in self.shortcut_edits.items()},
        })
        if self.shell.isChecked() and not self.settings.get("shell_integration"):
            data["shell_integration"] = install_shell_integration()
        else:
            data["shell_integration"] = self.shell.isChecked()
        self.settings.save()
        return self.settings


class GitDiffDialog(QDialog):
    """浏览 Git 仓库改动清单，选择文件比较 HEAD 版本与工作区。"""

    compare_requested = Signal(str, str)

    def __init__(self, parent, repo: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Git 仓库改动比较"))
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.repo_edit = QLineEdit(repo)
        self.repo_edit.setPlaceholderText(tr("Git 仓库根目录路径"))
        browse = QPushButton(tr("浏览…"))
        browse.clicked.connect(self._browse)
        refresh = QPushButton(tr("刷新改动"))
        refresh.clicked.connect(self._refresh)
        row.addWidget(QLabel(tr("仓库：")))
        row.addWidget(self.repo_edit, 1)
        row.addWidget(browse)
        row.addWidget(refresh)
        layout.addLayout(row)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self._compare())
        layout.addWidget(self.list, 1)
        self.note = QLabel("")
        self.note.setStyleSheet(f"color: {MUTED};")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        compare_btn = QPushButton(tr("比较选中文件（HEAD ⇄ 工作区）"))
        compare_btn.setProperty("accent", True)
        compare_btn.clicked.connect(self._compare)
        buttons.addWidget(compare_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(_buttons(self))

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("选择 Git 仓库"), self.repo_edit.text() or str(Path.home()))
        if path:
            self.repo_edit.setText(path)
            self._refresh()

    def _refresh(self) -> None:
        from .dialogs import show_error

        self.list.clear()
        repo = Path(self.repo_edit.text().strip())
        if not repo.exists():
            self.note.setText(tr("路径不存在。"))
            return
        try:
            entries = git_diff.changed_files(repo)
        except Exception as exc:
            show_error(self, exc, "读取 Git 改动")
            return
        if not entries:
            self.note.setText(tr("工作区干净：没有未提交的改动。"))
            return
        for code, rel, desc in entries:
            item = QListWidgetItem(f"{desc:<8} {rel}")
            item.setData(Qt.ItemDataRole.UserRole, rel)
            self.list.addItem(item)
        self.note.setText(tr(f"共 {len(entries)} 项改动。双击或选中后点“比较”。"))

    def _compare(self) -> None:
        import tempfile

        from .dialogs import show_error, show_info

        item = self.list.currentItem()
        if item is None:
            return
        repo = Path(self.repo_edit.text().strip())
        rel = item.data(Qt.ItemDataRole.UserRole)
        try:
            head_path = git_diff.export_head_file(repo, rel, Path(tempfile.gettempdir()) / "diffscope-git")
        except Exception as exc:
            show_error(self, exc, "读取 HEAD 版本")
            return
        working = repo / rel
        if head_path is None:
            show_info(self, "无 HEAD 版本", f"{rel} 是新增文件，HEAD 中不存在，无法比较。")
            return
        self.compare_requested.emit(str(head_path), str(working))
        self.accept()


class SqliteDiffDialog(QDialog):
    """SQLite 表数据比较：两库同名表按主键对齐，列出增/删/改。"""

    def __init__(self, parent, left: str = "", right: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("SQLite 数据比较"))
        self.resize(920, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.left_edit = QLineEdit(left)
        self.right_edit = QLineEdit(right)
        for name, edit in (("左侧数据库", self.left_edit), ("右侧数据库", self.right_edit)):
            edit.setPlaceholderText(tr("数据库文件路径（.db / .sqlite）"))
            browse = QPushButton(tr("浏览…"))
            browse.clicked.connect(lambda _=False, e=edit: self._browse(e))
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(browse)
            form.addRow(name, row)
        layout.addLayout(form)
        controls = QHBoxLayout()
        self.table_box = QComboBox()
        self.table_box.setMinimumWidth(220)
        load_btn = QPushButton(tr("加载表清单"))
        load_btn.clicked.connect(self._load_tables)
        compare_btn = QPushButton(tr("比较选中表"))
        compare_btn.setProperty("accent", True)
        compare_btn.clicked.connect(self._compare)
        controls.addWidget(QLabel(tr("数据表：")))
        controls.addWidget(self.table_box, 1)
        controls.addWidget(load_btn)
        controls.addWidget(compare_btn)
        layout.addLayout(controls)
        self.summary = QLabel(tr("加载数据库后选择表进行比较。"))
        self.summary.setStyleSheet(f"color: {MUTED};")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

    def _browse(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("选择数据库"), str(Path.home()),
                                              "SQLite 数据库 (*.db *.sqlite *.sqlite3);;所有文件 (*.*)")
        if path:
            edit.setText(path)

    def _load_tables(self) -> None:
        from .dialogs import show_error

        self.table_box.clear()
        try:
            left_tables = sqlite_diff.list_tables(Path(self.left_edit.text().strip()))
            right_tables = set(sqlite_diff.list_tables(Path(self.right_edit.text().strip())))
        except Exception as exc:
            show_error(self, exc, "加载数据库")
            return
        common = [t for t in left_tables if t in right_tables]
        if not common:
            self.summary.setText(tr("两侧没有同名的数据表。"))
            return
        self.table_box.addItems(common)
        self.summary.setText(tr(f"共 {len(common)} 张同名表。"))

    def _compare(self) -> None:
        from .dialogs import show_error

        if not self.table_box.currentText():
            return
        try:
            result = sqlite_diff.compare_tables(
                Path(self.left_edit.text().strip()), Path(self.right_edit.text().strip()),
                self.table_box.currentText(),
            )
        except Exception as exc:
            show_error(self, exc, "比较数据表")
            return
        names = {"only_left": "仅左侧", "only_right": "仅右侧", "modified": "已修改"}
        colors = {"only_left": "#63c2f8", "only_right": "#c9a6f7", "modified": "#f0ba5e"}

        def brief(record) -> str:
            if record is None:
                return "—"
            parts = [f"{col}={record[col]}" for col in result.columns
                     if col != result.key_column][:4]
            return ", ".join(parts)[:120]

        self.table.clear()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([tr("类型"), tr(result.key_column), tr("变更列"), tr("左侧值"), tr("右侧值")])
        from PySide6.QtGui import QColor

        changes = result.modified + result.only_left + result.only_right
        self.table.setRowCount(len(changes))
        for row, change in enumerate(changes):
            kind_item = QTableWidgetItem(names[change.kind])
            kind_item.setForeground(QColor(colors[change.kind]))
            self.table.setItem(row, 0, kind_item)
            self.table.setItem(row, 1, QTableWidgetItem(change.key))
            self.table.setItem(row, 2, QTableWidgetItem(", ".join(change.changed_columns)))
            self.table.setItem(row, 3, QTableWidgetItem(brief(change.left)))
            self.table.setItem(row, 4, QTableWidgetItem(brief(change.right)))
        self.table.resizeColumnsToContents()
        trunc = tr("（超过单表上限，已截断）") if result.truncated else ""
        self.summary.setText(
            tr(f"表 {result.table}（主键 {result.key_column}）：修改 {len(result.modified)}、"
               f"仅左侧 {len(result.only_left)}、仅右侧 {len(result.only_right)}、相同 {result.same_count}{trunc}")
        )


class TemplateDialog(QDialog):
    """比较模板：把当前「比较规则 + 比较选项」存成命名模板，之后一键应用。"""

    def __init__(self, parent, rules: CompareRules, options):
        super().__init__(parent)
        self.rules = rules
        self.options = options
        self.applied = None  # 应用成功时为 (rules, options)
        self.setWindowTitle(tr("比较模板"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self._apply())
        layout.addWidget(QLabel("模板保存比较规则与比较选项（不含路径），适合按项目语言复用。"))
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        save = QPushButton(tr("保存当前配置…"))
        save.clicked.connect(self._save)
        apply_btn = QPushButton(tr("应用选中模板"))
        apply_btn.setProperty("accent", True)
        apply_btn.clicked.connect(self._apply)
        delete_btn = QPushButton(tr("删除"))
        delete_btn.clicked.connect(self._delete)
        buttons.addWidget(save)
        buttons.addWidget(apply_btn)
        buttons.addWidget(delete_btn)
        layout.addLayout(buttons)
        layout.addWidget(_buttons(self))
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for path, data in list_templates():
            item = QListWidgetItem(f"{data.get('name', path.stem)}（{data.get('saved_at', '')}）")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list.addItem(item)
        if not self.list.count():
            self.list.addItem(QListWidgetItem("（暂无模板，点击“保存当前配置”创建）"))

    def _save(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, tr("保存模板"), tr("模板名称："))
        if not ok or not name.strip():
            return
        try:
            save_template(name, self.rules, self.options)
        except Exception as exc:
            from .dialogs import show_error

            show_error(self, exc, "保存模板")
            return
        self._refresh()

    def _apply(self) -> None:
        item = self.list.currentItem()
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        from ..core.models import CompareOptions

        try:
            data = load_template(item.data(Qt.ItemDataRole.UserRole))
            self.applied = (CompareRules.from_dict(data.get("rules")), CompareOptions.from_dict(data.get("options")))
        except Exception as exc:
            from .dialogs import show_error

            show_error(self, exc, "应用模板")
            return
        self.accept()

    def _delete(self) -> None:
        item = self.list.currentItem()
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        try:
            delete_template(item.data(Qt.ItemDataRole.UserRole))
        except Exception as exc:
            from .dialogs import show_error

            show_error(self, exc, "删除模板")
            return
        self._refresh()


class SelectiveMergeDialog(QDialog):
    """勾选要应用的差异块，统一向一个方向合并。"""

    def __init__(self, parent, blocks: list, rows: list):
        super().__init__(parent)
        self.setWindowTitle(tr("选择性合并"))
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("勾选需要合并的差异块；只读过滤视图下不可用。")))
        self.list = QListWidget()
        status_names = {"replace": "修改", "delete": "仅左侧", "insert": "仅右侧"}
        for index, block_rows in enumerate(blocks, 1):
            first = rows[block_rows[0]] if rows and block_rows else None
            if first is None:
                continue
            status = status_names.get(first.op, first.op)
            text = (first.left_text if first.op != "insert" else first.right_text) or ""
            item = QListWidgetItem(f"{index}. 左 {first.left_no or '—'} / 右 {first.right_no or '—'}  {status}  {text.expandtabs(2)[:48]}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, index - 1)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        controls = QHBoxLayout()
        layout.addWidget(QLabel(tr("合并方向：")))
        self.direction_box = QComboBox()
        self.direction_box.addItem(MergeDirection.LEFT_TO_RIGHT.label, MergeDirection.LEFT_TO_RIGHT)
        self.direction_box.addItem(MergeDirection.RIGHT_TO_LEFT.label, MergeDirection.RIGHT_TO_LEFT)
        controls.addWidget(self.direction_box)
        toggle_all = QPushButton(tr("全选 / 全不选"))
        toggle_all.clicked.connect(self._toggle_all)
        controls.addWidget(toggle_all)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(_buttons(self))

    def _toggle_all(self) -> None:
        first = self.list.item(0)
        target = Qt.CheckState.Unchecked if first and first.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(target)

    def selected_blocks(self) -> list[int]:
        return [item.data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if (item := self.list.item(i)) and item.checkState() == Qt.CheckState.Checked]

    def direction(self) -> MergeDirection:
        return self.direction_box.currentData()


class RulesDialog(QDialog):
    def __init__(self, parent, rules: CompareRules):
        super().__init__(parent)
        self.rules = rules
        self.setWindowTitle(tr("比较规则 / 忽略不重要差异"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.language = QComboBox()
        self.language.addItems(["(无)"] + sorted(COMMENT_PRESETS))
        self.language.setCurrentText(rules.language or "(无)")
        self.kind = QComboBox()
        self.kind.addItems(["comments", "strings", "regex", "columns", "header",
                            "line_endings", "replacement", "unimportant", "selection"])
        self.value = QLineEdit()
        self.value.setPlaceholderText("参数：语言/正则/列号(逗号)/行数/替换文本")
        add = QPushButton("添加规则")
        add.clicked.connect(self._add)
        bar.addWidget(self.language)
        bar.addWidget(self.kind)
        bar.addWidget(self.value, 1)
        bar.addWidget(add)
        layout.addLayout(bar)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._remove())
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        remove = QPushButton("删除选中规则")
        remove.clicked.connect(self._remove)
        preset = QPushButton("应用语言预设")
        preset.clicked.connect(self._preset)
        row.addWidget(remove)
        row.addWidget(preset)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel(
            "说明：comments/strings/regex 移除匹配片段；columns 填列号；header 填行数；"
            "replacement 填 left=>right；selection 填 起:止 行号。"
        ))
        layout.addWidget(_buttons(self))
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for rule in self.rules.rules:
            self.list.addItem(f"{rule.kind} | {rule.pattern} | {rule.value} | "
                              f"{'启用' if rule.enabled else '停用'}")

    def _add(self) -> None:
        kind, text = self.kind.currentText(), self.value.text().strip()
        value: object = text
        if kind == "header":
            value = int(text) if text.isdigit() else 0
        elif kind == "columns":
            value = [int(x) for x in text.split(",") if x.strip().isdigit()]
        elif kind == "selection":
            parts = text.split(":")
            value = [(int(parts[0]), int(parts[1]))] if len(parts) == 2 and all(p.isdigit() for p in parts) else []
        elif kind == "replacement":
            pair = text.split("=>")
            value = {"left": pair[0].strip(), "right": pair[1].strip()} if len(pair) == 2 else {"left": text, "right": ""}
        language = "" if self.language.currentText() == "(无)" else self.language.currentText()
        self.rules.rules.append(IgnoreRule(kind, language, True, value))
        self.rules.language = language
        self._refresh()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.rules.rules):
            self.rules.rules.pop(row)
            self._refresh()

    def _preset(self) -> None:
        name = self.language.currentText()
        if name in COMMENT_PRESETS:
            self.rules.rules.extend(IgnoreRule("comments", name) for _ in COMMENT_PRESETS[name])
            self.rules.language = name
            self._refresh()


class AlignmentDialog(QDialog):
    def __init__(self, parent, config: AlignmentConfig):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("文件夹对齐覆盖"))
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.left = QLineEdit()
        self.left.setPlaceholderText("左侧掩码，如 *.gif / Apples*")
        self.right = QLineEdit()
        self.right.setPlaceholderText("右侧掩码，如 *.png / Bananas*")
        add = QPushButton(tr("添加"))
        add.clicked.connect(self._add)
        bar.addWidget(self.left)
        bar.addWidget(self.right)
        bar.addWidget(add)
        layout.addLayout(bar)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.unicode = QCheckBox("对齐不同 Unicode 规范化形式的文件名")
        self.unicode.setChecked(config.unicode_normalize)
        self.case = QCheckBox("文件名忽略大小写")
        self.case.setChecked(config.ignore_case)
        layout.addWidget(self.unicode)
        layout.addWidget(self.case)
        row = QHBoxLayout()
        remove = QPushButton("删除选中")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(_buttons(self))
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for rule in self.config.rules:
            self.list.addItem(f"{rule.left_pattern}  →  {rule.right_pattern}")

    def _add(self) -> None:
        if self.left.text().strip() and self.right.text().strip():
            self.config.rules.append(AlignmentRule(self.left.text().strip(), self.right.text().strip()))
            self._refresh()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.config.rules):
            self.config.rules.pop(row)
            self._refresh()

    def values(self) -> AlignmentConfig:
        self.config.unicode_normalize = self.unicode.isChecked()
        self.config.ignore_case = self.case.isChecked()
        return self.config


class SyncModeDialog(QDialog):
    def __init__(self, parent, mode: SyncMode, count: int):
        super().__init__(parent)
        self.setWindowTitle(tr("目录同步模式"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mode = QComboBox()
        for item in SyncMode:
            self.mode.addItem(item.label, item.value)
        self.mode.setCurrentIndex(list(SyncMode).index(mode))
        self.direction = QComboBox()
        self.direction.addItems(["左侧 → 右侧", "右侧 → 左侧"])
        form.addRow("同步模式", self.mode)
        form.addRow("方向", self.direction)
        layout.addLayout(form)
        layout.addWidget(QLabel(f"将处理 {count} 个条目；执行前会再次列出完整操作清单。"))
        layout.addWidget(_buttons(self))

    def values(self):
        from ..core.merge import MergeDirection

        return (SyncMode(self.mode.currentData()),
                MergeDirection.LEFT_TO_RIGHT if self.direction.currentIndex() == 0
                else MergeDirection.RIGHT_TO_LEFT)


class ScriptDialog(QDialog):
    def __init__(self, parent, text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("脚本处理器"))
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "命令：load / compare / criteria / filter / select / folder-report / text-report / "
            "file-report / sync / snapshot / log；# 开头为注释。"
        ))
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text or (
            '# 示例：比较两个目录并生成 HTML 报告\n'
            'criteria ignore-case:on\n'
            'load "C:\\left" "C:\\right"\n'
            'compare\n'
            'folder-report output-to:report.html\n'
        ))
        layout.addWidget(self.editor, 1)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(160)
        layout.addWidget(self.output)
        row = QHBoxLayout()
        open_btn = QPushButton("打开脚本…")
        open_btn.clicked.connect(self._open)
        self.allow = QCheckBox("允许写操作（sync）")
        run = QPushButton(tr("运行"))
        run.setProperty("accent", True)
        run.clicked.connect(self._run)
        row.addWidget(open_btn)
        row.addWidget(self.allow)
        row.addStretch(1)
        row.addWidget(run)
        layout.addLayout(row)
        self.result_code = 0
        layout.addWidget(_buttons(self))

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开脚本", "", "脚本 (*.txt *.bcpkgs);;所有文件 (*.*)")
        if path:
            self.editor.setPlainText(Path(path).read_text(encoding="utf-8", errors="replace"))

    def _run(self) -> None:
        from ..core.scripting import ScriptRunner

        runner = ScriptRunner(Path.cwd(), allow_write=self.allow.isChecked())
        result = runner.run(self.editor.toPlainText())
        self.result_code = result.exit_code
        self.output.setPlainText(result.text() + f"\n退出码：{result.exit_code}")


class SourceDialog(QDialog):
    def __init__(self, parent, side: str = "left"):
        super().__init__(parent)
        self.setWindowTitle(f"打开数据源（{side}）")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItems([
            "本地路径 / UNC", "ZIP/JAR 压缩包", "TAR 压缩包", "文件系统快照",
            "FTP / FTPS", "SFTP", "WebDAV / SVN", "Amazon S3",
            "Dropbox", "OneDrive", "Windows 注册表",
        ])
        self.spec = QLineEdit()
        self.spec.setPlaceholderText("路径或 URL，例如 ftp://user:pass@host/dir、s3://bucket/prefix")
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.token = QLineEdit()
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("附加参数：region / access_key / key（SSH 私钥）")
        for label, widget in (("类型", self.kind), ("地址", self.spec), ("用户名", self.user),
                              ("密码", self.password), ("令牌", self.token), ("附加参数", self.extra)):
            form.addRow(label, widget)
        layout.addLayout(form)
        browse = QPushButton(tr("浏览本地文件/文件夹…"))
        browse.clicked.connect(self._browse)
        layout.addWidget(browse)
        layout.addWidget(QLabel("注意：FTP/云盘/注册表会发起实际连接，请确认凭证与目标位置。"))
        layout.addWidget(_buttons(self))

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self.spec.setText(path)

    def values(self) -> tuple[str, dict]:
        index = self.kind.currentIndex()
        spec = self.spec.text().strip()
        prefixes = ["", "zip:", "tar:", "snapshot:", "ftp://", "sftp://", "webdav://",
                    "s3://", "dropbox://", "onedrive://", "reg://"]
        prefix = prefixes[index]
        if prefix and prefix.startswith(("ftp://", "sftp://", "webdav://", "s3://",
                                         "dropbox://", "onedrive://")):
            if spec.startswith(prefix) or "://" in spec:
                full = spec
            else:
                full = prefix + spec
        elif prefix and not spec.startswith(prefix):
            full = prefix + spec
        else:
            full = spec
        options = {"user": self.user.text(), "password": self.password.text(),
                   "token": self.token.text()}
        for item in self.extra.text().replace("，", ",").split(","):
            if "=" in item:
                key, _, value = item.partition("=")
                options[key.strip()] = value.strip()
        return full, options


class ReportDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("生成比较报告"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItems(["HTML", "CSV", "纯文本"])
        self.path = QLineEdit()
        self.path.setPlaceholderText("输出文件路径")
        browse = QPushButton("…")
        browse.setMaximumWidth(36)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.linked = QCheckBox("同时生成可点击的单文件报告")
        self.linked.setChecked(True)
        self.print_box = QCheckBox("生成后打印报告")
        form.addRow("格式", self.format)
        form.addRow("输出", row)
        layout.addLayout(form)
        layout.addWidget(self.linked)
        layout.addWidget(self.print_box)
        layout.addWidget(_buttons(self))

    def _browse(self) -> None:
        suffix = {"HTML": "*.html", "CSV": "*.csv", "纯文本": "*.txt"}[self.format.currentText()]
        path, _ = QFileDialog.getSaveFileName(self, "保存报告", "", f"报告 ({suffix})")
        if path:
            self.path.setText(path)

    def values(self) -> tuple[str, Path, bool]:
        fmt = {"HTML": "html", "CSV": "csv", "纯文本": "txt"}[self.format.currentText()]
        return fmt, Path(self.path.text()), self.linked.isChecked()


class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("帮助"))
        self.resize(680, 520)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setHtml(f"""
        <h2>DiffScope {__version__} 使用说明</h2>
        <p>首版覆盖文件/文件夹对比，并扩展了多种专用比较器与数据源。</p>
        <h3>数据源</h3>
        <p>本地路径、UNC、ZIP/TAR 压缩包、文件系统快照、FTP/FTPS、SFTP、WebDAV、SVN、
        Amazon S3、Dropbox、OneDrive、Windows 注册表。远程数据源需要凭证，连接失败会给出明确提示。</p>
        <h3>比较类型</h3>
        <p>文本、文件夹、表格（CSV/TSV/HTML 表格/Excel）、十六进制、图片、
        MP3/FLAC/MP4/OGG 标签、exe/dll 版本信息、注册表、PDF/Word/RTF 转文本、补丁文件。</p>
        <h3>合并与同步</h3>
        <p>两路文本合并（按块/整文件）、三向文本与文件夹合并、单向覆盖/镜像/更新/双向同步，
        所有写操作都必须先确认操作清单。删除优先进入回收站。</p>
        <h3>自动化</h3>
        <p>命令行 <code>python cli.py --diff L R --report r.html</code>、
        脚本 <code>python cli.py --script s.txt --allow-write</code>、
        三向合并 <code>python cli.py --merge BASE LEFT RIGHT OUT</code>。
        退出码：0 无差异 / 1 有差异 / 2 错误 / 3 合并冲突。</p>
        <h3>快捷键</h3>
        <p>Ctrl+O 打开 · Ctrl+R 刷新 · Ctrl+S 保存 · F7/F8 差异导航 · Ctrl+F 查找 ·
        F3 下一个匹配 · Ctrl+Shift+S 保存会话 · Ctrl+Shift+O 打开会话</p>
        """)
        layout.addWidget(browser, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText(tr("关闭"))
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)


class SnapshotDialog(QDialog):
    def __init__(self, parent, default_root: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("创建文件系统快照"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.root = QLineEdit(default_root)
        self.output = QLineEdit()
        self.excludes = QLineEdit("*.tmp, .git, __pycache__")
        for label, widget in (("目录", self.root), ("快照文件", self.output), ("排除规则", self.excludes)):
            form.addRow(label, widget)
        layout.addLayout(form)
        row = QHBoxLayout()
        pick = QPushButton(tr("选择目录"))
        pick.clicked.connect(self._pick_root)
        save = QPushButton(tr("选择输出位置"))
        save.clicked.connect(self._pick_output)
        row.addWidget(pick)
        row.addWidget(save)
        layout.addLayout(row)
        layout.addWidget(_buttons(self))

    def _pick_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要快照的目录", self.root.text())
        if path:
            self.root.setText(path)

    def _pick_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存快照", "", "快照 (*.json)")
        if path:
            self.output.setText(path)

    def values(self) -> tuple[str, str, tuple[str, ...]]:
        excludes = tuple(x.strip() for x in self.excludes.text().replace("，", ",").split(",") if x.strip())
        return self.root.text(), self.output.text(), excludes


class FormatEditorDialog(QDialog):
    """文件格式规则编辑器：扩展名 → 语法语言 / 查看器 / 外部转换器 / 忽略规则。"""

    def __init__(self, parent, registry):
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle(tr("文件格式"))
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("格式列表"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load_current)
        left_layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        for label, handler in (("新建", self._add), ("删除", self._remove), ("恢复默认", self._reset)):
            button = QPushButton(tr(label))
            button.clicked.connect(handler)
            row.addWidget(button)
        row2 = QHBoxLayout()
        for label, handler in (("导出…", self._export), ("导入…", self._import)):
            button = QPushButton(tr(label))
            button.clicked.connect(handler)
            row2.addWidget(button)
        left_layout.addLayout(row)
        left_layout.addLayout(row2)
        splitter.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        self.name = QLineEdit()
        self.extensions = QLineEdit()
        self.extensions.setPlaceholderText("逗号分隔，如 .py, .pyw")
        self.language = QComboBox()
        self.language.setEditable(True)
        self.language.addItems(["", "python", "cpp", "java", "javascript", "sql", "csharp", "xml", "html", "ini"])
        self.viewer = QComboBox()
        self.viewer.addItems(["auto", "text", "table", "hex", "image", "tags", "registry"])
        self.converter = QLineEdit()
        self.converter.setPlaceholderText("外部转换命令，{in} 输入 / {out} 输出，如 ConvertDfm.exe {in} {out}")
        for label, widget in (("名称", self.name), ("扩展名", self.extensions),
                              ("语法语言", self.language), ("查看器", self.viewer),
                              ("转换程序", self.converter)):
            form.addRow(label, widget)
        edit_rules = QPushButton("编辑忽略规则…")
        edit_rules.clicked.connect(self._edit_rules)
        form.addRow(edit_rules)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        layout.addWidget(_buttons(self))
        self._reload()
        if self.registry.formats:
            self.list.setCurrentRow(0)

    def _reload(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for item in self.registry.formats:
            self.list.addItem(f"{item.name}  ({', '.join(item.extensions) or '无扩展名'})")
        self.list.blockSignals(False)

    def _current(self):
        row = self.list.currentRow()
        return self.registry.formats[row] if 0 <= row < len(self.registry.formats) else None

    def _load_current(self, row: int) -> None:
        item = self._current()
        if item is None:
            return
        self.name.setText(item.name)
        self.extensions.setText(", ".join(item.extensions))
        self.language.setCurrentText(item.language)
        self.viewer.setCurrentText(item.viewer)
        self.converter.setText(item.converter)

    def _store_current(self) -> None:
        item = self._current()
        if item is None:
            return
        item.name = self.name.text().strip() or item.name
        item.extensions = tuple(
            "." + part.strip().lower().lstrip(".") for part in self.extensions.text().replace("，", ",").split(",")
            if part.strip()
        )
        item.language = self.language.currentText().strip()
        item.viewer = self.viewer.currentText()
        item.converter = self.converter.text().strip()
        self._reload()

    def _add(self) -> None:
        from ..core.file_formats import FileFormat

        self.registry.formats.append(FileFormat("新格式", (".ext",)))
        self._reload()
        self.list.setCurrentRow(len(self.registry.formats) - 1)

    def _remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.registry.formats):
            self.registry.formats.pop(row)
            self._reload()

    def _reset(self) -> None:
        from ..core.file_formats import default_formats

        self.registry.formats = default_formats()
        self._reload()

    def _export(self) -> None:
        from ..core.file_formats import export_formats

        path, _ = QFileDialog.getSaveFileName(self, tr("导出…"), "", "格式规则 (*.json)")
        if path:
            export_formats(self.registry, Path(path))
            self.status_hint()

    def _import(self) -> None:
        from ..core.file_formats import import_formats

        path, _ = QFileDialog.getOpenFileName(self, tr("导入…"), "", "格式规则 (*.json)")
        if not path:
            return
        from PySide6.QtWidgets import QMessageBox

        replace = QMessageBox.question(
            self, tr("导入…"),
            "导入方式：选 Yes 覆盖全部现有格式，选 No 合并（同名覆盖、其余保留）。"
        ) == QMessageBox.StandardButton.Yes
        count = import_formats(self.registry, Path(path), replace=replace)
        self._reload()
        QMessageBox.information(self, tr("导入…"), f"已导入 {count} 条格式规则。")

    def status_hint(self) -> None:
        pass

    def _edit_rules(self) -> None:
        item = self._current()
        if item is None:
            return
        RulesDialog(self, item.rules).exec()

    def accept(self) -> None:
        self._store_current()
        try:
            self.registry.save()
        except Exception as exc:
            from .dialogs import show_error

            show_error(self, exc, "保存文件格式")
            return
        super().accept()
