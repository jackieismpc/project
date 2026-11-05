"""从 Qt 生成的 PDF 里提取文字和字体信息，只用 pyHanko 已有的读取能力，不新增依赖。

Qt 把每个字形写成 ``<glyph> Tj``，字形到 Unicode 的对应在字体的 ``ToUnicode`` 里。
每个 ``BT … ET`` 块是一段连续文字（表格里是一个单元格）。
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from pyhanko.pdf_utils.reader import PdfFileReader

_BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")


def _unicode(value: bytes) -> str:
    return bytes.fromhex(value.decode()).decode("utf-16-be")


def parse_to_unicode(data: bytes) -> dict[int, str]:
    """解析 ToUnicode CMap（bfchar 和 bfrange，含数组写法）。"""
    mapping: dict[int, str] = {}
    for block in _BFCHAR.findall(data):
        values = _HEX.findall(block)
        for code, target in zip(values[0::2], values[1::2], strict=True):
            mapping[int(code, 16)] = _unicode(target)
    for block in _BFRANGE.findall(data):
        for line in block.strip().splitlines():
            if b"[" in line:
                head, _, tail = line.partition(b"[")
                low, high = (int(x, 16) for x in _HEX.findall(head)[:2])
                for offset, target in enumerate(_HEX.findall(tail)):
                    mapping[low + offset] = _unicode(target)
                continue
            values = _HEX.findall(line)
            if len(values) == 3:
                low, high = int(values[0], 16), int(values[1], 16)
                first = _unicode(values[2])
                for offset in range(high - low + 1):
                    mapping[low + offset] = chr(ord(first[0]) + offset) + first[1:]
    return mapping


def _page_streams(page: Any) -> bytes:
    contents = page["/Contents"]
    streams = [contents] if hasattr(contents, "data") else list(contents)
    return b"\n".join(s.get_object().data for s in streams)


def _fonts(page: Any) -> dict[str, dict[int, str]]:
    result: dict[str, dict[int, str]] = {}
    for name, ref in page["/Resources"]["/Font"].items():
        font = ref.get_object()
        if "/ToUnicode" in font:
            result[str(name)] = parse_to_unicode(font["/ToUnicode"].data)
    return result


def _walk(node: Any) -> list[Any]:
    node = node.get_object() if hasattr(node, "get_object") else node
    if node["/Type"] == "/Page":
        return [node]
    return [page for kid in node["/Kids"] for page in _walk(kid)]


def _pages(reader: PdfFileReader) -> list[Any]:
    return _walk(reader.root["/Pages"])


def pdf_pages_text(data: bytes) -> list[str]:
    """每页的文字：每个文字块一行。"""
    reader = PdfFileReader(BytesIO(data))
    texts: list[str] = []
    for page in _pages(reader):
        fonts = _fonts(page)
        stream = _page_streams(page)
        lines: list[str] = []
        for block in re.findall(rb"BT(.*?)ET", stream, re.S):
            current: dict[int, str] = {}
            chars: list[str] = []
            for token in re.finditer(rb"/(F\d+) [\d.]+ Tf|<([0-9A-Fa-f]+)> Tj", block):
                if token.group(1):
                    current = fonts.get("/" + token.group(1).decode(), {})
                else:
                    code = int(token.group(2), 16)
                    chars.append(current.get(code, "�"))
            if chars:
                # 字体把空格字形登记为制表符（空格字形同时对应 U+0009 和 U+0020），还原成空格。
                lines.append("".join(chars).replace("\t", " "))
        texts.append("\n".join(lines))
    return texts


def pdf_font_report(data: bytes) -> list[tuple[str, bool]]:
    """全部 Type0 字体：``(BaseFont, 是否嵌入字体文件)``。"""
    reader = PdfFileReader(BytesIO(data))
    seen: dict[str, bool] = {}
    for page in _pages(reader):
        for ref in page["/Resources"]["/Font"].values():
            font = ref.get_object()
            descendant = font["/DescendantFonts"][0].get_object()
            descriptor = descendant["/FontDescriptor"].get_object()
            embedded = any(k in descriptor for k in ("/FontFile2", "/FontFile3", "/FontFile"))
            seen[str(font["/BaseFont"])] = embedded
    return sorted(seen.items())


def pdf_page_count(data: bytes) -> int:
    """页数。"""
    return int(PdfFileReader(BytesIO(data)).root["/Pages"]["/Count"])


def flat_text(data: bytes) -> str:
    """全部文字连成一段（不分页、不分块），方便断言跨字体的短语，例如「0.5 EU/mL」。"""
    return "".join(line for page in pdf_pages_text(data) for line in page.split("\n"))


def has_text(data: bytes, phrase: str) -> bool:
    """文字里是否有这个短语。比较时去掉全部空白：换行处的空格不会写进 PDF。"""
    squash = "".join
    return squash(phrase.split()) in squash(flat_text(data).split())
