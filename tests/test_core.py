"""核心引擎单元测试（不需要图形界面）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.compare import compare
from app.core.errors import (
    AccessDeniedError,
    DecodeFailure,
    InvalidPathError,
    PathNotFoundError,
    PathTypeError,
    SessionError,
    WriteFailure,
    map_os_error,
    validate_path,
)
from app.core.fs_utils import format_size, is_within, read_text, safe_join, write_text_atomic
from app.core.merge import MergeDirection, merge_all, merge_block
from app.core.models import (
    CompareMode,
    CompareOptions,
    DiffStatus,
    PathKind,
)
from app.core import session as session_mod
from app.core.session import (
    Session,
    load_session,
    save_session,
    save_last_session,
    list_sessions,
    validate_session,
)
from app.core.sync import execute_sync, plan_sync
from app.core.text_diff import compute_text_diff, diff_blocks, split_lines


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="diffscope-test-")
        self.root = Path(self._tmp.name)
        self.left = self.root / "L"
        self.right = self.root / "R"
        self.left.mkdir()
        self.right.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_sample(self) -> None:
        (self.left / "same.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.right / "same.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.left / "mod.txt").write_text("aaa\nbbb\n", encoding="utf-8")
        (self.right / "mod.txt").write_text("aaa\nXXX\n", encoding="utf-8")
        (self.left / "only_left.txt").write_text("L\n", encoding="utf-8")
        (self.right / "only_right.txt").write_text("R\n", encoding="utf-8")
        (self.left / "sub").mkdir()
        (self.right / "sub").mkdir()
        (self.left / "sub" / "inner.txt").write_text("1\n", encoding="utf-8")
        (self.right / "sub" / "inner.txt").write_text("2\n", encoding="utf-8")


class TestFsUtils(Fixture):
    def test_encoding_detection(self) -> None:
        cases = {
            "utf8.txt": ("中文内容\n".encode("utf-8"), "中文内容\n", "utf-8"),
            "gbk.txt": ("中文内容\n".encode("gbk"), "中文内容\n", None),
            "bom.txt": ("中文内容\n".encode("utf-8-sig"), "中文内容\n", "utf-8-sig"),
            "u16.txt": ("中文内容\n".encode("utf-16"), "中文内容\n", "utf-16"),
        }
        for name, (data, expected, encoding) in cases.items():
            path = self.left / name
            path.write_bytes(data)
            doc = read_text(path)
            self.assertEqual(doc.text, expected)
            if encoding:
                self.assertEqual(doc.encoding, encoding)
            self.assertEqual(doc.newline, "\n")

    def test_newline_detection_and_atomic_write(self) -> None:
        path = self.left / "crlf.txt"
        path.write_bytes("a\r\nb\r\n".encode("utf-8"))
        doc = read_text(path)
        self.assertEqual(doc.newline, "\r\n")
        write_text_atomic(path, "a\r\nc\r\n", doc.encoding, doc.newline)
        self.assertEqual(path.read_bytes(), "a\r\nc\r\n".encode("utf-8"))

    def test_binary_rejected(self) -> None:
        path = self.left / "bin.dat"
        path.write_bytes(b"\x00\x01\x02binary\x00")
        with self.assertRaises(DecodeFailure):
            read_text(path)

    def test_oversize_rejected(self) -> None:
        path = self.left / "big.txt"
        path.write_text("x", encoding="utf-8")
        with self.assertRaises(DecodeFailure):
            read_text(path, max_bytes=0)

    def test_safe_join_blocks_escape(self) -> None:
        with self.assertRaises(WriteFailure):
            safe_join(self.left, "../outside.txt")
        self.assertEqual(safe_join(self.left, "sub/f.txt"), (self.left / "sub" / "f.txt").resolve())

    def test_format_size(self) -> None:
        self.assertEqual(format_size(512), "512 B")
        self.assertEqual(format_size(2048), "2.0 KB")


class TestTextDiff(unittest.TestCase):
    def test_roundtrip_and_alignment(self) -> None:
        left = "a\nb\nc\n"
        right = "a\nB\nc\nd\n"
        result = compute_text_diff(left, right)
        self.assertEqual(result.left_text, left)
        self.assertEqual(result.right_text, right)
        self.assertEqual(len(result.rows), 4)
        self.assertEqual(result.diff_rows, [1, 3])
        self.assertEqual(diff_blocks(result), [[1], [3]])
        self.assertEqual(result.rows[3].left_text, None)
        self.assertEqual(result.rows[3].right_no, 4)

    def test_inline_spans(self) -> None:
        result = compute_text_diff("hello world\n", "hello WORLD\n")
        row = result.rows[0]
        self.assertEqual(row.op, "replace")
        self.assertTrue(row.left_spans)
        self.assertTrue(row.right_spans)

    def test_ignore_rules(self) -> None:
        opts = CompareOptions(ignore_case=True)
        self.assertTrue(compute_text_diff("ABC\n", "abc\n", opts).identical)
        self.assertTrue(compute_text_diff("a b\n", "ab\n", CompareOptions(ignore_whitespace=True)).identical)
        self.assertFalse(compute_text_diff("ABC\n", "abc\n").identical)

    def test_crlf_preserved(self) -> None:
        left, right = "a\r\nb\r\n", "a\r\nB\r\n"
        result = compute_text_diff(left, right)
        self.assertEqual(result.left_text, left)
        self.assertEqual(result.right_text, right)
        self.assertEqual(result.left_newline, "\r\n")

    def test_empty_and_placeholder(self) -> None:
        result = compute_text_diff("", "only right\n")
        self.assertEqual(result.diff_rows, [0])
        self.assertIsNone(result.rows[0].left_text)
        self.assertEqual(result.right_text, "only right\n")

    def test_split_lines(self) -> None:
        self.assertEqual(split_lines("a\r\nb\n"), [("a", "\r\n"), ("b", "\n")])
        self.assertEqual(split_lines("a"), [("a", "")])
        self.assertEqual(split_lines(""), [])

    def test_merge_block_both_directions(self) -> None:
        left, right = "a\nb\nc\n", "a\nB\nc\nd\n"
        result = compute_text_diff(left, right)
        merged = merge_block(result, 1, MergeDirection.LEFT_TO_RIGHT)
        self.assertEqual(merged.right_text, "a\nb\nc\nd\n")
        self.assertEqual(merged.diff_rows, [3])
        merged2 = merge_block(result, 1, MergeDirection.RIGHT_TO_LEFT)
        self.assertEqual(merged2.left_text, "a\nB\nc\n")

    def test_merge_all(self) -> None:
        result = compute_text_diff("a\nb\n", "a\nB\nc\n")
        l2r = merge_all(result, MergeDirection.LEFT_TO_RIGHT)
        self.assertTrue(l2r.identical)
        self.assertEqual(l2r.right_text, "a\nb\n")
        r2l = merge_all(result, MergeDirection.RIGHT_TO_LEFT)
        self.assertTrue(r2l.identical)
        self.assertEqual(r2l.left_text, "a\nB\nc\n")


class TestCompare(Fixture):
    def test_folder_states(self) -> None:
        self.make_sample()
        result = compare(str(self.left), str(self.right))
        self.assertIs(result.mode, CompareMode.FOLDERS)
        nodes = {n.rel: n for n in result.root.walk() if n.rel}
        self.assertIs(nodes["same.txt"].status, DiffStatus.SAME)
        self.assertIs(nodes["mod.txt"].status, DiffStatus.MODIFIED)
        self.assertIs(nodes["only_left.txt"].status, DiffStatus.LEFT_ONLY)
        self.assertIs(nodes["only_right.txt"].status, DiffStatus.RIGHT_ONLY)
        self.assertIs(nodes["sub"].status, DiffStatus.FOLDER_DIFF)
        self.assertIs(nodes["sub/inner.txt"].status, DiffStatus.MODIFIED)
        self.assertEqual(result.stats["differences"], 5)
        self.assertEqual(result.stats["same"], 1)

    def test_identical_folders(self) -> None:
        (self.left / "a.txt").write_text("x\n", encoding="utf-8")
        (self.right / "a.txt").write_text("x\n", encoding="utf-8")
        result = compare(str(self.left), str(self.right))
        self.assertEqual(result.stats["differences"], 0)

    def test_file_mode(self) -> None:
        a, b = self.left / "a.txt", self.right / "b.txt"
        a.write_text("x\n", encoding="utf-8")
        b.write_text("y\n", encoding="utf-8")
        result = compare(str(a), str(b))
        self.assertIs(result.mode, CompareMode.FILES)
        self.assertEqual(len(result.root.children), 1)
        self.assertIs(result.root.children[0].status, DiffStatus.MODIFIED)

    def test_type_mismatch_and_missing(self) -> None:
        a = self.left / "a.txt"
        a.write_text("x", encoding="utf-8")
        with self.assertRaises(PathTypeError):
            compare(str(a), str(self.right))
        with self.assertRaises(PathNotFoundError):
            compare(str(self.root / "missing"), str(self.right))
        with self.assertRaises(InvalidPathError):
            validate_path("")

    def test_excludes(self) -> None:
        (self.left / "cache.tmp").write_text("a", encoding="utf-8")
        (self.right / "cache.tmp").write_text("b", encoding="utf-8")
        result = compare(str(self.left), str(self.right), CompareOptions(excludes=("*.tmp",)))
        self.assertEqual(result.stats["differences"], 0)

    def test_content_rule_applies_to_files(self) -> None:
        (self.left / "a.txt").write_text("ABC\n", encoding="utf-8")
        (self.right / "a.txt").write_text("abc\n", encoding="utf-8")
        normal = compare(str(self.left), str(self.right))
        self.assertEqual(normal.stats["differences"], 1)
        ignored = compare(str(self.left), str(self.right), CompareOptions(ignore_case=True))
        self.assertEqual(ignored.stats["differences"], 0)

    def test_without_content_compare_uses_timestamp(self) -> None:
        a, b = self.left / "a.txt", self.right / "a.txt"
        a.write_text("x", encoding="utf-8")
        b.write_text("y", encoding="utf-8")
        options = CompareOptions(compare_content=False, timestamp_tolerance=0)
        os.utime(a, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
        os.utime(b, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        result = compare(str(self.left), str(self.right), options)
        self.assertEqual(result.stats["differences"], 1)
        os.utime(b, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
        result2 = compare(str(self.left), str(self.right), options)
        self.assertEqual(result2.stats["differences"], 0)

    def test_single_side_directory_expanded(self) -> None:
        (self.left / "newdir").mkdir()
        (self.left / "newdir" / "f.txt").write_text("x", encoding="utf-8")
        result = compare(str(self.left), str(self.right))
        nodes = {n.rel: n for n in result.root.walk() if n.rel}
        self.assertIs(nodes["newdir"].status, DiffStatus.LEFT_ONLY)
        self.assertIs(nodes["newdir/f.txt"].status, DiffStatus.LEFT_ONLY)

    def test_type_conflict(self) -> None:
        (self.left / "item").write_text("x", encoding="utf-8")
        (self.right / "item").mkdir()
        result = compare(str(self.left), str(self.right))
        nodes = {n.rel: n for n in result.root.walk() if n.rel}
        self.assertIs(nodes["item"].status, DiffStatus.TYPE_CONFLICT)


class TestSync(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.make_sample()
        self.result = compare(str(self.left), str(self.right))
        self._patcher = mock.patch(
            "app.core.sync.remove_path",
            side_effect=lambda p: shutil.rmtree(p) if p.is_dir() else p.unlink(),
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        super().tearDown()

    def test_plan_l2r(self) -> None:
        plan = plan_sync(self.result, [self.result.root], MergeDirection.LEFT_TO_RIGHT)
        copies = {a.rel for a in plan.actions if a.kind == "copy"}
        removes = {a.rel for a in plan.actions if a.kind == "remove"}
        self.assertIn("mod.txt", copies)
        self.assertIn("only_left.txt", copies)
        self.assertIn("sub/inner.txt", copies)
        self.assertIn("only_right.txt", removes)
        for action in plan.actions:
            self.assertTrue(is_within(action.dst, self.right), action.describe())

    def test_plan_r2l(self) -> None:
        plan = plan_sync(self.result, [self.result.root], MergeDirection.RIGHT_TO_LEFT)
        copies = {a.rel for a in plan.actions if a.kind == "copy"}
        removes = {a.rel for a in plan.actions if a.kind == "remove"}
        self.assertIn("only_right.txt", copies)
        self.assertIn("only_left.txt", removes)
        self.assertIn("mod.txt", copies)

    def test_execute_makes_folders_identical(self) -> None:
        plan = plan_sync(self.result, [self.result.root], MergeDirection.LEFT_TO_RIGHT)
        report = execute_sync(plan)
        self.assertTrue(report.ok, report.failed)
        after = compare(str(self.left), str(self.right))
        self.assertEqual(after.stats["differences"], 0, after.stats)

    def test_selected_node_only(self) -> None:
        node = next(n for n in self.result.root.walk() if n.rel == "mod.txt")
        plan = plan_sync(self.result, [node], MergeDirection.LEFT_TO_RIGHT)
        self.assertEqual([a.rel for a in plan.actions], ["mod.txt"])
        execute_sync(plan)
        after = compare(str(self.left), str(self.right))
        self.assertEqual(after.stats["differences"], 4)

    def test_no_actions_for_same(self) -> None:
        node = next(n for n in self.result.root.walk() if n.rel == "same.txt")
        self.assertTrue(plan_sync(self.result, [node], MergeDirection.LEFT_TO_RIGHT).empty)

    def test_type_conflict_plan_has_remove_and_copy(self) -> None:
        (self.left / "clash").write_text("x", encoding="utf-8")
        (self.right / "clash").mkdir()
        (self.right / "clash" / "child.txt").write_text("y", encoding="utf-8")
        result = compare(str(self.left), str(self.right))
        node = next(n for n in result.root.walk() if n.rel == "clash")
        plan = plan_sync(result, [node], MergeDirection.LEFT_TO_RIGHT)
        self.assertIn("remove", [a.kind for a in plan.actions])
        self.assertIn("copy", [a.kind for a in plan.actions])
        self.assertEqual(plan.actions[0].kind, "remove", "类型冲突必须先删除目标")
        report = execute_sync(plan)
        self.assertTrue(report.ok, report.failed)
        after = compare(str(self.left), str(self.right))
        nodes = {n.rel: n for n in after.root.walk() if n.rel}
        self.assertIs(nodes["clash"].status, DiffStatus.SAME)


class TestSession(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self._dir = self.root / "sessions"
        self._dir.mkdir()
        self._patcher = mock.patch.object(session_mod, "session_dir", return_value=self._dir)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        super().tearDown()

    def test_roundtrip(self) -> None:
        session = Session(
            name="测试会话",
            mode="folders",
            left=str(self.left),
            right=str(self.right),
            options=CompareOptions(ignore_case=True, excludes=("*.tmp",)),
            filters={"folder_text": "abc", "folder_status": "differences"},
            current_diff=3,
        )
        path = save_session(session)
        loaded = load_session(path)
        self.assertEqual(loaded.name, "测试会话")
        self.assertEqual(loaded.left, str(self.left))
        self.assertTrue(loaded.options.ignore_case)
        self.assertEqual(loaded.options.excludes, ("*.tmp",))
        self.assertEqual(loaded.filters["folder_status"], "differences")
        self.assertEqual(loaded.current_diff, 3)
        self.assertEqual(validate_session(loaded), [])

    def test_last_session_and_list(self) -> None:
        save_last_session(Session(name="last", left=str(self.left), right=str(self.right)))
        save_session(Session(name="named", left=str(self.left), right=str(self.right)))
        self.assertEqual(len(list_sessions()), 1)  # _last 不计入列表

    def test_invalid_session_file(self) -> None:
        bad = self._dir / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SessionError):
            load_session(bad)
        wrong = self._dir / "wrong.json"
        wrong.write_text(json.dumps({"app": "DiffScope", "version": 99}), encoding="utf-8")
        with self.assertRaises(SessionError):
            load_session(wrong)

    def test_validate_detects_missing_path(self) -> None:
        session = Session(left=str(self.root / "gone"), right=str(self.right))
        problems = validate_session(session)
        self.assertTrue(any("路径" in p for p in problems))


class TestErrors(Fixture):
    def test_validate_path_types(self) -> None:
        path = self.left / "a.txt"
        path.write_text("x", encoding="utf-8")
        self.assertEqual(validate_path(str(path), PathKind.FILE), Path(os.path.abspath(path)))
        with self.assertRaises(PathTypeError):
            validate_path(str(self.left), PathKind.FILE)

    def test_map_os_error(self) -> None:
        mapped = map_os_error("X:/a", PermissionError(13, "denied"), "读取")
        self.assertIsInstance(mapped, AccessDeniedError)
        self.assertIn("无访问权限", mapped.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
