"""扩展功能测试：VFS、规则、对齐、三向合并、格式、报告、脚本、同步模式、CLI、GUI。"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core import binary, formats, media, report, scripting  # noqa: E402
from app.core.alignment import AlignmentConfig, AlignmentRule  # noqa: E402
from app.core.compare import compare  # noqa: E402
from app.core.vfs import VfsUnsupported  # noqa: E402
from app.core.merge import MergeDirection  # noqa: E402
from app.core.merge3 import merge3_text  # noqa: E402
from app.core.models import CompareOptions, DiffStatus  # noqa: E402
from app.core.rules import CompareRules, IgnoreRule  # noqa: E402
from app.core.services import Settings, TaskController, backup_file, check_update, shell_commands, vcs_config_commands  # noqa: E402
from app.core.sync_modes import SyncMode, plan_mode  # noqa: E402
from app.core.text_diff import compute_text_diff  # noqa: E402
from app.core.vfs import LocalVfs, SnapshotVfs, ZipVfs, create_snapshot, open_vfs  # noqa: E402
from app.core.workspace import Workspace, WorkspaceTab, load_workspace, save_workspace  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ds-ext-test-")
        self.root = Path(self._tmp.name)
        self.left = self.root / "L"
        self.right = self.root / "R"
        self.left.mkdir()
        self.right.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def sample(self) -> None:
        (self.left / "same.txt").write_text("x\n", encoding="utf-8")
        (self.right / "same.txt").write_text("x\n", encoding="utf-8")
        (self.left / "mod.txt").write_text("a\n", encoding="utf-8")
        (self.right / "mod.txt").write_text("b\n", encoding="utf-8")
        (self.left / "only_left.txt").write_text("L\n", encoding="utf-8")
        (self.right / "only_right.txt").write_text("R\n", encoding="utf-8")


class TestVfs(Fixture):
    def test_local_list_and_read(self) -> None:
        (self.left / "a.txt").write_text("hello", encoding="utf-8")
        provider = LocalVfs(self.left)
        entries = provider.list_dir()
        self.assertEqual([e.name for e in entries], ["a.txt"])
        self.assertEqual(provider.read_bytes("a.txt"), b"hello")

    def test_local_rejects_escape(self) -> None:
        from app.core.vfs import VfsError

        with self.assertRaises(VfsError):
            LocalVfs(self.left).read_bytes("../outside.txt")

    def test_zip_roundtrip(self) -> None:
        archive = self.root / "a.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("dir/a.txt", "hello")
            handle.writestr("dir/b.txt", "world")
        provider = open_vfs("zip:" + str(archive))
        self.assertEqual([e.name for e in provider.list_dir()], ["dir"])
        self.assertEqual([e.name for e in provider.list_dir("dir")], ["a.txt", "b.txt"])
        self.assertEqual(provider.read_bytes("dir/a.txt"), b"hello")
        provider.write_bytes("dir/a.txt", b"changed")
        provider.remove("dir/b.txt")
        provider.flush()
        with zipfile.ZipFile(archive) as handle:
            self.assertEqual(handle.read("dir/a.txt"), b"changed")
            self.assertNotIn("dir/b.txt", handle.namelist())

    def test_tar(self) -> None:
        import tarfile

        archive = self.root / "a.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            data = b"hello"
            info = tarfile.TarInfo("dir/a.txt")
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
        provider = open_vfs("tar:" + str(archive))
        self.assertEqual(provider.read_bytes("dir/a.txt"), b"hello")

    def test_snapshot_is_readonly(self) -> None:
        (self.left / "a.txt").write_text("x", encoding="utf-8")
        path = create_snapshot(self.left, self.root / "snap.json")
        provider = open_vfs("snapshot:" + str(path))
        self.assertEqual([e.name for e in provider.list_dir()], ["a.txt"])
        with self.assertRaises(VfsUnsupported):
            provider.read_bytes("a.txt")

    def test_compare_through_vfs(self) -> None:
        (self.left / "a.txt").write_text("x", encoding="utf-8")
        (self.right / "a.txt").write_text("y", encoding="utf-8")
        result = compare(str(self.left), str(self.right),
                         left_vfs=LocalVfs(self.left), right_vfs=LocalVfs(self.right))
        self.assertEqual(result.stats["differences"], 1)
        self.assertTrue(result.left_label)

    def test_remote_constructors(self) -> None:
        from app.core.vfs_remote import FtpVfs, SftpVfs, WebDavVfs, S3Vfs, DropboxVfs, OneDriveVfs

        specs = [
            (FtpVfs, "ftp://user:pass@example.org/dir"),
            (SftpVfs, "sftp://user@example.org/dir"),
            (WebDavVfs, "webdav://example.org/dav/path"),
            (S3Vfs, "s3://bucket/prefix"),
            (DropboxVfs, "dropbox://Apps/diffscope"),
            (OneDriveVfs, "onedrive://root/folder"),
        ]
        for cls, spec in specs:
            provider = cls(spec)
            self.assertTrue(provider.label)


class TestRulesAndAlignment(unittest.TestCase):
    def test_ignore_comments_and_header(self) -> None:
        rules = CompareRules(rules=[IgnoreRule("comments", "python"), IgnoreRule("header", value=1)])
        diff = compute_text_diff("HEADER\na=1 # note\nb=2\n", "OTHER\na=1\nb=2\n",
                                 CompareOptions(), rules=rules)
        self.assertEqual(diff.diff_rows, [])

    def test_ignore_columns_regex_and_unimportant(self) -> None:
        rules = CompareRules(rules=[
            IgnoreRule("columns", value=[2]),
            IgnoreRule("regex", r"v\d+"),
        ])
        diff = compute_text_diff("a\tv1\tc\n", "a\tv2\tc\n", CompareOptions(), rules=rules)
        self.assertEqual(diff.diff_rows, [])
        unimportant = CompareRules(rules=[IgnoreRule("unimportant", r"DEBUG")])
        diff2 = compute_text_diff("DEBUG a\n", "DEBUG b\n", CompareOptions(), rules=unimportant)
        self.assertEqual(diff2.diff_rows, [])

    def test_replacement_rule(self) -> None:
        rules = CompareRules(rules=[IgnoreRule("replacement", value={"left": "apple", "right": "orange"})])
        diff = compute_text_diff("apple pie\n", "orange pie\n", CompareOptions(), rules=rules)
        self.assertEqual(diff.diff_rows, [])

    def test_rules_serialization(self) -> None:
        rules = CompareRules(rules=[IgnoreRule("header", value=2)], language="python")
        restored = CompareRules.from_dict(rules.to_dict())
        self.assertEqual(restored.rules[0].value, 2)
        self.assertEqual(restored.language, "python")

    def test_alignment_different_extensions(self) -> None:
        config = AlignmentConfig(rules=[AlignmentRule("*.gif", "*.png")])
        pairs = config.build_map(["logo.gif", "a.txt"], ["logo.png", "a.txt"])
        self.assertEqual(pairs.get("logo.gif"), "logo.png")
        self.assertNotIn("a.txt", pairs)

    def test_alignment_wildcard_prefix(self) -> None:
        config = AlignmentConfig(rules=[AlignmentRule("Apples*", "Bananas*")])
        pairs = config.build_map(["Apples_01.txt"], ["Bananas_01.txt"])
        self.assertEqual(pairs.get("Apples_01.txt"), "Bananas_01.txt")

    def test_compare_with_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "A", Path(folder) / "B"
            a.mkdir()
            b.mkdir()
            (a / "logo.gif").write_bytes(b"same")
            (b / "logo.png").write_bytes(b"same")
            result = compare(str(a), str(b), alignment=AlignmentConfig(rules=[AlignmentRule("*.gif", "*.png")]))
            nodes = [n for n in result.root.walk() if n.rel]
            self.assertEqual(len(nodes), 1)
            self.assertIs(nodes[0].status, DiffStatus.SAME)
            self.assertEqual(nodes[0].right_rel, "logo.png")


class TestMerge3(unittest.TestCase):
    def test_independent_changes_auto_merge(self) -> None:
        result = merge3_text("a\nb\nc\n", "a\nB\nc\n", "a\nb\nC\n")
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.output_text, "a\nB\nC\n")

    def test_conflict_and_resolve(self) -> None:
        result = merge3_text("a\n", "b\n", "c\n")
        self.assertEqual(len(result.conflicts), 1)
        result.resolve_all("left")
        self.assertEqual(result.output_text, "b\n")
        self.assertEqual(result.conflicts, [])

    def test_favor(self) -> None:
        result = merge3_text("a\n", "b\n", "c\n", favor="right")
        self.assertEqual(result.output_text, "c\n")

    def test_same_change_no_conflict(self) -> None:
        result = merge3_text("a\n", "X\n", "X\n")
        self.assertEqual(result.output_text, "X\n")
        self.assertEqual(result.conflicts, [])


class TestFormats(Fixture):
    def test_csv_and_key_columns(self) -> None:
        left = formats.load_table(b"id,v\na,1\nb,2\n", "a.csv")
        right = formats.load_table(b"id,v\na,1\nb,3\n", "b.csv")
        diff = formats.compare_tables(left, right, formats.TableOptions(key_columns=[0]))
        self.assertEqual(diff.diff_indexes, [1])

    def test_ignore_columns_and_tolerance(self) -> None:
        left = formats.load_table(b"a,1,10\n", "a.csv")
        right = formats.load_table(b"a,2,10.4\n", "b.csv")
        diff = formats.compare_tables(left, right, formats.TableOptions(
            ignore_columns=[1], tolerance=0.5))
        self.assertEqual(diff.diff_indexes, [])

    def test_html_table_and_xlsx(self) -> None:
        html = formats.load_table(b"<table><tr><th>n</th></tr><tr><td>a</td></tr></table>", "a.html")
        self.assertEqual(html.headers, ["n"])
        self.assertEqual(html.rows, [["a"]])
        try:
            import openpyxl

            workbook = openpyxl.Workbook()
            workbook.active.append(["n", "v"])
            workbook.active.append(["a", "1"])
            buffer = io.BytesIO()
            workbook.save(buffer)
            table = formats.load_table(buffer.getvalue(), "a.xlsx")
            self.assertEqual(table.headers, ["n", "v"])
            self.assertIn("Sheet", table.sheets)
        except ImportError:
            pass

    def test_document_conversion(self) -> None:
        self.assertIn("Hello", formats.to_text(b"<p>Hello <b>World</b></p>", "a.html"))
        self.assertIn("Hello", formats.to_text(br"{\rtf1 Hello \b World\b0}", "a.rtf"))

    def test_patch(self) -> None:
        left, right, hunks = formats.parse_patch(
            "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
        )
        self.assertEqual(left, "a\nb\n")
        self.assertEqual(right, "a\nc\n")
        self.assertEqual(len(hunks), 1)

    def test_external_tool_requires_path(self) -> None:
        with self.assertRaises(formats.FormatError):
            formats.run_external_compare("", "a", "b")


class TestBinaryAndMedia(Fixture):
    def test_hex_compare_and_edit(self) -> None:
        result = binary.compare_hex(b"abc\x00def", b"abc\x01def")
        self.assertEqual(result.diff_offsets, [3])
        self.assertEqual(binary.patch_hex(b"abc", 1, b"Z"), b"aZc")
        self.assertEqual(binary.find_hex(b"abab", b"ab"), [0, 2])
        self.assertEqual(binary.replace_hex(b"abab", b"ab", b"XY"), (b"XYXY", 2))

    def test_registry_file_compare(self) -> None:
        left = binary.parse_reg_file(b'[HKEY_CURRENT_USER\\Test]\r\n"a"="1"\r\n')
        right = binary.parse_reg_file(b'[HKEY_CURRENT_USER\\Test]\r\n"a"="2"\r\n')
        diff = binary.compare_registry(left, right)
        self.assertEqual(diff.differences, 1)

    def test_images_tolerance(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow 未安装")

        def png(color):
            image = Image.new("RGB", (4, 4), color)
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue()

        a, b = png((10, 10, 10)), png((11, 10, 10))
        self.assertEqual(media.compare_images(a, b).diff_pixels, 16)
        self.assertEqual(media.compare_images(a, b, tolerance=2).diff_pixels, 0)

    def test_id3v1_tags(self) -> None:
        tag = (b"TAG" + b"TITLE".ljust(30, b"\x00") + b"ARTIST".ljust(30, b"\x00")
               + b"ALBUM".ljust(30, b"\x00") + b"2024" + b"\x00" * 31)
        tags = media.read_tags(b"x" * 100 + tag, "a.mp3")
        self.assertEqual(tags["TIT2"], "TITLE")

    def test_exe_version_fallback(self) -> None:
        info = media.exe_version(b"random bytes", "nonexistent.exe")
        self.assertIn("大小", info)


class TestReportAndScripting(Fixture):
    def test_reports(self) -> None:
        self.sample()
        result = compare(str(self.left), str(self.right))
        self.assertIn("差异明细", report.html_folder_report(result))
        self.assertIn("mod.txt", report.csv_report(result))
        self.assertIn("mod.txt", report.text_report(result))
        target = report.save_report(result, self.root / "r.html", "html")
        self.assertTrue(target.exists())
        linked = report.save_linked_reports(result, self.root / "repo")
        self.assertTrue(linked.exists())

    def test_cli_diff_exit_codes(self) -> None:
        from cli import main as cli_main

        (self.left / "a.txt").write_text("x", encoding="utf-8")
        (self.right / "a.txt").write_text("x", encoding="utf-8")
        self.assertEqual(cli_main(["--diff", str(self.left), str(self.right), "--quiet"]), 0)
        (self.right / "a.txt").write_text("y", encoding="utf-8")
        self.assertEqual(cli_main(["--diff", str(self.left), str(self.right), "--quiet"]), 1)

    def test_cli_merge_conflict(self) -> None:
        from cli import main as cli_main

        for name, text in (("base.txt", "a\n"), ("left.txt", "b\n"), ("right.txt", "c\n")):
            (self.root / name).write_text(text, encoding="utf-8")
        code = cli_main(["--merge", str(self.root / "base.txt"), str(self.root / "left.txt"),
                         str(self.root / "right.txt"), str(self.root / "out.txt")])
        self.assertEqual(code, 3)
        self.assertIn("<<<<<<< LEFT", (self.root / "out.txt").read_text(encoding="utf-8"))

    def test_script_runner(self) -> None:
        self.sample()
        script = (
            f'load "{self.left}" "{self.right}"\n'
            "compare\n"
            f'folder-report output-to:{self.root / "script.html"}\n'
            "log finished\n"
        )
        result = scripting.ScriptRunner(self.root).run(script)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.differences, 3)
        self.assertEqual(result.exit_code, 1)
        self.assertTrue((self.root / "script.html").exists())

    def test_script_rejects_write_without_permission(self) -> None:
        self.sample()
        script = f'load "{self.left}" "{self.right}"\ncompare\nsync mirror\n'
        result = scripting.ScriptRunner(self.root, allow_write=False).run(script)
        self.assertFalse(result.ok)


class TestServicesAndWorkspace(Fixture):
    def test_settings_roundtrip(self) -> None:
        path = self.root / "settings.json"
        settings = Settings(path)
        settings.set("theme", "light")
        settings.save()
        self.assertEqual(Settings(path).get("theme"), "light")

    def test_backup(self) -> None:
        from app.core import services

        target = self.left / "a.txt"
        target.write_text("x", encoding="utf-8")
        with mock.patch.object(services, "BACKUP_DIR", self.root / "backups"):
            backup = backup_file(target, keep=2)
        self.assertTrue(backup and backup.exists())

    def test_task_controller(self) -> None:
        controller = TaskController()
        controller.pause()
        self.assertTrue(controller.state.paused)
        controller.resume()
        self.assertFalse(controller.state.paused)
        controller.cancel()
        self.assertTrue(controller.cancelled)

    def test_integration_helpers(self) -> None:
        self.assertTrue(any("reg add" in cmd for cmd in shell_commands()))
        self.assertTrue(any("difftool" in cmd for cmd in vcs_config_commands("run.bat")["git"]))
        self.assertIn("DiffScope", check_update())

    def test_workspace_roundtrip(self) -> None:
        workspace = Workspace(name="测试工作区", tabs=[WorkspaceTab("标签1", left="A", right="B")])
        path = save_workspace(workspace, self.root / "ws.json")
        loaded = load_workspace(path)
        self.assertEqual(loaded.name, "测试工作区")
        self.assertEqual(loaded.tabs[0].left, "A")


class TestSyncModes(Fixture):
    def test_mirror_update_two_way(self) -> None:
        self.sample()
        result = compare(str(self.left), str(self.right))
        mirror = plan_mode(result, SyncMode.MIRROR, MergeDirection.LEFT_TO_RIGHT)
        update = plan_mode(result, SyncMode.UPDATE, MergeDirection.LEFT_TO_RIGHT)
        two_way = plan_mode(result, SyncMode.TWO_WAY, MergeDirection.LEFT_TO_RIGHT)
        self.assertIn("remove", [a.kind for a in mirror.actions])
        self.assertNotIn("remove", [a.kind for a in update.actions])
        self.assertNotIn("remove", [a.kind for a in two_way.actions])
        self.assertTrue(any(a.rel == "only_right.txt" for a in two_way.actions))

    def test_extended_actions_execute(self) -> None:
        from app.core.sync import SyncPlan, execute_sync
        from app.core.sync_modes import move_action, touch_action

        (self.left / "a.txt").write_text("x", encoding="utf-8")
        result = compare(str(self.left), str(self.right))
        actions = [
            touch_action(result, "a.txt", 1_600_000_000_000_000_000, "left"),
            move_action(result, "a.txt", "renamed.txt", "left"),
        ]
        output = execute_sync(SyncPlan(actions=actions))
        self.assertTrue(output.ok, output.failed)
        self.assertTrue((self.left / "renamed.txt").exists())


class TestExtendedGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_viewers_construct_and_load(self) -> None:
        from app.ui.viewers import create_viewer

        viewer = create_viewer("table")
        viewer.set_data(b"a,b\n1,2\n", b"a,b\n1,3\n", "a.csv", "b.csv")
        self.assertEqual(viewer.left_table.rowCount(), 1)  # 首行为表头
        hex_viewer = create_viewer("hex")
        hex_viewer.set_data(b"abc\x00", b"abc\x01", "a.bin", "b.bin")
        self.assertIn("00000000", hex_viewer.left_text.toPlainText())

    def test_dialogs_and_views_construct(self) -> None:
        from app.ui.dialogs_ext import HelpDialog, RulesDialog, SettingsDialog
        from app.ui.extended_views import Merge3View, WorkspaceWindow
        from app.ui.main_window import MainWindow

        window = MainWindow()
        dialogs = [HelpDialog(window), RulesDialog(window, CompareRules()),
                   SettingsDialog(window, Settings(Path(tempfile.gettempdir()) / "ds-test-settings.json"))]
        self.assertEqual(len(dialogs), 3)
        self.assertEqual(WorkspaceWindow().tabs.count(), 1)
        self.assertIsNotNone(Merge3View(window))

    def test_main_window_extensions(self) -> None:
        import tempfile

        from app.core.services import Settings
        from app.ui.main_window import MainWindow

        # 主题切换会写盘，必须用临时配置：既不依赖用户当前主题，也不污染 ~/.diffscope
        with tempfile.TemporaryDirectory(prefix="ds-theme-") as tmp:
            settings = Settings(Path(tmp) / "settings.json")
            with mock.patch("app.ui.main_window.Settings", return_value=settings), \
                    mock.patch("app.ui.main_window.save_last_session", lambda *a, **k: None):
                window = MainWindow()
                window.show()
                self.assertEqual([a.text() for a in window.menuBar().actions()][-1], "扩展")
                self.assertEqual(window.settings.get("theme"), "dark")
                window.file_view.apply_settings(window.settings)
                window.toggle_theme()
                self.assertEqual(window.settings.get("theme"), "light")
                window.toggle_theme()
                self.assertEqual(window.settings.get("theme"), "dark")
                window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
