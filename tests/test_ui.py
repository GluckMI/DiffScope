"""界面层离屏冒烟测试：验证主流程能真实跑通。

运行：python -m unittest discover -s tests -v
需要 PySide6；测试使用 offscreen 平台，不弹出实际窗口。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.merge import MergeDirection  # noqa: E402
from app.ui import file_view as fvm  # noqa: E402
from app.ui import folder_view as fvmod  # noqa: E402
from app.ui import main_window as mwmod  # noqa: E402
from app.ui.dialogs import ConfirmActionsDialog, SyncResultDialog  # noqa: E402
from app.ui.theme import STYLESHEET  # noqa: E402


class UiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLESHEET)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="diffscope-ui-")
        self.root = Path(self._tmp.name)
        self.left = self.root / "L"
        self.right = self.root / "R"
        self.left.mkdir()
        self.right.mkdir()
        self.errors: list[Exception] = []
        self.warnings: list[str] = []
        self._patchers: list[mock._patch] = []

        def patch(target, value):
            p = mock.patch(target, value)
            p.start()
            self._patchers.append(p)

        def record_error(*args, **_kwargs):
            self.errors.append(args[1] if len(args) > 1 else args[0])

        def record_warning(title, text, *args, **_kwargs):
            self.warnings.append(f"{title}: {text}")

        patch("app.ui.main_window.confirm", lambda *a, **k: True)
        patch("app.ui.folder_view.confirm", lambda *a, **k: True)
        patch("app.ui.file_view.confirm", lambda *a, **k: True)
        patch("app.ui.main_window.show_error", record_error)
        patch("app.ui.folder_view.show_error", record_error)
        patch("app.ui.file_view.show_error", record_error)
        patch("app.ui.main_window.show_warning", record_warning)
        patch("app.ui.folder_view.show_info", lambda *a, **k: None)
        patch("app.ui.file_view.show_info", lambda *a, **k: None)
        patch("app.ui.main_window.ask_session_name", lambda *a, **k: "ui-test-session")
        patch("app.ui.main_window.last_session_path", lambda: self.root / "no-last-session.json")
        patch("app.ui.main_window.save_last_session", lambda *a, **k: None)
        patch("app.ui.dialogs.ConfirmActionsDialog.ask", staticmethod(lambda *a, **k: True))
        patch("app.ui.dialogs.SyncResultDialog.exec", lambda self: 0)
        patch("app.core.session.session_dir", lambda: self.root / "sessions")
        patch(
            "app.core.sync.remove_path",
            lambda p: shutil.rmtree(p) if p.is_dir() else p.unlink(),
        )
        self.window = mwmod.MainWindow()
        self.window.show()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()
        for p in reversed(self._patchers):
            p.stop()
        self._tmp.cleanup()

    def make_sample(self) -> None:
        (self.left / "same.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.right / "same.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.left / "mod.txt").write_text("aaa\nbbb\nccc\n", encoding="utf-8")
        (self.right / "mod.txt").write_text("aaa\nXXX\nccc\nzzz\n", encoding="utf-8")
        (self.left / "only_left.txt").write_text("L\n", encoding="utf-8")
        (self.right / "only_right.txt").write_text("R\n", encoding="utf-8")
        (self.left / "sub").mkdir()
        (self.right / "sub").mkdir()
        (self.left / "sub" / "f.txt").write_text("1\n", encoding="utf-8")
        (self.right / "sub" / "f.txt").write_text("2\n", encoding="utf-8")

    def wait_text_view(self, timeout_ms: int = 8000) -> bool:
        """两阶段等待文本异步加载：先等加载发起，再等完成。"""
        view = self.window.file_view
        waited = 0
        while view._async_thread is None and waited < timeout_ms:
            QApplication.processEvents()
            time.sleep(0.01)
            waited += 10
        while view._async_thread is not None and waited < timeout_ms:
            QApplication.processEvents()
            time.sleep(0.01)
            waited += 10
        QApplication.processEvents()
        return view._async_thread is None

    def wait_compare(self) -> bool:
        for _ in range(300):
            QApplication.processEvents()
            if self.window._thread is None:
                return True
            time.sleep(0.01)
        return False

    def compare_folders(self) -> None:
        self.make_sample()
        self.window.left_edit.setText(str(self.left))
        self.window.right_edit.setText(str(self.right))
        self.window.start_compare()
        self.assertTrue(self.wait_compare())

    # ---- 测试用例 -------------------------------------------------
    def test_initial_state(self) -> None:
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertIn("就绪", self.window.status_label.text())
        self.assertIsNone(self.window.result)

    def test_compare_button_switches_view(self) -> None:
        self.make_sample()
        w = self.window
        w.left_edit.setText(str(self.left))
        w.right_edit.setText(str(self.right))
        w.btn_compare.click()  # 真实按钮信号，验证不会把 checked 当成 switch_view
        self.assertTrue(self.wait_compare())
        self.assertEqual(w.stack.currentIndex(), 1)
        self.assertEqual(w.folder_view.model.rowCount(QModelIndex()), 5)

    def test_back_button_returns_to_folders(self) -> None:
        self.compare_folders()
        w = self.window
        w.open_text_pair(str(self.left / "mod.txt"), str(self.right / "mod.txt"), "mod.txt")
        self.assertTrue(self.wait_text_view())
        self.assertEqual(w.stack.currentIndex(), 2)
        w.file_view.btn_back.click()
        self.assertEqual(w.stack.currentIndex(), 1)

    def test_folder_compare_filter_and_navigation(self) -> None:
        self.compare_folders()
        w = self.window
        self.assertEqual(w.stack.currentIndex(), 1)
        self.assertEqual(w.folder_view.model.rowCount(QModelIndex()), 5)
        self.assertGreater(w.folder_view.difference_count(), 0)

        w.folder_view.next_difference()
        self.assertGreaterEqual(w.folder_view.current_difference_index(), 0)
        w.folder_view.previous_difference()
        self.assertGreaterEqual(w.folder_view.current_difference_index(), 0)

        w.apply_status_filter("differences")
        self.assertLessEqual(w.folder_view.model.rowCount(QModelIndex()), 5)
        w.folder_view.set_filter_state("only_left", None)
        self.assertEqual(w.folder_view.model.rowCount(QModelIndex()), 1)
        w.folder_view.set_filter_state("", None)
        w.apply_status_filter(None)
        self.assertEqual(w.folder_view.model.rowCount(QModelIndex()), 5)

    def test_text_diff_merge_and_save(self) -> None:
        self.compare_folders()
        w = self.window
        w.open_text_pair(str(self.left / "mod.txt"), str(self.right / "mod.txt"), "mod.txt")
        self.assertTrue(self.wait_text_view())
        self.assertEqual(w.stack.currentIndex(), 2)
        self.assertEqual(len(w.file_view.blocks), 2)
        self.assertEqual(
            w.file_view.left_edit.document().blockCount(),
            w.file_view.right_edit.document().blockCount(),
        )
        self.assertTrue(w.file_view.left_edit.extraSelections())

        w.file_view.set_status_filter("modified")
        self.assertEqual(len(w.file_view.visible_blocks()), 1)
        w.file_view.set_status_filter(None)
        w.file_view.set_filter_state("aaa", False)
        self.assertIn("匹配", w.file_view.match_label.text())

        w.file_view.goto_block(0)
        w.file_view.merge_current(MergeDirection.LEFT_TO_RIGHT)
        self.assertEqual(len(w.file_view.blocks), 1)
        self.assertTrue(w.file_view.save_side("right"))
        saved = (self.right / "mod.txt").read_text(encoding="utf-8")
        self.assertIn("bbb", saved)
        self.assertNotIn("XXX", saved)
        w.file_view.recompare()
        self.assertEqual(len(w.file_view.blocks), 1)
        self.assertEqual(self.errors, [])

    def test_single_side_file_can_be_saved_as_new(self) -> None:
        self.compare_folders()
        w = self.window
        target = self.right / "only_left.txt"
        self.assertFalse(target.exists())
        w.open_text_pair(str(self.left / "only_left.txt"), str(target), "only_left.txt")
        self.assertTrue(self.wait_text_view())
        self.assertEqual(w.stack.currentIndex(), 2)
        self.assertEqual(w.file_view.right_edit.text(), "")
        w.file_view.merge_all(MergeDirection.LEFT_TO_RIGHT)
        self.assertTrue(w.file_view.result.identical)
        self.assertTrue(w.file_view.save_side("right"))
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "L\n")

    def test_binary_file_opens_hex_viewer(self) -> None:
        from app.ui.viewers import HexViewer

        binary = self.left / "data.bin"
        binary.write_bytes(b"\x00\x01\x02\x00binary")
        self.compare_folders()
        w = self.window
        before = len(w._extra_windows)
        w.open_text_pair(str(binary), str(binary), "data.bin")
        self.assertEqual(len(w._extra_windows), before + 1)
        self.assertIsInstance(w._extra_windows[-1].findChild(HexViewer), HexViewer)

    def test_unreadable_text_shows_clear_error(self) -> None:
        from app.core.errors import DecodeFailure

        (self.left / "a.txt").write_text("x", encoding="utf-8")
        (self.right / "a.txt").write_text("y", encoding="utf-8")
        w = self.window
        w.stack.setCurrentIndex(2)
        with mock.patch("app.ui.file_view.read_text",
                        side_effect=DecodeFailure("无法按文本方式读取", "模拟读取失败")):
            ok = w.file_view.load_pair(str(self.left / "a.txt"), str(self.right / "a.txt"), None)
        self.assertFalse(ok)
        self.assertTrue(w.file_view.error_banner.isVisible())
        self.assertIn("无法按文本方式读取", w.file_view.error_banner.text())
        self.assertTrue(self.errors)

    def test_sync_then_refresh(self) -> None:
        self.compare_folders()
        w = self.window
        before = w.result.stats["differences"]
        self.assertGreater(before, 0)
        w.folder_view.sync(MergeDirection.LEFT_TO_RIGHT)
        self.assertTrue(self.wait_compare())
        self.assertEqual(w.result.stats["differences"], 0)

    def test_session_save_and_restore(self) -> None:
        self.compare_folders()
        w = self.window
        w.folder_view.set_filter_state("mod", None)
        w.folder_view.goto_difference(0)
        w.save_session_as()
        self.assertEqual(w.session_name, "ui-test-session")
        self.assertTrue((self.root / "sessions" / "ui-test-session.json").exists())

        from app.core.session import load_session

        session = load_session(self.root / "sessions" / "ui-test-session.json")
        self.assertEqual(session.filters["folder_text"], "mod")
        w.restore_session(session)
        self.assertTrue(self.wait_compare())
        self.assertEqual(w.folder_view.current_filter()[0], "mod")
        self.assertEqual(w.left_edit.text(), str(self.left))

    def test_invalid_path_is_reported(self) -> None:
        w = self.window
        w.left_edit.setText(str(self.root / "definitely-missing"))
        w.right_edit.setText(str(self.right))
        w.start_compare()
        self.assertTrue(self.wait_compare())
        self.assertTrue(any("不存在" in getattr(e, "message", "") for e in self.errors))

    def test_file_mode_opens_text_view_directly(self) -> None:
        a, b = self.left / "a.txt", self.right / "b.txt"
        a.write_text("one\ntwo\n", encoding="utf-8")
        b.write_text("one\nTWO\n", encoding="utf-8")
        w = self.window
        w.mode_box.setCurrentIndex(1)
        w.left_edit.setText(str(a))
        w.right_edit.setText(str(b))
        w.start_compare()
        self.assertTrue(self.wait_compare())
        self.assertTrue(self.wait_text_view())
        self.assertEqual(w.stack.currentIndex(), 2)
        self.assertEqual(len(w.file_view.blocks), 1)
        self.assertEqual(w.result.mode.value, "files")


if __name__ == "__main__":
    unittest.main(verbosity=2)
