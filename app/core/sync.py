"""目录同步：先生成可审阅的操作计划，确认后再逐项执行。"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import DiffScopeError, WriteFailure
from .fs_utils import copy_path, is_within, remove_path, safe_join
from .merge import MergeDirection
from .models import CompareNode, CompareResult, DiffStatus, SyncAction, SyncReport

ProgressCB = Callable[[int, int, str], None]
CancelCB = Callable[[], bool]


@dataclass
class SyncPlan:
    actions: list[SyncAction] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.actions


def _collect(node: CompareNode) -> list[CompareNode]:
    """把选中的目录节点展开为需要实际操作的节点集合。"""
    if node.status is DiffStatus.SAME:
        return []
    if node.status is DiffStatus.ERROR:
        return []
    if node.status is DiffStatus.FOLDER_DIFF:
        out: list[CompareNode] = []
        for child in node.children:
            out.extend(_collect(child))
        return out
    return [node]


def _prune(nodes: list[CompareNode]) -> list[CompareNode]:
    """若父目录整体被处理，则去掉其后代，避免重复或冲突动作。"""
    selected = sorted(nodes, key=lambda n: (n.rel.count("/"), n.rel))
    kept: list[CompareNode] = []
    for node in selected:
        if any(node.rel.startswith(parent.rel + "/") for parent in kept):
            continue
        kept.append(node)
    return kept


def plan_sync(
    result: CompareResult,
    nodes: list[CompareNode],
    direction: MergeDirection,
) -> SyncPlan:
    """根据选中节点生成同步计划，不触碰磁盘。"""
    plan = SyncPlan()
    left_root, right_root = result.left_root, result.right_root
    source_side = direction.source
    tag = direction.value

    expanded: list[CompareNode] = []
    for node in nodes:
        if not node.rel:
            expanded.extend(node.leaf_differences())
        else:
            expanded.extend(_collect(node))
    expanded = _prune(expanded)

    seen: set[str] = set()
    for node in expanded:
        if node.rel in seen:
            continue
        seen.add(node.rel)
        if node.status is DiffStatus.ERROR:
            plan.skipped.append(f"{node.rel}：读取失败，未纳入同步")
            continue
        if node.status is DiffStatus.SAME or node.status is DiffStatus.FOLDER_DIFF:
            continue

        left = node.left
        right = node.right
        src_info = left if source_side == "left" else right
        dst_info = right if source_side == "left" else left
        src_root = left_root if source_side == "left" else right_root
        dst_root = right_root if source_side == "left" else left_root
        src_exists = bool(src_info and src_info.exists and src_info.path)
        dst_exists = bool(dst_info and dst_info.exists and dst_info.path)

        try:
            src = safe_join(src_root, node.rel) if src_exists else None
            dst = safe_join(dst_root, node.rel)
        except WriteFailure as exc:
            plan.skipped.append(f"{node.rel}：{exc.message}")
            continue

        if src is not None and (node.status is DiffStatus.TYPE_CONFLICT or dst_exists and dst.is_dir() != src.is_dir()):
            plan.actions.append(
                SyncAction("remove", node.rel, None, dst, is_dir=dst.is_dir(),
                           note="类型冲突，先移除目标", direction=tag, priority=-1)
            )
            dst_exists = False

        if src is not None:
            if src.is_symlink():
                plan.skipped.append(f"{node.rel}：源为符号链接，为安全起见未同步")
                continue
            parent = dst.parent
            if not parent.exists() and not any(a.kind == "mkdir" and a.dst == parent for a in plan.actions):
                plan.actions.append(
                    SyncAction("mkdir", os.path.relpath(parent, dst_root).replace("\\", "/"), None, parent,
                               is_dir=True, note="创建缺失的父目录", direction=tag)
                )
            plan.actions.append(
                SyncAction("copy", node.rel, src, dst, is_dir=src.is_dir(),
                           size=0 if src.is_dir() else (src_info.size if src_info else 0),
                           note=node.note, direction=tag)
            )
        elif dst_exists:
            if dst.is_symlink():
                plan.skipped.append(f"{node.rel}：目标为符号链接，为安全起见未删除")
                continue
            plan.actions.append(
                SyncAction("remove", node.rel, None, dst, is_dir=dst.is_dir() or node.is_dir,
                           note=f"源侧不存在（{node.status.value}）", direction=tag)
            )
        else:
            plan.skipped.append(f"{node.rel}：两侧均无有效路径，已跳过")

    # 校验所有目标路径都在对应根目录内
    safe_actions: list[SyncAction] = []
    for action in plan.actions:
        target_root = right_root if direction is MergeDirection.LEFT_TO_RIGHT else left_root
        if action.dst is not None and not is_within(action.dst, target_root):
            plan.skipped.append(f"{action.rel}：目标路径越界，已拒绝")
            continue
        safe_actions.append(action)

    order = {"mkdir": 0, "copy": 1, "remove": 2}
    safe_actions.sort(
        key=lambda a: (
            a.priority,
            order[a.kind],
            a.rel.count("/") if a.kind != "remove" else -a.rel.count("/"),
            a.rel,
        )
    )
    plan.actions = safe_actions
    return plan


def _apply_attrs(path: Path, note: str) -> None:
    """设置 DOS/Unix 属性；note 中形如 {'mode': 0o644, 'dos_attributes': 3}。"""
    import ast
    import re

    match = re.search(r"\{.*\}", note)
    attrs = ast.literal_eval(match.group(0)) if match else {}
    if "mode" in attrs:
        os.chmod(path, int(attrs["mode"]))
    if "dos_attributes" in attrs and os.name == "nt":
        value = int(attrs["dos_attributes"])
        for bit, flag in ((1, "R"), (2, "H"), (4, "S")):
            subprocess.run(["attrib", ("+" if value & bit else "-") + flag, str(path)],
                           capture_output=True, check=False)


def _copy_acl(src: Path, dst: Path) -> None:
    """复制 NTFS ACL（Windows icacls）；非 Windows 平台复制权限位。"""
    if os.name != "nt":
        os.chmod(dst, src.stat().st_mode)
        return
    result = subprocess.run(["icacls", str(src), "/save", str(dst) + ".acl"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise WriteFailure("读取源 ACL 失败。", result.stderr.strip(), src)
    result = subprocess.run(["icacls", str(dst.parent), "/restore", str(dst) + ".acl"],
                            capture_output=True, text=True)
    try:
        Path(str(dst) + ".acl").unlink()
    except OSError:
        pass
    if result.returncode != 0:
        raise WriteFailure("应用 ACL 失败。", result.stderr.strip(), dst)


def execute_sync(
    plan: SyncPlan,
    progress: ProgressCB | None = None,
    cancel: CancelCB | None = None,
) -> SyncReport:
    """执行已确认的计划。删除优先进入回收站，失败时移动到备份目录。"""
    report = SyncReport()
    total = len(plan.actions)
    for index, action in enumerate(plan.actions, start=1):
        if cancel and cancel():
            report.cancelled = True
            break
        try:
            if action.kind == "mkdir":
                assert action.dst is not None
                action.dst.mkdir(parents=True, exist_ok=True)
            elif action.kind == "copy":
                assert action.src is not None and action.dst is not None
                if action.dst.exists() and action.dst.is_dir() and action.src.is_dir():
                    remove_path(action.dst)
                copy_path(action.src, action.dst)
            elif action.kind == "remove":
                assert action.dst is not None
                if action.dst.exists():
                    remove_path(action.dst)
            elif action.kind == "move":
                assert action.src is not None and action.dst is not None
                action.dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(action.src), str(action.dst))
            elif action.kind == "touch":
                assert action.dst is not None
                os.utime(action.dst, ns=(action.size, action.size))
            elif action.kind == "attrs":
                assert action.dst is not None
                _apply_attrs(action.dst, action.note)
            elif action.kind == "acl":
                assert action.src is not None and action.dst is not None
                _copy_acl(action.src, action.dst)
            # move / touch / attrs / acl 等动作不走 fs_utils 写原语，这里统一失效缓存
            from .compare import invalidate_signatures

            invalidate_signatures((action.dst, action.src))
            report.succeeded.append(action)
        except DiffScopeError as exc:
            report.failed.append((action, exc.user_message()))
        except OSError as exc:
            report.failed.append((action, f"系统错误：{exc.strerror or exc}"))
        if progress:
            progress(index, total, action.rel)
    return report
