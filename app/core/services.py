"""平台服务：设置持久化、自动备份、后台任务、定时调度、Shell / VCS 集成、更新检查。"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import APP_NAME, __version__
from .errors import DiffScopeError, WriteFailure
from .fs_utils import write_text_atomic

ROOT = Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    """数据目录：便携模式（DIFFSCOPE_PORTABLE=1 或存在 portable.flag）用程序目录 Data/。"""
    if os.environ.get("DIFFSCOPE_PORTABLE") == "1" or (ROOT / "portable.flag").exists():
        data = ROOT / "Data"
        try:
            data.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return data
    return Path.home() / ".diffscope"


CONFIG_DIR = config_dir()
SETTINGS_FILE = CONFIG_DIR / "settings.json"
BACKUP_DIR = CONFIG_DIR / "backups"

DEFAULTS: dict = {
    "theme": "dark",
    "font_family": "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI",
    "font_size": 9,
    "editor_font_family": "SF Mono" if sys.platform == "darwin" else "Consolas",
    "editor_font_size": 9,
    "word_wrap": False,
    "syntax_highlight": True,
    "auto_recompare": True,
    "show_line_numbers": True,
    "show_minimap": True,
    "reduce_motion": False,
    "show_onboarding": True,
    "auto_backup": True,
    "backup_keep": 10,
    "default_compare_content": True,
    "expand_to_differences": True,
    "check_updates": False,
    "language": "zh",
    "update_url": "",
    "max_text_mb": 64,
    "max_nodes": 100000,
    "external_tool": "",
    "shell_integration": False,
    "shortcuts": {
        "open": "Ctrl+O", "refresh": "Ctrl+R", "save": "Ctrl+S",
        "prev_diff": "F7", "next_diff": "F8", "find": "Ctrl+F",
        "next_match": "F3", "save_session": "Ctrl+Shift+S",
        "open_session": "Ctrl+Shift+O", "merge_left": "Ctrl+Left", "merge_right": "Ctrl+Right",
    },
    "colors": {},
    "recent_workspaces": [],
    "recent_comparisons": [],
    "sync_schedule": [],
}


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or SETTINGS_FILE
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in loaded.items():
                    if key in self.data and isinstance(value, type(self.data[key])):
                        if isinstance(value, dict):
                            self.data[key] = {**self.data[key], **value}
                        else:
                            self.data[key] = value
        except (OSError, json.JSONDecodeError) as exc:
            raise DiffScopeError("设置文件损坏，已使用默认设置。", str(exc), self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8", "\n")

    def get(self, key: str, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def shortcut(self, name: str) -> str:
        return self.data["shortcuts"].get(name, DEFAULTS["shortcuts"].get(name, ""))


def backup_file(path: Path, keep: int = 10) -> Path | None:
    """保存前自动备份，保留最近 keep 份。"""
    if not path.exists() or not path.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = BACKUP_DIR / f"{stamp}-{path.name}"
    try:
        shutil.copy2(path, target)
    except OSError as exc:
        raise WriteFailure("自动备份失败，已取消写入以保证原文件安全。", str(exc), path) from exc
    backups = sorted(BACKUP_DIR.glob(f"*-{path.name}"))
    for old in backups[:-max(keep, 1)]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


# ---------------------------------------------------------------- 后台任务
@dataclass
class TaskState:
    running: bool = False
    paused: bool = False
    cancelled: bool = False
    done: int = 0
    total: int = 0
    label: str = ""
    error: str = ""


class TaskController:
    """跨线程可暂停 / 取消的任务控制器（纯 Python，UI 层负责线程调度）。"""

    def __init__(self):
        self.state = TaskState(running=True)
        self._event = threading.Event()
        self._event.set()

    def pause(self) -> None:
        self.state.paused = True
        self._event.clear()

    def resume(self) -> None:
        self.state.paused = False
        self._event.set()

    def cancel(self) -> None:
        self.state.cancelled = True
        self._event.set()

    def wait_if_paused(self) -> None:
        self._event.wait()

    @property
    def cancelled(self) -> bool:
        return self.state.cancelled

    def progress(self, done: int, total: int, label: str) -> None:
        self.state.done, self.state.total, self.state.label = done, total, label


def run_task(items: list, handler: Callable, controller: TaskController) -> tuple[int, list[str]]:
    """顺序执行任务列表，支持暂停与取消，返回 (成功数, 错误信息列表)。"""
    succeeded = 0
    errors: list[str] = []
    controller.progress(0, len(items), "准备中")
    for index, item in enumerate(items, start=1):
        controller.wait_if_paused()
        if controller.cancelled:
            break
        controller.progress(index, len(items), str(item))
        try:
            handler(item)
            succeeded += 1
        except Exception as exc:
            errors.append(f"{item}: {exc}")
    controller.state.running = False
    return succeeded, errors


class Scheduler:
    """定时同步：注册 (间隔秒, 回调函数)，后台守护线程按间隔触发。"""

    def __init__(self):
        self._jobs: list[tuple[int, Callable, float]] = []
        self._running = False
        self._thread: threading.Thread | None = None

    def add(self, interval_seconds: int, callback: Callable) -> None:
        self._jobs.append((max(interval_seconds, 10), callback, 0.0))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="diffscope-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            now = time.monotonic()
            updated = []
            for interval, callback, last in self._jobs:
                if now - last >= interval:
                    try:
                        callback()
                    except Exception:
                        pass
                    updated.append((interval, callback, now))
                else:
                    updated.append((interval, callback, last))
            self._jobs = updated
            time.sleep(1)


# ---------------------------------------------------------------- 外部集成
def shell_commands() -> list[str]:
    """生成 Windows 右键菜单集成命令（只生成，不自动修改注册表）。"""
    exe = Path(__file__).resolve().parents[2] / "run.bat"
    return [
        f'reg add "HKCU\\Software\\Classes\\*\\shell\\{APP_NAME}" /ve /d "用 {APP_NAME} 比较" /f',
        f'reg add "HKCU\\Software\\Classes\\*\\shell\\{APP_NAME}\\command" /ve /d "\\"{exe}\\" \\"%1\\"" /f',
        f'reg add "HKCU\\Software\\Classes\\Directory\\shell\\{APP_NAME}" /ve /d "用 {APP_NAME} 比较" /f',
        f'reg add "HKCU\\Software\\Classes\\Directory\\shell\\{APP_NAME}\\command" /ve /d "\\"{exe}\\" \\"%1\\"" /f',
    ]


def install_shell_integration() -> bool:
    """按需注册右键菜单；失败返回 False，不抛异常打断主流程。"""
    if os.name != "nt":
        return False
    import winreg

    exe = Path(__file__).resolve().parents[2] / "run.bat"
    try:
        for area in ("*", "Directory"):
            key_path = f"Software\\Classes\\{area}\\shell\\{APP_NAME}\\command"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValue(key, "", winreg.REG_SZ, f'"{exe}" --compare-with "%1"')
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{area}\\shell\\{APP_NAME}") as key:
                winreg.SetValue(key, "", winreg.REG_SZ, f"用 {APP_NAME} 比较")
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(exe))
        return True
    except OSError:
        return False


def vcs_config_commands(exe: str | Path) -> dict[str, list[str]]:
    """生成把本工具配置为 Git / SVN 外部 diff 与 merge 工具的命令。"""
    exe = str(exe)
    return {
        "git": [
            'git config --global diff.tool diffscope',
            f'git config --global difftool.diffscope.cmd \'"{exe}" --diff "$LOCAL" "$REMOTE"\'',
            'git config --global merge.tool diffscope',
            f'git config --global mergetool.diffscope.cmd \'"{exe}" --merge "$BASE" "$LOCAL" "$REMOTE" "$MERGED"\'',
            'git config --global mergetool.diffscope.trustExitCode true',
        ],
        "svn": [
            f'# ~/.subversion/config: diff-cmd = "{exe}" --diff',
            f'# ~/.subversion/config: merge-tool-cmd = "{exe}" --merge',
        ],
    }


def check_update(url: str = "") -> str:
    """可选的版本检查：未配置 URL 时返回本地版本信息，不联网。

    url 支持两种形式：
    - GitHub 仓库 "owner/repo"：读最新 Release 的 tag 作为版本号
    - JSON 地址：{"version": "x.y.z"} 或 {"latest": "x.y.z"}
    """
    if not url:
        return f"{APP_NAME} {__version__}（未配置更新源）"
    import urllib.request

    def fetch(target: str) -> str:
        with urllib.request.urlopen(target, timeout=6) as response:
            return response.read().decode("utf-8")

    cleaned = url.strip()
    if "/" in cleaned and "://" not in cleaned and " " not in cleaned:
        # GitHub 仓库形式：owner/repo
        try:
            data = json.loads(fetch(f"https://api.github.com/repos/{cleaned}/releases/latest"))
            latest = str(data.get("tag_name") or data.get("name") or "").lstrip("v")
            return f"发现新版本 {latest}" if latest and latest != __version__ else f"已是最新版本 {__version__}"
        except Exception as exc:
            return f"更新检查失败：{exc}"
    try:
        data = json.loads(fetch(cleaned))
        latest = str(data.get("version") or data.get("latest") or "")
        return f"发现新版本 {latest}" if latest and latest != __version__ else f"已是最新版本 {__version__}"
    except Exception as exc:
        return f"更新检查失败：{exc}"
