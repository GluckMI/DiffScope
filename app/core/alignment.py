"""文件名对齐覆盖：不同扩展名、通配符掩码、Unicode 规范化。

返回 {左侧条目名: 右侧条目名} 的映射，由 compare 引擎在建立对齐键时使用。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .vfs import normalize_name


@dataclass
class AlignmentRule:
    left_pattern: str = ""    # 如 "*.gif" 或 "Apples*"
    right_pattern: str = ""   # 如 "*.png" 或 "Bananas*"
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"left": self.left_pattern, "right": self.right_pattern, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict) -> "AlignmentRule":
        return cls(str(data.get("left", "")), str(data.get("right", "")), bool(data.get("enabled", True)))


@dataclass
class AlignmentConfig:
    rules: list[AlignmentRule] = field(default_factory=list)
    unicode_normalize: bool = True
    ignore_case: bool = False

    def to_dict(self) -> dict:
        return {"rules": [r.to_dict() for r in self.rules],
                "unicode_normalize": self.unicode_normalize, "ignore_case": self.ignore_case}

    @classmethod
    def from_dict(cls, data: dict | None) -> "AlignmentConfig":
        data = data or {}
        return cls(
            rules=[AlignmentRule.from_dict(item) for item in data.get("rules", [])],
            unicode_normalize=bool(data.get("unicode_normalize", True)),
            ignore_case=bool(data.get("ignore_case", False)),
        )

    def key(self, name: str) -> str:
        value = normalize_name(name, self.unicode_normalize)
        return value.casefold() if self.ignore_case else value

    def build_map(self, left_names: list[str], right_names: list[str]) -> dict[str, str]:
        """生成 左侧名 -> 右侧名 的覆盖映射（只返回原本无法直接对齐的配对）。"""
        result: dict[str, str] = {}
        right_keys = {self.key(name): name for name in right_names}
        for rule in self.rules:
            if not rule.enabled or not rule.left_pattern or not rule.right_pattern:
                continue
            left_candidates = [n for n in left_names if fnmatch.fnmatchcase(self.key(n), self.key(rule.left_pattern))]
            right_candidates = [n for n in right_names if fnmatch.fnmatchcase(self.key(n), self.key(rule.right_pattern))]
            prefix_left = _static_prefix(rule.left_pattern)
            prefix_right = _static_prefix(rule.right_pattern)
            for left in left_candidates:
                stem = _stem(left, prefix_left, rule.left_pattern)
                if not stem:
                    continue
                for right in right_candidates:
                    if _stem(right, prefix_right, rule.right_pattern) == stem:
                        if self.key(left) not in right_keys and self.key(right) not in {self.key(x) for x in result.values()}:
                            result[left] = right
                        break
        return result

    def remap(self, name: str, side: str, pair_map: dict[str, str]) -> str:
        """把实际文件名映射为对齐键：右侧别名统一改用左侧名。"""
        if side == "right":
            for left, right in pair_map.items():
                if self.key(name) == self.key(right):
                    return self.key(left)
        return self.key(name)


def _static_prefix(pattern: str) -> str:
    return re.split(r"[*?\[]", pattern, maxsplit=1)[0]


def _stem(name: str, prefix: str, pattern: str) -> str:
    """去掉通配符前缀与扩展名，得到可比的主体部分。"""
    if prefix and not name.startswith(prefix):
        return ""
    base = name[len(prefix):] if prefix else name
    suffix = PurePosixPath(pattern).suffix
    if suffix and "*" not in suffix and base.lower().endswith(suffix.lower()):
        base = base[: -len(suffix)]
    return PurePosixPath(base).stem.rstrip(".")
