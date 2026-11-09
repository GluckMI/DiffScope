"""工作区：多个比较会话以标签页形式组织，支持整体保存与恢复。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .errors import SessionError
from .fs_utils import write_text_atomic
from .services import config_dir
from .models import CompareOptions

WORKSPACE_VERSION = 1


@dataclass
class WorkspaceTab:
    name: str = "未命名"
    mode: str = "folders"
    left: str = ""
    right: str = ""
    viewer: str = "auto"
    options: CompareOptions = field(default_factory=CompareOptions)
    filters: dict = field(default_factory=dict)
    current_diff: int = 0
    base: str = ""  # 三向合并的共同祖先
    output: str = ""  # 合并输出位置

    def to_dict(self) -> dict:
        return {
            "name": self.name, "mode": self.mode, "left": self.left, "right": self.right,
            "viewer": self.viewer, "options": self.options.to_dict(),
            "filters": self.filters, "current_diff": self.current_diff,
            "base": self.base, "output": self.output,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkspaceTab":
        return cls(
            name=str(data.get("name") or "未命名"),
            mode=str(data.get("mode") or "folders"),
            left=str(data.get("left") or ""),
            right=str(data.get("right") or ""),
            viewer=str(data.get("viewer") or "auto"),
            options=CompareOptions.from_dict(data.get("options")),
            filters=data.get("filters") or {},
            current_diff=int(data.get("current_diff") or 0),
            base=str(data.get("base") or ""),
            output=str(data.get("output") or ""),
        )


@dataclass
class Workspace:
    name: str = "默认工作区"
    tabs: list[WorkspaceTab] = field(default_factory=list)
    active: int = 0
    geometry: str = ""
    saved_at: str = ""

    def to_dict(self) -> dict:
        return {
            "app": "DiffScope", "kind": "workspace", "version": WORKSPACE_VERSION,
            "name": self.name, "tabs": [tab.to_dict() for tab in self.tabs],
            "active": self.active, "geometry": self.geometry,
            "saved_at": self.saved_at or datetime.now().isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Workspace":
        if data.get("kind") != "workspace":
            raise SessionError("该文件不是 DiffScope 工作区文件。")
        if data.get("version") != WORKSPACE_VERSION:
            raise SessionError("工作区文件版本不兼容。")
        return cls(
            name=str(data.get("name") or "默认工作区"),
            tabs=[WorkspaceTab.from_dict(item) for item in data.get("tabs", [])],
            active=int(data.get("active") or 0),
            geometry=str(data.get("geometry") or ""),
            saved_at=str(data.get("saved_at") or ""),
        )


def workspace_dir() -> Path:
    target = config_dir() / "workspaces"
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_workspace(workspace: Workspace, path: Path | None = None) -> Path:
    target = path or workspace_dir() / f"{_safe(workspace.name)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, json.dumps(workspace.to_dict(), ensure_ascii=False, indent=2), "utf-8", "\n")
    return target


def load_workspace(path: Path) -> Workspace:
    try:
        return Workspace.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SessionError("工作区文件无法读取或已损坏。", str(exc), path) from exc


def list_workspaces() -> list[tuple[Path, Workspace]]:
    out = []
    for path in sorted(workspace_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append((path, load_workspace(path)))
        except SessionError:
            continue
    return out


def _safe(name: str) -> str:
    return "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip() or "workspace"
