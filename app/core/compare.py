"""文件 / 文件夹比较引擎（纯 Python，可在后台线程运行）。"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import time
from pathlib import Path, PurePosixPath
from typing import Callable

from .errors import (
    AccessDeniedError,
    Cancelled,
    DiffScopeError,
    PathTypeError,
    ReadFailure,
    validate_path,
)
from .fs_utils import MAX_TEXT_BYTES, decode_bytes, format_size, hash_file, read_text
from .vfs import LocalVfs, VfsProvider
from .models import (
    DIFF_STATUSES,
    CompareMode,
    CompareNode,
    CompareOptions,
    CompareResult,
    DiffStatus,
    FileInfo,
)
from .text_diff import normalize_text

MAX_NODES = 100_000
ProgressCB = Callable[[int, int, str], None]
CancelCB = Callable[[], bool]


class _Context:
    def __init__(
        self,
        left_root: Path,
        right_root: Path,
        options: CompareOptions,
        progress: ProgressCB | None,
        cancel: CancelCB | None,
        alignment=None,
        rules=None,
    ):
        self.left_root = left_root
        self.right_root = right_root
        self.left_provider: VfsProvider = LocalVfs(left_root)
        self.right_provider: VfsProvider = LocalVfs(right_root)
        self.options = options
        self.alignment = alignment
        self.rules = rules
        self.progress = progress
        self.cancel = cancel
        self.errors: list[str] = []
        self.count = 0
        self.nodes = 0
        self.truncated = False

    def tick(self, rel: str) -> None:
        self.count += 1
        self.nodes += 1
        if self.cancel and self.count % 32 == 0 and self.cancel():
            raise Cancelled()
        if self.progress and self.count % 16 == 0:
            self.progress(self.count, 0, rel)

    def fail(self, rel: str, exc: Exception) -> None:
        message = exc.message if isinstance(exc, DiffScopeError) else str(exc)
        self.errors.append(f"{rel or '.'}: {message}")

    def note_limit(self) -> None:
        if not self.truncated:
            self.truncated = True
            self.errors.append(
                f"目录规模超过上限（{MAX_NODES} 个条目），部分目录未展开。"
                "请缩小范围或使用排除规则后重试。"
            )


def _excluded(rel: str, name: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    low_rel, low_name = rel.lower(), name.lower()
    for pattern in patterns:
        p = pattern.strip()
        if not p:
            continue
        low = p.lower().replace("\\", "/")
        if fnmatch.fnmatchcase(low_name, low) or fnmatch.fnmatchcase(low_rel, low):
            return True
        if "/" in low and PurePosixPath(low_rel).match(low):
            return True
    return False


def _stat(entry: os.DirEntry, rel: str) -> FileInfo:
    try:
        is_dir = entry.is_dir(follow_symlinks=False)
        st = entry.stat(follow_symlinks=False)
        return FileInfo(
            rel=rel,
            path=Path(entry.path),
            exists=True,
            is_dir=is_dir,
            is_symlink=entry.is_symlink(),
            size=0 if is_dir else st.st_size,
            mtime_ns=st.st_mtime_ns,
        )
    except OSError as exc:
        return FileInfo(rel=rel, path=Path(entry.path), exists=True, error=str(exc))


def _scan(provider: VfsProvider, rel: str, ctx: _Context) -> dict[str, FileInfo]:
    if ctx.nodes >= MAX_NODES:
        ctx.note_limit()
        return {}
    result: dict[str, FileInfo] = {}
    try:
        for entry in provider.list_dir(rel):
            child_rel = entry.rel
            if _excluded(child_rel, entry.name, ctx.options.excludes):
                continue
            key = ctx.alignment.key(entry.name) if ctx.alignment else (
                entry.name.casefold() if ctx.options.ignore_case else entry.name
            )
            local_path = Path(provider.display_path(child_rel)) if isinstance(provider, LocalVfs) else None
            result[key] = FileInfo(
                rel=child_rel, path=local_path, exists=True,
                is_dir=entry.is_dir, is_symlink=entry.is_symlink,
                size=0 if entry.is_dir else entry.size, mtime_ns=entry.mtime_ns,
                error=str(entry.attrs.get("error", "")), vfs=provider,
            )
    except DiffScopeError as exc:
        raise AccessDeniedError(
            "无法列出目录内容，可能是权限不足、目录被占用或路径已失效。",
            exc.detail or exc.message,
            provider.display_path(rel),
        ) from exc
    return result


def _content_signature(info: FileInfo, options: CompareOptions, rules=None) -> str:
    """按比较规则生成内容指纹；文本规则启用时先规范化再哈希。"""
    if (options.ignore_case or options.ignore_whitespace or rules) and info.size <= MAX_TEXT_BYTES:
        try:
            data = info.vfs.read_bytes(info.rel) if info.vfs is not None else info.path.read_bytes()
            text, _ = decode_bytes(data, info.rel)
            if "\x00" not in text:
                return hashlib.sha256(normalize_text(text, options, rules).encode("utf-8")).hexdigest()
        except (OSError, DiffScopeError):
            pass  # 二进制或无法解码：退回原始内容指纹
    if info.vfs is not None:
        data = info.vfs.read_bytes(info.rel) if info.size <= options.max_hash_bytes else b""
        return hashlib.sha256(str(info.size).encode() + data).hexdigest()
    return hash_file(info.path, limit=options.max_hash_bytes)


def _compare_files(node: CompareNode, left: FileInfo, right: FileInfo, ctx: _Context) -> None:
    if left.error or right.error:
        node.status = DiffStatus.ERROR
        node.note = f"元数据读取失败：{left.error or right.error}"
        ctx.fail(node.rel, ReadFailure(node.note, path=left.path or right.path))
        return
    if left.is_symlink or right.is_symlink:
        node.status = DiffStatus.SAME if left.size == right.size else DiffStatus.MODIFIED
        node.note = "符号链接（按元数据比较，未展开）"
        return
    if left.size != right.size and (not ctx.options.compare_content or ctx.rules is None):
        # 启用忽略规则时，大小不同不代表规范化内容不同，仍需比较签名
        node.status = DiffStatus.MODIFIED
        node.note = f"大小不同：{format_size(left.size)} / {format_size(right.size)}"
        return
    if ctx.options.compare_content:
        try:
            if _content_signature(left, ctx.options, ctx.rules) != _content_signature(right, ctx.options, ctx.rules):
                node.status = DiffStatus.MODIFIED
                node.note = "内容不同"
            else:
                node.status = DiffStatus.SAME
        except DiffScopeError as exc:
            node.status = DiffStatus.ERROR
            node.note = exc.message
            ctx.fail(node.rel, exc)
    else:
        delta = abs(left.mtime_ns - right.mtime_ns) / 1e9
        if delta > ctx.options.timestamp_tolerance:
            node.status = DiffStatus.MODIFIED
            node.note = "修改时间不同（未比较内容）"
        else:
            node.status = DiffStatus.SAME


def _build(rel: str, left: FileInfo | None, right: FileInfo | None, ctx: _Context) -> CompareNode:
    ctx.tick(rel)
    name = rel.rsplit("/", 1)[-1] if rel else ""
    node = CompareNode(rel=rel, name=name, left=left, right=right)
    only_left = right is None or not right.exists
    only_right = left is None or not left.exists

    if only_left:
        node.status = DiffStatus.LEFT_ONLY
        node.is_dir = bool(left and left.is_dir and not left.is_symlink)
        node.note = "仅左侧存在"
    elif only_right:
        node.status = DiffStatus.RIGHT_ONLY
        node.is_dir = bool(right and right.is_dir and not right.is_symlink)
        node.note = "仅右侧存在"
    elif left is not None and right is not None and left.is_dir != right.is_dir:
        node.status = DiffStatus.TYPE_CONFLICT
        node.note = "一侧为文件，另一侧为文件夹"
    elif left is not None and right is not None and left.is_dir and right.is_dir:
        if (left.is_symlink or right.is_symlink) and not ctx.options.follow_symlinks:
            node.status = DiffStatus.SAME
            node.is_dir = True
            node.note = "符号链接目录（未展开）"
            return node
        node.is_dir = True
    else:
        assert left is not None and right is not None
        _compare_files(node, left, right, ctx)
        return node

    if not node.is_dir:
        return node

    try:
        left_map = _scan(ctx.left_provider, left.rel if left else rel, ctx) if left and not only_right else {}
        right_map = _scan(ctx.right_provider, right.rel if right else rel, ctx) if right and not only_left else {}
    except DiffScopeError as exc:
        node.status = DiffStatus.ERROR
        node.note = exc.message
        ctx.fail(rel, exc)
        return node

    if ctx.alignment is not None and left_map and right_map:
        right_map = _apply_alignment(left_map, right_map, ctx.alignment)

    keys = sorted(set(left_map) | set(right_map), key=lambda k: (not _is_dir_key(k, left_map, right_map), k))
    for key in keys:
        li, ri = left_map.get(key), right_map.get(key)
        info = li or ri
        assert info is not None
        child = _build(info.rel, li, ri, ctx)
        child.left_rel = li.rel if li else ""
        child.right_rel = ri.rel if ri else ""
        node.add(child)

    if node.status not in (DiffStatus.ERROR, DiffStatus.TYPE_CONFLICT):
        has_diff = any(child.is_difference() or child.has_diff_descendant() for child in node.children)
        if node.status in (DiffStatus.LEFT_ONLY, DiffStatus.RIGHT_ONLY):
            pass  # 单侧目录保持原状态，语义更明确
        elif has_diff:
            node.status = DiffStatus.FOLDER_DIFF
            node.note = "目录内存在差异"
        else:
            node.status = DiffStatus.SAME
    return node


def _apply_alignment(left_map: dict, right_map: dict, alignment) -> dict:
    """把右侧按对齐规则改名到左侧键上，使不同扩展名/前缀的文件正确配对。"""
    left_names = [info.rel.rsplit("/", 1)[-1] for info in left_map.values()]
    right_names = [info.rel.rsplit("/", 1)[-1] for info in right_map.values()]
    pairs = alignment.build_map(left_names, right_names)
    if not pairs:
        return right_map
    lookup = {alignment.key(right): alignment.key(left) for left, right in pairs.items()}
    rebuilt: dict = {}
    for key, info in right_map.items():
        name = info.rel.rsplit("/", 1)[-1]
        target = lookup.get(alignment.key(name))
        new_key = target if target and target not in rebuilt else key
        rebuilt[new_key] = info
    return rebuilt


def _is_dir_key(key: str, left: dict[str, FileInfo], right: dict[str, FileInfo]) -> bool:
    info = left.get(key) or right.get(key)
    return bool(info and info.is_dir)


def _collect_stats(root: CompareNode) -> dict[str, int]:
    stats = {status.value: 0 for status in DiffStatus}
    stats["files"] = 0
    stats["folders"] = 0
    for node in root.walk():
        if not node.rel:
            continue
        stats[node.status.value] += 1
        stats["folders" if node.is_dir else "files"] += 1
    stats["differences"] = sum(stats[s.value] for s in DIFF_STATUSES)
    return stats


def compare(
    left: str | os.PathLike,
    right: str | os.PathLike,
    options: CompareOptions | None = None,
    progress: ProgressCB | None = None,
    cancel: CancelCB | None = None,
    left_vfs: VfsProvider | None = None,
    right_vfs: VfsProvider | None = None,
    alignment=None,
    rules=None,
) -> CompareResult:
    """比较两侧路径；同为文件时生成单节点树，同为目录时递归比较。

    传入 VfsProvider 时可比较压缩包 / FTP / 云存储 / 注册表等数据源，
    不传则走本地路径（原有行为完全不变）。
    """
    started = time.perf_counter()
    options = options or CompareOptions()

    if left_vfs is None:
        left_root = validate_path(left)
    else:
        left_vfs.connect()
        left_root = Path(str(left))
    if right_vfs is None:
        right_root = validate_path(right)
    else:
        right_vfs.connect()
        right_root = Path(str(right))

    if left_vfs is None and right_vfs is None and left_root.is_dir() != right_root.is_dir():
        raise PathTypeError(
            "左右两侧类型不一致：一侧是文件，另一侧是文件夹。"
            "请重新选择同为文件或同为文件夹的路径。"
        )

    mode = CompareMode.FOLDERS if (left_vfs or right_vfs or left_root.is_dir()) else CompareMode.FILES
    ctx = _Context(left_root, right_root, options, progress, cancel, alignment, rules)
    if left_vfs is not None:
        ctx.left_provider = left_vfs
    if right_vfs is not None:
        ctx.right_provider = right_vfs

    if mode is CompareMode.FILES:
        root = CompareNode(rel="", name="", is_dir=True)
        root.add(_build(left_root.name, _make_file_info(left_root, left_root.name),
                        _make_file_info(right_root, right_root.name), ctx))
    else:
        root = _build("", _make_dir_info(left_root), _make_dir_info(right_root), ctx)

    if progress:
        progress(ctx.count, ctx.count, "完成")
    return CompareResult(
        mode=mode,
        left_root=left_root,
        right_root=right_root,
        root=root,
        options=options,
        stats=_collect_stats(root),
        errors=ctx.errors,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        truncated=ctx.truncated,
        left_vfs=left_vfs,
        right_vfs=right_vfs,
        left_label=left_vfs.label if left_vfs else str(left_root),
        right_label=right_vfs.label if right_vfs else str(right_root),
    )


def _make_file_info(path: Path, rel: str) -> FileInfo:
    try:
        st = path.stat()
        return FileInfo(rel=rel, path=path, exists=True, size=st.st_size, mtime_ns=st.st_mtime_ns)
    except OSError as exc:
        return FileInfo(rel=rel, path=path, exists=True, error=str(exc))


def _make_dir_info(path: Path) -> FileInfo:
    return FileInfo(rel="", path=path, exists=True, is_dir=True)
