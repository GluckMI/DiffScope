"""文件与路径操作：路径安全、文本读写、编码探测、哈希、回收站删除。"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import (
    DecodeFailure,
    ReadFailure,
    WriteFailure,
    map_os_error,
)

MAX_TEXT_BYTES = 64 * 1024 * 1024
CHUNK = 1024 * 1024
_ENCODINGS = ("utf-8", "gb18030", "big5", "cp1252")


@dataclass
class TextDocument:
    path: Path
    text: str
    encoding: str
    newline: str
    size: int

    @property
    def name(self) -> str:
        return self.path.name


def format_size(size: int) -> str:
    if size < 0:
        return "-"
    if size < 1024:
        return f"{size} B"
    units = ("KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{size} B"


def format_time(mtime_ns: int) -> str:
    if not mtime_ns:
        return "-"
    try:
        return datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "-"


def decode_bytes(data: bytes, path: str | Path = "") -> tuple[str, str]:
    """按 BOM 与常见编码探测解码字节串，供文本与远程数据源复用。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(b"\xff\xfe\x00\x00"):
        return data[4:].decode("utf-32-le"), "utf-32-le"
    if data.startswith(b"\x00\x00\xfe\xff"):
        return data[4:].decode("utf-32-be"), "utf-32-be"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    for enc in _ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    try:
        return data.decode("latin-1"), "latin-1"
    except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 不会失败
        raise DecodeFailure("无法识别文件编码，不能按文本方式比较。", str(exc), path) from exc


def _detect_newline(text: str) -> str:
    crlf = text.find("\r\n")
    lf = text.find("\n")
    cr = text.find("\r")
    if crlf >= 0 and (lf < 0 or crlf <= lf):
        return "\r\n"
    if lf >= 0:
        return "\n"
    if cr >= 0:
        return "\r"
    return "\n"


_CONTROL_CODES = tuple(b for b in range(32) if b not in (9, 10, 13))


def _control_byte(code: int) -> bytes:
    return bytes((code,))


def read_text(path: Path, max_bytes: int = MAX_TEXT_BYTES) -> TextDocument:
    """安全读取文本，返回编码与换行风格，供无损回写使用。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise map_os_error(path, exc, "读取") from exc
    if size > max_bytes:
        raise DecodeFailure(
            f"文件过大（{format_size(size)}），超过文本比较上限 {format_size(max_bytes)}。"
            "请在文件夹模式下按大小/哈希比较，或拆分文件后重试。",
            path=path,
        )
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise map_os_error(path, exc, "读取") from exc

    text, encoding = decode_bytes(data, path)
    if "\x00" in text:
        raise DecodeFailure(
            "检测到二进制内容（含 NUL 字节），无法按文本方式显示与合并。"
            "可在文件夹模式中查看其差异状态并执行同步。",
            path=path,
        )
    # 控制字符统计走字节层（C 速度）；UTF-16/32 的字节流含交错 NUL，退回文本扫描
    enc_low = encoding.lower().replace("-", "").replace("_", "")
    if enc_low.startswith(("utf16", "utf32", "ucs2", "ucs4")):
        control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t")
    else:
        control = sum(data.count(_control_byte(b)) for b in _CONTROL_CODES)
    if control > max(16, len(text) // 100):
        raise DecodeFailure(
            "文件含大量不可打印字符，可能是二进制文件，无法按文本方式显示与合并。",
            path=path,
        )
    return TextDocument(path, text, encoding, _detect_newline(text), size)


def hash_file(path: Path, limit: int | None = None) -> str:
    """计算内容指纹。超过 limit 时取首尾片段，兼顾速度与准确性。"""
    digest = hashlib.sha256()
    try:
        size = path.stat().st_size
        digest.update(str(size).encode("ascii"))
        with open(path, "rb") as handle:
            if limit and size > limit:
                digest.update(handle.read(limit // 2))
                handle.seek(-(limit // 2), os.SEEK_END)
                digest.update(handle.read(limit // 2))
            else:
                while True:
                    block = handle.read(CHUNK)
                    if not block:
                        break
                    digest.update(block)
    except OSError as exc:
        raise map_os_error(path, exc, "读取") from exc
    return digest.hexdigest()


def write_text_atomic(
    path: Path,
    text: str,
    encoding: str,
    newline: str = "\n",
    normalize_newline: bool = True,
) -> None:
    """原子写入文本：同目录临时文件 + os.replace，失败时保留原文件。"""
    if normalize_newline:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
    try:
        data = text.encode(encoding)
    except (UnicodeEncodeError, LookupError) as exc:
        raise WriteFailure(
            f"文本无法按原编码 {encoding} 保存，请改用 UTF-8 或另存副本。", str(exc), path
        ) from exc

    parent = path.parent
    if not parent.is_dir():
        raise WriteFailure("目标文件夹不存在，无法保存。", path=str(parent))
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=parent, prefix=".diffscope-", suffix=".tmp"
        ) as handle:
            tmp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = ""
    except OSError as exc:
        err = map_os_error(path, exc, "写入")
        raise WriteFailure(err.message, err.detail, err.path) from exc
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def is_within(path: Path, root: Path) -> bool:
    """判断路径是否位于 root 之内，防止越界写入。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def safe_join(root: Path, rel: str) -> Path:
    """把相对路径安全地拼接到 root 下。"""
    clean = (rel or "").replace("\\", "/").strip("/")
    candidate = (root / clean).resolve() if clean else root.resolve()
    if not is_within(candidate, root):
        raise WriteFailure("路径超出对比根目录，操作已被拒绝。", path=str(candidate))
    return candidate


def backup_root() -> Path:
    from .services import config_dir

    return config_dir() / "trash"


def _move_to_backup(path: Path) -> Path:
    target_dir = backup_root()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    target = target_dir / f"{stamp}-{path.name}"
    counter = 1
    while target.exists():
        target = target_dir / f"{stamp}-{counter}-{path.name}"
        counter += 1
    shutil.move(str(path), str(target))
    return target


def _windows_recycle(path: Path) -> bool:  # pragma: no cover - 依赖 Windows Shell
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = 3  # FO_DELETE
        op.pFrom = str(path) + "\x00\x00"
        op.pTo = None
        op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


def remove_path(path: Path) -> str:
    """删除文件/目录。优先回收站；不可用或失败时移动到备份目录。返回处置说明。"""
    if _windows_recycle(path):
        return "已移入回收站"
    target = _move_to_backup(path)
    return f"已移至备份目录：{target}"


def copy_path(src: Path, dst: Path) -> None:
    """复制文件或目录，失败时统一抛 WriteFailure。"""
    try:
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=False)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    except OSError as exc:
        err = map_os_error(dst, exc, "写入")
        raise WriteFailure(err.message, err.detail, err.path) from exc


def read_error_message(exc: Exception) -> str:
    """把任意异常转成单行可读信息，用于节点备注与报告。"""
    if isinstance(exc, ReadFailure):
        return exc.message
    return str(exc) or exc.__class__.__name__
