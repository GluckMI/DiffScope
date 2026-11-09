"""远程数据源：FTP/FTPS、SFTP、WebDAV、S3、Dropbox、OneDrive、SVN、SMB。

全部使用标准库 + requests（可选），不引入 paramiko / boto3 等额外依赖。
需要凭证的数据源由调用方在 URL 或 options 中提供；连接失败时给出明确提示。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from .errors import PathNotFoundError
from .vfs import LocalVfs, VfsEntry, VfsError, VfsProvider, VfsUnsupported, _join, _norm

try:
    import requests
except ImportError:  # pragma: no cover - 环境依赖
    requests = None


def _http():
    if requests is None:
        raise VfsError("缺少 requests 库，无法访问网络数据源。请安装 requests 后重试。")
    return requests


def _parse_url(spec: str):
    parsed = urlparse(spec)
    return (
        parsed.scheme.lower(),
        unquote(parsed.username or ""),
        unquote(parsed.password or ""),
        parsed.hostname or "",
        parsed.port,
        unquote(parsed.path or "").lstrip("/"),
        {k: v[0] for k, v in __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(parsed.query).items()},
    )


class _CachedVfs(VfsProvider):
    def __init__(self, label: str):
        super().__init__(label)
        self._cache: dict[str, list[VfsEntry]] = {}

    def list_dir(self, rel: str = "") -> list[VfsEntry]:
        key = _norm(rel)
        if key not in self._cache:
            self._cache[key] = self._fetch_dir(key)
        return self._cache[key]

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        raise NotImplementedError

    def invalidate(self, rel: str = "") -> None:
        parent = _norm(rel).rpartition("/")[0]
        self._cache.pop(parent, None)
        self._cache.pop(_norm(rel), None)

    def stat(self, rel: str) -> VfsEntry:
        key = _norm(rel)
        if not key:
            return VfsEntry("", "", is_dir=True)
        parent = key.rpartition("/")[0]
        for entry in self.list_dir(parent):
            if entry.rel == key:
                return entry
        raise PathNotFoundError("远程数据源中不存在该条目。", path=rel)

    def exists(self, rel: str) -> bool:
        try:
            self.stat(rel)
            return True
        except VfsError:
            return False

    def display_path(self, rel: str = "") -> str:
        return f"{self.label}/{_norm(rel)}" if rel else self.label


# ---------------------------------------------------------------- FTP / FTPS
class FtpVfs(_CachedVfs):
    scheme = "ftp"

    def __init__(self, spec: str, **options):
        scheme, user, password, host, port, path, query = _parse_url(spec)
        self.secure = scheme == "ftps"
        self.user = options.get("user") or user or "anonymous"
        self.password = options.get("password") or password or "anonymous@"
        self.host = host
        self.port = port or (990 if scheme == "ftps" else 21)
        self.base = path
        self.ftp = None
        self.passive = options.get("passive", True)
        super().__init__(f"{scheme}://{host}/{path}")

    def connect(self) -> None:
        import ftplib

        try:
            cls = ftplib.FTP_TLS if self.secure else ftplib.FTP
            self.ftp = cls()
            self.ftp.connect(self.host, self.port, timeout=20)
            self.ftp.login(self.user, self.password)
            if self.secure:
                self.ftp.prot_p()
            self.ftp.set_pasv(self.passive)
            if self.base:
                self.ftp.cwd(self.base)
        except Exception as exc:
            raise VfsError("FTP 连接失败，请检查地址、端口、账号密码与被动模式设置。", str(exc), self.host) from exc

    def close(self) -> None:
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                pass
            self.ftp = None

    def _remote(self, rel: str) -> str:
        return "/".join(filter(None, [self.base, _norm(rel)]))

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        if self.ftp is None:
            self.connect()
        target = self._remote(rel)
        out: list[VfsEntry] = []
        try:
            try:
                for name, facts in self.ftp.mlsd(target):
                    if name in (".", ".."):
                        continue
                    is_dir = facts.get("type") == "dir"
                    stamp = facts.get("modify", "")
                    mtime = int(datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp() * 1e9) if stamp else 0
                    out.append(VfsEntry(name, _join(rel, name), is_dir=is_dir,
                                        size=0 if is_dir else int(facts.get("size", 0)), mtime_ns=mtime))
            except Exception:
                lines: list[str] = []
                self.ftp.retrlines(f"LIST {target}", lines.append)
                for line in lines:
                    parts = line.split(None, 8)
                    if len(parts) < 9:
                        continue
                    name = parts[-1]
                    is_dir = parts[0].startswith("d")
                    out.append(VfsEntry(name, _join(rel, name), is_dir=is_dir,
                                        size=0 if is_dir else int(parts[4]) if parts[4].isdigit() else 0))
        except Exception as exc:
            raise VfsError("无法列出 FTP 目录。", str(exc), target) from exc
        return out

    def read_bytes(self, rel: str) -> bytes:
        if self.ftp is None:
            self.connect()
        buffer = io.BytesIO()
        try:
            self.ftp.retrbinary(f"RETR {self._remote(rel)}", buffer.write)
        except Exception as exc:
            raise VfsError("FTP 下载失败。", str(exc), rel) from exc
        return buffer.getvalue()

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        if self.ftp is None:
            self.connect()
        try:
            self.ftp.storbinary(f"STOR {self._remote(rel)}", io.BytesIO(data))
        except Exception as exc:
            raise VfsError("FTP 上传失败。", str(exc), rel) from exc
        self.invalidate(rel)

    def mkdir(self, rel: str) -> None:
        self._writable()
        if self.ftp is None:
            self.connect()
        try:
            self.ftp.mkd(self._remote(rel))
        except Exception as exc:
            raise VfsError("FTP 创建目录失败。", str(exc), rel) from exc
        self.invalidate(rel)

    def remove(self, rel: str) -> None:
        self._writable()
        if self.ftp is None:
            self.connect()
        target = self._remote(rel)
        try:
            try:
                self.ftp.delete(target)
            except Exception:
                self.ftp.rmd(target)
        except Exception as exc:
            raise VfsError("FTP 删除失败。", str(exc), rel) from exc
        self.invalidate(rel)

    def move(self, src: str, dst: str) -> None:
        self._writable()
        if self.ftp is None:
            self.connect()
        try:
            self.ftp.rename(self._remote(src), self._remote(dst))
        except Exception as exc:
            raise VfsError("FTP 重命名失败。", str(exc), src) from exc
        self.invalidate(src)
        self.invalidate(dst)

    def set_mtime(self, rel: str, mtime_ns: int) -> None:
        if self.ftp is None:
            self.connect()
        stamp = datetime.fromtimestamp(mtime_ns / 1e9, timezone.utc).strftime("%Y%m%d%H%M%S")
        try:
            self.ftp.sendcmd(f"MFMT {stamp} {self._remote(rel)}")
        except Exception as exc:
            raise VfsUnsupported("FTP 服务器不支持设置修改时间（MFMT）。", str(exc)) from exc


# ---------------------------------------------------------------- SFTP (OpenSSH CLI)
class SftpVfs(_CachedVfs):
    """通过系统 OpenSSH 的 ssh/sftp 命令访问，无需 paramiko。"""

    scheme = "sftp"

    def __init__(self, spec: str, **options):
        _, user, password, host, port, path, query = _parse_url(spec)
        self.user = options.get("user") or user
        self.password = options.get("password") or password
        self.host = host
        self.port = port or 22
        self.base = path
        self.key = options.get("key") or query.get("key")
        super().__init__(f"sftp://{host}/{path}")

    def _ssh_cmd(self, command: str) -> list[str]:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(self.port)]
        if self.key:
            cmd += ["-i", self.key]
        cmd.append(f"{self.user}@{self.host}" if self.user else self.host)
        cmd.append(command)
        return cmd

    def _sftp_batch(self, commands: str) -> str:
        cmd = ["sftp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-P", str(self.port), "-b", "-"]
        if self.key:
            cmd[1:1] = ["-i", self.key]
        cmd.append(f"{self.user}@{self.host}" if self.user else self.host)
        try:
            out = subprocess.run(cmd, input=commands, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VfsError("SFTP 连接失败，请确认系统已安装 OpenSSH 客户端并配置免密登录或密钥。", str(exc)) from exc
        if out.returncode != 0:
            raise VfsError("SFTP 命令执行失败。", (out.stderr or out.stdout).strip()[:400])
        return out.stdout

    def connect(self) -> None:
        self._ssh_cmd("true")

    def _remote(self, rel: str) -> str:
        base = self.base or "."
        return "/".join(filter(None, [base, _norm(rel)]))

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        path = self._remote(rel)
        script = f'ls -la "{path}"\nquit\n'
        text = self._sftp_batch(script)
        out: list[VfsEntry] = []
        for line in text.splitlines():
            match = re.match(r"^([d\-l])[rwx\-st]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\S+\s+\S+\s+\S+)\s+(.+)$", line.strip())
            if not match:
                continue
            name = match.group(4).strip()
            if name in (".", ".."):
                continue
            is_dir = match.group(1) == "d"
            out.append(VfsEntry(name, _join(rel, name), is_dir=is_dir,
                                is_symlink=match.group(1) == "l",
                                size=0 if is_dir else int(match.group(2))))
        return out

    def read_bytes(self, rel: str) -> bytes:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ds-sftp-") as folder:
            local = Path(folder) / "download"
            self._sftp_batch(f'get "{self._remote(rel)}" "{local.as_posix()}"\nquit\n')
            if not local.exists():
                raise VfsError("SFTP 下载失败。", path=rel)
            return local.read_bytes()

    def write_bytes(self, rel: str, data: bytes) -> None:
        import tempfile

        self._writable()
        with tempfile.TemporaryDirectory(prefix="ds-sftp-") as folder:
            local = Path(folder) / "upload"
            local.write_bytes(data)
            self._sftp_batch(f'put "{local.as_posix()}" "{self._remote(rel)}"\nquit\n')
        self.invalidate(rel)

    def mkdir(self, rel: str) -> None:
        self._writable()
        self._sftp_batch(f'mkdir "{self._remote(rel)}"\nquit\n')
        self.invalidate(rel)

    def remove(self, rel: str) -> None:
        self._writable()
        try:
            self._sftp_batch(f'rm "{self._remote(rel)}"\nquit\n')
        except VfsError:
            self._sftp_batch(f'rmdir "{self._remote(rel)}"\nquit\n')
        self.invalidate(rel)

    def move(self, src: str, dst: str) -> None:
        self._writable()
        self._sftp_batch(f'rename "{self._remote(src)}" "{self._remote(dst)}"\nquit\n')
        self.invalidate(src)
        self.invalidate(dst)


# ---------------------------------------------------------------- HTTP 系数据源
class HttpVfs(_CachedVfs):
    """WebDAV / S3 / Dropbox / OneDrive / Subversion 的 HTTP 基类。"""

    def __init__(self, spec: str, **options):
        scheme, user, password, host, port, path, query = _parse_url(spec)
        self.scheme_name = scheme
        self.user = options.get("user") or user
        self.password = options.get("password") or password
        self.host = host
        self.base_path = path
        self.query = query
        self.token = options.get("token") or query.get("token") or password
        self.session = None
        super().__init__(spec.split("?")[0])

    def connect(self) -> None:
        http = _http()
        self.session = http.Session()
        if self.user and self.password and self.scheme_name in ("webdav", "dav", "svn", "http", "https"):
            self.session.auth = (self.user, self.password)

    def close(self) -> None:
        if self.session:
            self.session.close()
            self.session = None

    def _request(self, method: str, url: str, **kwargs):
        if self.session is None:
            self.connect()
        try:
            response = self.session.request(method, url, timeout=30, **kwargs)
        except Exception as exc:
            raise VfsError("网络请求失败，请检查地址、网络与凭证。", str(exc), url) from exc
        if response.status_code in (401, 403):
            raise VfsError("认证失败或无访问权限，请检查账号、令牌或密钥。", f"HTTP {response.status_code}", url)
        if response.status_code >= 400:
            raise VfsError("远程数据源返回错误。", f"HTTP {response.status_code}: {response.text[:200]}", url)
        return response

    # 由子类重写
    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        raise NotImplementedError

    def read_bytes(self, rel: str) -> bytes:
        raise NotImplementedError

    def write_bytes(self, rel: str, data: bytes) -> None:
        raise NotImplementedError

    def remove(self, rel: str) -> None:
        raise NotImplementedError


class WebDavVfs(HttpVfs):
    scheme = "webdav"

    def __init__(self, spec: str, **options):
        super().__init__(spec, **options)
        self.origin = spec.split("?")[0]
        for prefix in ("webdav://", "dav://", "svn://", "http://", "https://"):
            if self.origin.lower().startswith(prefix):
                break
        self.base_url = self.origin.replace("webdav://", "https://").replace("dav://", "https://").replace("svn://", "https://")
        if not self.base_url.startswith("http"):
            self.base_url = "https://" + self.base_url

    def _url(self, rel: str) -> str:
        return self.base_url.rstrip("/") + "/" + quote(_norm(rel)) if rel else self.base_url

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        response = self._request(
            "PROPFIND", self._url(rel),
            headers={"Depth": "1", "Content-Type": "application/xml"},
            data=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                 b'<d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>',
        )
        out: list[VfsEntry] = []
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise VfsError("WebDAV 响应不是有效的 XML。", str(exc)) from exc
        for node in root.iter():
            if not node.tag.endswith("response"):
                continue
            href = next((c.text or "" for c in node if c.tag.endswith("href")), "")
            name = unquote(href.rstrip("/").rsplit("/", 1)[-1])
            if not name or unquote(href).rstrip("/") == unquote(self._url(rel)).rstrip("/"):
                continue
            is_dir = any(c.tag.endswith("collection") for c in node.iter())
            size = next((int(c.text) for c in node.iter() if c.tag.endswith("getcontentlength") and c.text), 0)
            out.append(VfsEntry(name, _join(rel, name), is_dir=is_dir, size=0 if is_dir else size))
        return out

    def read_bytes(self, rel: str) -> bytes:
        return self._request("GET", self._url(rel)).content

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        self._request("PUT", self._url(rel), data=data)
        self.invalidate(rel)

    def mkdir(self, rel: str) -> None:
        self._writable()
        self._request("MKCOL", self._url(rel))
        self.invalidate(rel)

    def remove(self, rel: str) -> None:
        self._writable()
        self._request("DELETE", self._url(rel))
        self.invalidate(rel)

    def move(self, src: str, dst: str) -> None:
        self._writable()
        self._request("MOVE", self._url(src), headers={"Destination": self._url(dst), "Overwrite": "T"})
        self.invalidate(src)
        self.invalidate(dst)


class S3Vfs(HttpVfs):
    """Amazon S3：手写 AWS SigV4 签名，ListObjectsV2 / GET / PUT / DELETE。"""

    scheme = "s3"

    def __init__(self, spec: str, **options):
        super().__init__(spec, **options)
        self.bucket = self.host
        self.prefix = self.base_path
        self.access_key = options.get("access_key") or self.user
        self.secret_key = options.get("secret_key") or self.password
        self.region = options.get("region") or self.query.get("region") or "us-east-1"
        self.endpoint = options.get("endpoint") or f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

    def _signed(self, method: str, url: str, payload: bytes = b"", params: dict | None = None, extra: dict | None = None):
        if not self.access_key or not self.secret_key:
            raise VfsError("S3 访问需要 access_key 与 secret_key。")
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        parsed = urlparse(url)
        query = dict(params or {})
        canonical_query = "&".join(f"{quote(k, safe='')}={quote(str(v), safe='')}" for k, v in sorted(query.items()))
        headers = {
            "host": parsed.netloc,
            "x-amz-content-sha256": hashlib.sha256(payload).hexdigest(),
            "x-amz-date": amz_date,
        }
        headers.update({k.lower(): v for k, v in (extra or {}).items()})
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical = f"{method}\n{parsed.path or '/'}\n{canonical_query}\n{canonical_headers}\n{signed_headers}\n{headers['x-amz-content-sha256']}"
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        key = sign(f"AWS4{self.secret_key}".encode(), date_stamp)
        key = sign(key, self.region)
        key = sign(key, "s3")
        key = sign(key, "aws4_request")
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        full = url + (f"?{canonical_query}" if canonical_query else "")
        return self._request(method, full, headers=headers, data=payload)

    def _key(self, rel: str) -> str:
        return "/".join(filter(None, [self.prefix, _norm(rel)]))

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        prefix = self._key(rel)
        prefix = prefix + "/" if prefix else ""
        response = self._signed("GET", self.endpoint, params={"list-type": "2", "prefix": prefix, "delimiter": "/"})
        out: list[VfsEntry] = []
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise VfsError("S3 响应不是有效的 XML。", str(exc)) from exc
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        for node in root.findall(f"{ns}CommonPrefixes"):
            text = node.findtext(f"{ns}Prefix", "").rstrip("/")
            name = text.rsplit("/", 1)[-1]
            if name:
                out.append(VfsEntry(name, _join(rel, name), is_dir=True))
        for node in root.findall(f"{ns}Contents"):
            key = node.findtext(f"{ns}Key", "")
            if key == prefix or not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            if "/" in rest:
                continue
            out.append(VfsEntry(rest, _join(rel, rest), size=int(node.findtext(f"{ns}Size", "0"))))
        return out

    def read_bytes(self, rel: str) -> bytes:
        return self._signed("GET", f"{self.endpoint}/{quote(self._key(rel))}").content

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        self._signed("PUT", f"{self.endpoint}/{quote(self._key(rel))}", payload=data)
        self.invalidate(rel)

    def remove(self, rel: str) -> None:
        self._writable()
        self._signed("DELETE", f"{self.endpoint}/{quote(self._key(rel))}")
        self.invalidate(rel)


class _TokenVfs(HttpVfs):
    """Dropbox / OneDrive 共用的 Bearer 令牌 REST 适配。"""

    api = ""

    def _headers(self) -> dict:
        if not self.token:
            raise VfsError(f"{self.scheme_name} 需要访问令牌（token）。")
        return {"Authorization": f"Bearer {self.token}"}


class DropboxVfs(_TokenVfs):
    scheme = "dropbox"

    def _rpc(self, endpoint: str, payload: dict) -> dict:
        response = self._request(
            "POST", f"https://api.dropboxapi.com/2/{endpoint}",
            headers={**self._headers(), "Content-Type": "application/json"}, json=payload,
        )
        return response.json()

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        path = "/" + "/".join(filter(None, [self.base_path, _norm(rel)]))
        data = self._rpc("files/list_folder", {"path": path.rstrip("/") or ""})
        out = []
        for item in data.get("entries", []):
            name = item.get("name", "")
            out.append(VfsEntry(name, _join(rel, name), is_dir=item.get(".tag") == "folder",
                                size=item.get("size", 0)))
        return out

    def read_bytes(self, rel: str) -> bytes:
        path = "/" + "/".join(filter(None, [self.base_path, _norm(rel)]))
        response = self._request(
            "POST", "https://content.dropboxapi.com/2/files/download",
            headers={**self._headers(), "Dropbox-API-Arg": __import__("json").dumps({"path": path})},
        )
        return response.content

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        path = "/" + "/".join(filter(None, [self.base_path, _norm(rel)]))
        self._request(
            "POST", "https://content.dropboxapi.com/2/files/upload",
            headers={**self._headers(), "Content-Type": "application/octet-stream",
                     "Dropbox-API-Arg": __import__("json").dumps({"path": path, "mode": "overwrite"})},
            data=data,
        )
        self.invalidate(rel)

    def remove(self, rel: str) -> None:
        self._writable()
        self._rpc("files/delete_v2", {"path": "/" + "/".join(filter(None, [self.base_path, _norm(rel)]))})
        self.invalidate(rel)


class OneDriveVfs(_TokenVfs):
    scheme = "onedrive"

    def _item_url(self, rel: str) -> str:
        path = "/".join(filter(None, [self.base_path, _norm(rel)]))
        return f"https://graph.microsoft.com/v1.0/me/drive/root:/{quote(path)}" if path else "https://graph.microsoft.com/v1.0/me/drive/root"

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        response = self._request("GET", f"{self._item_url(rel)}:/children", headers=self._headers())
        out = []
        for item in response.json().get("value", []):
            name = item.get("name", "")
            out.append(VfsEntry(name, _join(rel, name), is_dir="folder" in item,
                                size=item.get("size", 0)))
        return out

    def read_bytes(self, rel: str) -> bytes:
        return self._request("GET", f"{self._item_url(rel)}:/content", headers=self._headers()).content

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._writable()
        self._request("PUT", f"{self._item_url(rel)}:/content", headers=self._headers(), data=data)
        self.invalidate(rel)

    def remove(self, rel: str) -> None:
        self._writable()
        self._request("DELETE", self._item_url(rel), headers=self._headers())
        self.invalidate(rel)


class SvnVfs(WebDavVfs):
    """Subversion 仓库：优先 WebDAV 只读浏览；有 svn CLI 时用 svn list/export。"""

    scheme = "svn"

    def _fetch_dir(self, rel: str) -> list[VfsEntry]:
        import shutil as _shutil

        if _shutil.which("svn"):
            url = self._url(rel)
            try:
                out = subprocess.run(["svn", "list", "--xml", url], capture_output=True, text=True, timeout=60)
                if out.returncode == 0:
                    root = ET.fromstring(out.stdout)
                    result = []
                    for node in root.iter("entry"):
                        name = node.findtext("name", "")
                        kind = node.get("kind", "file")
                        result.append(VfsEntry(name, _join(rel, name), is_dir=kind == "dir"))
                    return result
            except (subprocess.SubprocessError, ET.ParseError):
                pass
        return super()._fetch_dir(rel)


_PROVIDERS = {
    "ftp": FtpVfs, "ftps": FtpVfs, "sftp": SftpVfs,
    "webdav": WebDavVfs, "dav": WebDavVfs, "svn": SvnVfs,
    "s3": S3Vfs, "dropbox": DropboxVfs, "onedrive": OneDriveVfs,
    "http": WebDavVfs, "https": WebDavVfs,
}


def open_remote_vfs(spec: str, **options) -> VfsProvider:
    scheme = urlparse(spec).scheme.lower()
    if scheme == "smb":
        return LocalVfs("\\\\" + urlparse(spec).netloc + urlparse(spec).path.replace("/", "\\"))
    provider = _PROVIDERS.get(scheme)
    if provider is None:
        raise VfsError(f"不支持的数据源协议：{scheme}")
    return provider(spec, **options)
