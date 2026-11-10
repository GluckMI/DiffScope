"""BC 细节完善测试：手动对齐、文件格式、三向文件夹合并、i18n、便携目录、HTML 格式化。"""

from __future__ import annotations

import os
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
        from app.core.services import check_update

        self.assertIn("未配置更新源", check_update())
        payload = self.root / "version.json"
        payload.write_text('{"version": "9.9.9"}', encoding="utf-8")
        self.assertIn("9.9.9", check_update(payload.as_uri()))
        payload.write_text('{"version": "2.0.0"}', encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
