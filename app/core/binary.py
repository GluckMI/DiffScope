"""十六进制比较与内联编辑、Windows 注册表 / .reg 文件比较。"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .errors import ReadFailure
from .vfs import RegistryVfs


class BinaryError(ReadFailure):
    title = "二进制数据解析失败"


# ---------------------------------------------------------------- 十六进制
@dataclass
class HexRow:
    offset: int
    left: bytes = b""
    right: bytes = b""
    changed: set[int] = field(default_factory=set)

    @property
    def is_diff(self) -> bool:
        return bool(self.changed)


@dataclass
class HexDiffResult:
    rows: list[HexRow] = field(default_factory=list)
    left_size: int = 0
    right_size: int = 0
    bytes_per_line: int = 16
    align: str = "quick"

    @property
    def diff_offsets(self) -> list[int]:
        return [row.offset + index for row in self.rows for index in row.changed]

    @property
    def identical(self) -> bool:
        return not self.diff_offsets and self.left_size == self.right_size


def compare_hex(
    left: bytes,
    right: bytes,
    bytes_per_line: int = 16,
    align: str = "quick",
    max_exact: int = 1024 * 1024,
) -> HexDiffResult:
    """quick 按偏移逐字节比较；exact 用序列对齐寻找插入/删除后的对应位置。"""
    width = max(8, min(bytes_per_line, 64))
    if align == "exact" and max(len(left), len(right)) <= max_exact:
        matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
        a_new, b_new = bytearray(), bytearray()
        for _tag, i1, i2, j1, j2 in matcher.get_opcodes():
            size = max(i2 - i1, j2 - j1)
            a_new.extend(left[i1:i2] + b"\x00" * (size - (i2 - i1)))
            b_new.extend(right[j1:j2] + b"\x00" * (size - (j2 - j1)))
        left, right = bytes(a_new), bytes(b_new)

    total = max(len(left), len(right))
    rows = []
    for offset in range(0, total, width):
        la = left[offset:offset + width]
        rb = right[offset:offset + width]
        changed = set()
        for index in range(width):
            a = la[index] if index < len(la) else None
            b = rb[index] if index < len(rb) else None
            if a != b:
                changed.add(index)
        rows.append(HexRow(offset, la, rb, changed))
    return HexDiffResult(rows, len(left), len(right), width, align)


def format_hex_row(row: HexRow) -> tuple[str, str, str]:
    def hex_part(data: bytes) -> str:
        return " ".join(f"{byte:02X}" if index < len(data) else "  "
                        for index, byte in enumerate(data)) if data else " ".join(["  "] * 16)

    def ascii_part(data: bytes) -> str:
        return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data)

    return (
        f"{row.offset:08X}  {hex_part(row.left)}  |{ascii_part(row.left)}|",
        f"{row.offset:08X}  {hex_part(row.right)}  |{ascii_part(row.right)}|",
        "".join(f"{i:02X} " for i in sorted(row.changed)),
    )


def parse_hex(text: str) -> bytes:
    cleaned = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(cleaned) % 2:
        raise BinaryError("十六进制字符串长度必须为偶数。")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise BinaryError("十六进制字符串包含非法字符。", str(exc)) from exc


def patch_hex(data: bytes, offset: int, replacement: bytes) -> bytes:
    if offset < 0 or offset > len(data):
        raise BinaryError("写入偏移越界。")
    return data[:offset] + replacement + data[offset + len(replacement):]


def find_hex(data: bytes, pattern: bytes | str) -> list[int]:
    if isinstance(pattern, str):
        pattern = pattern.encode("latin-1", "replace")
    out, start = [], 0
    while start <= len(data) - len(pattern):
        index = data.find(pattern, start)
        if index < 0:
            break
        out.append(index)
        start = index + 1
    return out


def replace_hex(data: bytes, pattern: bytes, replacement: bytes) -> tuple[bytes, int]:
    offsets = find_hex(data, pattern)
    if not pattern:
        return data, 0
    return data.replace(pattern, replacement), len(offsets)


# ---------------------------------------------------------------- 注册表
def registry_snapshot(spec: str, max_depth: int = 8) -> dict[str, str]:
    """递归枚举注册表键与值，返回 {路径: 值文本}。"""
    provider = RegistryVfs(spec)
    provider.connect()
    out: dict[str, str] = {}

    def walk(rel: str, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = provider.list_dir(rel)
        except Exception:
            return
        for entry in entries:
            if entry.is_dir:
                walk(entry.rel, depth + 1)
            else:
                value = entry.attrs.get("value", "")
                out[entry.rel] = f"[{entry.attrs.get('kind', 1)}] {value}"

    walk("", 0)
    return out


def parse_reg_file(data: bytes) -> dict[str, str]:
    """解析 .reg 导出文件为 {路径: 值文本}。"""
    text = data.decode("utf-16", "replace") if data.startswith(b"\xff\xfe") else data.decode("utf-8-sig", "replace")
    out: dict[str, str] = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out[section] = "<键>"
        elif section and "=" in line and not line.startswith(";"):
            name, _, value = line.partition("=")
            out[f"{section}\\{name.strip().strip('\"')}"] = value.strip()
    return out


@dataclass
class RegistryDiff:
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)  # path, left, right, status

    @property
    def differences(self) -> int:
        return sum(1 for row in self.rows if row[3] != "same")


def compare_registry(left: dict[str, str], right: dict[str, str]) -> RegistryDiff:
    out = []
    for path in sorted(set(left) | set(right)):
        a, b = left.get(path), right.get(path)
        status = "same" if a == b else ("modified" if a is not None and b is not None
                                       else "left_only" if b is None else "right_only")
        out.append((path, a or "", b or "", status))
    return RegistryDiff(out)
