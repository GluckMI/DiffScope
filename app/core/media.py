"""图片比较、音频标签解析、可执行文件版本信息。"""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .errors import ReadFailure


class MediaError(ReadFailure):
    title = "媒体文件解析失败"


# ---------------------------------------------------------------- 图片
@dataclass
class ImageDiffResult:
    left_size: tuple[int, int] = (0, 0)
    right_size: tuple[int, int] = (0, 0)
    width: int = 0
    height: int = 0
    diff_pixels: int = 0
    total_pixels: int = 0
    tolerance: int = 0
    diff_image: bytes = b""
    similarity: float | None = None  # dHash 感知相似度（0~1），Pillow 不可用时为 None

    @property
    def identical(self) -> bool:
        return self.diff_pixels == 0 and self.left_size == self.right_size

    @property
    def ratio(self) -> float:
        return self.diff_pixels / self.total_pixels if self.total_pixels else 0.0


def _dhash_bits(image, hash_size: int = 8) -> list[int]:
    """dHash：9x8 灰度缩放后按行比较相邻像素，64 位指纹，容忍压缩/缩放噪声。"""
    Image, _, _ = _pil()
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    bits: list[int] = []
    for row in range(hash_size):
        base = row * (hash_size + 1)
        for col in range(hash_size):
            bits.append(1 if pixels[base + col] > pixels[base + col + 1] else 0)
    return bits


def perceptual_similarity(left, right) -> float | None:
    """感知相似度 0~1；解码失败返回 None。left/right 为 PIL Image。"""
    try:
        ha, hb = _dhash_bits(left), _dhash_bits(right)
        same = sum(1 for x, y in zip(ha, hb) if x == y)
        return same / len(ha) if ha else None
    except Exception:
        return None


def _pil():
    try:
        from PIL import Image, ImageChops, ImageEnhance

        return Image, ImageChops, ImageEnhance
    except ImportError as exc:
        raise MediaError("图片比较需要 Pillow 库。", str(exc)) from exc


def load_image(data: bytes, transform: str = "", scale: float = 1.0):
    Image, _, _ = _pil()
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise MediaError("无法打开图片，格式可能不受支持。", str(exc)) from exc
    if transform == "flip_h":
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    elif transform == "flip_v":
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    elif transform == "rotate_90":
        image = image.transpose(Image.Transpose.ROTATE_90)
    elif transform == "rotate_180":
        image = image.transpose(Image.Transpose.ROTATE_180)
    if scale and scale != 1.0:
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    return image


def compare_images(
    left: bytes,
    right: bytes,
    tolerance: int = 0,
    scale: float = 1.0,
    transform_left: str = "",
    transform_right: str = "",
    ignore_colors: Iterable[tuple[int, int, int]] = (),
    mode: str = "difference",
) -> ImageDiffResult:
    """逐像素比较；tolerance 为每通道允许的 RGB 差值；支持缩放、翻转旋转、忽略替换颜色。"""
    Image, ImageChops, ImageEnhance = _pil()
    a = load_image(left, transform_left, scale)
    b = load_image(right, transform_right, scale)
    similarity = perceptual_similarity(a, b)
    width, height = max(a.width, b.width), max(a.height, b.height)
    if (a.width, a.height) != (width, height):
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        canvas.paste(a, (0, 0))
        a = canvas
    if (b.width, b.height) != (width, height):
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        canvas.paste(b, (0, 0))
        b = canvas

    ignored = {tuple(color) for color in ignore_colors}
    difference = ImageChops.difference(a, b)
    red, green, blue = difference.split()
    mask = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = mask.point(lambda value: 255 if value > tolerance else 0)
    if ignored:  # 忽略指定替换颜色
        from PIL import ImageDraw

        draw = ImageDraw.Draw(mask)
        a_pixels, b_pixels = a.load(), b.load()
        for y in range(height):
            for x in range(width):
                if a_pixels[x, y] in ignored or b_pixels[x, y] in ignored:
                    draw.point((x, y), fill=0)
    diff_pixels = mask.histogram()[255]
    if mode == "difference":
        output = a.copy()
        output.paste((255, 60, 60), mask=mask)
    else:  # 叠加：左侧偏红、右侧偏蓝
        left_tint = Image.new("RGB", (width, height), (90, 0, 0))
        right_tint = Image.new("RGB", (width, height), (0, 0, 90))
        output = Image.blend(ImageChops.add(a, left_tint, scale=1.6),
                             ImageChops.add(b, right_tint, scale=1.6), 0.5)
    buffer = io.BytesIO()
    output.save(buffer, format="PNG")
    return ImageDiffResult(
        left_size=a.size, right_size=b.size, width=width, height=height,
        diff_pixels=diff_pixels, total_pixels=width * height,
        tolerance=tolerance, diff_image=buffer.getvalue(),
        similarity=similarity,
    )


def thumbnail(data: bytes, size: tuple[int, int] = (160, 120)) -> bytes:
    image = load_image(data)
    image.thumbnail(size)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------- 音频标签
def _synchsafe(data: bytes) -> int:
    return (data[0] << 21) | (data[1] << 14) | (data[2] << 7) | data[3]


def _id3v2(data: bytes) -> dict[str, str]:
    if not data.startswith(b"ID3") or len(data) < 10:
        return {}
    size = _synchsafe(data[6:10])
    version = data[3]
    pos, end = 10, min(10 + size, len(data))
    tags: dict[str, str] = {}
    while pos + 10 <= end:
        frame_id = data[pos:pos + 4].decode("latin-1", "ignore")
        if not frame_id.strip("\x00"):
            break
        frame_size = _synchsafe(data[pos + 4:pos + 8]) if version == 4 else struct.unpack(">I", data[pos + 4:pos + 8])[0]
        if frame_size <= 0 or pos + 10 + frame_size > end:
            break
        payload = data[pos + 10:pos + 10 + frame_size]
        pos += 10 + frame_size + (0 if version == 4 else 0)
        encoding = payload[0] if payload else 0
        body = payload[1:] if encoding in (0, 3) else payload
        text = body.decode({0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(encoding, "latin-1"), "replace")
        tags[frame_id] = text.strip("\x00 \r\n")
    return tags


def _id3v1(data: bytes) -> dict[str, str]:
    if len(data) < 128 or data[-128:-125] != b"TAG":
        return {}
    block = data[-128:]
    def field(start: int, end: int) -> str:
        return block[start:end].rstrip(b"\x00 ").decode("latin-1", "replace").strip()
    return {
        "TIT2": field(3, 33), "TPE1": field(33, 63), "TALB": field(63, 93),
        "TYER": field(93, 97), "TCON": str(block[127]) if block[127] < 255 else "",
    }


def _vorbis_comments(data: bytes) -> dict[str, str]:
    tags: dict[str, str] = {}
    index = data.find(b"\x03vorbis")
    if index < 0:
        index = data.find(b"OpusTags")
    if index < 0:
        return tags
    cursor = index + (7 if data[index:index + 7] == b"\x03vorbis" else 8)
    if data[index:index + 7] == b"\x03vorbis":
        vendor_len = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4 + vendor_len
    else:
        vendor_len = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4 + vendor_len
    if cursor + 4 > len(data):
        return tags
    count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    for _ in range(min(count, 500)):
        if cursor + 4 > len(data):
            break
        length = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        entry = data[cursor:cursor + length].decode("utf-8", "replace")
        cursor += length
        if "=" in entry:
            key, _, value = entry.partition("=")
            tags[key.upper()] = value
    return tags


def _mp4_tags(data: bytes) -> dict[str, str]:
    names = {b"\xa9nam": "TITLE", b"\xa9ART": "ARTIST", b"\xa9alb": "ALBUM",
             b"\xa9day": "YEAR", b"\xa9gen": "GENRE", b"trkn": "TRACK"}
    tags: dict[str, str] = {}
    for marker, label in names.items():
        index = data.find(marker)
        if index < 0:
            continue
        size = struct.unpack(">I", data[max(index - 4, 0):index])[0] if index >= 4 else 0
        chunk = data[index + 4:index + 4 + min(max(size - 12, 0), 256)]
        text = re.sub(rb"[^\x20-\x7e\u0080-\uffff]", b"", chunk).decode("utf-8", "replace").strip()
        if text:
            tags[label] = text
    return tags


LABELS = {
    "TIT2": "标题", "TPE1": "艺术家", "TALB": "专辑", "TYER": "年份", "TDRC": "年份",
    "TRCK": "音轨", "TCON": "流派", "TCOM": "作曲", "TPOS": "碟号",
    "TITLE": "标题", "ARTIST": "艺术家", "ALBUM": "专辑", "DATE": "年份",
    "TRACKNUMBER": "音轨", "GENRE": "流派", "COMMENT": "注释",
}


def read_tags(data: bytes, name: str = "") -> dict[str, str]:
    """解析 MP3 / FLAC / MP4 / OGG / WAV 的元数据标签。"""
    suffix = Path(name).suffix.lower()
    tags: dict[str, str] = {}
    if suffix in (".mp3", ""):
        tags.update(_id3v1(data))
        tags.update(_id3v2(data))
    elif suffix in (".flac", ".ogg", ".oga", ".opus"):
        tags.update(_vorbis_comments(data))
    elif suffix in (".m4a", ".mp4", ".aac"):
        tags.update(_mp4_tags(data))
    elif suffix == ".wav":
        index = data.find(b"LIST")
        if index >= 0:
            chunk = data[index:index + 512]
            for key, label in ((b"INAM", "标题"), (b"IART", "艺术家"), (b"IPRD", "专辑")):
                pos = chunk.find(key)
                if pos >= 0:
                    value = chunk[pos + 8:pos + 128].split(b"\x00")[0].decode("latin-1", "replace").strip()
                    if value:
                        tags[LABELS.get(key.decode(), key.decode())] = value
    if not tags:
        tags = {"文件": Path(name).name or "未知", "大小": str(len(data))}
    return tags


def tags_to_text(data: bytes, name: str = "") -> str:
    tags = read_tags(data, name)
    return "\n".join(f"{key} = {value}" for key, value in sorted(tags.items()))


def compare_tags(left: bytes, right: bytes, name: str = "") -> dict[str, tuple[str, str]]:
    a, b = read_tags(left, name), read_tags(right, name)
    return {key: (a.get(key, ""), b.get(key, "")) for key in sorted(set(a) | set(b))}


# ---------------------------------------------------------------- 可执行文件版本
def exe_version(data: bytes, path: str = "") -> dict[str, str]:
    """读取 exe/dll 版本资源；Windows 用 version.dll，其它平台用字节扫描回退。"""
    import os

    if path and Path(path).is_file() and os.name == "nt":
        import ctypes
        from ctypes import wintypes

        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(path, None)
        if size:
            buffer = ctypes.create_string_buffer(size)
            if version.GetFileVersionInfoW(path, 0, size, buffer):
                info = {}
                for label in ("FileVersion", "ProductVersion", "CompanyName", "FileDescription",
                              "ProductName", "LegalCopyright", "OriginalFilename"):
                    for lang in ("040904b0", "04090404", "080404b0", "04090000", "000004b0"):
                        value = ctypes.c_wchar_p()
                        length = wintypes.UINT()
                        query = f"\\StringFileInfo\\{lang}\\{label}"
                        if version.VerQueryValueW(buffer, query, ctypes.byref(value), ctypes.byref(length)) and value.value:
                            info[label] = value.value
                            break
                if info:
                    return info
    strings = re.findall(rb"[\x20-\x7e]{6,}", data[:2 * 1024 * 1024])
    keys = (b"FileVersion", b"ProductVersion", b"CompanyName", b"FileDescription", b"ProductName")
    out: dict[str, str] = {}
    for index, raw in enumerate(strings):
        text = raw.decode("latin-1", "replace")
        for key in keys:
            label = key.decode()
            if label in text and label not in out:
                out[label] = text.split(label, 1)[-1].strip(" \x00.:")[:120]
                if not out[label] and index + 1 < len(strings):
                    out[label] = strings[index + 1].decode("latin-1", "replace")[:120]
    return out or {"大小": str(len(data))}
