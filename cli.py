"""DiffScope 命令行入口：比较、脚本、三向合并、快照、报告。

退出码：0 无差异 / 1 发现差异 / 2 执行错误 / 3 合并冲突。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import __version__
from app.core.errors import DiffScopeError
from app.core.fs_utils import read_text, write_text_atomic
from app.core.merge3 import merge3_text
from app.core.models import CompareOptions
from app.core.report import save_report
from app.core.scripting import EXIT_DIFFERENCES, EXIT_ERROR, ScriptRunner
from app.core.vfs import create_snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="DiffScope", description="文件与文件夹对比工具（命令行）")
    parser.add_argument("--version", action="version", version=f"DiffScope {__version__}")
    parser.add_argument("--diff", nargs=2, metavar=("LEFT", "RIGHT"), help="比较并输出结果（不启动界面）")
    parser.add_argument("--script", metavar="FILE", help="执行批处理脚本")
    parser.add_argument("--allow-write", action="store_true", help="允许脚本执行同步等写操作")
    parser.add_argument("--merge", nargs=4, metavar=("BASE", "LEFT", "RIGHT", "OUTPUT"),
                        help="三向文本合并，输出到 OUTPUT")
    parser.add_argument("--favor", choices=("left", "right", "base"), default="", help="合并冲突时优先选择")
    parser.add_argument("--snapshot", nargs=2, metavar=("DIR", "FILE"), help="创建文件系统快照")
    parser.add_argument("--report", metavar="FILE", help="把 --diff 结果写入报告（html/csv/txt）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出 --diff 结果")
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--ignore-whitespace", action="store_true")
    parser.add_argument("--timestamp-only", action="store_true", help="只按时间戳与大小比较")
    parser.add_argument("--exclude", action="append", default=[], help="排除规则，可重复")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _run_diff(args) -> int:
    from app.core.alignment import AlignmentConfig
    from app.core.compare import compare

    options = CompareOptions(
        compare_content=not args.timestamp_only,
        ignore_case=args.ignore_case,
        ignore_whitespace=args.ignore_whitespace,
        excludes=tuple(args.exclude),
    )
    # 与 GUI 一致：默认启用文件名 Unicode 规范化（macOS NFD ↔ Windows NFC 对齐）
    result = compare(args.diff[0], args.diff[1], options, alignment=AlignmentConfig())
    if args.json:
        print(json.dumps({"left": str(result.left_root), "right": str(result.right_root),
                          "stats": result.stats, "errors": result.errors,
                          "elapsed_ms": result.elapsed_ms}, ensure_ascii=False, indent=2))
    elif not args.quiet:
        print(f"左侧：{result.left_root}\n右侧：{result.right_root}")
        print(f"差异 {result.stats.get('differences', 0)} 项 / 文件 {result.stats.get('files', 0)} 个 / "
              f"耗时 {result.elapsed_ms} ms")
        for node in result.root.walk():
            if node.rel and node.is_difference():
                print(f"  [{node.status.value:<13}] {node.rel}  {node.note}")
        for item in result.errors:
            print(f"  [读取问题] {item}", file=sys.stderr)
    if args.report:
        path = save_report(result, Path(args.report), Path(args.report).suffix.lstrip(".") or "html")
        if not args.quiet:
            print(f"报告：{path}")
    return EXIT_DIFFERENCES if result.stats.get("differences", 0) else 0


def _run_merge(args) -> int:
    base, left, right, output = args.merge
    texts = [read_text(Path(item)).text for item in (base, left, right)]
    result = merge3_text(*texts, favor=args.favor)
    write_text_atomic(Path(output), result.output_text, "utf-8", "\n")
    conflicts = len(result.conflicts)
    print(f"合并完成：自动合并 {result.auto_merged} 处，冲突 {conflicts} 处 → {output}")
    return 3 if conflicts else 0


def _run_script(args) -> int:
    path = Path(args.script)
    runner = ScriptRunner(path.parent, allow_write=args.allow_write)
    result = runner.run(read_text(path).text)
    if not args.quiet:
        print(result.text())
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.snapshot:
            path = create_snapshot(Path(args.snapshot[0]), Path(args.snapshot[1]))
            print(f"快照：{path}")
            return 0
        if args.merge:
            return _run_merge(args)
        if args.script:
            return _run_script(args)
        if args.diff:
            return _run_diff(args)
    except DiffScopeError as exc:
        print(f"[{exc.title}] {exc.user_message()}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:
        print(f"[执行失败] {exc}", file=sys.stderr)
        return EXIT_ERROR
    _parser().print_help()
    return EXIT_ERROR if len(sys.argv) > 1 else 0


if __name__ == "__main__":
    sys.exit(main())
