"""专用格式解析：表格（CSV/TSV/HTML表格/Excel）、文档转文本、补丁文件、第三方工具钩子。"""

from __future__ import annotations

import csv
import difflib
import io
import re
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

from .errors import DiffScopeError, ReadFailure


class FormatError(ReadFailure):
    title = "文件格式解析失败"


# ---------------------------------------------------------------- 表格
@dataclass
class TableData:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    name: str = ""
    sheets: dict[str, "TableData"] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return max([len(self.headers)] + [len(row) for row in self.rows] or [0])


@dataclass
class TableOptions:
    key_columns: list[int] = field(default_factory=list)  # 0 基列索引
    ignore_columns: list[int] = field(default_factory=list)
    hide_columns: list[int] = field(default_factory=list)
    column_order: list[int] = field(default_factory=list)  # 重排列顺序
    tolerance: float = 0.0  # 数值/日期容差
    sort_before_compare: bool = False
    use_headers: bool = True

    def to_dict(self) -> dict:
        return {
            "key_columns": self.key_columns, "ignore_columns": self.ignore_columns,
            "hide_columns": self.hide_columns, "column_order": self.column_order,
            "tolerance": self.tolerance, "sort_before_compare": self.sort_before_compare,
            "use_headers": self.use_headers,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "TableOptions":
        data = data or {}
        return cls(
            key_columns=[int(x) for x in data.get("key_columns", [])],
            ignore_columns=[int(x) for x in data.get("ignore_columns", [])],
            hide_columns=[int(x) for x in data.get("hide_columns", [])],
            column_order=[int(x) for x in data.get("column_order", [])],
            tolerance=float(data.get("tolerance", 0)),
            sort_before_compare=bool(data.get("sort_before_compare", False)),
            use_headers=bool(data.get("use_headers", True)),
        )


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def load_table(data: bytes, name: str = "") -> TableData:
    """按扩展名解析表格，返回含多工作表的 TableData。"""
    suffix = Path(name).suffix.lower()
    try:
        text = data.decode("utf-8-sig", "replace")
    except Exception:
        text = ""
    if suffix in (".csv", ".tsv", ".txt"):
        delimiter = "\t" if suffix == ".tsv" or text.count("\t") > text.count(",") else ","
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if row]
        return TableData(headers=rows[0] if rows else [], rows=rows[1:], name=name)
    if suffix in (".html", ".htm"):
        parser = _TableParser()
        parser.feed(text)
        sheets = {}
        for index, table in enumerate(parser.tables):
            if not table:
                continue
            label = f"表格 {index + 1}"
            sheets[label] = TableData(headers=table[0], rows=table[1:], name=label)
        first = next(iter(sheets.values()), TableData(name=name))
        first.sheets = sheets
        return first
    if suffix in (".xlsx", ".xlsm"):
        return _load_xlsx(data, name)
    raise FormatError(
        f"不支持的表格格式：{suffix or '未知'}。支持 .csv / .tsv / .html / .xlsx / .xlsm。", path=name
    )


def _load_xlsx(data: bytes, name: str) -> TableData:
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets: dict[str, TableData] = {}
        for sheet in workbook.worksheets:
            rows = [[("" if cell is None else str(cell)) for cell in row] for row in sheet.iter_rows(values_only=True)]
            rows = [row for row in rows if any(cell for cell in row)]
            sheets[sheet.title] = TableData(
                headers=rows[0] if rows else [], rows=rows[1:], name=sheet.title
            )
        workbook.close()
        first = next(iter(sheets.values()), TableData(name=name))
        first.sheets = sheets
        return first
    except ImportError:
        return _load_xlsx_fallback(data, name)
    except Exception as exc:
        raise FormatError("Excel 工作簿解析失败。", str(exc), name) from exc


def _load_xlsx_fallback(data: bytes, name: str) -> TableData:
    """无 openpyxl 时用 zipfile + XML 读取首个工作表（只读，够用于比较）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                shared = ["".join(t.text or "" for t in si.iter(ns + "t")) for si in root.findall(ns + "si")]
            sheet_name = next((n for n in archive.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")), "")
            if not sheet_name:
                raise FormatError("Excel 文件中没有工作表。", path=name)
            root = ET.fromstring(archive.read(sheet_name))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            rows = []
            for row in root.iter(ns + "row"):
                cells = []
                for cell in row.findall(ns + "c"):
                    value = cell.findtext(ns + "v", "")
                    if cell.get("t") == "s" and value.isdigit():
                        value = shared[int(value)]
                    cells.append(value)
                rows.append(cells)
        return TableData(headers=rows[0] if rows else [], rows=rows[1:], name=name)
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise FormatError("Excel 工作簿解析失败。", str(exc), name) from exc


@dataclass
class TableDiffRow:
    op: str  # equal | replace | delete | insert
    left_no: int | None = None
    right_no: int | None = None
    left: list[str] = field(default_factory=list)
    right: list[str] = field(default_factory=list)
    changed: set[int] = field(default_factory=set)


@dataclass
class TableDiffResult:
    rows: list[TableDiffRow] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    options: TableOptions = field(default_factory=TableOptions)

    @property
    def diff_indexes(self) -> list[int]:
        return [i for i, row in enumerate(self.rows) if row.op != "equal"]


def _cell_equal(a: str, b: str, tolerance: float) -> bool:
    if a == b:
        return True
    if tolerance > 0:
        try:
            return abs(float(a) - float(b)) <= tolerance
        except ValueError:
            pass
        try:
            from datetime import datetime

            fmt_a, fmt_b = _parse_date(a), _parse_date(b)
            if fmt_a and fmt_b:
                return abs((fmt_a - fmt_b).total_seconds()) <= tolerance * 86400
        except Exception:
            pass
    return False


def _parse_date(value: str):
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def compare_tables(left: TableData, right: TableData, options: TableOptions | None = None) -> TableDiffResult:
    """按关键列或整行对齐比较；支持忽略列、数值/日期容差、列重排、预排序。"""
    options = options or TableOptions()
    width = max(left.width, right.width)
    order = options.column_order or list(range(width))

    def project(row: list[str]) -> list[str]:
        padded = row + [""] * (width - len(row))
        return [padded[i] if 0 <= i < len(padded) else "" for i in order]

    def key(row: list[str]) -> tuple:
        if options.key_columns:
            return tuple(row[i] if i < len(row) else "" for i in options.key_columns)
        return tuple(cell for i, cell in enumerate(row) if i not in options.ignore_columns)

    left_rows = [project(row) for row in left.rows]
    right_rows = [project(row) for row in right.rows]
    if options.sort_before_compare:
        left_rows.sort(key=key)
        right_rows.sort(key=key)
    left_keys = [key(row) for row in left_rows]
    right_keys = [key(row) for row in right_rows]
    matcher = difflib.SequenceMatcher(None, left_keys, right_keys, autojunk=False)
    result = TableDiffResult(headers=project(left.headers or right.headers), options=options)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                li, ri = left_rows[i1 + offset], right_rows[j1 + offset]
                changed = {c for c in range(len(li)) if c not in options.ignore_columns
                           and not _cell_equal(li[c] if c < len(li) else "", ri[c] if c < len(ri) else "", options.tolerance)}
                result.rows.append(TableDiffRow("replace" if changed else "equal",
                                                i1 + offset + 1, j1 + offset + 1, li, ri, changed))
        else:
            for k in range(max(i2 - i1, j2 - j1)):
                li = left_rows[i1 + k] if i1 + k < i2 else []
                ri = right_rows[j1 + k] if j1 + k < j2 else []
                op = "replace" if li and ri else ("delete" if li else "insert")
                changed = {c for c in range(max(len(li), len(ri))) if c not in options.ignore_columns
                           and not _cell_equal(li[c] if c < len(li) else "", ri[c] if c < len(ri) else "", options.tolerance)}
                result.rows.append(TableDiffRow(
                    op if changed else "equal",
                    i1 + k + 1 if li else None, j1 + k + 1 if ri else None, li, ri, changed))
    return result


# ---------------------------------------------------------------- 文档转文本
def _dfm_prefix_string(data: bytes, pos: int) -> tuple[str | None, int]:
    """读取 DFM 二进制的前缀字符串；失败返回 (None, pos)。"""
    if pos >= len(data):
        return None, pos
    length = data[pos]
    if length == 0xFF:
        if pos + 3 > len(data):
            return None, pos
        length = int.from_bytes(data[pos + 1:pos + 3], "big")
        pos += 2
    pos += 1
    if length < 2 or pos + length > len(data):
        return None, pos
    chunk = data[pos:pos + length]
    text = None
    for encoding in ("utf-8", "cp1252"):
        try:
            text = chunk.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return None, pos
    printable = sum(1 for ch in text if ch.isprintable())
    if printable / max(len(text), 1) < 0.9:
        return None, pos
    return text, pos + length


def binary_dfm_outline(data: bytes, max_items: int = 20000) -> str:
    """从二进制 Delphi 表单（TPF0）提取可读字符串序列（类名/属性名/字符串值）。

    这是启发式结构提取，用于文本对比，不是精确的 DFM 反编译。
    """
    pos = 4  # 跳过 TPF0 标识
    lines: list[str] = []
    depth = 0
    while pos < len(data) and len(lines) < max_items:
        byte = data[pos]
        if byte == 0x00:  # 对象结束标记
            depth = max(0, depth - 1)
            pos += 1
            continue
        if byte == 0xFF:  # 新对象开始：类名 + 对象名
            cls, pos2 = _dfm_prefix_string(data, pos + 1)
            if cls is not None:
                name, pos3 = _dfm_prefix_string(data, pos2)
                if name is not None:
                    lines.append("  " * depth + f"object {name}: {cls}")
                    depth += 1
                    pos = pos3
                    continue
        text, new_pos = _dfm_prefix_string(data, pos)
        if text is not None and text.strip():
            lines.append("  " * depth + text.strip())
            pos = new_pos
        else:
            pos += 1
    header = "（二进制 Delphi 表单：结构化字符串提取，用于对比，非精确反编译）\n"
    return header + "\n".join(lines)


def to_text(data: bytes, name: str = "") -> str:
    """比较前把 PDF / Word / RTF / HTML 转为纯文本。"""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        try:
            import PyPDF2

            reader = PyPDF2.PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError as exc:
            raise FormatError("读取 PDF 需要 PyPDF2。", str(exc), name) from exc
        except Exception as exc:
            raise FormatError("PDF 文本提取失败。", str(exc), name) from exc
    if suffix in (".docx", ".dotx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            lines = []
            for para in root.iter(ns + "p"):
                text = "".join(node.text or "" for node in para.iter(ns + "t"))
                lines.append(text)
            return "\n".join(lines)
        except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise FormatError("Word 文档解析失败。", str(exc), name) from exc
    if suffix == ".rtf":
        text = data.decode("latin-1", "replace")
        text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
        text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
        return text.replace("{", "").replace("}", "")
    if suffix in (".html", ".htm", ".xml"):
        class _Text(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data):
                self.parts.append(data)

        parser = _Text()
        parser.feed(data.decode("utf-8", "replace"))
        return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    if suffix == ".doc":
        raise FormatError("旧版 .doc 需要外部转换工具，请在“文件格式”中配置转换程序。", path=name)
    if suffix == ".dfm":
        if data.startswith(b"TPF0"):
            return binary_dfm_outline(data)
        from .fs_utils import decode_bytes

        text, _encoding = decode_bytes(data, name)
        return text
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------- HTML 格式化
_BLOCK_TAGS = {
    "html", "head", "body", "div", "p", "table", "thead", "tbody", "tfoot", "tr",
    "td", "th", "ul", "ol", "li", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6",
    "form", "fieldset", "select", "option", "optgroup", "section", "article",
    "nav", "aside", "footer", "header", "main", "figure", "figcaption", "blockquote",
    "pre", "script", "style", "title", "head", "meta", "link", "hr",
}
_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}


class _HtmlBeautifier(HTMLParser):
    def __init__(self, indent: str = "  "):
        super().__init__(convert_charrefs=True)
        self.indent = indent
        self.parts: list[str] = []
        self.depth = 0

    def _emit(self, text: str) -> None:
        if text.strip():
            self.parts.append(text.rstrip() + "\n")

    def _pad(self) -> str:
        return self.indent * self.depth

    def handle_starttag(self, tag, attrs):
        attrs_text = "".join(f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs)
        self._emit(self._pad() + f"<{tag}{attrs_text}>")
        if tag not in _VOID_TAGS:
            self.depth += 1
            if tag in _BLOCK_TAGS:
                self._newline()

    def handle_endtag(self, tag):
        if tag not in _VOID_TAGS:
            self.depth = max(0, self.depth - 1)
        self._emit(self._pad() + f"</{tag}>")
        if tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_startendtag(self, tag, attrs):
        attrs_text = "".join(f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs)
        self._emit(self._pad() + f"<{tag}{attrs_text}/>")

    def handle_data(self, data):
        if data.strip():
            self._emit(self._pad() + data.strip())

    def _newline(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")


def format_html(text: str, indent: str = "  ") -> str:
    """把 HTML 源码按标签层级重新缩进，便于比较格式差异。"""
    parser = _HtmlBeautifier(indent)
    parser.feed(text)
    return "\n".join(line for line in "".join(parser.parts).splitlines() if line.strip()) + "\n"


# ---------------------------------------------------------------- 补丁文件
@dataclass
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


def parse_patch(text: str) -> tuple[str, str, list[PatchHunk]]:
    """把统一 diff 补丁还原成左右两侧文本，用于并排查看。"""
    left: dict[int, str] = {}
    right: dict[int, str] = {}
    hunks: list[PatchHunk] = []
    current: PatchHunk | None = None
    old_no = new_no = 0
    for line in text.splitlines():
        if line.startswith("@@"):
            match = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                old_no = int(match.group(1))
                new_no = int(match.group(3))
                current = PatchHunk(old_no, int(match.group(2) or 1), new_no, int(match.group(4) or 1))
                hunks.append(current)
            continue
        if line.startswith(("---", "+++", "diff ", "index ")):
            continue
        if current is None:
            continue
        current.lines.append(line)
        body = line[1:] if line[:1] in " +-" else line
        if line.startswith("-"):
            left[old_no] = body
            old_no += 1
        elif line.startswith("+"):
            right[new_no] = body
            new_no += 1
        elif line.startswith(" "):
            left[old_no] = right[new_no] = body
            old_no += 1
            new_no += 1
    max_no = max([0] + list(left) + list(right))
    left_text = "".join(left.get(i, "") + "\n" for i in range(1, max_no + 1))
    right_text = "".join(right.get(i, "") + "\n" for i in range(1, max_no + 1))
    return left_text, right_text, hunks


# ---------------------------------------------------------------- 第三方工具
def run_external_compare(tool: str, left: str | Path, right: str | Path, timeout: int = 300) -> int:
    """把不支持的文件交给第三方比较工具；返回退出码。"""
    if not tool:
        raise FormatError("未配置第三方比较工具，请在设置中填写可执行文件路径。")
    import shlex

    try:
        command = shlex.split(tool) + [str(left), str(right)]
        return subprocess.call(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise FormatError("启动第三方比较工具失败。", str(exc)) from exc
