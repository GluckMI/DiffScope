"""文本内容合并：按差异块或整文件单向覆盖，合并后重新计算差异。"""

from __future__ import annotations

from enum import Enum

from .models import TextDiffResult, compose_side
from .text_diff import block_at, compute_text_diff, split_lines


class MergeDirection(Enum):
    LEFT_TO_RIGHT = "L2R"
    RIGHT_TO_LEFT = "R2L"

    @property
    def label(self) -> str:
        return "左侧 → 右侧" if self is MergeDirection.LEFT_TO_RIGHT else "右侧 → 左侧"

    @property
    def source(self) -> str:
        return "left" if self is MergeDirection.LEFT_TO_RIGHT else "right"

    @property
    def target(self) -> str:
        return "right" if self is MergeDirection.LEFT_TO_RIGHT else "left"


def rebuild(result: TextDiffResult, left_text: str, right_text: str) -> TextDiffResult:
    """用新的两侧文本重新计算差异，保留编码与换行风格信息。"""
    return compute_text_diff(
        left_text,
        right_text,
        result.options,
        result.left_encoding,
        result.right_encoding,
        result.rules,
        getattr(result, "alignments", None),
    )


def _row_has(row, side: str) -> bool:
    return (row.left_text if side == "left" else row.right_text) is not None


def _row_pair(row, side: str) -> tuple[str, str]:
    if side == "left":
        return row.left_text or "", row.left_eol
    return row.right_text or "", row.right_eol


def merge_all(result: TextDiffResult, direction: MergeDirection) -> TextDiffResult:
    """整文件单向覆盖：目标侧内容被源侧完全替换。"""
    left = compose_side(result, "left")
    right = compose_side(result, "right")
    source = compose_side(result, direction.source)
    if direction is MergeDirection.LEFT_TO_RIGHT:
        right = source
    else:
        left = source
    return rebuild(result, left, right)


def merge_block(result: TextDiffResult, row_index: int, direction: MergeDirection) -> TextDiffResult:
    """把指定差异块内源侧的行覆盖到目标侧；块外内容保持不变。"""
    block = block_at(result, row_index)
    if not block:
        return result
    source_side, target_side = direction.source, direction.target
    rows = result.rows

    start = sum(1 for row in rows[: block[0]] if _row_has(row, target_side))
    target_row_count = sum(1 for idx in block if _row_has(rows[idx], target_side))
    source_lines = [_row_pair(rows[idx], source_side) for idx in block if _row_has(rows[idx], source_side)]

    target_lines = split_lines(compose_side(result, target_side))
    target_lines[start : start + target_row_count] = source_lines
    new_target = "".join(text + eol for text, eol in target_lines)

    left = compose_side(result, "left")
    right = compose_side(result, "right")
    if target_side == "left":
        left = new_target
    else:
        right = new_target
    return rebuild(result, left, right)

