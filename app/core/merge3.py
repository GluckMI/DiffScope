"""三向文本 / 文件夹合并（diff3）：共同祖先 + 左 + 右 → 输出。"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .fs_utils import safe_join
from .models import CompareNode, DiffStatus, SyncAction
from .text_diff import split_lines

CONFLICT_LEFT = "<<<<<<< LEFT"
CONFLICT_BASE = "||||||| BASE"
CONFLICT_SEP = "======="
CONFLICT_RIGHT = ">>>>>>> RIGHT"


@dataclass
class Merge3Chunk:
    kind: str  # unchanged | left | right | both | conflict
    base: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    right: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    resolved: bool = True

    @property
    def is_conflict(self) -> bool:
        return self.kind == "conflict" and not self.resolved

    def conflict_lines(self) -> list[str]:
        return [CONFLICT_LEFT, *self.left, CONFLICT_BASE, *self.base,
                CONFLICT_SEP, *self.right, CONFLICT_RIGHT]


@dataclass
class Merge3Result:
    chunks: list[Merge3Chunk] = field(default_factory=list)
    newline: str = "\n"
    favor: str = ""

    @property
    def output_text(self) -> str:
        lines = [line for chunk in self.chunks for line in chunk.output]
        return "\n".join(lines) + ("\n" if lines else "")

    @property
    def conflicts(self) -> list[Merge3Chunk]:
        return [chunk for chunk in self.chunks if chunk.is_conflict]

    @property
    def auto_merged(self) -> int:
        return sum(1 for c in self.chunks if c.kind in ("left", "right", "both"))

    def resolve(self, index: int, choice: str = "left", custom: list[str] | None = None) -> None:
        """手动解决冲突：left / right / base / both / custom。"""
        chunk = self.chunks[index]
        if custom is not None:
            chunk.output = list(custom)
        elif choice == "left":
            chunk.output = list(chunk.left)
        elif choice == "right":
            chunk.output = list(chunk.right)
        elif choice == "base":
            chunk.output = list(chunk.base)
        elif choice == "both":
            chunk.output = list(chunk.left) + list(chunk.right)
        chunk.resolved = True

    def resolve_all(self, choice: str) -> None:
        for index, chunk in enumerate(self.chunks):
            if chunk.is_conflict:
                self.resolve(index, choice)


def _changes(base: list[str], other: list[str]) -> list[tuple[int, int, list[str]]]:
    matcher = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [
        (i1, i2, other[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def merge3_text(base: str, left: str, right: str, favor: str = "", newline: str | None = None) -> Merge3Result:
    """按共同祖先合并左右两个版本，冲突块保留标记并可手动解决。"""
    newline = newline or ("\r\n" if "\r\n" in base else "\n")
    base_lines = [text for text, _ in split_lines(base)]
    left_lines = [text for text, _ in split_lines(left)]
    right_lines = [text for text, _ in split_lines(right)]
    left_changes = _changes(base_lines, left_lines)
    right_changes = _changes(base_lines, right_lines)
    result = Merge3Result(newline=newline, favor=favor)

    position = 0
    length = len(base_lines)
    while position < length:
        touching = [c for c in left_changes + right_changes if c[0] <= position < c[1]]
        if not touching:
            end = min([c[0] for c in left_changes + right_changes if c[0] > position] or [length])
            lines = base_lines[position:end]
            result.chunks.append(Merge3Chunk("unchanged", lines, lines, lines, lines))
            position = end
            continue

        start = position
        end = max(c[1] for c in touching)
        # 区间重叠时继续扩张
        while True:
            overlaps = [c for c in left_changes + right_changes if c[0] < end and c[1] > start]
            new_end = max([end] + [c[1] for c in overlaps])
            new_start = min([start] + [c[0] for c in overlaps])
            if (new_start, new_end) == (start, end):
                break
            start, end = new_start, new_end

        left_hit = [(i1, i2, rep) for i1, i2, rep in left_changes if i1 < end and i2 > start]
        right_hit = [(i1, i2, rep) for i1, i2, rep in right_changes if i1 < end and i2 > start]
        if left_hit:
            end = max(end, max(c[1] for c in left_hit))
        if right_hit:
            end = max(end, max(c[1] for c in right_hit))
        base_chunk = base_lines[start:end]
        left_rep = _replacement(base_chunk, left_hit, start, end)
        right_rep = _replacement(base_chunk, right_hit, start, end)

        if not left_hit:
            chunk = Merge3Chunk("right", base_chunk, base_chunk, right_rep, right_rep)
        elif not right_hit:
            chunk = Merge3Chunk("left", base_chunk, left_rep, base_chunk, left_rep)
        elif left_rep == right_rep:
            chunk = Merge3Chunk("both", base_chunk, left_rep, right_rep, left_rep)
        else:
            chunk = Merge3Chunk("conflict", base_chunk, left_rep, right_rep, [], resolved=False)
            if favor == "left":
                chunk.output, chunk.resolved = list(left_rep), True
            elif favor == "right":
                chunk.output, chunk.resolved = list(right_rep), True
            elif favor == "base":
                chunk.output, chunk.resolved = list(base_chunk), True
            else:
                chunk.output = chunk.conflict_lines()
        result.chunks.append(chunk)
        position = end

    # 追加在文件末尾的插入
    tail_left = [rep for i1, i2, rep in left_changes if i1 >= length]
    tail_right = [rep for i1, i2, rep in right_changes if i1 >= length]
    if tail_left or tail_right:
        left_rep = [line for rep in tail_left for line in rep]
        right_rep = [line for rep in tail_right for line in rep]
        if not tail_right or left_rep == right_rep:
            result.chunks.append(Merge3Chunk("left", [], left_rep, right_rep, left_rep))
        elif not tail_left:
            result.chunks.append(Merge3Chunk("right", [], left_rep, right_rep, right_rep))
        else:
            chunk = Merge3Chunk("conflict", [], left_rep, right_rep, [], resolved=bool(favor))
            chunk.output = (left_rep if favor == "left" else right_rep if favor == "right"
                            else chunk.conflict_lines())
            result.chunks.append(chunk)
    return result


def _replacement(base_chunk: list[str], hits: list[tuple[int, int, list[str]]], start: int, end: int) -> list[str]:
    if not hits:
        return list(base_chunk)
    out: list[str] = []
    cursor = start
    for i1, i2, rep in sorted(hits):
        out.extend(base_chunk[max(cursor, start) - start:max(i1, start) - start])
        out.extend(rep)
        cursor = i2
    out.extend(base_chunk[max(cursor, start) - start:])
    return out


def merge3_folders(
    base_left_diff,
    base_right_diff,
    output_root,
    favor: str = "",
) -> tuple[list[SyncAction], list[str]]:
    """三向文件夹合并：基于「祖先→左」「祖先→右」两次比较生成输出操作。

    返回 (操作列表, 冲突路径)。操作带真实源/目标路径，确认后可用 execute_sync 执行。
    """
    def diff_map(diff_result):
        return {
            node.rel: node
            for node in diff_result.root.walk()
            if node.rel and node.is_difference()
        }

    nodes_a = diff_map(base_left_diff)   # 祖先 → 左
    nodes_b = diff_map(base_right_diff)  # 祖先 → 右
    unchanged = {
        node.rel: node for node in base_left_diff.root.walk()
        if node.rel and node.status is DiffStatus.SAME
    }
    actions: list[SyncAction] = []
    conflicts: list[str] = []
    out_root = Path(output_root)

    def _changed_info(node):
        # compare(祖先, X) 中：RIGHT_ONLY = X 新增，LEFT_ONLY = X 删除，MODIFIED = X 修改
        if node.status in (DiffStatus.MODIFIED, DiffStatus.RIGHT_ONLY, DiffStatus.TYPE_CONFLICT):
            return node.right
        return None  # LEFT_ONLY：该侧相对祖先删除了文件

    def _read(info):
        try:
            if info.vfs is not None:
                return info.vfs.read_bytes(info.rel)
            if info.path is not None:
                return info.path.read_bytes()
        except Exception:
            pass
        return b""

    def _signature(node):
        if node is None:
            return ("none",)
        info = _changed_info(node)
        if info is None:
            return (node.status.value, "deleted")
        digest = hashlib.sha256(_read(info)).hexdigest()[:16]
        return (node.status.value, info.size, digest)

    def _copy_action(diff_result, rel, note, from_base=False):
        src = safe_join(diff_result.left_root if from_base else diff_result.right_root, rel)
        dst = safe_join(out_root, rel)
        return SyncAction("copy", rel, src, dst, is_dir=src.is_dir(),
                          note=note, direction="merge")

    def _remove_action(rel, note):
        dst = safe_join(out_root, rel)
        return SyncAction("remove", rel, None, dst,
                          is_dir=dst.is_dir(), note=note, direction="merge")

    def _copy_or_remove(node, diff_result, rel, note):
        if node.status == DiffStatus.LEFT_ONLY:
            return _remove_action(rel, note + "（删除）")
        return _copy_action(diff_result, rel, note)

    for rel in sorted(set(nodes_a) | set(nodes_b) | set(unchanged)):
        if rel not in nodes_a and rel not in nodes_b:
            actions.append(_copy_action(base_left_diff, rel, "两侧与祖先一致，复制到输出"))
            continue
        node_a, node_b = nodes_a.get(rel), nodes_b.get(rel)
        if node_a and node_b:
            if _signature(node_a) == _signature(node_b):
                if _signature(node_a)[1] == "deleted":
                    actions.append(_remove_action(rel, "两侧一致删除，自动合并"))
                else:
                    actions.append(_copy_action(base_left_diff, rel, "两侧相同变更，自动合并"))
            else:
                conflicts.append(rel)
                if favor == "left":
                    actions.append(_copy_or_remove(node_a, base_left_diff, rel, "冲突，取左侧"))
                elif favor == "right":
                    actions.append(_copy_or_remove(node_b, base_right_diff, rel, "冲突，取右侧"))
                elif favor == "base":
                    if node_a.left and node_a.left.exists:
                        actions.append(_copy_action(base_left_diff, rel, "冲突，保持祖先内容", from_base=True))
                    else:
                        actions.append(_remove_action(rel, "冲突，祖先也无此条目"))
        elif node_a:
            actions.append(_copy_or_remove(node_a, base_left_diff, rel, "仅左侧变更，自动合并"))
        else:
            actions.append(_copy_or_remove(node_b, base_right_diff, rel, "仅右侧变更，自动合并"))

    order = {"mkdir": 0, "copy": 1, "remove": 2}
    actions.sort(key=lambda a: (order.get(a.kind, 9), a.rel.count("/"), a.rel))
    return actions, conflicts
