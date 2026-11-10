"""SQLite 表数据比较（纯标准库）：按主键/rowid 对齐两库中的同名表。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .errors import DiffScopeError


class SqliteError(DiffScopeError):
    title = "SQLite 比较失败"


@dataclass
class RowChange:
    key: str                      # 主键显示值
    kind: str                     # only_left | only_right | modified
    left: dict | None = None      # 列名 → 值
    right: dict | None = None
    changed_columns: list[str] = field(default_factory=list)


@dataclass
class TableDiffResult:
    table: str
    columns: list[str]
    key_column: str
    only_left: list[RowChange]
    only_right: list[RowChange]
    modified: list[RowChange]
    same_count: int
    truncated: bool = False

    @property
    def total_changes(self) -> int:
        return len(self.only_left) + len(self.only_right) + len(self.modified)


def _connect(path: Path) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise SqliteError("数据库文件不存在。", path=str(path))
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise SqliteError("无法打开数据库。", str(exc), path) from exc


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def list_tables(path: Path) -> list[str]:
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error as exc:
        raise SqliteError("读取表清单失败。", str(exc), path) from exc
    finally:
        con.close()


def _pick_key(columns: list[tuple[str, str, int]]) -> str | None:
    """PRAGMA table_info 行：cid, name, type, notnull, dflt_value, pk。pk>0 的列按位序拼接。"""
    pk = [c[1] for c in columns if c[5] > 0]
    if len(pk) == 1:
        return pk[0]
    return None  # 联合主键或无主键：退回 rowid


def _load_rows(con: sqlite3.Connection, table: str, columns: list[str], key: str, limit: int):
    order = _quote(key) if key != "rowid" else "rowid"
    sql = f"SELECT {', '.join(_quote(c) for c in columns)} FROM {_quote(table)} ORDER BY {order} LIMIT {limit + 1}"
    rows = con.execute(sql).fetchall()
    truncated = len(rows) > limit
    data = {}
    for row in rows[:limit]:
        record = dict(zip(columns, row))
        data[str(record.get(key))] = record
    return data, truncated


def compare_tables(
    left_path: Path, right_path: Path, table: str, key_column: str | None = None, limit: int = 5000,
) -> TableDiffResult:
    """比较两库同名表；按主键对齐行，输出增/删/改清单。单表行数超过 limit 时截断。"""
    lcon, rcon = _connect(left_path), _connect(right_path)
    try:
        lcols = lcon.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
        rcols = rcon.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
        if not lcols or not rcols:
            raise SqliteError(f"表 {table} 在某一侧不存在或无列信息。", path=str(left_path))
        lnames = [c[1] for c in lcols]
        if lnames != [c[1] for c in rcols]:
            raise SqliteError(
                f"表 {table} 两侧列结构不同，无法按行对齐。\n左：{lnames}\n右：{[c[1] for c in rcols]}",
                path=str(left_path),
            )
        key = key_column or _pick_key(lcols) or "rowid"
        if key != "rowid" and key not in lnames:
            raise SqliteError(f"主键列 {key} 不存在于表 {table}。", path=str(left_path))
        columns = [c[1] for c in lcols]
        left_rows, l_trunc = _load_rows(lcon, table, columns, key, limit)
        right_rows, r_trunc = _load_rows(rcon, table, columns, key, limit)
    finally:
        lcon.close()
        rcon.close()

    only_left, only_right, modified, same = [], [], [], 0
    for key_str in sorted(set(left_rows) | set(right_rows), key=lambda k: (len(k), k)):
        lrow, rrow = left_rows.get(key_str), right_rows.get(key_str)
        if lrow is not None and rrow is None:
            only_left.append(RowChange(key_str, "only_left", lrow, None))
        elif rrow is not None and lrow is None:
            only_right.append(RowChange(key_str, "only_right", None, rrow))
        else:
            changed = [c for c in columns if c != key and lrow[c] != rrow[c]]
            if changed:
                modified.append(RowChange(key_str, "modified", lrow, rrow, changed))
            else:
                same += 1
    return TableDiffResult(
        table=table, columns=columns, key_column=key,
        only_left=only_left, only_right=only_right, modified=modified,
        same_count=same, truncated=l_trunc or r_trunc,
    )
