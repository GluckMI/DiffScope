"""扩展对话框：设置、比较规则、对齐覆盖、同步模式、脚本、数据源、报告、帮助。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
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
    QTableWidget,
    QTableWidgetItem,
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
from .theme import (
    FONT_BASE, FONT_HELP_BODY, FONT_HELP_H2, FONT_HELP_H3, FONT_NOTE,
    apply_role, mono_font_stack, ui_font_stack,
)


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
        footer = QHBoxLayout()
        self.btn_reset = QPushButton(tr("恢复默认外观"))
        self.btn_reset.setToolTip("只重置主题、字体与字号，不改动比较与集成设置")
        self.btn_reset.clicked.connect(self._reset_appearance)
        footer.addWidget(self.btn_reset)
        footer.addStretch(1)
        footer.addWidget(_buttons(self))
        layout.addLayout(footer)

    def _appearance(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.theme = QComboBox()
        for label, value in (("深色", "dark"), ("浅色", "light"), ("跟随系统", "system")):
            self.theme.addItem(label, value)
        index = self.theme.findData(str(self.settings.get("theme")))
        self.theme.setCurrentIndex(max(0, index))
        self.language = QComboBox()
        self.language.addItems(["zh", "en"])
        self.language.setCurrentText(str(self.settings.get("language")))
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
        self.reduce_motion = QCheckBox("减少界面动效")
        self.reduce_motion.setChecked(bool(self.settings.get("reduce_motion", False)))
        self.show_onboarding = QCheckBox("下次启动显示新手引导")
        self.show_onboarding.setChecked(bool(self.settings.get("show_onboarding", True)))
        self._build_color_picker(form)
        for label, widget in (("主题", self.theme), ("界面语言（重启生效）", self.language),
                              ("界面字体", self.font), ("界面字号", self.font_size),
                              ("编辑器字体", self.editor_font), ("编辑器字号", self.editor_size)):
            form.addRow(label, widget)
        for widget in (self.wrap, self.syntax, self.minimap, self.lines,
                       self.reduce_motion, self.show_onboarding):
            form.addRow(widget)
        return page

    def _build_color_picker(self, form: QFormLayout) -> None:
        """差异徽章自定义色：仅覆盖徽章，文字色由主题自动保证对比度。"""
        from PySide6.QtGui import QColor
        from .theme import DARK, _on_badge, custom_color_keys

        labels = {"modified": "修改", "left_only": "仅左", "right_only": "仅右",
                  "folder_diff": "含差异目录", "error": "错误"}
        saved = dict(self.settings.get("colors") or {})
        self.color_buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        row.setSpacing(8)
        for key in custom_color_keys():
            button = QPushButton(labels.get(key, key))
            default = DARK["status"][key][2]
            self._paint_color_button(button, str(saved.get(key) or default))
            button.clicked.connect(
                lambda checked=False, b=button, k=key, d=default: self._pick_color(b, k, d)
            )
            row.addWidget(button)
            self.color_buttons[key] = button
        form.addRow("差异徽章配色", row)
        note = QLabel("自定义色只作用于状态徽章，文字色自动匹配对比度；「恢复默认外观」可清除。")
        apply_role(note, "note")
        note.setWordWrap(True)
        form.addRow(note)

    @staticmethod
    def _paint_color_button(button: QPushButton, color: str) -> None:
        from .theme import _on_badge

        button.setProperty("dsColorValue", color)
        button.setStyleSheet(
            f"QPushButton {{ background: {color}; color: {_on_badge(color)};"
            f" border: 1px solid {color}; border-radius: 4px; padding: 4px 10px; }}"
        )

    def _pick_color(self, button: QPushButton, key: str, default: str) -> None:
        from PySide6.QtGui import QColor

        current = QColor(str(button.property("dsColorValue") or default))
        chosen = QColorDialog.getColor(current, self, "选择差异徽章颜色")
        if chosen.isValid():
            self._paint_color_button(button, chosen.name())

    def _reset_appearance(self) -> None:
        """恢复默认外观：只重置本页字段，需点击「确定」才写入。"""
        from .theme import DARK, default_mono_font, default_ui_font

        self.theme.setCurrentIndex(0)
        self.language.setCurrentText("zh")
        self.font.setText(default_ui_font())
        self.font_size.setValue(FONT_BASE)
        self.editor_font.setText(default_mono_font())
        self.editor_size.setValue(FONT_BASE)
        self.wrap.setChecked(False)
        self.syntax.setChecked(True)
        self.minimap.setChecked(True)
        self.lines.setChecked(True)
        self.reduce_motion.setChecked(False)
        self.show_onboarding.setChecked(True)
        for key, button in self.color_buttons.items():
            self._paint_color_button(button, DARK["status"][key][2])

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
            "theme": self.theme.currentData() or "dark",
            "font_family": self.font.text(),
            "font_size": self.font_size.value(),
            "editor_font_family": self.editor_font.text(),
            "editor_font_size": self.editor_size.value(),
            "word_wrap": self.wrap.isChecked(),
            "syntax_highlight": self.syntax.isChecked(),
            "show_minimap": self.minimap.isChecked(),
            "show_line_numbers": self.lines.isChecked(),
            "reduce_motion": self.reduce_motion.isChecked(),
            "show_onboarding": self.show_onboarding.isChecked(),
            "colors": {key: str(button.property("dsColorValue"))
                       for key, button in self.color_buttons.items()},
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
        apply_role(self.note, "muted")
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
        from .dialog_kit import section_bar

        layout = QVBoxLayout(self)
        layout.addWidget(section_bar("数据库文件"))
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
        apply_role(self.summary, "muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addWidget(section_bar("比较结果"))
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
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
        from .theme import STATUS_COLORS
        from ..core.models import DiffStatus

        names = {"only_left": "< 仅左侧", "only_right": "> 仅右侧", "modified": "≠ 已修改"}
        colors = {
            "only_left": STATUS_COLORS[DiffStatus.LEFT_ONLY][0],
            "only_right": STATUS_COLORS[DiffStatus.RIGHT_ONLY][0],
            "modified": STATUS_COLORS[DiffStatus.MODIFIED][0],
        }

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
        from .dialog_kit import section_bar

        layout = QVBoxLayout(self)
        layout.addWidget(section_bar("新增规则"))
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
        layout.addWidget(section_bar("当前规则"))
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
        note = QLabel(
            "说明：comments/strings/regex 移除匹配片段；columns 填列号；header 填行数；"
            "replacement 填 left=>right；selection 填 起:止 行号。"
        )
        note.setWordWrap(True)
        apply_role(note, "note")
        layout.addWidget(note)
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
        from .dialog_kit import section_bar

        layout = QVBoxLayout(self)
        hint = QLabel(
            "命令：load / compare / criteria / filter / select / folder-report / text-report / "
            "file-report / sync / snapshot / log；# 开头为注释。"
        )
        hint.setWordWrap(True)
        apply_role(hint, "note")
        layout.addWidget(hint)
        layout.addWidget(section_bar("脚本内容"))
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text or (
            '# 示例：比较两个目录并生成 HTML 报告\n'
            'criteria ignore-case:on\n'
            'load "C:\\left" "C:\\right"\n'
            'compare\n'
            'folder-report output-to:report.html\n'
        ))
        layout.addWidget(self.editor, 1)
        layout.addWidget(section_bar("执行结果"))
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
        from .dialog_kit import section_title

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
        form.addRow(section_title("连接信息"))
        for label, widget in (("类型", self.kind), ("地址", self.spec)):
            form.addRow(label, widget)
        form.addRow(section_title("凭证与参数"))
        for label, widget in (("用户名", self.user), ("密码", self.password),
                              ("令牌", self.token), ("附加参数", self.extra)):
            form.addRow(label, widget)
        layout.addLayout(form)
        browse = QPushButton(tr("浏览本地文件/文件夹…"))
        browse.clicked.connect(self._browse)
        layout.addWidget(browse)
        caution = QLabel("注意：FTP/云盘/注册表会发起实际连接，请确认凭证与目标位置。")
        caution.setWordWrap(True)
        apply_role(caution, "warning-note")
        layout.addWidget(caution)
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


def _help_css() -> str:
    """帮助正文的 HTML 样式跟随主题，避免深色主题下出现深底深字。"""
    from .theme import c

    return f"""
<style>
body{{font-family:{ui_font_stack()};font-size:{FONT_HELP_BODY}px;line-height:1.65;
     color:{c('text')};background:{c('panel_alt')}}}
h2{{font-size:{FONT_HELP_H2}px;margin:0 0 10px}}
h3{{font-size:{FONT_HELP_H3}px;margin:18px 0 6px}}
p{{margin:6px 0}} ul{{margin:6px 0;padding-left:20px}} li{{margin:4px 0}}
code{{font-family:{mono_font_stack()};background:{c('panel')};color:{c('text')};
     padding:1px 5px;border-radius:4px}}
kbd{{font-family:{mono_font_stack()};background:{c('panel')};color:{c('text')};
    border:1px solid {c('border')};border-bottom-width:2px;border-radius:4px;
    padding:1px 6px;font-size:{FONT_NOTE}px}}
table{{border-collapse:collapse;width:100%}}
td{{padding:5px 8px;border-bottom:1px solid {c('border')}}}
</style>
"""

_HELP_SECTIONS = [
    (
        "快速开始", "Getting started",
        f"<h2>DiffScope {__version__}</h2><p>文件与文件夹对比 · 差异查看 · 合并同步。</p>"
        "<h3>三步完成第一次比较</h3><ul>"
        "<li>在工具栏选择「文件夹对比」或「文件对比」，填写左右两个路径。</li>"
        "<li>点击「开始比较」；目录树按状态着色，双击文件进入逐行文本对比。</li>"
        "<li>用 <kbd>F7</kbd> / <kbd>F8</kbd> 跳转差异；写盘前先确认操作清单。</li></ul>"
        "<p>也可以把两个文件/文件夹直接拖到窗口，或在开始页恢复上次会话。</p>",
        f"<h2>DiffScope {__version__}</h2><p>File &amp; folder compare, diff review, merge and sync.</p>"
        "<h3>Three steps to your first comparison</h3><ul>"
        "<li>Choose <b>Folder Compare</b> or <b>File Compare</b> in the toolbar and enter both paths.</li>"
        "<li>Click <b>Compare</b>; the tree is colour-coded, double-click a file for line-by-line text diff.</li>"
        "<li>Use <kbd>F7</kbd> / <kbd>F8</kbd> to jump between differences; confirm before any write.</li></ul>"
        "<p>You can also drop two files/folders onto the window, or resume the last session from the start page.</p>",
    ),
    (
        "数据源", "Data sources",
        "<h3>可用数据源</h3><p>本地路径、UNC、ZIP/TAR 压缩包、文件系统快照、FTP/FTPS、SFTP、"
        "WebDAV、SVN、Amazon S3、Dropbox、OneDrive、Windows 注册表。</p>"
        "<p>远程数据源需要凭证，连接失败会给出明确提示。文件系统快照可用于离线比较。</p>",
        "<h3>Available sources</h3><p>Local paths, UNC, ZIP/TAR archives, filesystem snapshots, "
        "FTP/FTPS, SFTP, WebDAV, SVN, Amazon S3, Dropbox, OneDrive and the Windows registry.</p>"
        "<p>Remote sources need credentials; failures are reported clearly. Snapshots allow offline comparison.</p>",
    ),
    (
        "比较类型", "Compare types",
        "<h3>专用比较器</h3><p>文本、文件夹、表格（CSV/TSV/HTML 表格/Excel）、十六进制、图片、"
        "MP3/FLAC/MP4/OGG 标签、exe/dll 版本信息、注册表、PDF/Word/RTF 转文本、补丁文件。</p>"
        "<p>文本比较支持逐行与行内字符级差异、语法着色、手动对齐、显示过滤与书签。</p>",
        "<h3>Specialised viewers</h3><p>Text, folders, tables (CSV/TSV/HTML/Excel), hex, images, "
        "MP3/FLAC/MP4/OGG tags, exe/dll version info, registry, PDF/Word/RTF as text and patch files.</p>"
        "<p>Text compare supports line and inline character diffs, syntax colouring, manual alignment, "
        "display filters and bookmarks.</p>",
    ),
    (
        "合并与同步", "Merge & sync",
        "<h3>写操作始终需要确认</h3><p>两路文本合并（按块/整文件）、三向文本与文件夹合并、"
        "单向覆盖/镜像/更新/双向同步。所有写操作都必须先确认操作清单，删除优先进入回收站，"
        "保存前自动备份且可从备份恢复。</p>",
        "<h3>Every write is confirmed</h3><p>Two-way text merge (by block or whole file), three-way "
        "text and folder merge, one-way copy / mirror / update / two-way sync. Every write shows an "
        "action list first; deletions go to the Recycle Bin, saving creates a backup that can be restored.</p>",
    ),
    (
        "自动化", "Automation",
        "<h3>命令行与脚本</h3><p><code>python cli.py --diff L R --report r.html</code><br>"
        "<code>python cli.py --script s.txt --allow-write</code><br>"
        "<code>python cli.py --merge BASE LEFT RIGHT OUT</code></p>"
        "<p>退出码：0 无差异 / 1 有差异 / 2 错误 / 3 合并冲突。</p>",
        "<h3>CLI and scripting</h3><p><code>python cli.py --diff L R --report r.html</code><br>"
        "<code>python cli.py --script s.txt --allow-write</code><br>"
        "<code>python cli.py --merge BASE LEFT RIGHT OUT</code></p>"
        "<p>Exit codes: 0 no differences / 1 differences found / 2 error / 3 merge conflict.</p>",
    ),
    (
        "快捷键", "Shortcuts",
        "<h3>常用快捷键</h3><table>"
        "<tr><td><kbd>Ctrl+O</kbd></td><td>打开对比</td></tr>"
        "<tr><td><kbd>Ctrl+R</kbd></td><td>重新比较</td></tr>"
        "<tr><td><kbd>Ctrl+S</kbd></td><td>保存修改</td></tr>"
        "<tr><td><kbd>F7</kbd> / <kbd>F8</kbd></td><td>上一处 / 下一处差异</td></tr>"
        "<tr><td><kbd>Ctrl+F</kbd> / <kbd>F3</kbd></td><td>查找 / 下一个匹配</td></tr>"
        "<tr><td><kbd>Ctrl+B</kbd></td><td>切换书签</td></tr>"
        "<tr><td><kbd>Ctrl+Shift+S</kbd></td><td>保存会话</td></tr>"
        "<tr><td><kbd>Ctrl+Shift+O</kbd></td><td>打开会话文件</td></tr>"
        "<tr><td><kbd>Ctrl+T</kbd></td><td>标签页工作区</td></tr></table>",
        "<h3>Common shortcuts</h3><table>"
        "<tr><td><kbd>Ctrl+O</kbd></td><td>Open comparison</td></tr>"
        "<tr><td><kbd>Ctrl+R</kbd></td><td>Recompare</td></tr>"
        "<tr><td><kbd>Ctrl+S</kbd></td><td>Save changes</td></tr>"
        "<tr><td><kbd>F7</kbd> / <kbd>F8</kbd></td><td>Previous / next difference</td></tr>"
        "<tr><td><kbd>Ctrl+F</kbd> / <kbd>F3</kbd></td><td>Find / next match</td></tr>"
        "<tr><td><kbd>Ctrl+B</kbd></td><td>Toggle bookmark</td></tr>"
        "<tr><td><kbd>Ctrl+Shift+S</kbd></td><td>Save session</td></tr>"
        "<tr><td><kbd>Ctrl+Shift+O</kbd></td><td>Open session file</td></tr>"
        "<tr><td><kbd>Ctrl+T</kbd></td><td>Tabbed workspace</td></tr></table>",
    ),
]


class HelpDialog(QDialog):
    """按任务分组的帮助；中英文各自提供完整正文，避免术语替换破坏 HTML。"""

    def __init__(self, parent):
        super().__init__(parent)
        from ..core.i18n import current_language

        self.setWindowTitle(tr("帮助"))
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        english = current_language() == "en"
        for zh_title, en_title, zh_body, en_body in _HELP_SECTIONS:
            browser = QTextBrowser()
            browser.setHtml(_help_css() + (en_body if english else zh_body))
            tabs.addTab(browser, en_title if english else zh_title)
        layout.addWidget(tabs, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText(
            "Close" if english else tr("关闭")
        )
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
