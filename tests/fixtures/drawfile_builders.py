"""Hand-built byte fixtures for the DrawFile format (see
src/riscos_impression/formats/drawfile.py and the riscos-output skill's
drawfile-format.md), shared by the PDF and HTML converters' tests.
"""

from __future__ import annotations

import struct


def _obj_header(type_: int, size: int, bounds: tuple[int, int, int, int]) -> bytes:
    return struct.pack("<6i", type_, size, *bounds)


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((-len(data)) % 4)


def build_font_table(fonts: dict[int, str]) -> bytes:
    body = b"".join(bytes([number]) + name.encode("latin-1") + b"\x00" for number, name in fonts.items())
    body = _pad4(body)
    return _obj_header(0, 24 + len(body), (0, 0, 0, 0)) + body


def build_path(
    *,
    ops: bytes,
    bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000),
    fill_colour: int = 0xFFFFFFFF,
    stroke_colour: int = 0xFFFFFFFF,
    line_width: int = 0,
    even_odd: bool = False,
    dashed: bool = False,
    join_style: int = 0,
    start_cap: int = 0,
    end_cap: int = 0,
    triangle_cap_width: int = 0,
    triangle_cap_length: int = 0,
) -> bytes:
    style = (
        (join_style & 3)
        | ((end_cap & 3) << 2)
        | ((start_cap & 3) << 4)
        | (0x40 if even_odd else 0)
        | (0x80 if dashed else 0)
        | ((triangle_cap_width & 0xFF) << 16)
        | ((triangle_cap_length & 0xFF) << 24)
    )
    dash = struct.pack("<4i", 0, 2, 10, 5) if dashed else b""
    body = struct.pack("<4I", fill_colour, stroke_colour, line_width, style) + dash + ops
    return _obj_header(2, 24 + len(body), bounds) + body


def move(x: int, y: int) -> bytes:
    return struct.pack("<3i", 2, x, y)


def line(x: int, y: int) -> bytes:
    return struct.pack("<3i", 8, x, y)


def curve(cx1: int, cy1: int, cx2: int, cy2: int, x: int, y: int) -> bytes:
    return struct.pack("<7i", 6, cx1, cy1, cx2, cy2, x, y)


def close_line() -> bytes:
    return struct.pack("<i", 5)


def end_path() -> bytes:
    return struct.pack("<2i", 0, 0)


def build_text(
    *,
    text: str,
    bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000),
    colour: int = 0x000000FF,
    font_number: int = 0,
    size_x: int = 640,
    size_y: int = 640,
    baseline_x: int = 0,
    baseline_y: int = 0,
) -> bytes:
    raw = text.encode("latin-1") + b"\x00"
    raw = _pad4(raw)
    body = (
        struct.pack("<4I", colour, 0, font_number, size_x)
        + struct.pack("<2I", size_y, baseline_x & 0xFFFFFFFF)
        + struct.pack("<i", baseline_y)
        + raw
    )
    return _obj_header(1, 24 + len(body), bounds) + body


def build_sprite(bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)) -> bytes:
    return _obj_header(5, 24, bounds)


def build_group(name: str, children: bytes, bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)) -> bytes:
    body = name.encode("latin-1").ljust(12)[:12] + children
    return _obj_header(6, 24 + len(body), bounds) + body


def build_tagged(tag: int, inner: bytes, bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)) -> bytes:
    body = struct.pack("<i", tag) + inner
    return _obj_header(7, 24 + len(body), bounds) + body


def build_unknown(type_: int, bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)) -> bytes:
    return _obj_header(type_, 24, bounds)


def build_drawfile(objects: bytes, bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)) -> bytes:
    header = b"Draw" + struct.pack("<2i", 201, 0) + b"Fixture     " + struct.pack("<4i", *bounds)
    assert len(header) == 40
    return header + objects
