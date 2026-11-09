"""文本差异：逐行对齐 + 行内字符级高亮，基于 difflib。

支持 Beyond Compare 式的手动对齐（links）、断开对齐（breaks）与对齐样式。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Iterable

from .models import CompareOptions, DiffLine, TextDiffResult


@dataclass
class AlignmentPoint:
    left: int   # 1 基左侧行号
    right: int  # 1 基右侧行号


@dataclass
class TextAlignments:
    """手动对齐设置：links 强制左右两行对齐，breaks 在该处断开跨块匹配。"""

    links: list[AlignmentPoint] = field(default_factory=list)
    breaks: list[AlignmentPoint] = field(default_factory=list)
    style: str = "auto"  # auto | none（none = 禁止差异对齐）

    def to_dict(self) -> dict:
        return {
            "links": [[p.left, p.right] for p in self.links],
            "breaks": [[p.left, p.right] for p in self.breaks],
            "style": self.style,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "TextAlignments":
        data = data or {}
        return cls(
            links=[AlignmentPoint(int(p[0]), int(p[1])) for p in data.get("links", [])],
            breaks=[AlignmentPoint(int(p[0]), int(p[1])) for p in data.get("breaks", [])],
            style=str(data.get("style", "auto")),
        )

    def clear(self) -> None:
        self.links.clear()
        self.breaks.clear()
        self.style = "auto"


def normalize_line(text: str, options: CompareOptions) -> str:
    if options.ignore_whitespace:
        text = "".join(text.split())
    if options.ignore_case:
        text = text.casefold()
    return text


def normalize_text(text: str, options: CompareOptions, rules=None) -> str:
    lines = text.splitlines()
    if rules is not None:
        return "\n".join(rules.normalize(line, index + 1, options) for index, line in enumerate(lines))
    return "\n".join(normalize_line(line, options) for line in lines)


_EOL_RE = re.compile(r"\r\n|\r|\n")


def split_lines(text: str) -> list[tuple[str, str]]:
    """拆成 (不含换行的文本, 换行符)，可无损重建原文。正则实现走 C 层，百万字符也在 10ms 内。"""
    out: list[tuple[str, str]] = []
    start = 0
    for match in _EOL_RE.finditer(text):
        out.append((text[start:match.start()], match.group()))
        start = match.end()
    if start < len(text):
        out.append((text[start:], ""))
    return out


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _inline_spans(left: str, right: str, options: CompareOptions) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """返回两侧需要深一档高亮的字符区间。"""
    a = left.casefold() if options.ignore_case else left
    b = right.casefold() if options.ignore_case else right
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ls: list[tuple[int, int]] = []
    rs: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            ls.append((i1, i2))
        if j2 > j1:
            rs.append((j1, j2))
    # 合并相邻区间，避免零碎高亮
    return _merge_spans(ls), _merge_spans(rs)


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def compute_text_diff(
    left_text: str,
    right_text: str,
    options: CompareOptions | None = None,
    left_encoding: str = "utf-8",
    right_encoding: str = "utf-8",
    rules=None,
    alignments: "TextAlignments | None" = None,
) -> TextDiffResult:
    """生成左右对齐的差异行；两侧显示行数始终相同，便于同步滚动。

    rules 为 CompareRules 时，被忽略的内容不影响差异判定，但仍按原文显示。
    """
    options = options or CompareOptions()
    a = split_lines(left_text)
    b = split_lines(right_text)
    if rules is not None:
        a_keys = [rules.normalize(text, index + 1, options, "left") for index, (text, _) in enumerate(a)]
        b_keys = [rules.normalize(text, index + 1, options, "right") for index, (text, _) in enumerate(b)]
    else:
        a_keys = [normalize_line(text, options) for text, _ in a]
        b_keys = [normalize_line(text, options) for text, _ in b]

    result = TextDiffResult(
        left_raw=left_text,
        right_raw=right_text,
        left_encoding=left_encoding,
        right_encoding=right_encoding,
        left_newline=_detect_newline(left_text),
        right_newline=_detect_newline(right_text),
        options=options,
        rules=rules,
        alignments=alignments,
    )
    if alignments is not None and (alignments.links or alignments.breaks or alignments.style == "none"):
        rows = _build_with_anchors(a, b, a_keys, b_keys, options, rules, alignments)
    else:
        rows = _build_rows(a, b, a_keys, b_keys, options,
                           autojunk=len(a_keys) > 20000 or len(b_keys) > 20000)

    result.rows = rows
    result.diff_rows = [row.index for row in rows if row.is_diff]
    return result


def _build_rows(
    a: list[tuple[str, str]],
    b: list[tuple[str, str]],
    a_keys: list[str],
    b_keys: list[str],
    options: CompareOptions,
    rules=None,
    left_start: int = 0,
    right_start: int = 0,
    autojunk: bool = False,
    align_similar: bool = True,
) -> list[DiffLine]:
    """对一段行序列做差异；left_start/right_start 为该段在全文中的 0 基起始行。"""
    rows: list[DiffLine] = []
    left_no = left_start
    right_no = right_start

    def push(op, li, ri, cosmetic=False, manual=False):
        nonlocal left_no, right_no
        l_spans: list[tuple[int, int]] = []
        r_spans: list[tuple[int, int]] = []
        if op == "replace" and li is not None and ri is not None:
            l_spans, r_spans = _inline_spans(li[0], ri[0], options)
        if li is not None:
            left_no += 1
        if ri is not None:
            right_no += 1
        rows.append(DiffLine(
            index=len(rows), op=op,
            left_no=left_no if li is not None else None,
            right_no=right_no if ri is not None else None,
            left_text=li[0] if li else None,
            right_text=ri[0] if ri else None,
            left_eol=li[1] if li else "",
            right_eol=ri[1] if ri else "",
            left_spans=l_spans, right_spans=r_spans,
            cosmetic=cosmetic, manual=manual,
        ))

    if not align_similar:  # 禁止差异对齐：按索引逐行配对，不做相似行匹配
        for offset in range(max(len(a), len(b))):
            li = a[offset] if offset < len(a) else None
            ri = b[offset] if offset < len(b) else None
            same = (a_keys[offset] == b_keys[offset]) if (li and ri) else False
            if li and ri:
                push("equal" if same else "replace", li, ri,
                     cosmetic=(not same and (li[0] != ri[0] or li[1] != ri[1])))
            elif li:
                push("delete", li, None)
            else:
                push("insert", None, ri)
        return rows

    matcher = difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=autojunk)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                li, ri = a[i1 + offset], b[j1 + offset]
                push("equal", li, ri, cosmetic=(li[0] != ri[0] or li[1] != ri[1]))
        elif tag == "replace":
            for offset in range(max(i2 - i1, j2 - j1)):
                li = a[i1 + offset] if i1 + offset < i2 else None
                ri = b[j1 + offset] if j1 + offset < j2 else None
                push("replace" if li and ri else ("delete" if li else "insert"), li, ri)
        elif tag == "delete":
            for idx in range(i1, i2):
                push("delete", a[idx], None)
        else:
            for idx in range(j1, j2):
                push("insert", None, b[idx])
    return rows


def _manual_row(index, li, ri, left_no, right_no, options, rules=None) -> DiffLine:
    """手动对齐行：无论内容是否相同都强制排到同一显示行。"""
    left_key = rules.normalize(li[0], left_no, options, "left") if rules else normalize_line(li[0], options)
    right_key = rules.normalize(ri[0], right_no, options, "right") if rules else normalize_line(ri[0], options)
    same = left_key == right_key
    l_spans, r_spans = ([], []) if same else _inline_spans(li[0], ri[0], options)
    return DiffLine(
        index=index, op="equal" if same else "replace",
        left_no=left_no, right_no=right_no,
        left_text=li[0], right_text=ri[0],
        left_eol=li[1], right_eol=ri[1],
        left_spans=l_spans, right_spans=r_spans,
        manual=True,
    )


def _build_with_anchors(a, b, a_keys, b_keys, options, rules, alignments) -> list[DiffLine]:
    """按手动对齐链接与断开点切分段落后逐段比较。

    link：把左右指定行强制对齐为一行；break：在该位置禁止跨块匹配。
    """
    rows: list[DiffLine] = []
    pairs = sorted(
        [(p.left, p.right, "link") for p in alignments.links]
        + [(p.left, p.right, "break") for p in alignments.breaks],
        key=lambda item: (item[0], item[1]),
    )
    pos_l = pos_r = 0
    for left_line, right_line, kind in pairs:
        l_idx, r_idx = left_line - 1, right_line - 1
        if l_idx < pos_l or r_idx < pos_r:
            continue  # 与先前锚点重叠的无效锚点，忽略
        rows += _build_rows(a[pos_l:l_idx], b[pos_r:r_idx], a_keys[pos_l:l_idx],
                            b_keys[pos_r:r_idx], options, rules, pos_l, pos_r)
        if kind == "link":
            rows.append(_manual_row(len(rows), a[l_idx], b[r_idx], left_line, right_line, options, rules))
            pos_l, pos_r = l_idx + 1, r_idx + 1
        else:
            pos_l, pos_r = l_idx, r_idx
    rows += _build_rows(a[pos_l:], b[pos_r:], a_keys[pos_l:], b_keys[pos_r:],
                        options, rules, pos_l, pos_r)
    return rows


def diff_blocks(result: TextDiffResult) -> list[list[int]]:
    """把连续差异行合并为可操作的差异块。"""
    blocks: list[list[int]] = []
    current: list[int] = []
    for index in result.diff_rows:
        if current and index != current[-1] + 1:
            blocks.append(current)
            current = []
        current.append(index)
    if current:
        blocks.append(current)
    return blocks


def block_at(result: TextDiffResult, row_index: int) -> list[int] | None:
    for block in diff_blocks(result):
        if row_index in block:
            return block
    return None
