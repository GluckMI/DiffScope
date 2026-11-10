"""对比会话的保存、加载与恢复前校验。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import APP_NAME
from .errors import SessionError, validate_path
from .models import CompareMode, CompareOptions, PathKind
from .services import config_dir

SESSION_VERSION = 1
LAST_SESSION = "_last.json"


@dataclass
class Session:
    name: str = "未命名会话"
    mode: str = CompareMode.FOLDERS.value
    left: str = ""
    right: str = ""
    options: CompareOptions = field(default_factory=CompareOptions)
    filters: dict = field(default_factory=dict)
    current_diff: int = 0
    geometry: dict = field(default_factory=dict)
    saved_at: str = ""

    def to_dict(self) -> dict:
        return {
            "app": APP_NAME,
            "version": SESSION_VERSION,
            "name": self.name,
            "mode": self.mode,
            "left": self.left,
            "right": self.right,
            "options": self.options.to_dict(),
            "filters": self.filters,
            "current_diff": self.current_diff,
            "geometry": self.geometry,
            "saved_at": self.saved_at or datetime.now().isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        if not isinstance(data, dict):
            raise SessionError("会话文件内容不是有效的对象。")
        if data.get("app") not in (APP_NAME, None):
            raise SessionError("该文件不是 DiffScope 会话文件。")
        version = data.get("version", 1)
        if version != SESSION_VERSION:
            raise SessionError(f"会话文件版本 {version} 与当前版本 {SESSION_VERSION} 不兼容。")
        mode = data.get("mode", CompareMode.FOLDERS.value)
        if mode not in (m.value for m in CompareMode):
            mode = CompareMode.FOLDERS.value
        filters = data.get("filters") or {}
        geometry = data.get("geometry") or {}
        return cls(
            name=str(data.get("name") or "未命名会话"),
            mode=mode,
            left=str(data.get("left") or ""),
            right=str(data.get("right") or ""),
            options=CompareOptions.from_dict(data.get("options")),
            filters=filters if isinstance(filters, dict) else {},
            current_diff=int(data.get("current_diff") or 0),
            geometry=geometry if isinstance(geometry, dict) else {},
            saved_at=str(data.get("saved_at") or ""),
        )


def session_dir() -> Path:
    target = config_dir() / "sessions"
    target.mkdir(parents=True, exist_ok=True)
    return target


def last_session_path() -> Path:
    return session_dir() / LAST_SESSION


def _write_json(path: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=path.parent, prefix=".session-", suffix=".tmp"
        ) as handle:
            tmp = handle.name
            handle.write(payload)
        os.replace(tmp, path)
        tmp = ""
    except OSError as exc:
        raise SessionError("会话文件写入失败。", exc.strerror or str(exc), path) from exc
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def save_session(session: Session, path: Path | None = None) -> Path:
    target = path or session_dir() / f"{_safe_name(session.name)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json(target, session.to_dict())
    return target


def save_last_session(session: Session) -> Path:
    return save_session(session, last_session_path())


def load_session(path: Path) -> Session:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SessionError("会话文件不存在，可能已被移动或删除。", path=path) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise SessionError("会话文件无法读取。", str(exc), path) from exc
    except json.JSONDecodeError as exc:
        raise SessionError("会话文件不是有效的 JSON，可能已损坏。", str(exc), path) from exc
    return Session.from_dict(data)


def list_sessions() -> list[tuple[Path, Session]]:
    out: list[tuple[Path, Session]] = []
    for path in sorted(session_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name == LAST_SESSION or path.name.startswith(".session-"):
            continue
        try:
            out.append((path, load_session(path)))
        except SessionError:
            continue
    return out


def _safe_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip() or "session"
    return cleaned[:60]


def validate_session(session: Session) -> list[str]:
    """恢复前检查路径是否仍有效，返回问题列表（空列表表示可直接恢复）。"""
    problems: list[str] = []
    if not session.left or not session.right:
        problems.append("会话中缺少对比路径。")
        return problems
    expect = PathKind.DIR if session.mode == CompareMode.FOLDERS.value else PathKind.FILE
    for side, raw in (("左侧", session.left), ("右侧", session.right)):
        try:
            validate_path(raw, expect)
        except Exception as exc:
            message = getattr(exc, "message", str(exc))
            problems.append(f"{side}路径不可用：{message} ({raw})")
    return problems


# ---- 比较模板：规则 + 选项（不绑定路径） --------------------------------
def templates_dir() -> Path:
    target = config_dir() / "templates"
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_template(name: str, rules, options: CompareOptions) -> Path:
    if not name.strip():
        raise SessionError("模板名称不能为空。")
    payload = {
        "app": APP_NAME,
        "version": SESSION_VERSION,
        "name": name.strip(),
        "rules": rules.to_dict() if hasattr(rules, "to_dict") else {},
        "options": options.to_dict(),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    target = templates_dir() / f"{_safe_name(name)}.template.json"
    _write_json(target, payload)
    return target


def load_template(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SessionError("模板文件不存在，可能已被删除。", path=path) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError("模板文件无法读取，可能已损坏。", str(exc), path) from exc
    if not isinstance(data, dict):
        raise SessionError("模板文件内容不是有效的对象。", path=path)
    return data


def list_templates() -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for path in sorted(templates_dir().glob("*.template.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append((path, load_template(path)))
        except SessionError:
            continue
    return out


def delete_template(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        raise SessionError("模板删除失败。", str(exc), path) from exc
