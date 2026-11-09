"""统一的可读错误类型：界面层只依赖这些类型给出明确提示。"""

from __future__ import annotations

import os
from pathlib import Path

from .models import PathKind


class DiffScopeError(Exception):
    """所有面向用户的异常基类。"""

    title = "操作失败"

    def __init__(self, message: str, detail: str = "", path: str | os.PathLike | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.path = str(path) if path is not None else ""

    def user_message(self) -> str:
        parts = [self.message]
        if self.path:
            parts.append(f"路径：{self.path}")
        if self.detail:
            parts.append(f"原因：{self.detail}")
        return "\n".join(parts)

    def __str__(self) -> str:  # pragma: no cover - 仅日志使用
        return self.user_message()


class InvalidPathError(DiffScopeError):
    title = "路径无效"


class PathNotFoundError(DiffScopeError):
    title = "路径不存在"


class PathTypeError(DiffScopeError):
    title = "类型不匹配"


class AccessDeniedError(DiffScopeError):
    title = "无访问权限"


class ReadFailure(DiffScopeError):
    title = "内容读取失败"


class DecodeFailure(ReadFailure):
    title = "无法按文本方式读取"


class WriteFailure(DiffScopeError):
    title = "写入失败"


class SessionError(DiffScopeError):
    title = "会话文件错误"


class Cancelled(Exception):
    """用户取消后台任务。"""


def normalize_path(raw: str) -> Path:
    """清理用户输入的路径（引号、环境变量、~），不做存在性判断。"""
    text = (raw or "").strip().strip('"').strip("'").strip()
    if not text:
        raise InvalidPathError("路径为空，请先选择要比较的文件或文件夹。")
    text = os.path.expandvars(os.path.expanduser(text))
    try:
        path = Path(text)
        # 触发一次底层路径解析，暴露非法字符等问题
        os.fspath(path)
    except (OSError, ValueError, TypeError) as exc:
        raise InvalidPathError(f"路径格式不合法：{text}", str(exc), text) from exc
    return path


def validate_path(
    raw: str | os.PathLike,
    expect: PathKind = PathKind.ANY,
) -> Path:
    """校验路径存在、类型正确且可读，返回绝对路径。

    expect=ANY 时允许文件或目录；比较两侧类型是否一致由调用方判断。
    """
    path = normalize_path(str(raw))
    absolute = Path(os.path.abspath(path))
    if not absolute.exists():
        raise PathNotFoundError(
            "路径不存在，请检查拼写、盘符或网络位置后重试。", path=str(absolute)
        )

    is_dir = absolute.is_dir()
    if expect is PathKind.FILE and is_dir:
        raise PathTypeError("此处需要选择文件，但所选路径是文件夹。", path=str(absolute))
    if expect is PathKind.DIR and not is_dir:
        raise PathTypeError("此处需要选择文件夹，但所选路径是文件。", path=str(absolute))

    try:
        if is_dir:
            with os.scandir(absolute):
                pass
        else:
            if not os.access(absolute, os.R_OK):
                raise PermissionError(13, "文件不可读")
            with open(absolute, "rb") as handle:
                handle.read(1)
    except OSError as exc:
        raise map_os_error(absolute, exc, "访问") from exc
    return absolute


def map_os_error(path: str | os.PathLike, exc: OSError, action: str = "读取") -> DiffScopeError:
    """把底层 OSError 翻译成用户能理解的提示。"""
    text = str(path)
    reason = exc.strerror or str(exc)
    code = getattr(exc, "errno", None)
    if isinstance(exc, PermissionError) or code in (13, 5):
        return AccessDeniedError(
            f"无访问权限，无法{action}该路径。请以有权限的账户运行，或检查文件/文件夹安全属性。",
            reason,
            text,
        )
    if isinstance(exc, FileNotFoundError) or code == 2:
        return PathNotFoundError(f"路径不存在或已被移动，无法{action}。", reason, text)
    if isinstance(exc, NotADirectoryError) or code == 20:
        return PathTypeError(f"路径中的某一级不是文件夹，无法{action}。", reason, text)
    if isinstance(exc, IsADirectoryError) or code == 21:
        return PathTypeError(f"该路径是文件夹而不是文件，无法按文件方式{action}。", reason, text)
    if code in (32, 33):
        return ReadFailure(f"文件被其它程序占用，无法{action}。请关闭占用程序后重试。", reason, text)
    if code in (28,):
        return WriteFailure(f"磁盘空间不足，无法{action}。", reason, text)
    return ReadFailure(f"{action}路径时发生系统错误。", reason, text)
