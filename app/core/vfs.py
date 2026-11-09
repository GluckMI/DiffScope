"""统一虚拟文件系统：本地、压缩包、快照、注册表与远程数据源的公共接口。

compare / sync 只依赖 VfsProvider，不再直接调用 os.scandir，
因此新增数据源无需改动比较引擎。
"""

from __future__ import annotations

import abc
import io
import json
import os
import shutil
import tarfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .errors import DiffScopeError, PathNotFoundError, WriteFailure, map_os_error
from .fs_utils import remove_path, write_text_atomic


class VfsError(DiffScopeError):
    title = "数据源访问失败"


class VfsUnsupported(VfsError):
    title = "数据源不支持此操作"


@dataclass
class VfsEntry:
    name: str
    rel: str
    is_dir: bool = False
    size: int = 0
    mtime_ns: int = 0
    is_symlink: bool = False
    attrs: dict = field(default_factory=dict)


def _norm(rel: str) -> str:
    return (rel or "").replace("\\", "/").strip("/")


def _join(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


class VfsProvider(abc.ABC):
    """所有数据源的基类。只读源只需实现 list_dir / read_bytes。"""

    scheme = "local"
    readonly = False

    def __init__(self, label: str = ""):
        self.label = label or self.scheme

    # ---- 生命周期 ----
    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_args):
        self.close()

    # ---- 读 ----
    @abc.abstractmethod
    def list_dir(self, rel: str = "") -> list[VfsEntry]:
        ...

    @abc.abstractmethod
    def read_bytes(self, rel: str) -> bytes:
        ...

    def stat(self, rel: str) -> VfsEntry:
        rel = _norm(rel)
        parent, _, name = rel.rpartition("/")
        for entry in self.list_dir(parent):
            if entry.rel == rel:
                return entry
        raise PathNotFoundError("条目在数据源中不存在。", path=rel)

    def exists(self, rel: str) -> bool:
        try:
            self.stat(rel)
            return True
        except DiffScopeError:
            return False

    def display_path(self, rel: str = "") -> str:
        return f"{self.label}/{_norm(rel)}" if rel else self.label

    # ---- 写（可选） ----
    def _writable(self) -> None:
        if self.readonly:
            raise VfsUnsupported(f"{self.label} 是只读数据源，不能写入。")

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        raise VfsUnsupported("该数据源未实现写入。")

    def mkdir(self, rel: str) -> None:
        self._writable()
        raise VfsUnsupported("该数据源未实现创建目录。")

    def remove(self, rel: str) -> None:
        self._writable()
        raise VfsUnsupported("该数据源未实现删除。")

    def move(self, src: str, dst: str) -> None:
        self._writable()
        data = self.read_bytes(src)
        self.write_bytes(dst, data)
        self.remove(src)

    def set_mtime(self, rel: str, mtime_ns: int) -> None:
        self._writable()
        raise VfsUnsupported("该数据源不支持设置修改时间。")

    def set_attrs(self, rel: str, attrs: dict) -> None:
        self._writable()
        raise VfsUnsupported("该数据源不支持修改属性。")

    def flush(self) -> None:
        pass


class LocalVfs(VfsProvider):
    """本地磁盘 / UNC 网络路径。"""

    scheme = "local"

    def __init__(self, root: str | Path):
        self.root = Path(os.path.abspath(os.fspath(root)))
        super().__init__(str(self.root))

    def connect(self) -> None:
        if not self.root.exists():
            raise PathNotFoundError("路径不存在，请检查拼写、盘符或网络位置。", path=str(self.root))

    def _path(self, rel: str) -> Path:
        clean = _norm(rel)
        target = (self.root / clean) if clean else self.root
        try:
            target.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise VfsError("路径超出数据源根目录，已拒绝。", path=str(target)) from exc
        return target

    def list_dir(self, rel: str = "") -> list[VfsEntry]:
        target = self._path(rel)
        if not target.is_dir():
            raise VfsError("路径不是文件夹，无法列出内容。", path=str(target))
        out: list[VfsEntry] = []
        try:
            with os.scandir(target) as entries:
                for item in entries:
                    try:
                        st = item.stat(follow_symlinks=False)
                        out.append(
                            VfsEntry(
                                name=item.name,
                                rel=_join(_norm(rel), item.name),
                                is_dir=item.is_dir(follow_symlinks=False),
                                size=0 if item.is_dir(follow_symlinks=False) else st.st_size,
                                mtime_ns=st.st_mtime_ns,
                                is_symlink=item.is_symlink(),
                                attrs={"mode": st.st_mode, "dos_attributes": getattr(st, "st_file_attributes", 0)},
                            )
                        )
                    except OSError as exc:
                        out.append(VfsEntry(item.name, _join(_norm(rel), item.name), attrs={"error": str(exc)}))
        except OSError as exc:
            raise VfsError("无法列出目录内容，可能是权限不足或路径已失效。", exc.strerror or str(exc), target) from exc
        return out

    def read_bytes(self, rel: str) -> bytes:
        target = self._path(rel)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise map_os_error(target, exc, "读取") from exc

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        target = self._path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(data)
        except OSError as exc:
            raise map_os_error(target, exc, "写入") from exc

    def mkdir(self, rel: str) -> None:
        self._writable()
        self._path(rel).mkdir(parents=True, exist_ok=True)

    def remove(self, rel: str) -> None:
        self._writable()
        target = self._path(rel)
        if target.exists():
            remove_path(target)

    def move(self, src: str, dst: str) -> None:
        self._writable()
        try:
            shutil.move(str(self._path(src)), str(self._path(dst)))
        except OSError as exc:
            raise WriteFailure("移动/重命名失败。", exc.strerror or str(exc), src) from exc

    def set_mtime(self, rel: str, mtime_ns: int) -> None:
        self._writable()
        target = self._path(rel)
        os.utime(target, ns=(mtime_ns, mtime_ns))

    def set_attrs(self, rel: str, attrs: dict) -> None:
        self._writable()
        target = self._path(rel)
        if "mode" in attrs:
            os.chmod(target, int(attrs["mode"]))
        if "dos_attributes" in attrs and os.name == "nt":
            import subprocess

            flags = {1: "+R", 2: "+H", 4: "+S"}
            value = int(attrs["dos_attributes"])
            for bit, flag in flags.items():
                subprocess.run(
                    ["attrib", ("-" if not value & bit else "+") + flag[1:], str(target)],
                    capture_output=True,
                    check=False,
                )

    def display_path(self, rel: str = "") -> str:
        return str(self._path(rel))


class _ArchiveVfs(VfsProvider):
    """压缩包免解压比较：把归档条目映射为目录树，支持写回 zip。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        super().__init__(str(self.path))
        self._entries: dict[str, VfsEntry] = {}
        self._data: dict[str, bytes] = {}
        self._deleted: set[str] = set()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        raise NotImplementedError

    def _member_name(self, rel: str) -> str:
        return _norm(rel)

    def list_dir(self, rel: str = "") -> list[VfsEntry]:
        parent = _norm(rel)
        prefix = parent + "/" if parent else ""
        names: dict[str, VfsEntry] = {}
        for key, entry in self._entries.items():
            if key in self._deleted or not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            if not rest:
                continue
            first, _, tail = rest.partition("/")
            child_rel = _join(parent, first)
            if tail:
                names.setdefault(first, VfsEntry(first, child_rel, is_dir=True))
            elif first not in names or not names[first].is_dir:
                names[first] = entry
        for key in self._data:
            if key in self._deleted or not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            if not rest:
                continue
            first, _, tail = rest.partition("/")
            child_rel = _join(parent, first)
            if tail:
                names.setdefault(first, VfsEntry(first, child_rel, is_dir=True))
            else:
                names[first] = VfsEntry(first, child_rel, size=len(self._data[key]), mtime_ns=int(time.time_ns()))
        return sorted(names.values(), key=lambda e: (not e.is_dir, e.name))

    def read_bytes(self, rel: str) -> bytes:
        key = self._member_name(rel)
        if key in self._deleted:
            raise PathNotFoundError("压缩包条目已被删除。", path=rel)
        if key in self._data:
            return self._data[key]
        if key in self._entries:
            return self._raw(key)
        raise PathNotFoundError("压缩包中不存在该条目。", path=rel)

    def _raw(self, key: str) -> bytes:
        raise NotImplementedError

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        self._data[_norm(rel)] = data
        self._deleted.discard(_norm(rel))
        self._dirty = True

    def remove(self, rel: str) -> None:
        self._writable()
        key = _norm(rel)
        self._deleted.add(key)
        self._dirty = True

    def mkdir(self, rel: str) -> None:
        self._writable()

    def stat(self, rel: str) -> VfsEntry:
        key = _norm(rel)
        if key in self._deleted:
            raise PathNotFoundError("压缩包条目已被删除。", path=rel)
        if key in self._data:
            return VfsEntry(key.rsplit("/", 1)[-1], key, size=len(self._data[key]))
        if key in self._entries:
            return self._entries[key]
        for entry in self.list_dir(key.rpartition("/")[0]):
            if entry.rel == key:
                return entry
        raise PathNotFoundError("压缩包中不存在该条目。", path=rel)


class ZipVfs(_ArchiveVfs):
    scheme = "zip"

    def _load(self) -> None:
        try:
            with zipfile.ZipFile(self.path) as archive:
                for info in archive.infolist():
                    name = info.filename.replace("\\", "/").strip("/")
                    if not name:
                        continue
                    stamp = time.mktime(tuple(info.date_time) + (0, 0, -1))
                    self._entries[name] = VfsEntry(
                        name.rsplit("/", 1)[-1], name, is_dir=name.endswith("/"),
                        size=0 if name.endswith("/") else info.file_size,
                        mtime_ns=int(stamp * 1e9), attrs={"crc32": info.CRC},
                    )
        except (OSError, zipfile.BadZipFile) as exc:
            raise VfsError("无法读取 ZIP 压缩包。", str(exc), self.path) from exc

    def _raw(self, key: str) -> bytes:
        try:
            with zipfile.ZipFile(self.path) as archive:
                return archive.read(key)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise VfsError("无法读取压缩包条目。", str(exc), key) from exc

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            with zipfile.ZipFile(self.path) as source:
                original = {
                    i.filename.replace("\\", "/").strip("/"): source.read(i)
                    for i in source.infolist()
                    if not i.is_dir()
                }
            original.update({k: v for k, v in self._data.items() if k not in self._deleted})
            for key in self._deleted:
                original.pop(key, None)
            with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as target:
                for name, data in original.items():
                    target.writestr(name, data)
        except OSError as exc:
            raise WriteFailure("写回压缩包失败。", str(exc), self.path) from exc
        self._dirty = False


class TarVfs(_ArchiveVfs):
    scheme = "tar"

    def _load(self) -> None:
        try:
            with tarfile.open(self.path) as archive:
                for info in archive.getmembers():
                    name = info.name.replace("\\", "/").strip("/")
                    if not name:
                        continue
                    self._entries[name] = VfsEntry(
                        name.rsplit("/", 1)[-1], name, is_dir=info.isdir(),
                        size=0 if info.isdir() else info.size,
                        mtime_ns=int(info.mtime * 1e9), is_symlink=info.issym(),
                    )
        except (OSError, tarfile.TarError) as exc:
            raise VfsError("无法读取 TAR 压缩包。", str(exc), self.path) from exc

    def _raw(self, key: str) -> bytes:
        try:
            with tarfile.open(self.path) as archive:
                handle = archive.extractfile(key)
                return handle.read() if handle else b""
        except (OSError, KeyError, tarfile.TarError) as exc:
            raise VfsError("无法读取压缩包条目。", str(exc), key) from exc


class SnapshotVfs(VfsProvider):
    """文件系统快照：只保存元数据，不含内容，用于离线比较与备份校验。"""

    scheme = "snapshot"
    readonly = True

    def __init__(self, path: str | Path):
        self.path = Path(path)
        super().__init__(str(self.path))
        self._entries: dict[str, VfsEntry] = {}
        self.root_label = ""
        self.connect()  # 快照是静态文件，创建时即载入

    def connect(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VfsError("快照文件无法读取或已损坏。", str(exc), self.path) from exc
        self.root_label = data.get("root", "")
        self.label = f"快照: {self.root_label}"
        for item in data.get("entries", []):
            rel = _norm(item.get("rel", ""))
            if not rel:
                continue
            self._entries[rel] = VfsEntry(
                rel.rsplit("/", 1)[-1], rel, is_dir=bool(item.get("is_dir")),
                size=int(item.get("size", 0)), mtime_ns=int(item.get("mtime_ns", 0)),
                is_symlink=bool(item.get("is_symlink")), attrs=item.get("attrs", {}),
            )

    def list_dir(self, rel: str = "") -> list[VfsEntry]:
        parent = _norm(rel)
        prefix = parent + "/" if parent else ""
        out: dict[str, VfsEntry] = {}
        for key, entry in self._entries.items():
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            if not rest:
                continue
            first, _, tail = rest.partition("/")
            child_rel = _join(parent, first)
            if tail:
                out.setdefault(first, VfsEntry(first, child_rel, is_dir=True))
            else:
                out[first] = entry
        return sorted(out.values(), key=lambda e: (not e.is_dir, e.name))

    def read_bytes(self, rel: str) -> bytes:
        raise VfsUnsupported("快照不包含文件内容，不能按内容比较；请改用大小与时间条件。", path=rel)

    def stat(self, rel: str) -> VfsEntry:
        key = _norm(rel)
        if key in self._entries:
            return self._entries[key]
        for entry in self.list_dir(key.rpartition("/")[0]):
            if entry.rel == key:
                return entry
        raise PathNotFoundError("快照中不存在该条目。", path=rel)


def create_snapshot(root: str | Path, output: str | Path, excludes: Iterable[str] = ()) -> Path:
    """把目录树元数据保存为快照文件（不含文件内容）。"""
    import fnmatch

    source = LocalVfs(root)
    source.connect()
    entries: list[dict] = []

    def walk(rel: str) -> None:
        for entry in source.list_dir(rel):
            if any(fnmatch.fnmatch(entry.name, pattern) for pattern in excludes):
                continue
            entries.append({
                "rel": entry.rel, "is_dir": entry.is_dir, "size": entry.size,
                "mtime_ns": entry.mtime_ns, "is_symlink": entry.is_symlink,
                "attrs": {k: v for k, v in entry.attrs.items() if isinstance(v, (int, str))},
            })
            if entry.is_dir and not entry.is_symlink:
                walk(entry.rel)

    walk("")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        target,
        json.dumps({"root": str(root), "created": time.time(), "entries": entries},
                   ensure_ascii=False, indent=2),
        "utf-8", "\n",
    )
    return target


class RegistryVfs(VfsProvider):
    """Windows 注册表：键映射为目录，值映射为文件；.reg 导出文件映射为文本。"""

    scheme = "reg"
    readonly = False

    _ROOTS = {
        "HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE",
        "HKCR": "HKEY_CLASSES_ROOT", "HKU": "HKEY_USERS", "HKCC": "HKEY_CURRENT_CONFIG",
    }

    def __init__(self, spec: str):
        super().__init__(spec)
        spec = spec.replace("reg://", "").strip("/")
        head, _, tail = spec.partition("/")
        self.hive = head.upper() or "HKCU"
        self.subkey = tail.replace("/", "\\")
        if self.hive not in self._ROOTS:
            raise VfsError(f"未知的注册表根键：{self.hive}（可用 {', '.join(self._ROOTS)}）")

    def _winreg(self):
        if os.name != "nt":
            raise VfsUnsupported("注册表比较仅在 Windows 上可用。")
        import winreg

        return winreg

    def _open(self, rel: str = "", write: bool = False):
        winreg = self._winreg()
        full = "\\".join(filter(None, [self.subkey, _norm(rel).replace("/", "\\")]))
        roots = {
            "HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCR": winreg.HKEY_CLASSES_ROOT, "HKU": winreg.HKEY_USERS,
            "HKCC": winreg.HKEY_CURRENT_CONFIG,
        }
        return winreg.OpenKeyEx(
            roots[self.hive], full, 0, winreg.KEY_WRITE | winreg.KEY_READ if write else winreg.KEY_READ
        )

    def list_dir(self, rel: str = "") -> list[VfsEntry]:
        winreg = self._winreg()
        out: list[VfsEntry] = []
        try:
            with self._open(rel) as key:
                index = 0
                while True:
                    try:
                        out.append(VfsEntry(winreg.EnumKey(key, index), _join(_norm(rel), winreg.EnumKey(key, index)), is_dir=True))
                        index += 1
                    except OSError:
                        break
                index = 0
                while True:
                    try:
                        name, value, kind = winreg.EnumValue(key, index)
                        out.append(VfsEntry(name or "(默认)", _join(_norm(rel), name or "(默认)"),
                                            size=len(str(value)), attrs={"kind": kind, "value": value}))
                        index += 1
                    except OSError:
                        break
        except OSError as exc:
            raise VfsError("无法读取注册表项。", str(exc), rel) from exc
        return out

    def read_bytes(self, rel: str) -> bytes:
        for entry in self.list_dir(_norm(rel).rpartition("/")[0]):
            if entry.rel == _norm(rel):
                value = entry.attrs.get("value", "")
                kind = entry.attrs.get("kind", 1)
                return f"[{self._ROOTS[self.hive]}\\{self.subkey}\\{_norm(rel).replace('/', '\\')}]\n" \
                       f"type={kind}\nvalue={value!r}\n".encode("utf-8")
        raise PathNotFoundError("注册表值不存在。", path=rel)

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        winreg = self._winreg()
        name = _norm(rel).rsplit("/", 1)[-1]
        parent = _norm(rel).rpartition("/")[0]
        text = data.decode("utf-8", "replace")
        value = text.split("value=", 1)[-1].strip() if "value=" in text else text
        try:
            with self._open(parent, write=True) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        except OSError as exc:
            raise VfsError("写入注册表失败。", str(exc), rel) from exc

    def remove(self, rel: str) -> None:
        self._writable()
        winreg = self._winreg()
        name = _norm(rel).rsplit("/", 1)[-1]
        parent = _norm(rel).rpartition("/")[0]
        try:
            with self._open(parent, write=True) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            with self._open(_norm(rel), write=True) as key:
                winreg.DeleteKey(key, "")


def open_vfs(spec: str, **options) -> VfsProvider:
    """按路径或 URL 方案创建数据源。

    支持：普通路径 / UNC、zip:、tar:、snapshot:、reg://、
    ftp://、ftps://、sftp://、webdav://、s3://、dropbox://、onedrive://、svn://、smb://
    """
    text = (spec or "").strip().strip('"')
    if not text:
        raise VfsError("数据源路径为空。")
    lower = text.lower()
    if lower.startswith("reg://"):
        return RegistryVfs(text)
    if lower.startswith("snapshot:"):
        return SnapshotVfs(text[len("snapshot:"):])
    if lower.startswith(("zip:", "jar:", "whl:")):
        return ZipVfs(text.partition(":")[2])
    if lower.startswith(("tar:", "tgz:")):
        return TarVfs(text.partition(":")[2])
    if lower.endswith((".zip", ".jar", ".whl", ".apk", ".epub")) and lower.startswith(("zip@", "archive@")):
        return ZipVfs(text.partition("@")[2])
    if lower.startswith(("ftp://", "ftps://", "sftp://", "webdav://", "dav://", "s3://",
                         "dropbox://", "onedrive://", "svn://", "smb://", "http://", "https://")):
        from .vfs_remote import open_remote_vfs

        return open_remote_vfs(text, **options)
    return LocalVfs(text)


def normalize_name(name: str, unicode_normalize: bool = True) -> str:
    """文件名对齐：可选的 Unicode 规范化，用于 OS X / Windows 文件名互比。"""
    return unicodedata.normalize("NFC", name) if unicode_normalize else name


__all__ = [
    "VfsError", "VfsUnsupported", "VfsEntry", "VfsProvider", "LocalVfs",
    "ZipVfs", "TarVfs", "SnapshotVfs", "RegistryVfs", "create_snapshot",
    "open_vfs", "normalize_name",
]
