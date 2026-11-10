"""BC 细节完善测试：手动对齐、文件格式、三向文件夹合并、i18n、便携目录、HTML 格式化。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core import i18n  # noqa: E402
from app.core.text_diff import AlignmentPoint, TextAlignments  # noqa: E402
from app.core.compare import compare  # noqa: E402
from app.core.file_formats import FormatRegistry, default_formats, run_converter  # noqa: E402
from app.core.formats import format_html  # noqa: E402
from app.core.merge3 import merge3_folders  # noqa: E402
from app.core.services import config_dir  # noqa: E402
from app.core.sync import SyncPlan, execute_sync  # noqa: E402
from app.core.text_diff import compute_text_diff  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-detail-")
        self.root = Path(self._tmp.name)
        self.left = self.root / "L"
        self.right = self.root / "R"
        self.left.mkdir()
        self.right.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestManualAlignment(unittest.TestCase):
    def test_link_forces_alignment(self) -> None:
        alignments = TextAlignments(links=[AlignmentPoint(1, 3)])
        result = compute_text_diff("a\nx\ny\nz\n", "p\nq\nc\n", alignments=alignments)
        manual_rows = [row for row in result.rows if row.manual]
        self.assertEqual(len(manual_rows), 1)
        self.assertEqual((manual_rows[0].left_no, manual_rows[0].right_no), (1, 3))

    def test_break_prevents_cross_match(self) -> None:
        alignments = TextAlignments(breaks=[AlignmentPoint(2, 1)])
        result = compute_text_diff("a\nb\nc\n", "a\nb\nc\n", alignments=alignments)
        self.assertEqual(result.rows[0].op, "delete")
        self.assertEqual(result.rows[1].op, "insert")

    def test_style_none_disables_alignment(self) -> None:
        alignments = TextAlignments(style="none")
        result = compute_text_diff("a\nb\n", "a\n", alignments=alignments)
        self.assertEqual([(r.op, r.left_no, r.right_no) for r in result.rows],
                         [("equal", 1, 1), ("delete", 2, None)])

    def test_serialization(self) -> None:
        alignments = TextAlignments(links=[AlignmentPoint(1, 2)], breaks=[AlignmentPoint(3, 3)], style="none")
        restored = TextAlignments.from_dict(alignments.to_dict())
        self.assertEqual(restored.links[0].left, 1)
        self.assertEqual(restored.breaks[0].right, 3)
        self.assertEqual(restored.style, "none")


class TestFileFormats(Fixture):
    def test_match_and_persist(self) -> None:
        registry = FormatRegistry()
        self.assertEqual(registry.match("a.py").name, "Python 源码")
        self.assertIsNone(registry.match("a.unknownext"))
        target = self.root / "formats.json"
        registry.save(target)
        loaded = FormatRegistry.load(target)
        self.assertEqual(loaded.match("b.py").language, "python")

    def test_default_formats(self) -> None:
        self.assertGreaterEqual(len(default_formats()), 15)
        self.assertIsNone(FormatRegistry().match("a.unknownext"))

    def test_external_converter(self) -> None:
        source = self.left / "a.txt"
        source.write_text("hello", encoding="utf-8")
        conv = self.root / "conv.py"
        conv.write_text(
            "import sys\n"
            "open(sys.argv[2], 'w', encoding='utf-8').write("
            "open(sys.argv[1], encoding='utf-8').read().upper())\n",
            encoding="utf-8",
        )
        command = f'"{sys.executable}" "{conv}" {{in}} {{out}}'
        self.assertEqual(run_converter(command, source), "HELLO")
        with self.assertRaises(Exception):
            run_converter("", source)

    def test_format_rules_merge_into_compare(self) -> None:
        fmt = next(f for f in default_formats() if f.name == "Python 源码")
        (self.left / "a.py").write_text("a=1 # note\n", encoding="utf-8")
        (self.right / "a.py").write_text("a=1\n", encoding="utf-8")
        result = compare(str(self.left), str(self.right), rules=fmt.rules)
        self.assertEqual(result.stats["differences"], 0)


class TestFolderMerge3(Fixture):
    def build_tree(self, favor: str):
        base, left, right, out = (self.root / name for name in ("BASE", "L", "R", "OUT"))
        for folder in (base, left, right, out):
            folder.mkdir(exist_ok=True)
        for folder in (base, left, right):
            (folder / "keep.txt").write_text("same", encoding="utf-8")
        (base / "conflict.txt").write_text("base", encoding="utf-8")
        (left / "conflict.txt").write_text("L", encoding="utf-8")
        (right / "conflict.txt").write_text("R", encoding="utf-8")
        (left / "left-new.txt").write_text("L", encoding="utf-8")
        (right / "right-new.txt").write_text("R", encoding="utf-8")
        (base / "gone.txt").write_text("x", encoding="utf-8")
        (left / "gone.txt").write_text("x", encoding="utf-8")  # 右侧删除了 gone.txt
        diff_left = compare(str(base), str(left))
        diff_right = compare(str(base), str(right))
        actions, conflicts = merge3_folders(diff_left, diff_right, out, favor=favor)
        report = execute_sync(SyncPlan(actions=actions))
        return out, conflicts, report

    def test_auto_merge_and_conflict(self) -> None:
        out, conflicts, report = self.build_tree("")
        self.assertEqual(conflicts, ["conflict.txt"])
        self.assertTrue(report.ok, report.failed)
        # 未解决冲突的文件不写入输出，其余完整合并
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["keep.txt", "left-new.txt", "right-new.txt"])

    def test_favor_left_resolves_conflict(self) -> None:
        out, conflicts, report = self.build_tree("left")
        self.assertEqual(conflicts, ["conflict.txt"])
        self.assertTrue(report.ok)
        self.assertEqual((out / "conflict.txt").read_text(encoding="utf-8"), "L")
        self.assertNotIn("gone.txt", [p.name for p in out.iterdir()])

    def test_favor_base_keeps_ancestor(self) -> None:
        out, _conflicts, report = self.build_tree("base")
        self.assertTrue(report.ok)
        self.assertEqual((out / "conflict.txt").read_text(encoding="utf-8"), "base")


class TestHtmlFormatAndI18N(unittest.TestCase):
    def test_format_html(self) -> None:
        pretty = format_html("<div><p>Hello</p><ul><li>1</li></ul></div>")
        self.assertIn("  <p>", pretty)
        self.assertGreater(pretty.count("\n"), 3)

    def test_i18n(self) -> None:
        i18n.set_language("zh")
        self.assertEqual(i18n.tr("文件"), "文件")
        i18n.set_language("en")
        self.assertEqual(i18n.tr("文件"), "File")
        self.assertEqual(i18n.tr("不存在的词条"), "不存在的词条")
        i18n.set_language("zh")

    def test_portable_config_dir(self) -> None:
        self.assertIn(".diffscope", str(config_dir()))
        os.environ["DIFFSCOPE_PORTABLE"] = "1"
        try:
            self.assertEqual(config_dir().name, "Data")
        finally:
            del os.environ["DIFFSCOPE_PORTABLE"]


class TestDetailsGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls._session_patches = [
            mock.patch("app.ui.main_window.last_session_path", lambda: Path(tempfile.gettempdir()) / "ds-no-last.json"),
            mock.patch("app.ui.main_window.save_last_session", lambda *a, **k: None),
        ]
        for item in cls._session_patches:
            item.start()

    @classmethod
    def tearDownClass(cls) -> None:
        for item in cls._session_patches:
            item.stop()

    def test_format_editor_and_folder_merge_view(self) -> None:
        from app.ui.dialogs_ext import FormatEditorDialog
        from app.ui.extended_views import FolderMerge3View
        from app.ui.main_window import MainWindow

        window = MainWindow()
        editor = FormatEditorDialog(window, window.formats)
        self.assertEqual(editor.windowTitle(), "文件格式")
        self.assertIsNotNone(FolderMerge3View(window).favor)

    def test_manual_alignment_in_file_view(self) -> None:
        from app.ui.main_window import MainWindow

        window = MainWindow()
        left = Path(tempfile.gettempdir()) / "ds-align-left.txt"
        right = Path(tempfile.gettempdir()) / "ds-align-right.txt"
        left.write_text("a\nx\ny\nz\n", encoding="utf-8")
        right.write_text("p\nq\nc\n", encoding="utf-8")
        try:
            self.assertTrue(window.file_view.load_pair(str(left), str(right), None))
            window.file_view.left_edit.goto_row(0)
            window.file_view.right_edit.goto_row(2)
            window.file_view.align_current_lines()
            manual = [row for row in window.file_view.result.rows if row.manual]
            self.assertEqual(len(manual), 1)
            window.file_view.clear_alignments()
            self.assertFalse(any(row.manual for row in window.file_view.result.rows))
        finally:
            left.unlink()
            right.unlink()


class TestAlignmentGutter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_gutter_renders_and_signals(self) -> None:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QDialog, QVBoxLayout

        from app.ui.widgets import AlignmentGutter, DiffTextEdit

        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        left, right = DiffTextEdit("left"), DiffTextEdit("right")
        gutter = AlignmentGutter(left, right)
        layout.addWidget(left)
        layout.addWidget(gutter)
        layout.addWidget(right)
        dialog.show()

        received = []
        gutter.link_requested.connect(lambda l, r: received.append(("link", l, r)))
        gutter.break_requested.connect(lambda l, r: received.append(("break", l, r)))

        def mouse(side_x_ratio, y_ratio, event_type):
            event = QMouseEvent(
                event_type,
                QPointF(gutter.width() * side_x_ratio, gutter.height() * y_ratio),
                QPointF(gutter.width() * side_x_ratio, gutter.height() * y_ratio),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            if event_type == QMouseEvent.Type.MouseButtonPress:
                gutter.mousePressEvent(event)
            elif event_type == QMouseEvent.Type.MouseButtonRelease:
                gutter.mouseReleaseEvent(event)
            else:
                gutter.mouseMoveEvent(event)

        # 左侧按下 → 拖过右侧 → 释放：连线
        mouse(0.1, 0.3, QMouseEvent.Type.MouseButtonPress)
        mouse(0.9, 0.5, QMouseEvent.Type.MouseMove)
        mouse(0.9, 0.6, QMouseEvent.Type.MouseButtonRelease)
        # 同侧按下并释放：断开
        mouse(0.9, 0.6, QMouseEvent.Type.MouseButtonPress)
        mouse(0.9, 0.6, QMouseEvent.Type.MouseButtonRelease)
        self.assertIn(("link", 0, 0), received)
        self.assertIn(("break", 0, 0), received)
        gutter.links = [(0, 0)]
        gutter.setFixedSize(30, 400)
        gutter.show()
        for _ in range(3):
            self.app.processEvents()
        self.assertGreater(gutter.width() * gutter.height(), 0)
        pixmap = gutter.grab()  # 触发 paintEvent，不允许崩溃
        self.assertFalse(pixmap.isNull())


class TestDfmExtraction(Fixture):
    @staticmethod
    def _pref(text: str) -> bytes:
        raw = text.encode("utf-8")
        return bytes([len(raw)]) + raw

    def test_binary_dfm_outline(self) -> None:
        from app.core.formats import binary_dfm_outline

        data = (b"TPF0" + b"\xff" + self._pref("TForm1") + self._pref("Form1")
                + self._pref("Caption") + b"\x00" + self._pref("你好界面"))
        outline = binary_dfm_outline(data)
        self.assertIn("object Form1: TForm1", outline)
        self.assertIn("Caption", outline)

    def test_dfm_via_to_text(self) -> None:
        from app.core.formats import to_text

        data = b"TPF0" + b"\xff" + self._pref("TButton") + self._pref("Button1") + b"\x00"
        self.assertIn("object Button1: TButton", to_text(data, "unit.dfm"))


class TestFormatImportExport(Fixture):
    def test_roundtrip_and_merge(self) -> None:
        from app.core.file_formats import export_formats, import_formats

        registry = FormatRegistry()
        target = self.root / "shared.json"
        export_formats(registry, target)
        other = FormatRegistry(formats=default_formats()[:3])
        self.assertEqual(import_formats(other, target, replace=False), len(registry.formats))
        self.assertEqual(len(other.formats), len(registry.formats))
        count = import_formats(other, target, replace=True)
        self.assertEqual(count, len(registry.formats))
        self.assertEqual(len(other.formats), count)

    def test_import_rejects_bad_file(self) -> None:
        from app.core.file_formats import import_formats

        bad = self.root / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        with self.assertRaises(Exception):
            import_formats(FormatRegistry(), bad)


class TestUpdateCheck(Fixture):
    def test_no_url_and_local_json(self) -> None:
        from app import __version__
        from app.core.services import check_update

        self.assertIn("未配置更新源", check_update())
        payload = self.root / "version.json"
        payload.write_text('{"version": "9.9.9"}', encoding="utf-8")
        self.assertIn("9.9.9", check_update(payload.as_uri()))
        payload.write_text(f'{{"version": "{__version__}"}}', encoding="utf-8")
        self.assertIn("已是最新版本", check_update(payload.as_uri()))


class TestI18nDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_titles_translate(self) -> None:
        from app.core.services import Settings
        from app.ui.dialogs_ext import HelpDialog, SettingsDialog
        from app.ui.main_window import MainWindow

        window = MainWindow()
        try:
            i18n.set_language("en")
            self.assertEqual(SettingsDialog(window, Settings(Path(tempfile.gettempdir()) / "ds-i18n.json")).windowTitle(), "Settings")
            self.assertEqual(HelpDialog(window).windowTitle(), "Help")
        finally:
            i18n.set_language("zh")


class TestDragAndDrop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_app_reference(self) -> None:
        self.assertIsNotNone(self.app)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-drop-")
        self.root = Path(self._tmp.name)
        self.dir_a = self.root / "A"
        self.dir_b = self.root / "B"
        self.dir_a.mkdir()
        self.dir_b.mkdir()
        (self.dir_a / "a.txt").write_text("x", encoding="utf-8")
        (self.dir_b / "a.txt").write_text("y", encoding="utf-8")
        self.file_a = self.root / "one.txt"
        self.file_a.write_text("one", encoding="utf-8")
        self.warnings: list[str] = []
        patcher = mock.patch(
            "app.ui.main_window.show_warning",
            lambda *a, **k: self.warnings.append(a[1] if len(a) > 1 else ""),
        )
        patcher.start()
        self._patches = [patcher]
        for target in ("app.ui.main_window.last_session_path",):
            patcher = mock.patch(target, lambda: self.root / "no-last.json")
            patcher.start()
            self._patches.append(patcher)
        patcher = mock.patch("app.ui.main_window.save_last_session", lambda *a, **k: None)
        patcher.start()
        self._patches.append(patcher)
        from app.ui.main_window import MainWindow

        window = MainWindow()
        window.show()
        self.window = window

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.window.close()
        self._tmp.cleanup()

    def _make_drop(self, paths: list, x: float = 200, y: float = 200):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QDropEvent
        from PySide6.QtCore import QMimeData, QUrl

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return QDropEvent(QPointF(x, y), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    def _make_enter(self, paths: list):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QDragEnterEvent
        from PySide6.QtCore import QMimeData, QUrl

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return QDragEnterEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    def test_drop_handlers(self) -> None:
        """不构造 QDropEvent（离屏下不可靠），直接验证拖放处理逻辑。"""
        w = self.window
        w._handle_drop_paths([str(self.dir_a), str(self.dir_b)])
        self.assertEqual(w.left_edit.text(), str(self.dir_a))
        self.assertEqual(w.right_edit.text(), str(self.dir_b))
        self.assertEqual(w.current_mode().value, "folders")
        for _ in range(200):
            self.app.processEvents()
            if w._thread is None:
                break
            time.sleep(0.01)
        self.assertEqual(w.stack.currentIndex(), 1)

        w2_warnings = self.warnings
        w._handle_drop_paths([str(self.dir_a), str(self.file_a)])
        self.assertTrue(w2_warnings, "混合类型应有明确提示")

        w._handle_drop_paths([str(self.file_a)])
        self._wait_async_load(w.file_view)
        # 单个文件直接进入文本对比界面（另一侧为空）
        self.assertEqual(w.stack.currentIndex(), 2)
        self.assertEqual(w.file_view.left_path, self.file_a)
        self.assertIsNone(w.file_view.right_path)
        self.assertFalse(w.file_view.left_edit.acceptDrops())
        self.assertEqual(w.file_view._dirty, set())  # 语法高亮 rehighlight 不得误标"已修改"
        self.assertEqual(w.file_view.left_edit.text(), "one")

    def test_event_filter_rejects_non_drag(self) -> None:
        from PySide6.QtCore import QEvent

        w = self.window
        self.assertFalse(w.eventFilter(w.left_edit, QEvent(QEvent.Type.MouseButtonPress)))

    def test_folder_view_drop_side(self) -> None:
        from PySide6.QtWidgets import QApplication

        w = self.window
        for _ in range(3):
            QApplication.processEvents()
        view = w.folder_view
        self.assertEqual(view.drop_side_at(view.left_panel.geometry().center()), "left")
        self.assertEqual(view.drop_side_at(view.right_panel.geometry().center()), "right")

    def _wait_async_load(self, view, timeout_ms: int = 8000) -> None:
        """两阶段等待：加载可能经 singleShot/完成回调延迟发起，
        先等到 _load_seq 增长（加载已请求），再等后台线程结束并应用结果。"""
        import time as _time

        waited = 0
        seq0 = view._load_seq
        while view._load_seq == seq0 and waited < timeout_ms:
            self.app.processEvents()
            _time.sleep(0.01)
            waited += 10
        while view._async_thread is not None and waited < timeout_ms:
            self.app.processEvents()
            _time.sleep(0.01)
            waited += 10
        self.app.processEvents()

    def test_file_view_drop_replaces_side(self) -> None:
        w = self.window
        other = self.root / "two.txt"
        other.write_text("two", encoding="utf-8")
        from PySide6.QtWidgets import QApplication

        w.file_view.load_pair(str(self.file_a), str(other), None)
        w.file_view.show()
        for _ in range(3):
            QApplication.processEvents()
        pos = w.file_view.right_panel.geometry().center()
        w.file_view._handle_drop([str(self.dir_a / "a.txt")], pos)
        self._wait_async_load(w.file_view)
        self.assertEqual(w.file_view.right_path, self.dir_a / "a.txt")
        self.assertEqual(w.file_view.left_path, self.file_a)


class TestDisplayFilter(unittest.TestCase):
    """BC 式显示过滤：全部 / 差别 / 相同。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-dfilter-")
        self.root = Path(self._tmp.name)
        self.f1 = self.root / "a.txt"
        self.f2 = self.root / "b.txt"
        left = [f"same {i}" for i in range(20)]
        right = list(left)
        left[5] = "CHANGED-L"
        right[5] = "CHANGED-R"
        left.insert(12, "only-left")
        right[10] = "replaced-right"
        self.f1.write_text("\n".join(left) + "\n", encoding="utf-8")
        self.f2.write_text("\n".join(right) + "\n", encoding="utf-8")
        self.warnings: list[str] = []
        self._patches = []
        for target, value in (
            ("app.ui.main_window.show_warning", lambda *a, **k: self.warnings.append(a[1] if len(a) > 1 else "")),
            ("app.ui.main_window.last_session_path", lambda: self.root / "no-last.json"),
            ("app.ui.main_window.save_last_session", lambda *a, **k: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self._patches.append(patcher)
        from app.ui.main_window import MainWindow

        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self.window.close()
        self._tmp.cleanup()

    def _load(self):
        view = self.window.file_view
        seq0 = view._load_seq
        view.load_pair_async(str(self.f1), str(self.f2), None)
        waited = 0
        while view._load_seq == seq0 and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        while view._async_thread is not None and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        self.app.processEvents()

    def _text(self):
        return self.window.file_view.left_edit.text().splitlines()

    def test_all_is_identity(self) -> None:
        self._load()
        view = self.window.file_view
        view.set_display_filter("all")
        self.assertIsNone(view._display_map)
        self.assertEqual(len(self._text()), 21)
        self.assertFalse(view.left_edit.isReadOnly())

    def test_differences_shows_context_and_original_numbers(self) -> None:
        self._load()
        view = self.window.file_view
        view.set_display_filter("differences")
        self.app.processEvents()
        lines = self._text()
        # 差异在原始第 6 行（替换）与第 11/12 行附近；上下文 3 行 → 每段 7 行左右
        self.assertLess(len(lines), 21)
        self.assertIn("CHANGED-L", "\n".join(lines))
        self.assertIn("only-left", "\n".join(lines))
        # 只读 + 动作禁用
        self.assertTrue(view.left_edit.isReadOnly())
        self.assertFalse(view.btn_save_left.isEnabled())
        self.assertFalse(view.btn_merge_l2r.isEnabled())
        # 行号仍显示原始行号（CHANGED-L 是原始第 6 行）
        rows = view.left_edit.rows
        changed_display = next(i for i, r in enumerate(rows) if r.left_text == "CHANGED-L")
        self.assertEqual(rows[changed_display].left_no, 6)
        # 差异导航只在过滤后的视图内跳转且能到达差异块
        self.assertTrue(view.goto_block(view.current_block))
        view.next_difference()
        self.assertGreaterEqual(view.current_block, 0)

    def test_same_shows_only_equal_rows(self) -> None:
        self._load()
        view = self.window.file_view
        view.set_display_filter("same")
        self.app.processEvents()
        lines = self._text()
        self.assertNotIn("CHANGED-L", "\n".join(lines))
        self.assertNotIn("only-left", "\n".join(lines))
        self.assertIn("same 0", "\n".join(lines))
        rows = view.left_edit.rows
        first = rows[0]
        self.assertEqual(first.op, "equal")
        self.assertEqual(first.left_no, 1)
        self.assertTrue(view.left_edit.isReadOnly())

    def test_switch_back_restores_editing(self) -> None:
        self._load()
        view = self.window.file_view
        view.set_display_filter("differences")
        view.set_display_filter("all")
        self.app.processEvents()
        self.assertFalse(view.left_edit.isReadOnly())
        self.assertTrue(view.btn_save_left.isEnabled())
        self.assertTrue(view.btn_merge_l2r.isEnabled())
        self.assertEqual(len(self._text()), 21)

    def test_recompare_resets_filter(self) -> None:
        self._load()
        view = self.window.file_view
        view.set_display_filter("differences")
        view.set_display_filter("all")  # 先回全部，便于重比较（过滤下只读）
        view.set_display_filter("differences")
        view.set_display_filter("all")
        # 模拟重新加载：_apply_result 应复位过滤状态
        from app.core.text_diff import compute_text_diff

        result = compute_text_diff("x\n", "y\n")
        view._apply_result(result, reset_counter=True)
        self.assertEqual(view._display_filter, "all")
        self.assertFalse(view.left_edit.isReadOnly())


class TestLineInfoPanel(unittest.TestCase):
    """底部行内容栏：显示光标所在行两侧的对应内容。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-lineinfo-")
        self.root = Path(self._tmp.name)
        self.f1 = self.root / "a.txt"
        self.f2 = self.root / "b.txt"
        self.f1.write_text("alpha\nCHANGED-L\nsame\nonly-left\ntail\n", encoding="utf-8")
        self.f2.write_text("alpha\nCHANGED-R\nsame\ntail\n", encoding="utf-8")
        self.warnings: list[str] = []
        self._patches = []
        for target, value in (
            ("app.ui.main_window.show_warning", lambda *a, **k: self.warnings.append(a[1] if len(a) > 1 else "")),
            ("app.ui.main_window.last_session_path", lambda: self.root / "no-last.json"),
            ("app.ui.main_window.save_last_session", lambda *a, **k: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self._patches.append(patcher)
        from app.ui.main_window import MainWindow

        self.window = MainWindow()
        self.app.processEvents()

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self.window.close()
        self._tmp.cleanup()

    def _load(self):
        view = self.window.file_view
        seq0 = view._load_seq
        view.load_pair_async(str(self.f1), str(self.f2), None)
        waited = 0
        while view._load_seq == seq0 and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        while view._async_thread is not None and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        self.app.processEvents()

    def _set_cursor(self, editor, display_row: int) -> None:
        from PySide6.QtGui import QTextCursor

        block = editor.document().findBlockByNumber(display_row)
        editor.setTextCursor(QTextCursor(block))
        self.app.processEvents()

    def test_shows_current_line_both_sides(self) -> None:
        self._load()
        view = self.window.file_view
        # 加载后光标停在首个差异块（第 2 行）
        self.assertIn("CHANGED-L", view.line_info_left.text())
        self.assertIn("CHANGED-R", view.line_info_right.text())
        # 光标移到第 1 行：两侧都是 same 行 alpha
        self._set_cursor(view.left_edit, 0)
        self.assertIn("左 1：alpha", view.line_info_left.text())
        self.assertIn("右 1：alpha", view.line_info_right.text())
        # 光标移到替换行：左 2 / 右 2，内容分别为 CHANGED-L / CHANGED-R
        rows = view.left_edit.rows
        changed = next(i for i, r in enumerate(rows) if r.left_text == "CHANGED-L")
        self._set_cursor(view.left_edit, changed)
        self.assertIn("左 2：CHANGED-L", view.line_info_left.text())
        self.assertIn("右 2：CHANGED-R", view.line_info_right.text())

    def test_placeholder_side_reports_missing(self) -> None:
        self._load()
        view = self.window.file_view
        rows = view.left_edit.rows
        only_left = next(i for i, r in enumerate(rows) if r.left_text == "only-left")
        self._set_cursor(view.left_edit, only_left)
        self.assertIn("左 4：only-left", view.line_info_left.text())
        self.assertIn("该侧没有对应内容", view.line_info_right.text())

    def test_right_pane_cursor_updates_active_row(self) -> None:
        self._load()
        view = self.window.file_view
        self._set_cursor(view.right_edit, 4)  # 右侧编辑器 block 4 = tail（block 3 是占位行）
        self.assertIn("右 4：tail", view.line_info_right.text())
        self.assertIn("左 5：tail", view.line_info_left.text())

    def test_load_error_resets_labels(self) -> None:
        from app.core.errors import DiffScopeError

        self._load()
        view = self.window.file_view
        self._set_cursor(view.left_edit, 1)
        self.assertIn("CHANGED-L", view.line_info_left.text())
        with mock.patch("app.ui.file_view.show_error"):
            view._show_load_error(DiffScopeError("读取失败", "测试错误"))
        self.app.processEvents()
        self.assertEqual(view.line_info_left.text(), "左：—")
        self.assertEqual(view.line_info_right.text(), "右：—")


class TestSyntaxHighlight(unittest.TestCase):
    """语法着色回归：带语言设置时 highlightBlock 不得抛错（曾因 background 未定义崩溃）。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_python_highlight_no_error(self) -> None:
        from app.ui.widgets import DiffTextEdit

        editor = DiffTextEdit("left")
        editor.set_language("main.py")
        editor.setPlainText("def main():\n    # comment\n    return 'ok'\n")
        self.app.processEvents()
        self.assertEqual(editor.highlighter.language, "python")


class TestFilteredHighlight(unittest.TestCase):
    """过滤模式回归：差异行整行高亮与占位行号必须跟随显示位置而非原始行号。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-fhighlight-")
        self.root = Path(self._tmp.name)
        self.f1 = self.root / "a.txt"
        self.f2 = self.root / "b.txt"
        left = [f"same {i}" for i in range(20)]
        right = list(left)
        left[5] = "CHANGED-L"
        right[5] = "CHANGED-R"
        left.insert(12, "only-left")
        right[10] = "replaced-right"
        self.f1.write_text("\n".join(left) + "\n", encoding="utf-8")
        self.f2.write_text("\n".join(right) + "\n", encoding="utf-8")
        self._patches = []
        for target, value in (
            ("app.ui.main_window.show_warning", lambda *a, **k: None),
            ("app.ui.main_window.last_session_path", lambda: self.root / "no-last.json"),
            ("app.ui.main_window.save_last_session", lambda *a, **k: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self._patches.append(patcher)
        from app.ui.main_window import MainWindow

        self.window = MainWindow()
        self.app.processEvents()

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self.window.close()
        self._tmp.cleanup()

    def _load(self):
        view = self.window.file_view
        seq0 = view._load_seq
        view.load_pair_async(str(self.f1), str(self.f2), None)
        waited = 0
        while view._load_seq == seq0 and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        while view._async_thread is not None and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        self.app.processEvents()

    def test_diff_rows_highlighted_in_filtered_view(self) -> None:
        self._load()
        view = self.window.file_view
        view.set_display_filter("differences")
        self.app.processEvents()
        rows = view.left_edit.rows
        changed = next(i for i, r in enumerate(rows) if r.left_text == "CHANGED-L")
        plain = next(i for i, r in enumerate(rows) if r.op == "equal")
        covered: set[int] = set()
        for sel in view.left_edit.extraSelections():
            cursor = sel.cursor
            if cursor.isNull():
                continue
            first = view.left_edit.document().findBlock(cursor.selectionStart()).blockNumber()
            last = view.left_edit.document().findBlock(cursor.selectionEnd()).blockNumber()
            covered.update(range(first, last + 1))
        self.assertIn(changed, covered)
        self.assertNotIn(plain, covered)

    def test_placeholder_positions_in_filtered_view(self) -> None:
        self._load()
        view = self.window.file_view
        view.set_display_filter("differences")
        self.app.processEvents()
        rows = view.left_edit.rows
        only_left = next(i for i, r in enumerate(rows) if r.left_text == "only-left")
        # only-left 行右侧缺内容 → 占位应记在右编辑器（按显示块号 10）
        self.assertIn(only_left, view.right_edit._placeholder)
        self.assertNotIn(only_left, view.left_edit._placeholder)


class TestEditorEnhancements(unittest.TestCase):
    """P1：Ctrl+滚轮缩放、自动重比较、差异/书签侧栏、新语法着色。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-editor-")
        self.root = Path(self._tmp.name)
        self.f1 = self.root / "a.txt"
        self.f2 = self.root / "b.txt"
        self.f1.write_text("alpha\nCHANGED-L\nsame\nonly-left\ntail\n", encoding="utf-8")
        self.f2.write_text("alpha\nCHANGED-R\nsame\ntail\n", encoding="utf-8")
        self._patches = []
        for target, value in (
            ("app.ui.main_window.show_warning", lambda *a, **k: None),
            ("app.ui.main_window.last_session_path", lambda: self.root / "no-last.json"),
            ("app.ui.main_window.save_last_session", lambda *a, **k: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self._patches.append(patcher)
        from app.ui.main_window import MainWindow

        self.window = MainWindow()
        self.app.processEvents()

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self.window.close()
        self._tmp.cleanup()

    def _load(self):
        view = self.window.file_view
        seq0 = view._load_seq
        view.load_pair_async(str(self.f1), str(self.f2), None)
        waited = 0
        while view._load_seq == seq0 and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        while view._async_thread is not None and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        self.app.processEvents()

    def test_language_detection_new_kinds(self) -> None:
        from app.ui.widgets import language_from_name

        for name, expected in (("x.json", "json"), ("x.yaml", "yaml"), ("x.yml", "yaml"),
                               ("x.go", "go"), ("x.rs", "rust"), ("x.md", "markdown")):
            self.assertEqual(language_from_name(name), expected)

    def test_zoom_updates_fonts_and_settings(self) -> None:
        self._load()
        view = self.window.file_view
        before = view._editor_size
        view._apply_zoom(1)
        self.assertEqual(view._editor_size, before + 1)
        self.assertEqual(int(view.settings.get("editor_font_size")), before + 1)
        # 字号必须写进编辑器自身样式表，否则被全局样式表覆盖不生效
        self.assertIn(f"font-size: {before + 1}pt", view.left_edit.styleSheet())
        view._apply_zoom(-1)
        self.assertEqual(view._editor_size, before)

    def test_nav_panel_lists_blocks_and_jumps(self) -> None:
        self._load()
        view = self.window.file_view
        view.btn_nav.setChecked(True)
        self.assertFalse(view.nav_panel.isHidden())
        self.assertEqual(view.diff_list.count(), len(view.blocks))
        first = view.diff_list.item(0)
        self.assertEqual(first.data(Qt.ItemDataRole.UserRole), 0)
        view._nav_diff_clicked(first)
        self.assertEqual(view.current_block, 0)

    def test_bookmark_tab_lists_bookmarks(self) -> None:
        self._load()
        view = self.window.file_view
        view.btn_nav.setChecked(True)
        view.goto_visible_row(0)
        view.toggle_bookmark()
        self.assertEqual(view.bookmark_list.count(), 1)

    def test_auto_recompare_refreshes_blocks(self) -> None:
        from PySide6.QtGui import QTextCursor

        self._load()
        view = self.window.file_view
        self.assertTrue(view.settings.get("auto_recompare", True))
        blocks_before = len(view.blocks)
        cursor = view.right_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("\nnew-right-line")
        # 防抖 800ms 后触发
        waited = 0
        while waited < 3000:
            self.app.processEvents(); time.sleep(0.05); waited += 50
            if len(view.blocks) != blocks_before:
                break
        self.assertNotEqual(len(view.blocks), blocks_before)
        self.assertEqual(view.left_edit.rows[-1].right_text, "new-right-line")


from PySide6.QtCore import Qt  # noqa: E402  (供以上用例使用)


class TestWorkflowP2(unittest.TestCase):
    """P2：最近比较列表、差异清单导出、比较模板。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-p2-")
        self.root = Path(self._tmp.name)
        self.f1 = self.root / "a.txt"
        self.f2 = self.root / "b.txt"
        self.f1.write_text("alpha\nCHANGED-L\nsame\nonly-left\ntail\n", encoding="utf-8")
        self.f2.write_text("alpha\nCHANGED-R\nsame\ntail\n", encoding="utf-8")
        self._patches = []
        for target, value in (
            ("app.ui.main_window.show_warning", lambda *a, **k: None),
            ("app.ui.main_window.last_session_path", lambda: self.root / "no-last.json"),
            ("app.ui.main_window.save_last_session", lambda *a, **k: None),
            # 防止测试把最近比较写进真实用户设置
            ("app.core.services.Settings.save", lambda self: None),
        ):
            patcher = mock.patch(target, value, create=True)
            patcher.start()
            self._patches.append(patcher)
        from app.ui.main_window import MainWindow

        self.window = MainWindow()
        self.app.processEvents()

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self.window.close()
        self._tmp.cleanup()

    def _load(self):
        view = self.window.file_view
        seq0 = view._load_seq
        view.load_pair_async(str(self.f1), str(self.f2), None)
        waited = 0
        while view._load_seq == seq0 and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        while view._async_thread is not None and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        self.app.processEvents()

    def test_recent_comparisons_recorded(self) -> None:
        # 其他测试可能已写入真实设置文件，先清空再验证
        self.window.settings.set("recent_comparisons", [])
        self.window._record_recent(str(self.f1), str(self.f2))
        self.window._record_recent(str(self.f1), str(self.f2))
        self.window._record_recent(str(self.f2), str(self.f1))
        items = self.window.settings.get("recent_comparisons")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["left"], str(self.f2))
        self.assertEqual(self.window.welcome.recent_list.count(), 2)

    def test_welcome_recent_reopens(self) -> None:
        from PySide6.QtWidgets import QListWidgetItem

        self.window.welcome.set_recent([{"left": str(self.f1), "right": str(self.f2)}])
        item = self.window.welcome.recent_list.item(0)
        self.window.welcome._emit_recent(item)
        self.app.processEvents()
        self.assertEqual(self.window.left_edit.text(), str(self.f1))
        self.assertEqual(self.window.right_edit.text(), str(self.f2))

    def test_export_diff_list_xml(self) -> None:
        self._load()
        view = self.window.file_view
        out = self.root / "diffs.xml"
        with mock.patch("app.ui.file_view.QFileDialog.getSaveFileName",
                        return_value=(str(out), "XML (*.xml)")):
            view.export_diff_list()
        text = out.read_text(encoding="utf-8")
        self.assertIn("<diffs>", text)
        self.assertIn("CHANGED-L", text)
        self.assertIn("仅左侧", text)

    def test_export_diff_list_csv(self) -> None:
        self._load()
        view = self.window.file_view
        out = self.root / "diffs.csv"
        with mock.patch("app.ui.file_view.QFileDialog.getSaveFileName",
                        return_value=(str(out), "CSV (*.csv)")):
            view.export_diff_list()
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("status", text)
        self.assertIn("修改", text)
        self.assertIn("CHANGED-R", text)

    def test_template_save_load_roundtrip(self) -> None:
        from app.core.models import CompareOptions
        from app.core.rules import CompareRules
        from app.core.session import list_templates, load_template, save_template

        with mock.patch("app.core.session.config_dir", return_value=self.root):
            rules = CompareRules.preset("python")
            options = CompareOptions(ignore_case=True, excludes=("build",))
            save_template("测试模板", rules, options)
            entries = list_templates()
            self.assertTrue(entries)
            data = load_template(entries[0][0])
            self.assertEqual(data["name"], "测试模板")
            restored = (CompareRules.from_dict(data["rules"]), CompareOptions.from_dict(data["options"]))
            self.assertTrue(restored[1].ignore_case)
            self.assertIn("build", restored[1].excludes)

    def test_template_dialog_apply(self) -> None:
        from app.core.models import CompareOptions
        from app.core.session import save_template
        from app.ui.dialogs_ext import TemplateDialog

        with mock.patch("app.core.session.config_dir", return_value=self.root):
            save_template("对话框模板", self.window.rules, CompareOptions(ignore_whitespace=True))
            dialog = TemplateDialog(self.window, self.window.rules, self.window.options)
            dialog.list.setCurrentRow(0)
            dialog._apply()
            self.assertIsNotNone(dialog.applied)
            self.assertTrue(dialog.applied[1].ignore_whitespace)


class TestMergeWorkflowP3(unittest.TestCase):
    """P3：选择性合并、预演报告导出、备份恢复。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-p3-")
        self.root = Path(self._tmp.name)
        self.f1 = self.root / "a.txt"
        self.f2 = self.root / "b.txt"
        self.f1.write_text("alpha\nCHANGED-L\nsame\nonly-left\ntail\n", encoding="utf-8")
        self.f2.write_text("alpha\nCHANGED-R\nsame\ntail\n", encoding="utf-8")
        self._patches = []
        for target, value in (
            ("app.ui.main_window.show_warning", lambda *a, **k: None),
            ("app.ui.main_window.last_session_path", lambda: self.root / "no-last.json"),
            ("app.ui.main_window.save_last_session", lambda *a, **k: None),
        ):
            patcher = mock.patch(target, value, create=True)
            patcher.start()
            self._patches.append(patcher)
        from app.ui.main_window import MainWindow

        self.window = MainWindow()
        self.app.processEvents()

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        self.window.close()
        self._tmp.cleanup()

    def _load(self):
        view = self.window.file_view
        seq0 = view._load_seq
        view.load_pair_async(str(self.f1), str(self.f2), None)
        waited = 0
        while view._load_seq == seq0 and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        while view._async_thread is not None and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        self.app.processEvents()

    def test_selective_merge_dialog_selection(self) -> None:
        from PySide6.QtCore import Qt as Qt

        from app.core.merge import MergeDirection
        from app.ui.dialogs_ext import SelectiveMergeDialog

        self._load()
        view = self.window.file_view
        dialog = SelectiveMergeDialog(view, view.blocks, view.result.rows)
        self.assertEqual(dialog.selected_blocks(), list(range(len(view.blocks))))
        # 只勾第一块
        for i in range(dialog.list.count()):
            dialog.list.item(i).setCheckState(QChecked := Qt.CheckState.Checked if i == 0 else Qt.CheckState.Unchecked)
        self.assertEqual(dialog.selected_blocks(), [0])
        self.assertEqual(dialog.direction(), MergeDirection.LEFT_TO_RIGHT)

    def test_selective_merge_applies_only_selected(self) -> None:
        self._load()
        view = self.window.file_view
        blocks_before = len(view.blocks)
        # 模拟：对话框选择全部块 → 直接调用合并逻辑（跳过对话框）
        from app.core.merge import merge_block

        targets = [0]
        for index in targets:
            view.result = merge_block(view.result, view.blocks[index][0], __import__("app.core.merge", fromlist=["MergeDirection"]).MergeDirection.LEFT_TO_RIGHT)
        view._apply_result(view.result, reset_counter=True)
        # 第 0 块（replace CHANGED）已合并为一致，块数减少
        self.assertLess(len(view.blocks), blocks_before)
        rows = view.result.rows
        changed = next(r for r in rows if r.left_text == "CHANGED-L")
        self.assertEqual(changed.right_text, "CHANGED-L")
        # 未选中的 only-left 仍存在
        self.assertTrue(any(r.left_text == "only-left" for r in rows))

    def test_restore_backup_recovers_content(self) -> None:
        from app.core.services import backup_file

        self._load()
        view = self.window.file_view
        # 造一个备份：把 f2 当前内容备份，然后改写 f2，再恢复（备份目录指向临时目录）
        with mock.patch("app.core.services.BACKUP_DIR", self.root / "backups"):
            backup = backup_file(self.f2)
            self.assertIsNotNone(backup)
            self.f2.write_text("alpha\nCHANGED-L\nsame\ntail\nextra\n", encoding="utf-8")
            self._load()
            with mock.patch("PySide6.QtWidgets.QInputDialog.getItem",
                            return_value=([c[0] for c in self._candidates(view)][0], True)), \
                 mock.patch("app.ui.file_view.confirm", return_value=True):
                view.restore_backup()
        waited = 0
        while getattr(view, "_async_thread", None) is not None and waited < 8000:
            self.app.processEvents(); time.sleep(0.01); waited += 10
        self.app.processEvents()
        self.assertIn("CHANGED-R", self.f2.read_text(encoding="utf-8"))

    def _candidates(self, view):
        from app.core.services import BACKUP_DIR

        out = []
        for side, path in (("左侧", view.left_path), ("右侧", view.right_path)):
            if path is None:
                continue
            for backup in sorted(BACKUP_DIR.glob(f"*-{path.name}"), reverse=True):
                stamp = backup.name.rsplit("-", 1)[0]
                out.append((f"{side} {path.name} · {stamp}", path, backup))
        return out

    def test_sync_preview_export(self) -> None:
        from app.core.models import SyncAction
        from app.ui.dialogs import ConfirmActionsDialog

        actions = [SyncAction("copy", "a.txt", None, None, note=""),
                   SyncAction("remove", "old/", None, None, is_dir=True)]
        dialog = ConfirmActionsDialog(self.window, "测试", "预演", actions)
        out = self.root / "preview.csv"
        with mock.patch("app.ui.dialogs.QFileDialog.getSaveFileName",
                        return_value=(str(out), "CSV (*.csv)")):
            dialog._export_preview()
        self.assertIn("preview.csv", dialog._preview_note.text())
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("复制覆盖,a.txt", text)
        self.assertIn("删除,old/", text)


class TestCoreP4(unittest.TestCase):
    """P4：SQLite 比较、Git 集成、图片感知相似度、文件夹缓存与并行。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls.dir = tempfile.TemporaryDirectory(prefix="ds-p4-")
        cls.root = Path(cls.dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.dir.cleanup()

    def _make_db(self, path: Path, rows: list[tuple], schema: str | None = None) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(schema or "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)")
            if rows:
                placeholders = ", ".join("?" * len(rows[0]))
                con.executemany(f"INSERT INTO items VALUES ({placeholders})", rows)
            con.commit()
        finally:
            con.close()

    def test_sqlite_compare_tables(self) -> None:
        from app.core.sqlite_diff import compare_tables, list_tables

        self._make_db(self.root / "a.db", [(1, "x", 10), (2, "y", 20), (3, "z", 30)])
        self._make_db(self.root / "b.db", [(1, "x", 10), (2, "y", 99), (4, "w", 40)])
        self.assertEqual(list_tables(self.root / "a.db"), ["items"])
        result = compare_tables(self.root / "a.db", self.root / "b.db", "items")
        self.assertEqual(result.key_column, "id")
        self.assertEqual([c.key for c in result.modified], ["2"])
        self.assertEqual(result.modified[0].changed_columns, ["qty"])
        self.assertEqual([c.key for c in result.only_left], ["3"])
        self.assertEqual([c.key for c in result.only_right], ["4"])
        self.assertEqual(result.same_count, 1)

    def test_sqlite_structural_mismatch(self) -> None:
        from app.core.sqlite_diff import SqliteError, compare_tables

        self._make_db(self.root / "c.db", [(1, "x")], schema="CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        self._make_db(self.root / "d.db", [], schema="CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, extra TEXT)")
        with self.assertRaises(SqliteError):
            compare_tables(self.root / "c.db", self.root / "d.db", "items")

    def test_git_changed_files_and_head_export(self) -> None:
        from app.core.git_diff import changed_files, export_head_file, is_repo

        repo = self.root / "repo"
        repo.mkdir(exist_ok=True)
        env_git = subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
        if env_git.returncode != 0:
            self.skipTest("git 不可用")
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], capture_output=True)
        (repo / "f.txt").write_text("head\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True)
        self.assertTrue(is_repo(repo))
        (repo / "f.txt").write_text("work\n", encoding="utf-8")
        (repo / "new.txt").write_text("new\n", encoding="utf-8")
        entries = {rel: (code, desc) for code, rel, desc in changed_files(repo)}
        self.assertIn("f.txt", entries)
        self.assertIn("new.txt", entries)
        head = export_head_file(repo, "f.txt", self.root / "headtmp")
        self.assertEqual(head.read_bytes(), b"head\n")
        self.assertIsNone(export_head_file(repo, "new.txt", self.root / "headtmp"))

    def test_image_perceptual_similarity(self) -> None:
        try:
            import io as _io

            from PIL import Image
        except ImportError:
            self.skipTest("Pillow 不可用")
        from app.core.media import compare_images

        def gradient(shift: int, invert: bool = False) -> bytes:
            buf = _io.BytesIO()
            img = Image.new("RGB", (64, 64))
            img.putdata([
                ((255 - ((x * 4 + shift) % 256)) if invert else (x * 4 + shift) % 256,) * 3
                for _ in range(64) for x in range(64)
            ])
            img.save(buf, format="PNG")
            return buf.getvalue()

        base = gradient(0)
        similar = compare_images(base, gradient(3))
        different = compare_images(base, gradient(0, invert=True))
        self.assertIsNotNone(similar.similarity)
        self.assertGreater(similar.similarity, 0.9)
        self.assertLess(different.similarity, 0.5)

    def test_folder_compare_cache_and_parallel(self) -> None:
        from app.core.compare import _SIGNATURE_CACHE, compare

        left = self.root / "fleft"
        right = self.root / "fright"
        left.mkdir(exist_ok=True)
        right.mkdir(exist_ok=True)
        for i in range(12):
            (left / f"f{i}.txt").write_text(f"same content {i}", encoding="utf-8")
            (right / f"f{i}.txt").write_text(f"same content {i}", encoding="utf-8")
        result1 = compare(str(left), str(right))
        self.assertEqual(result1.stats.get("differences", 0), 0)
        cached = len(_SIGNATURE_CACHE)
        self.assertGreater(cached, 0)
        # 再次比较：签名应全部命中缓存（缓存条目数不变），且结论一致
        result2 = compare(str(left), str(right))
        self.assertEqual(result2.stats.get("differences", 0), 0)
        self.assertEqual(len(_SIGNATURE_CACHE), cached)
        # 修改一侧后仍能检出差异
        (right / "f0.txt").write_text("changed", encoding="utf-8")
        result3 = compare(str(left), str(right))
        self.assertEqual(result3.stats.get("differences", 0), 1)


class TestStartupResume(unittest.TestCase):
    """启动不再自动恢复上次会话，改为主页一键恢复入口。"""

    def setUp(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-resume-")
        self.root = Path(self._tmp.name)
        self.dir_a = self.root / "A"
        self.dir_b = self.root / "B"
        self.dir_a.mkdir()
        self.dir_b.mkdir()
        (self.dir_a / "x.txt").write_text("1", encoding="utf-8")
        (self.dir_b / "x.txt").write_text("2", encoding="utf-8")
        self.last = self.root / "_last.json"
        self._patches = [
            mock.patch("app.ui.main_window.last_session_path", lambda: self.last),
            mock.patch("app.ui.main_window.save_last_session", lambda *a, **k: None),
            mock.patch("app.ui.main_window.show_warning", lambda *a, **k: None),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in self._patches:
            item.stop()
        self._tmp.cleanup()

    def _make_window(self):
        from app.ui.main_window import MainWindow

        window = MainWindow()
        window.show()
        self.app.processEvents()
        return window

    def _write_last(self, left: str, right: str) -> None:
        import json

        from app.core.models import CompareOptions
        from app.core.session import SESSION_VERSION

        payload = {
            "app": "DiffScope",
            "version": SESSION_VERSION,
            "name": "启动恢复测试",
            "mode": "folders",
            "left": left,
            "right": right,
            "options": CompareOptions().to_dict(),
            "filters": {},
            "current_diff": 0,
            "geometry": {},
            "saved_at": "",
        }
        self.last.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_startup_stays_on_welcome_with_resume_button(self) -> None:
        self._write_last(str(self.dir_a), str(self.dir_b))
        window = self._make_window()
        self.addCleanup(window.close)
        self.assertEqual(window.stack.currentIndex(), 0)  # 停在主页
        self.assertTrue(window.welcome.btn_resume.isVisible())
        self.assertIn("恢复上次会话", window.welcome.btn_resume.text())
        self.assertIsNone(window.result)  # 未自动开始比较

    def test_click_resume_starts_comparison(self) -> None:
        self._write_last(str(self.dir_a), str(self.dir_b))
        window = self._make_window()
        self.addCleanup(window.close)
        window.welcome.btn_resume.click()
        for _ in range(100):
            self.app.processEvents()
            time.sleep(0.02)
            if window.result is not None:
                break
        self.assertIsNotNone(window.result)
        self.assertEqual(window.left_edit.text(), str(self.dir_a))

    def test_invalid_last_session_hides_button(self) -> None:
        self._write_last("", "")  # 路径缺失 → 校验失败
        window = self._make_window()
        self.addCleanup(window.close)
        self.assertFalse(window.welcome.btn_resume.isVisible())
        self.assertEqual(window.stack.currentIndex(), 0)


class TestRecentDelete(unittest.TestCase):
    """首页「最近比较」列表支持删除单条与清空全部。"""

    def setUp(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-recentdel-")
        self.root = Path(self._tmp.name)
        self._patches = [
            mock.patch("app.ui.main_window.last_session_path", lambda: self.root / "no-last.json"),
            mock.patch("app.ui.main_window.save_last_session", lambda *a, **k: None),
            mock.patch("app.ui.main_window.show_warning", lambda *a, **k: None),
        ]
        for item in self._patches:
            item.start()
        from app.ui.main_window import MainWindow

        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()
        # 替换为内存 stub，避免测试写入真实 ~/.diffscope/settings.json
        self.store = {"recent_comparisons": [], "saved": False}

        class _StubSettings:
            def get(self, key, default=None):
                return self.data.get(key, default)

            def set(self, key, value):
                self.data[key] = value

            def save(self):
                self.saved = True

        self.stub = _StubSettings()
        self.stub.data = self.store
        self.window.settings = self.stub

    def tearDown(self) -> None:
        self.window.close()
        for item in self._patches:
            item.stop()
        self._tmp.cleanup()

    def _seed(self, n: int = 3) -> None:
        for i in range(n):
            self.window._record_recent(str(self.root / f"L{i}"), str(self.root / f"R{i}"))

    def test_delete_single_entry(self) -> None:
        self._seed(3)
        self.assertEqual(self.window.welcome.recent_list.count(), 3)
        self.window._delete_recent(1)  # 删除中间一条
        items = self.store["recent_comparisons"]
        self.assertEqual(len(items), 2)
        self.assertTrue(all("L1" not in e["left"] for e in items))
        self.assertEqual(self.window.welcome.recent_list.count(), 2)
        self.assertTrue(self.stub.saved)

    def test_delete_out_of_range_is_noop(self) -> None:
        self._seed(2)
        self.window._delete_recent(99)
        self.assertEqual(len(self.store["recent_comparisons"]), 2)

    def test_clear_all_confirmed(self) -> None:
        self._seed(3)
        with mock.patch("app.ui.main_window.confirm", return_value=True):
            self.window._clear_recent()
        self.assertEqual(self.store["recent_comparisons"], [])
        self.assertEqual(self.window.welcome.recent_list.count(), 0)
        self.assertFalse(self.window.welcome.recent_list.isVisible())
        self.assertFalse(self.window.welcome.btn_clear_recent.isVisible())

    def test_clear_all_cancelled_keeps_entries(self) -> None:
        self._seed(3)
        with mock.patch("app.ui.main_window.confirm", return_value=False):
            self.window._clear_recent()
        self.assertEqual(len(self.store["recent_comparisons"]), 3)

    def test_context_menu_signals(self) -> None:
        self._seed(2)
        received = []
        self.window.welcome.delete_recent.connect(received.append)
        self.window.welcome.clear_recent.connect(lambda: received.append("clear"))
        # emit 会同时触发窗口的真实处理：清空需 mock 确认框（离屏下模态弹窗会挂起）
        with mock.patch("app.ui.main_window.confirm", return_value=True):
            self.window.welcome.delete_recent.emit(0)
            self.window.welcome.clear_recent.emit()
        self.assertEqual(received, [0, "clear"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
