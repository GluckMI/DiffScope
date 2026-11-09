"""同步模式：双向 / 镜像 / 更新，以及移动、重命名、属性、时间戳等扩展操作。"""

from __future__ import annotations

from enum import Enum

from .merge import MergeDirection
from .models import CompareNode, CompareResult, SyncAction
from .sync import SyncPlan, plan_sync


class SyncMode(Enum):
    CUSTOM = "custom"      # 按选中项单向覆盖（原行为）
    MIRROR = "mirror"      # 目标严格等于源，删除目标独有
    UPDATE = "update"      # 只复制新增/较新，不删除
    TWO_WAY = "two_way"    # 双向：两侧新增互补，冲突按较新者

    @property
    def label(self) -> str:
        return {
            SyncMode.CUSTOM: "按选中项单向覆盖",
            SyncMode.MIRROR: "镜像（目标 = 源）",
            SyncMode.UPDATE: "更新（不删除）",
            SyncMode.TWO_WAY: "双向同步",
        }[self]


def plan_mode(
    result: CompareResult,
    mode: SyncMode,
    direction: MergeDirection,
    nodes: list[CompareNode] | None = None,
) -> SyncPlan:
    """按同步模式生成操作计划；所有计划都需经 UI 确认后执行。"""
    selected = nodes or [result.root]
    if mode is SyncMode.MIRROR or mode is SyncMode.CUSTOM:
        return plan_sync(result, selected, direction)
    if mode is SyncMode.UPDATE:
        plan = plan_sync(result, selected, direction)
        plan.actions = [a for a in plan.actions if a.kind != "remove"]
        plan.skipped.append("更新模式：已跳过全部删除操作")
        return plan
    # 双向：逐节点按时间戳决定方向，单侧条目一律复制到另一侧
    plan = SyncPlan()
    seen: set[str] = set()
    expanded: list[CompareNode] = []
    for node in selected:
        expanded.extend(node.leaf_differences() if not node.rel else _collect(node))
    for node in expanded:
        if node.rel in seen:
            continue
        seen.add(node.rel)
        left, right = node.left, node.right
        if not left or not right or not left.exists or not right.exists:
            # 单侧存在：复制到缺失侧
            src_direction = MergeDirection.LEFT_TO_RIGHT if left and left.exists else MergeDirection.RIGHT_TO_LEFT
            part = plan_sync(result, [node], src_direction)
            plan.actions.extend(a for a in part.actions if a.kind != "remove")
            continue
        if node.status.value == "same":
            continue
        newer = MergeDirection.LEFT_TO_RIGHT if left.mtime_ns >= right.mtime_ns else MergeDirection.RIGHT_TO_LEFT
        part = plan_sync(result, [node], newer)
        plan.actions.extend(a for a in part.actions if a.kind != "remove")
    plan.actions = _deduplicate(plan.actions)
    return plan


def _collect(node: CompareNode) -> list[CompareNode]:
    from .sync import _collect as collect

    return collect(node)


def _deduplicate(actions: list[SyncAction]) -> list[SyncAction]:
    seen: set[tuple] = set()
    out = []
    for action in actions:
        key = (action.kind, action.rel, str(action.dst))
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    order = {"mkdir": 0, "copy": 1, "remove": 2, "move": 3, "touch": 4, "attrs": 5, "acl": 6}
    out.sort(key=lambda a: (a.priority, order.get(a.kind, 9), a.rel.count("/"), a.rel))
    return out


def move_action(result: CompareResult, rel: str, new_rel: str, side: str = "right") -> SyncAction:
    """构造重命名/移动操作。"""
    from .fs_utils import safe_join

    root = result.left_root if side == "left" else result.right_root
    return SyncAction("move", rel, safe_join(root, rel), safe_join(root, new_rel),
                      note=f"重命名/移动 → {new_rel}", direction=side)


def touch_action(result: CompareResult, rel: str, mtime_ns: int, side: str = "right") -> SyncAction:
    from .fs_utils import safe_join

    root = result.left_root if side == "left" else result.right_root
    return SyncAction("touch", rel, None, safe_join(root, rel),
                      note=f"设置修改时间 {mtime_ns}", direction=side, size=mtime_ns)


def attrs_action(result: CompareResult, rel: str, attrs: dict, side: str = "right") -> SyncAction:
    from .fs_utils import safe_join

    root = result.left_root if side == "left" else result.right_root
    return SyncAction("attrs", rel, None, safe_join(root, rel),
                      note=f"设置属性 {attrs}", direction=side)


def acl_action(result: CompareResult, rel: str, side: str = "right") -> SyncAction:
    """复制 NTFS ACL 与 DOS 属性：源取另一侧同名文件。"""
    from .fs_utils import safe_join

    src_root = result.right_root if side == "left" else result.left_root
    dst_root = result.left_root if side == "left" else result.right_root
    return SyncAction("acl", rel, safe_join(src_root, rel), safe_join(dst_root, rel),
                      note="复制 NTFS ACL 与 DOS 属性", direction=side)
