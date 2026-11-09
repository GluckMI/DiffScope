"""文本比较规则：忽略不重要差异、替换文本、注释/字符串/正则/列/页眉、手动忽略选择。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import CompareOptions

COMMENT_PRESETS: dict[str, tuple[str, ...]] = {
    "python": (r"#.*",),
    "c": (r"//.*", r"/\*.*?\*/"),
    "sql": (r"--.*",),
    "ini": (r"[;#].*",),
    "xml": (r"<!--.*?-->",),
    "shell": (r"#.*",),
}


@dataclass
class IgnoreRule:
    kind: str  # comments | strings | regex | columns | header | line_endings | replacement | unimportant | selection | delimiter
    pattern: str = ""
    enabled: bool = True
    value: object = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "pattern": self.pattern, "enabled": self.enabled, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict) -> "IgnoreRule":
        return cls(str(data.get("kind", "")), str(data.get("pattern", "")),
                   bool(data.get("enabled", True)), data.get("value"))


@dataclass
class CompareRules:
    """一组可序列化的比较规则，作用于文本比较与目录内容判定。"""

    rules: list[IgnoreRule] = field(default_factory=list)
    language: str = ""
    delimiter: str = ""  # 列分隔符，空表示空白

    def to_dict(self) -> dict:
        return {"language": self.language, "delimiter": self.delimiter,
                "rules": [rule.to_dict() for rule in self.rules]}

    @classmethod
    def from_dict(cls, data: dict | None) -> "CompareRules":
        data = data or {}
        return cls(
            rules=[IgnoreRule.from_dict(item) for item in data.get("rules", [])],
            language=str(data.get("language", "")),
            delimiter=str(data.get("delimiter", "")),
        )

    @classmethod
    def preset(cls, language: str) -> "CompareRules":
        rules = [IgnoreRule("comments", lang) for lang in COMMENT_PRESETS.get(language, ())]
        return cls(rules=rules, language=language)

    def _active(self, kind: str) -> list[IgnoreRule]:
        return [rule for rule in self.rules if rule.kind == kind and rule.enabled]

    # ---- 主入口 ----
    def normalize(self, text: str, line_no: int, options: CompareOptions, side: str = "left") -> str:
        """把一行文本规范化为用于比较的键；规则移除的内容视为不重要差异。"""
        value = text
        removed = False
        for rule in self._active("header"):
            if line_no <= int(rule.value or 0):
                return ""
        for rule in self._active("selection"):
            spans = rule.value or []
            if any(start <= line_no <= end for start, end in spans):
                return ""
        for rule in self._active("comments"):
            for pattern in COMMENT_PRESETS.get(rule.pattern or self.language, ()):
                value = re.sub(pattern, "", value)
                removed = True
        for rule in self._active("regex"):
            if rule.pattern:
                try:
                    value = re.sub(rule.pattern, "", value)
                    removed = True
                except re.error:
                    continue
        for rule in self._active("strings"):
            value = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', value)
            removed = True
        if removed and not options.ignore_whitespace:
            value = value.rstrip()  # 忽略片段常留下尾随空格，不应算作差异
        for rule in self._active("delimiter") + self._active("columns"):
            value = self._drop_columns(value, rule)
        for rule in self._active("replacement"):
            value = self._replace(value, rule, side)
        for rule in self._active("unimportant"):
            if rule.pattern:
                try:
                    if re.search(rule.pattern, value):
                        return ""
                except re.error:
                    continue
        for rule in self._active("line_endings"):
            value = value.rstrip("\r\n")
        if options.ignore_whitespace:
            value = "".join(value.split())
        if options.ignore_case:
            value = value.casefold()
        return value

    def _drop_columns(self, value: str, rule: IgnoreRule) -> str:
        columns = rule.value or []
        indices = {int(c) - 1 for c in columns if str(c).lstrip("-").isdigit()}
        if not indices:
            return value
        separator = self.delimiter or rule.pattern or "\t"
        parts = value.split(separator) if separator not in ("", "whitespace") else value.split()
        kept = [part for index, part in enumerate(parts) if index not in indices]
        joiner = separator if separator not in ("", "whitespace") else " "
        return joiner.join(kept)

    @staticmethod
    def _replace(value: str, rule: IgnoreRule, side: str) -> str:
        pair = rule.value or {}
        if isinstance(pair, dict):
            source = pair.get("left", "") if side == "left" else pair.get("right", "")
            if source:
                return value.replace(source, "\x00replaced\x00")
        elif isinstance(pair, str) and side == "left" and pair:
            return value.replace(pair, "\x00replaced\x00")
        return value

    def is_unimportant(self, left: str, right: str, options: CompareOptions) -> bool:
        """两侧仅因被忽略内容而不同时返回 True（用于标记"不重要差异"）。"""
        if left == right:
            return True
        return self.normalize(left, 0, options, "left") == self.normalize(right, 0, options, "right")


def default_rules() -> CompareRules:
    return CompareRules()
