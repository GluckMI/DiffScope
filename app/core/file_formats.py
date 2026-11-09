"""文件格式规则库（对应 BC 的 File Formats）：扩展名 → 语法语言、忽略规则、查看器、外部转换器。"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .errors import DiffScopeError
from .formats import FormatError
from .rules import CompareRules, IgnoreRule
from .services import config_dir

FORMATS_VERSION = 1


@dataclass
class FileFormat:
    name: str
    extensions: tuple[str, ...] = ()
    language: str = ""          # 语法高亮语言（python / cpp / sql …）
    viewer: str = "auto"        # auto / text / table / hex / image / tags / registry
    converter: str = ""         # 外部转换命令，{in} {out} 占位，如 ConvertDfm.exe {in} {out}
    rules: CompareRules = field(default_factory=CompareRules)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "extensions": list(self.extensions),
            "language": self.language,
            "viewer": self.viewer,
            "converter": self.converter,
            "rules": self.rules.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileFormat":
        return cls(
            name=str(data.get("name") or "未命名格式"),
            extensions=tuple(str(x).lower() for x in data.get("extensions", [])),
            language=str(data.get("language", "")),
            viewer=str(data.get("viewer") or "auto"),
            converter=str(data.get("converter", "")),
            rules=CompareRules.from_dict(data.get("rules")),
        )


def default_formats() -> list[FileFormat]:
    def fmt(name, exts, language="", viewer="auto", converter="", rules=None):
        return FileFormat(name, tuple(exts), language, viewer, converter, rules or CompareRules())

    return [
        fmt("Python 源码", (".py", ".pyw"), "python",
            rules=CompareRules(language="python", rules=[IgnoreRule("comments", "python")])),
        fmt("C / C++ 源码", (".c", ".h", ".cpp", ".hpp", ".cc"), "cpp"),
        fmt("JSON", (".json",), "", rules=CompareRules(rules=[IgnoreRule("unimportant", r'"(id|timestamp|date)":')])),
        fmt("XML / SVG", (".xml", ".svg", ".xaml"), "xml"),
        fmt("HTML", (".html", ".htm"), "html"),
        fmt("SQL 脚本", (".sql",), "sql"),
        fmt("INI / 配置", (".ini", ".cfg", ".conf", ".toml"), "ini",
            rules=CompareRules(rules=[IgnoreRule("comments", "ini")])),
        fmt("CSV 表格", (".csv", ".tsv"), viewer="table"),
        fmt("Excel 工作簿", (".xlsx", ".xlsm"), viewer="table"),
        fmt("图片", (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"), viewer="image"),
        fmt("音频标签", (".mp3", ".flac", ".m4a", ".ogg", ".oga", ".opus", ".wav"), viewer="tags"),
        fmt("可执行文件", (".exe", ".dll", ".sys", ".ocx"), viewer="hex"),
        fmt("注册表导出", (".reg",), viewer="registry"),
        fmt("PDF 文档", (".pdf",), converter=""),
        fmt("Word 文档", (".docx", ".dotx"), converter=""),
        fmt("旧版 Word (需转换器)", (".doc",), converter='"{in}"'),
        fmt("旧版 Excel (需转换器)", (".xls",), converter=""),
        fmt("Delphi 表单", (".dfm", ".xfm"), converter=""),
        fmt("补丁文件", (".patch", ".diff"), viewer="text"),
    ]


class FormatRegistry:
    def __init__(self, formats: list[FileFormat] | None = None):
        self.formats = formats if formats is not None else default_formats()

    def match(self, filename: str) -> FileFormat | None:
        suffix = Path(filename).suffix.lower()
        for item in self.formats:
            if suffix and suffix in item.extensions:
                return item
        return None

    def save(self, path: Path | None = None) -> Path:
        target = path or formats_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        from .fs_utils import write_text_atomic

        write_text_atomic(
            target,
            json.dumps({"version": FORMATS_VERSION, "formats": [f.to_dict() for f in self.formats]},
                       ensure_ascii=False, indent=2),
            "utf-8", "\n",
        )
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> "FormatRegistry":
        target = path or formats_path()
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return cls([FileFormat.from_dict(item) for item in data.get("formats", [])])
        except (OSError, json.JSONDecodeError) as exc:
            raise DiffScopeError("文件格式规则库损坏，已使用默认规则。", str(exc), target)


def formats_path() -> Path:
    return config_dir() / "formats.json"


def export_formats(registry: "FormatRegistry", path: Path) -> Path:
    """把格式规则导出为可分享的 JSON 文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    from .fs_utils import write_text_atomic

    write_text_atomic(
        target,
        json.dumps({"kind": "diffscope-formats", "version": FORMATS_VERSION,
                    "formats": [f.to_dict() for f in registry.formats]},
                   ensure_ascii=False, indent=2),
        "utf-8", "\n",
    )
    return target


def import_formats(registry: "FormatRegistry", path: Path, replace: bool = False) -> int:
    """从 JSON 导入格式规则；replace=True 覆盖现有列表。返回导入条数。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("formats", []) if isinstance(data, dict) else data
    imported = [FileFormat.from_dict(item) for item in items]
    if replace:
        registry.formats = imported
    else:
        existing = {f.name: f.extensions for f in registry.formats}
        for item in imported:
            if item.name in existing:
                registry.formats = [item if f.name == item.name else f for f in registry.formats]
            else:
                registry.formats.append(item)
    return len(imported)


def run_converter(command: str, src: Path) -> str:
    """调用外部转换程序，把 src 转成文本并返回内容；{in}/{out} 为占位符。"""
    if not command.strip():
        raise FormatError("该格式没有配置转换程序，请在“文件格式”中设置。", path=src)
    handle, out_name = tempfile.mkstemp(suffix=".txt")
    os.close(handle)
    out_path = Path(out_name)
    cmd = command.replace("{in}", f'"{src}"').replace("{out}", f'"{out_path}"')
    try:
        completed = subprocess.run(
            cmd if os.name == "nt" else shlex.split(cmd),
            capture_output=True, text=True, timeout=120, shell=(os.name == "nt"),
        )
        if completed.returncode != 0:
            raise FormatError("外部转换程序返回错误。", completed.stderr.strip()[:300], src)
        if not out_path.exists():
            raise FormatError("外部转换程序未生成输出文件。", cmd, src)
        return out_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        raise FormatError("外部转换程序启动失败。", str(exc), src) from exc
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass
