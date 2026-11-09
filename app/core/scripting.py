"""批处理脚本处理器：解析并执行命令，可由命令行或界面调用。"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .alignment import AlignmentConfig
from .compare import compare
from .errors import DiffScopeError
from .models import CompareOptions, CompareResult
from .report import csv_report, html_folder_report, save_linked_reports, text_report
from .rules import CompareRules, IgnoreRule
from .services import TaskController, run_task
from .sync_modes import SyncMode, plan_mode
from .sync import execute_sync
from .merge import MergeDirection
from .vfs import create_snapshot

EXIT_OK = 0
EXIT_DIFFERENCES = 1
EXIT_ERROR = 2


@dataclass
class ScriptResult:
    log: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    differences: int = 0
    exit_code: int = EXIT_OK
    result: CompareResult | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self) -> str:
        return "\n".join(self.log + [f"错误：{item}" for item in self.errors])


def parse_script(text: str) -> list[list[str]]:
    commands = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = [part.strip('"').strip("'") for part in shlex.split(line, posix=False)]
        except ValueError:
            tokens = line.split()
        if tokens:
            commands.append(tokens)
    return commands


class ScriptRunner:
    """脚本执行器；默认只读，写操作需要 allow_write=True。"""

    def __init__(self, base_dir: Path | None = None, allow_write: bool = False):
        self.base_dir = Path(base_dir or Path.cwd())
        self.allow_write = allow_write
        self.left = ""
        self.right = ""
        self.options = CompareOptions()
        self.rules = CompareRules()
        self.alignment = AlignmentConfig()
        self.result: CompareResult | None = None
        self.selected: list = []
        self.log: list[str] = []
        self.errors: list[str] = []

    def _path(self, value: str) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else self.base_dir / path)

    def run(self, text: str) -> ScriptResult:
        result = ScriptResult()
        for tokens in parse_script(text):
            command, args = tokens[0].lower(), tokens[1:]
            try:
                self._execute(command, args)
            except DiffScopeError as exc:
                self.errors.append(f"{' '.join(tokens)} → {exc.message}")
                result.exit_code = EXIT_ERROR
            except Exception as exc:
                self.errors.append(f"{' '.join(tokens)} → {exc}")
                result.exit_code = EXIT_ERROR
        result.log = list(self.log)
        result.errors = list(self.errors)
        result.result = self.result
        result.differences = (self.result.stats.get("differences", 0) if self.result else 0)
        if not self.errors and result.differences and result.exit_code == EXIT_OK:
            result.exit_code = EXIT_DIFFERENCES
        return result

    def _execute(self, command: str, args: list[str]) -> None:
        if command == "log":
            message = " ".join(args)
            self.log.append(message)
        elif command == "load":
            if len(args) < 2:
                raise ValueError("load 需要两个路径")
            self.left, self.right = self._path(args[0]), self._path(args[1])
            self.log.append(f"载入：{self.left} ↔ {self.right}")
        elif command in ("criteria", "option"):
            self._option(args)
        elif command == "filter":
            excludes = tuple(self.options.excludes) + tuple(args)
            self.options.excludes = excludes
            self.log.append(f"排除规则：{', '.join(args)}")
        elif command == "compare":
            self.result = compare(self.left, self.right, self.options,
                                  alignment=self.alignment, rules=self.rules)
            self.log.append(f"比较完成：{self.result.stats.get('differences', 0)} 处差异")
        elif command == "expand":
            self.log.append("展开节点（命令行忽略）")
        elif command == "select":
            self._select(args)
        elif command in ("folder-report", "text-report", "file-report", "report"):
            self._report(command, args)
        elif command == "sync":
            self._sync(args)
        elif command == "snapshot":
            if len(args) < 2:
                raise ValueError("snapshot 需要 <目录> <输出文件>")
            output = create_snapshot(self._path(args[0]), self._path(args[1]),
                                     self.options.excludes)
            self.log.append(f"快照已保存：{output}")
        elif command in ("pause", "wait"):
            self.log.append("脚本继续执行")
        else:
            raise ValueError(f"未知脚本命令：{command}")

    def _option(self, args: list[str]) -> None:
        for item in args:
            key, _, value = item.partition(":")
            key = key.lower().strip()
            value = value.strip()
            if key == "confirm":
                self.allow_write = value.lower() in ("on", "yes", "true")
            elif key == "ignore-case":
                self.options.ignore_case = value.lower() in ("on", "yes", "true")
            elif key == "ignore-whitespace":
                self.options.ignore_whitespace = value.lower() in ("on", "yes", "true")
            elif key == "compare-content":
                self.options.compare_content = value.lower() not in ("off", "no", "false", "size", "timestamp")
            elif key == "ignore-comments" and value:
                self.rules.rules.append(IgnoreRule("comments", value))
            elif key == "ignore-regex" and value:
                self.rules.rules.append(IgnoreRule("regex", value))
            elif key == "ignore-header" and value.isdigit():
                self.rules.rules.append(IgnoreRule("header", value=int(value)))
            elif key == "align":
                from .alignment import AlignmentRule

                parts = value.split("=>")
                if len(parts) == 2:
                    self.alignment.rules.append(AlignmentRule(parts[0].strip(), parts[1].strip()))
            elif key == "tolerance" and value:
                self.options.timestamp_tolerance = float(value)
            else:
                self.log.append(f"忽略未知选项：{item}")

    def _select(self, args: list[str]) -> None:
        if not self.result:
            raise ValueError("select 前需要先 compare")
        selector = args[0].lower() if args else "all.diff"
        nodes = [n for n in self.result.root.walk() if n.rel]
        if selector.startswith("all.diff"):
            self.selected = [n for n in nodes if n.is_difference()]
        elif selector.startswith("left."):
            self.selected = [n for n in nodes if n.status.value in ("left_only", "modified")]
        elif selector.startswith("right."):
            self.selected = [n for n in nodes if n.status.value in ("right_only", "modified")]
        else:
            self.selected = [self.result.root]
        self.log.append(f"已选择 {len(self.selected)} 项")

    def _report(self, command: str, args: list[str]) -> None:
        if not self.result:
            raise ValueError("生成报告前需要先 compare")
        target = next((a.partition(":")[2] for a in args if a.lower().startswith("output-to:")),
                      str(self.base_dir / "report.html"))
        path = Path(self._path(target))
        path.parent.mkdir(parents=True, exist_ok=True)
        fmt = path.suffix.lower().lstrip(".") or "html"
        if fmt in ("html", "htm"):
            content = html_folder_report(self.result, f"{command} 报告")
        elif fmt == "csv":
            content = csv_report(self.result)
        else:
            content = text_report(self.result)
        from .fs_utils import write_text_atomic

        write_text_atomic(path, content, "utf-8", "\n")
        if command == "folder-report":
            save_linked_reports(self.result, path.parent / (path.stem + "_files"))
        self.log.append(f"报告已生成：{path}")

    def _sync(self, args: list[str]) -> None:
        if not self.result:
            raise ValueError("sync 前需要先 compare")
        if not self.allow_write:
            raise DiffScopeError("脚本写操作被禁用；请使用 option confirm:on 或 --allow-write 显式允许。")
        mode = SyncMode(args[0].lower()) if args and args[0].lower() in {m.value for m in SyncMode} else SyncMode.MIRROR
        direction = MergeDirection.RIGHT_TO_LEFT if any("right-to-left" in a for a in args) else MergeDirection.LEFT_TO_RIGHT
        plan = plan_mode(self.result, mode, direction, self.selected or None)
        controller = TaskController()
        report = execute_sync(plan, cancel=lambda: controller.cancelled)
        self.log.append(f"同步完成：成功 {len(report.succeeded)} 项，失败 {len(report.failed)} 项")
        for action, reason in report.failed:
            self.errors.append(f"同步失败 {action.rel}: {reason}")
