#!/usr/bin/env python3
"""DiffScope 启动入口。

负责依赖检查与解释器探测：若当前解释器没有 PySide6，自动寻找本机
其它可用的 Python 解释器并重新启动；找不到时给出明确的安装指引。
"""

from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

# PyInstaller 冻结模式下 __file__ 指向临时解包目录，改用 exe 所在目录
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
BOOT_MARKER = "DIFFSCOPE_BOOTSTRAPPED"
MIN_QT = (6, 6)


def _qapp_probe() -> str:
    return (
        "import sys, PySide6, PySide6.QtCore, PySide6.QtGui, PySide6.QtWidgets; "
        "print(PySide6.__version__)"
    )


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.strip().split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _probe(exe: str) -> bool:
    """检查解释器是否带有满足版本要求的 PySide6。"""
    try:
        out = subprocess.run(
            [exe, "-c", _qapp_probe()],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    return _version_tuple(out.stdout) >= MIN_QT


def _candidates() -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(path: str | None) -> None:
        if not path:
            return
        p = os.path.normcase(os.path.abspath(path))
        if p in seen or not os.path.isfile(p):
            return
        seen.add(p)
        found.append(p)

    add(sys.executable)

    # py 启动器解析出的实际解释器路径
    launcher = shutil.which("py")
    if launcher:
        for args in (["-3", "-c"], ["-0", "-c"]):
            try:
                out = subprocess.run(
                    [launcher, *args, "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if out.returncode == 0:
                    add(out.stdout.strip().splitlines()[-1])
            except (OSError, subprocess.SubprocessError, IndexError):
                pass

    for name in ("python", "python3"):
        add(shutil.which(name))

    patterns = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe"),
        r"C:\Python3*\python.exe",
        r"D:\Python3*\python.exe",
        r"C:\Program Files\Python3*\python.exe",
        r"D:\Program Files\Python3*\python.exe",
        r"D:\Programs\Python\Python3*\python.exe",
        os.path.expandvars(r"%USERPROFILE%\AppData\Roaming\uv\python\*\python.exe"),
        "/usr/bin/python3*",
        "/usr/local/bin/python3*",
    ]
    for pattern in patterns:
        for path in sorted(glob.glob(pattern), reverse=True):
            add(path)
    return found


def _launch_app() -> int:
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from app.main import main as app_main

    return app_main()


def _install_hint() -> str:
    return (
        "未找到带有 PySide6 的 Python 解释器。\n\n"
        "本应用仅依赖 PySide6（Qt for Python）。请任选一种方式后重试：\n"
        f"  1) 在当前环境执行：  {sys.executable} -m pip install \"PySide6>=6.6\"\n"
        "  2) 使用装有 PySide6 的 Python 运行：  <python.exe> run.py\n"
        "  3) 查看 requirements.txt\n\n"
        f"已探测的解释器：\n  " + "\n  ".join(_candidates())
    )


HELP = """DiffScope · 文件与文件夹对比工具

启动：
    python run.py              自动探测带 PySide6 的解释器并启动
    run.bat                    Windows 双击启动
    python run.py --check      诊断本机 Python / PySide6 环境
    python run.py --help       显示本说明

依赖：
    Python 3.10+ 与 PySide6>=6.6（见 requirements.txt），其余均为标准库。
    安装：python -m pip install -r requirements.txt

命令行（不启动界面）：
    python run.py --diff 左侧 右侧 [--report 报告.html] [--json]
    python run.py --script 脚本.txt [--allow-write]
    python run.py --merge 祖先 左 右 输出 [--favor left|right|base]
    python run.py --snapshot 目录 快照.json
    python run.py --compare 左侧 右侧      预填路径启动界面
    退出码：0 无差异 / 1 有差异 / 2 错误 / 3 合并冲突

测试：
    python -m unittest discover -s tests -v

主要快捷键：
    Ctrl+O 打开对比   Ctrl+R 重新比较   Ctrl+S 保存修改
    F7 / F8 上下差异  Ctrl+F 查找       F3 下一个匹配   Ctrl+H 查找替换
    Ctrl+Left / Ctrl+Right 合并当前差异块  Ctrl+B 书签  Ctrl+T 工作区
    Ctrl+Shift+S 保存会话    Ctrl+Shift+O 打开会话

图形界面「扩展」菜单：数据源、比较规则、对齐覆盖、同步模式、三向合并、
表格/十六进制/图片/标签/注册表查看器、报告、脚本、工作区、设置、浅色主题、帮助。
"""


def main() -> int:
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(HELP)
        return 0
    if "--check" in argv:
        print(f"Python: {sys.executable}")
        for exe in _candidates():
            print(f"  {'OK  ' if _probe(exe) else 'NO  '}{exe}")
        return 0

    if importlib.util.find_spec("PySide6") is not None:
        try:
            return _launch_app()
        except ImportError as exc:
            print(f"PySide6 组件加载失败：{exc}", file=sys.stderr)
            print(_install_hint(), file=sys.stderr)
            return 2

    if os.environ.get(BOOT_MARKER):
        print(_install_hint(), file=sys.stderr)
        return 2

    current = os.path.normcase(os.path.abspath(sys.executable))
    for exe in _candidates():
        if os.path.normcase(os.path.abspath(exe)) == current:
            continue
        if not _probe(exe):
            continue
        env = os.environ.copy()
        env[BOOT_MARKER] = "1"
        print(f"[DiffScope] 使用解释器：{exe}")
        try:
            return subprocess.call([exe, str(Path(__file__).resolve()), *argv], env=env, cwd=ROOT)
        except OSError as exc:
            print(f"启动失败：{exc}", file=sys.stderr)
            return 2

    print(_install_hint(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
