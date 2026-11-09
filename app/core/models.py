"""公共数据模型（Qt 无关）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator


class PathKind(Enum):
    ANY = "any"
    FILE = "file"
    DIR = "dir"


class CompareMode(Enum):
    FILES = "files"
    FOLDERS = "folders"


class DiffStatus(Enum):
    SAME = "same"
    MODIFIED = "modified"
    LEFT_ONLY = "left_only"
    RIGHT_ONLY = "right_only"
    TYPE_CONFLICT = "type_conflict"
    FOLDER_DIFF = "folder_diff"
    ERROR = "error"


STATUS_META: dict[DiffStatus, tuple[str, str, str]] = {
    # 状态: (中文名, 短标记, 颜色)
    DiffStatus.SAME: ("相同", "=", "#7d8492"),
    DiffStatus.MODIFIED: ("已修改", "≠", "#e6a445"),
    DiffStatus.LEFT_ONLY: ("仅左侧", "<", "#5aa9e6"),
    DiffStatus.RIGHT_ONLY: ("仅右侧", ">", "#b18bea"),
    DiffStatus.TYPE_CONFLICT: ("类型冲突", "!", "#e8705b"),
    DiffStatus.FOLDER_DIFF: ("含差异", "◆", "#d9a34a"),
    DiffStatus.ERROR: ("读取失败", "×", "#e65757"),
}

DIFF_STATUSES = frozenset(
    {
        DiffStatus.MODIFIED,
        DiffStatus.LEFT_ONLY,
        DiffStatus.RIGHT_ONLY,
        DiffStatus.TYPE_CONFLICT,
        DiffStatus.FOLDER_DIFF,
        DiffStatus.ERROR,
    }
)


@dataclass
class FileInfo:
    """单侧文件/目录的元数据。"""

    rel: str
    path: Path | None = None
    exists: bool = False
    is_dir: bool = False
    is_symlink: bool = False
    size: int = 0
    mtime_ns: int = 0
    digest: str = ""
    error: str = ""
    vfs: object | None = None  # 非本地数据源时由 VfsProvider 负责读取


@dataclass
class CompareOptions:
    compare_content: bool = True
    ignore_case: bool = False
    ignore_whitespace: bool = False
    timestamp_tolerance: float = 2.0
    excludes: tuple[str, ...] = ()
    follow_symlinks: bool = False
    max_hash_bytes: int = 512 * 1024 * 1024
    max_nodes: int = 100_000

    def to_dict(self) -> dict:
        return {
            "compare_content": self.compare_content,
            "ignore_case": self.ignore_case,
            "ignore_whitespace": self.ignore_whitespace,
            "timestamp_tolerance": self.timestamp_tolerance,
            "excludes": list(self.excludes),
            "follow_symlinks": self.follow_symlinks,
            "max_nodes": self.max_nodes,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "CompareOptions":
        data = data or {}
        excludes = data.get("excludes") or []
        return cls(
            compare_content=bool(data.get("compare_content", True)),
            ignore_case=bool(data.get("ignore_case", False)),
            ignore_whitespace=bool(data.get("ignore_whitespace", False)),
            timestamp_tolerance=float(data.get("timestamp_tolerance", 2.0)),
            excludes=tuple(str(x) for x in excludes),
            follow_symlinks=bool(data.get("follow_symlinks", False)),
            max_nodes=int(data.get("max_nodes", 100_000)),
        )


@dataclass(eq=False)
class CompareNode:
    rel: str
    name: str
    is_dir: bool = False
    left: FileInfo | None = None
    right: FileInfo | None = None
    status: DiffStatus = DiffStatus.SAME
    note: str = ""
    left_rel: str = ""   # 对齐覆盖时左侧实际相对路径
    right_rel: str = ""  # 对齐覆盖时右侧实际相对路径
    children: list["CompareNode"] = field(default_factory=list)
    parent: "CompareNode | None" = field(repr=False, default=None)

    def add(self, child: "CompareNode") -> None:
        child.parent = self
        self.children.append(child)

    def walk(self) -> Iterator["CompareNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def is_difference(self) -> bool:
        return self.status in DIFF_STATUSES

    def has_diff_descendant(self) -> bool:
        return any(node.is_difference() for node in self.children) or any(
            child.has_diff_descendant() for child in self.children
        )

    def leaf_differences(self) -> Iterator["CompareNode"]:
        """返回可实际执行同步的叶子节点（目录本身不产生复制动作）。"""
        for node in self.walk():
            if node.rel == "":
                continue
            if node.status is DiffStatus.FOLDER_DIFF:
                continue
            if node.is_difference():
                yield node


@dataclass
class CompareResult:
    mode: CompareMode
    left_root: Path
    right_root: Path
    root: CompareNode
    options: CompareOptions
    stats: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    truncated: bool = False
    left_vfs: object | None = None
    right_vfs: object | None = None
    left_label: str = ""
    right_label: str = ""

    def differences(self) -> list[CompareNode]:
        return [n for n in self.root.walk() if n.rel and n.is_difference()]


@dataclass
class DiffLine:
    """一行对齐后的差异行（左右行号一一对应）。"""

    index: int
    op: str  # equal | replace | delete | insert
    left_no: int | None = None
    right_no: int | None = None
    left_text: str | None = None
    right_text: str | None = None
    left_eol: str = ""
    right_eol: str = ""
    left_spans: list[tuple[int, int]] = field(default_factory=list)
    right_spans: list[tuple[int, int]] = field(default_factory=list)
    cosmetic: bool = False  # 按忽略规则视为相同，但原文有细微差别
    manual: bool = False    # 手动对齐产生的行

    @property
    def is_diff(self) -> bool:
        return self.op != "equal"


@dataclass
class TextDiffResult:
    rows: list[DiffLine] = field(default_factory=list)
    diff_rows: list[int] = field(default_factory=list)
    left_raw: str = ""
    right_raw: str = ""
    left_encoding: str = "utf-8"
    right_encoding: str = "utf-8"
    left_newline: str = "\n"
    right_newline: str = "\n"
    options: CompareOptions = field(default_factory=CompareOptions)
    rules: object | None = None
    alignments: object | None = None  # TextAlignments，rebuild 时保留手动对齐

    @property
    def identical(self) -> bool:
        return not self.diff_rows

    @property
    def left_text(self) -> str:
        return compose_side(self, "left")

    @property
    def right_text(self) -> str:
        return compose_side(self, "right")


def compose_side(result: TextDiffResult, side: str) -> str:
    """把对齐行还原成该侧的完整文本（占位行跳过，保留原有换行风格）。"""
    parts: list[str] = []
    for row in result.rows:
        text = row.left_text if side == "left" else row.right_text
        eol = row.left_eol if side == "left" else row.right_eol
        if text is None:
            continue
        parts.append(text + eol)
    return "".join(parts)


@dataclass
class SyncAction:
    kind: str  # mkdir | copy | remove
    rel: str
    src: Path | None
    dst: Path | None
    is_dir: bool = False
    size: int = 0
    note: str = ""
    direction: str = ""  # L2R | R2L
    priority: int = 0  # 负值优先执行，用于类型冲突时先删除目标

    def describe(self) -> str:
        symbols = {
            "mkdir": "创建目录", "copy": "复制", "remove": "删除", "move": "移动/重命名",
            "touch": "设置时间", "attrs": "设置属性", "acl": "复制 ACL",
        }
        head = symbols.get(self.kind, self.kind)
        if self.kind == "copy":
            return f"{head}：{self.src}  →  {self.dst}"
        return f"{head}：{self.dst or self.src}"


@dataclass
class SyncReport:
    succeeded: list[SyncAction] = field(default_factory=list)
    failed: list[tuple[SyncAction, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled
