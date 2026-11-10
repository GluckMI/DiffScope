"""Git 集成：读取仓库改动清单，比较 HEAD 版本与工作区文件。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import DiffScopeError


class GitError(DiffScopeError):
    title = "Git 操作失败"


def _run(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise GitError("未找到 git 命令，请确认已安装 Git。") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git 命令执行超时。") from exc
    except OSError as exc:
        raise GitError("无法启动 git。", str(exc)) from exc
    if result.returncode != 0:
        raise GitError("git 命令执行失败。", result.stderr.strip() or result.stdout.strip(), repo)
    return result.stdout


def is_repo(path: Path) -> bool:
    try:
        _run(path, "rev-parse", "--is-inside-work-tree")
        return True
    except GitError:
        return False


def changed_files(repo: Path) -> list[tuple[str, str, str]]:
    """工作区改动清单：[(状态码, 相对路径, 说明)]。未跟踪/重命名也列出。"""
    output = _run(repo, "status", "--porcelain", "-z")
    entries: list[tuple[str, str, str]] = []
    tokens = [t for t in output.split("\0") if t]
    index = 0
    descriptions = {
        "M": "已修改", "A": "新增", "D": "已删除", "R": "重命名",
        "C": "复制", "U": "未合并", "?": "未跟踪", "!": "已忽略",
    }
    while index < len(tokens):
        token = tokens[index]
        code, rel = token[:2], token[3:]
        index += 1
        if code[0] == "R" or code[0] == "C":
            if index < len(tokens):
                rel = tokens[index]  # 新路径
                index += 1
        clean = rel.strip('"')
        letter = code.strip() or code[0]
        desc = descriptions.get(letter[-1], code)
        entries.append((code, clean, desc))
    return entries


def head_file(repo: Path, rel: str) -> bytes | None:
    """读取 HEAD 中该文件的原始内容；文件不在 HEAD（新增）时返回 None。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "show", f"HEAD:{rel}"],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise GitError("未找到 git 命令，请确认已安装 Git。") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git show 执行超时。") from exc
    if result.returncode != 0:
        return None
    return result.stdout


def export_head_file(repo: Path, rel: str, target_dir: Path) -> Path | None:
    """把 HEAD 版本写到临时目录供比较；不在 HEAD 中返回 None。"""
    data = head_file(repo, rel)
    if data is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = rel.replace("/", "__")
    target = target_dir / f"HEAD__{safe}"
    target.write_bytes(data)
    return target
