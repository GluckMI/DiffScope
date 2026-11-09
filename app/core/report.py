"""比较报告：HTML（含链接到单文件报告）、CSV、纯文本。"""

from __future__ import annotations

import csv
import html
import io
from datetime import datetime
from pathlib import Path

from .errors import WriteFailure
from .fs_utils import format_size, format_time, write_text_atomic
from .models import STATUS_META, CompareResult, DiffStatus, TextDiffResult

CSS = """
body{font-family:'Segoe UI','Microsoft YaHei UI',sans-serif;background:#f7f8fa;color:#222;margin:24px}
h1{font-size:20px;margin-bottom:4px} h2{font-size:15px;margin-top:24px}
.meta{color:#666;font-size:12px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12px}
th,td{border:1px solid #dfe2e8;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#eef1f6} tr:nth-child(even){background:#fafbfc}
.summary span{display:inline-block;padding:4px 10px;margin:2px 6px 2px 0;border-radius:4px;color:#fff}
a{color:#2764c4} .mono{font-family:Consolas,monospace;white-space:pre-wrap}
.same{color:#7d8492}.modified{color:#a76b0a}.left_only{color:#1e6ea8}
.right_only{color:#7138a8}.error{color:#b52d2d}.folder_diff{color:#a76b0a}
.diff-add{background:#e6f4ea}.diff-del{background:#fdecea}.diff-mod{background:#fff3d6}
"""


def _badge(status: DiffStatus, count: int) -> str:
    label = STATUS_META[status][0]
    return f'<span style="background:{STATUS_META[status][2]}">{label} {count}</span>'


def html_folder_report(result: CompareResult, title: str = "文件夹比较报告", link_files: bool = True) -> str:
    stats = result.stats
    rows = []
    for node in result.root.walk():
        if not node.rel:
            continue
        left, right = node.left, node.right
        status = node.status.value
        name = html.escape(node.rel)
        if link_files and status in ("modified", "left_only", "right_only"):
            name = f'<a href="files/{html.escape(node.rel)}.html">{name}</a>'
        rows.append(
            "<tr>"
            f"<td>{name}</td>"
            f"<td class='{status}'>{html.escape(STATUS_META[node.status][0])}</td>"
            f"<td>{format_size(left.size) if left and left.exists else '—'}</td>"
            f"<td>{format_time(left.mtime_ns) if left and left.exists else '—'}</td>"
            f"<td>{format_size(right.size) if right and right.exists else '—'}</td>"
            f"<td>{format_time(right.mtime_ns) if right and right.exists else '—'}</td>"
            f"<td>{html.escape(node.note)}</td>"
            "</tr>"
        )
    badges = "".join(_badge(status, stats.get(status.value, 0)) for status in DiffStatus)
    errors = ""
    if result.errors:
        items = "".join(f"<li>{html.escape(item)}</li>" for item in result.errors[:50])
        errors = f"<h2>读取问题</h2><ul>{items}</ul>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        f"<h1>{html.escape(title)}</h1>"
        f"<div class='meta'>左侧：{html.escape(str(result.left_root))} ｜ 右侧：{html.escape(str(result.right_root))}"
        f" ｜ 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜ 耗时 {result.elapsed_ms} ms</div>"
        f"<div class='summary'>{badges}</div>"
        "<h2>差异明细</h2><table><tr><th>相对路径</th><th>状态</th><th>左大小</th><th>左修改时间</th>"
        "<th>右大小</th><th>右修改时间</th><th>说明</th></tr>"
        + "".join(rows) + "</table>" + errors + "</body></html>"
    )


def html_text_report(diff: TextDiffResult, left_name: str = "左侧", right_name: str = "右侧") -> str:
    rows = []
    for row in diff.rows:
        css_class = {"equal": "", "replace": "diff-mod", "delete": "diff-del", "insert": "diff-add"}[row.op]
        rows.append(
            f"<tr class='{css_class}'>"
            f"<td>{row.left_no or ''}</td><td class='mono'>{html.escape(row.left_text or '')}</td>"
            f"<td>{row.right_no or ''}</td><td class='mono'>{html.escape(row.right_text or '')}</td>"
            f"<td>{row.op}</td></tr>"
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>文本比较报告</title>"
        f"<style>{CSS}</style></head><body><h1>文本比较报告</h1>"
        f"<div class='meta'>{html.escape(left_name)} ↔ {html.escape(right_name)}"
        f" ｜ {len(diff.diff_rows)} 处差异</div>"
        "<table><tr><th>左行</th><th>左侧内容</th><th>右行</th><th>右侧内容</th><th>类型</th></tr>"
        + "".join(rows) + "</table></body></html>"
    )


def csv_report(result: CompareResult) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["相对路径", "状态", "左大小", "左修改时间", "右大小", "右修改时间", "说明"])
    for node in result.root.walk():
        if not node.rel:
            continue
        left, right = node.left, node.right
        writer.writerow([
            node.rel, STATUS_META[node.status][0],
            left.size if left and left.exists else "", format_time(left.mtime_ns) if left and left.exists else "",
            right.size if right and right.exists else "", format_time(right.mtime_ns) if right and right.exists else "",
            node.note,
        ])
    return output.getvalue()


def text_report(result: CompareResult) -> str:
    lines = [
        f"比较：{result.left_root}  ↔  {result.right_root}",
        f"差异 {result.stats.get('differences', 0)} 项 / 文件 {result.stats.get('files', 0)} 个",
        "",
    ]
    for node in result.root.walk():
        if node.rel and node.is_difference():
            lines.append(f"[{STATUS_META[node.status][0]:<8}] {node.rel}  {node.note}")
    return "\n".join(lines)


def save_report(result: CompareResult, path: Path, fmt: str = "html", title: str = "文件夹比较报告") -> Path:
    fmt = fmt.lower().lstrip(".")
    builders = {
        "html": lambda: html_folder_report(result, title),
        "htm": lambda: html_folder_report(result, title),
        "csv": lambda: csv_report(result),
        "txt": lambda: text_report(result),
        "text": lambda: text_report(result),
    }
    if fmt not in builders:
        raise WriteFailure(f"不支持的报告格式：{fmt}（支持 html / csv / txt）", path=path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, builders[fmt](), "utf-8", "\n")
    return path


def save_linked_reports(
    result: CompareResult,
    output_dir: Path,
    file_diffs: dict[str, TextDiffResult] | None = None,
) -> Path:
    """生成文件夹报告，并为每个差异文件生成可点击的单文件报告。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    files_dir = output_dir / "files"
    files_dir.mkdir(exist_ok=True)
    for rel, diff in (file_diffs or {}).items():
        target = files_dir / (rel + ".html")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, html_text_report(diff, rel, rel), "utf-8", "\n")
    main = output_dir / "index.html"
    return save_report(result, main, "html", "文件夹比较报告（含单文件报告）")
