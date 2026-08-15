"""Hand-built byte fixtures for the Impression document format, matching
the field layouts documented in docs/impression-documents.xml.

These are deliberately explicit (offset by offset) rather than driven by a
single struct format string, since the real format is not one contiguous
struct and future builders (frame/object records, style records, ...) will
need the same explicitness for their bitfields.
"""

from __future__ import annotations

import struct

HEADER_SIZE = 380

_HEADER_OFFSETS = {
    "colour1": 276,
    "colour2": 280,
    "colour3": 284,
    "tints": 288,
    "stylebase": 292,
    "x3": 296,
    "x4": 300,
    "x5": 304,
    "numbers": 308,
    "numbers_end": 312,
    "x8": 316,
    "x9": 320,
    "x10": 324,
    "dict1": 328,
    "dict2": 332,
    "mdict1": 336,
    "mdict2": 340,
    "masterpages1": 344,
    "masterpages2": 348,
    "mainpages1": 352,
    "mainpages2": 356,
    "contents1": 360,
    "contents2": 364,
}

_HEADER_DEFAULTS = {
    "v1": 0,
    "magic": 0x12345678,
    "version": 28,
    "x3": 0,
    "x4": 0,
    "x5": 0,
    "x8": 0,
    "x9": 0,
    "x10": 0,
    "oldname": "",
    **{name: HEADER_SIZE for name in _HEADER_OFFSETS},
}


def build_header(**overrides: object) -> bytes:
    """Build a minimal, valid 380-byte file header, every offset-table
    field defaulting to ``HEADER_SIZE`` (as though every table it points
    to were empty), with any given fields overridden."""
    fields = {**_HEADER_DEFAULTS, **overrides}

    data = bytearray(HEADER_SIZE)
    struct.pack_into("<I", data, 0, fields["v1"])
    struct.pack_into("<I", data, 4, fields["magic"])
    struct.pack_into("<I", data, 8, fields["version"])
    for name, offset in _HEADER_OFFSETS.items():
        struct.pack_into("<I", data, offset, fields[name])

    name_bytes = fields["oldname"].encode("latin-1")[:12]
    data[368 : 368 + len(name_bytes)] = name_bytes

    return bytes(data)


COLOUR_ENTRY_SIZE = 48


def build_colour_entry(
    *,
    palword: int = 0,
    flags: int = 0,
    y: int = 0,
    c: int = 0,
    m: int = 0,
    k: int = 0,
    name: str = "",
) -> bytes:
    """Build one 48-byte on-disk icolourstr record."""
    data = bytearray(COLOUR_ENTRY_SIZE)
    struct.pack_into("<I", data, 0, palword)
    struct.pack_into("<I", data, 4, flags)
    data[8] = y & 0xFF
    data[9] = c & 0xFF
    data[10] = m & 0xFF
    data[11] = k & 0xFF
    name_bytes = name.encode("latin-1")[:24]
    data[12 : 12 + len(name_bytes)] = name_bytes
    return bytes(data)


def build_colour_table(entries: list[bytes]) -> bytes:
    """Concatenate colour-entry records into one colour-table blob."""
    return b"".join(entries)


DICT_ENTRY_SIZE = 56


def build_dict_entry(*, type: int = 2, id: int = 0, types: int = 0) -> bytes:
    """Build one 56-byte on-disk dictstr record."""
    data = bytearray(DICT_ENTRY_SIZE)
    data[0] = type & 0xFF
    struct.pack_into("<I", data, 4, id)
    struct.pack_into("<I", data, 32, types)
    return bytes(data)


def build_object_header(*, type: int, length: int) -> bytes:
    """Build one 8-byte objhdr record header (type plus a 16-bit length,
    header included)."""
    data = bytearray(8)
    data[0] = type & 0xFF
    data[5] = length & 0xFF
    data[6] = (length >> 8) & 0xFF
    return bytes(data)


def build_object_record(*, type: int, body: bytes) -> bytes:
    """Build one full object record: 8-byte header (with a length
    matching *body*) followed by *body*."""
    length = 8 + len(body)
    return build_object_header(type=type, length=length) + body


FRAME_COMMON_SIZE = 104


def build_frame_common_body(
    *,
    x0: int = 0,
    y0: int = 0,
    x1: int = 0,
    y1: int = 0,
    selected: bool = False,
    repel: bool = False,
    filled: bool = False,
    master: bool = False,
    locked: bool = False,
    flags_bit16: bool = False,
    grouped: bool = False,
    repeating: bool = False,
    level: int = 0,
    dictionary_index: int = -1,
    exx0: int = 0,
    exy0: int = 0,
    exx1: int = 0,
    exy1: int = 0,
    master_index: int = 0,
    fill_colour_word: int = 0,
    hinset: int = 0,
    vinset: int = 0,
    border0: int = 0xFF,
    border1: int = 0xFF,
    border2: int = 0xFF,
    border3: int = 0xFF,
    border_colour_word: int = 0xFFFFFFFF,
    embed_tag: int = 0,
    group_number: int = 0,
    overprint: bool = False,
    group_flags_bit15: bool = False,
) -> bytes:
    """Build the 104-byte common body shared by XTEXT/XBLANK/XGUIDE/XGROUP,
    and the base of XPICT."""
    data = bytearray(FRAME_COMMON_SIZE)
    struct.pack_into("<i", data, 4, x0)
    struct.pack_into("<i", data, 8, y0)
    struct.pack_into("<i", data, 12, x1)
    struct.pack_into("<i", data, 16, y1)

    flags = 0
    if selected:
        flags |= 1 << 0
    if repel:
        flags |= 1 << 1
    if filled:
        flags |= 1 << 2
    if master:
        flags |= 1 << 8
    if locked:
        flags |= 1 << 9
    if flags_bit16:
        flags |= 1 << 16
    if grouped:
        flags |= 1 << 21
    if repeating:
        flags |= 1 << 22
    flags |= (level & 0xFF) << 24
    struct.pack_into("<I", data, 20, flags)

    struct.pack_into("<i", data, 24, dictionary_index)
    struct.pack_into("<i", data, 28, exx0)
    struct.pack_into("<i", data, 32, exy0)
    struct.pack_into("<i", data, 36, exx1)
    struct.pack_into("<i", data, 40, exy1)
    data[45] = master_index & 0xFF
    struct.pack_into("<I", data, 48, fill_colour_word & 0xFFFFFFFF)
    struct.pack_into("<i", data, 52, hinset)
    struct.pack_into("<i", data, 56, vinset)
    data[60] = border0 & 0xFF
    data[61] = border1 & 0xFF
    data[62] = border2 & 0xFF
    data[63] = border3 & 0xFF
    struct.pack_into("<I", data, 64, border_colour_word & 0xFFFFFFFF)
    struct.pack_into("<I", data, 68, embed_tag)

    group_flags = group_number & 0xFF
    if overprint:
        group_flags |= 1 << 13
    if group_flags_bit15:
        group_flags |= 1 << 15
    struct.pack_into("<I", data, 72, group_flags)

    return bytes(data)


def build_picture_extension(
    *,
    xscale: int = 0x10000,
    yscale: int = 0x10000,
    xshift: int = 0,
    yshift: int = 0,
    angle: int = 0,
    lpi: int = 0,
    psscreen: int = 0,
) -> bytes:
    """Build the 56-byte fixed extension XPICT adds after the common
    104-byte frame body (offsets 104-159)."""
    data = bytearray(160 - FRAME_COMMON_SIZE)
    struct.pack_into("<i", data, 0, xscale)
    struct.pack_into("<i", data, 4, yscale)
    struct.pack_into("<i", data, 8, xshift)
    struct.pack_into("<i", data, 12, yshift)
    struct.pack_into("<i", data, 16, angle)
    word = ((lpi & 0xFF) << 24) | ((psscreen & 0xFF) << 16)
    struct.pack_into("<I", data, 20, word)
    return bytes(data)


def build_boundary_subrecord(ops: list[tuple[int, "int | None", "int | None"]]) -> bytes:
    """Build an irregular-boundary (crop path) extension sub-record from a
    list of (opcode, x, y) tuples; x and y are omitted for opcodes that
    carry no coordinates."""
    payload = bytearray()
    for op, x, y in ops:
        payload += struct.pack("<i", op)
        if x is not None:
            payload += struct.pack("<ii", x, y)
    field = 8 + len(payload)
    code = 1 | (field << 8)
    return struct.pack("<I", code) + bytes(payload)


def build_tagged_subrecord(*, tag: int, payload_length: int) -> bytes:
    """Build a non-boundary extension sub-record with the given low-byte
    tag, to be skipped over while scanning for a boundary sub-record."""
    field = 8 + payload_length
    code = (tag & 0xFF) | (field << 8)
    return struct.pack("<I", code) + bytes(payload_length)


def build_page_body(
    *,
    x0: int = 0,
    y0: int = 0,
    x1: int = 0,
    y1: int = 0,
    bleed: int = 0,
    master_page_name: str = "",
) -> bytes:
    data = bytearray(68)
    struct.pack_into("<i", data, 4, x0)
    struct.pack_into("<i", data, 8, y0)
    struct.pack_into("<i", data, 12, x1)
    struct.pack_into("<i", data, 16, y1)
    struct.pack_into("<i", data, 28, bleed)
    name_bytes = master_page_name.encode("latin-1")[:28]
    data[40 : 40 + len(name_bytes)] = name_bytes
    return bytes(data)


def build_section_body(
    *,
    create_number: int = 0,
    master_page_index: int = 0,
    start_page_number: int = 0,
    override_start_page: bool = False,
    start_on_right: bool = False,
    copy_previous: bool = False,
    start_chapter_number: int = 0,
    override_start_chapter: bool = False,
) -> bytes:
    data = bytearray(116)
    struct.pack_into("<i", data, 4, create_number)
    struct.pack_into("<i", data, 8, master_page_index)
    struct.pack_into("<H", data, 12, start_page_number)
    flags1 = 0
    if override_start_page:
        flags1 |= 1 << 0
    if start_on_right:
        flags1 |= 1 << 1
    if copy_previous:
        flags1 |= 1 << 2
    struct.pack_into("<H", data, 14, flags1)
    struct.pack_into("<H", data, 80, start_chapter_number)
    flags2 = 1 << 0 if override_start_chapter else 0
    struct.pack_into("<H", data, 82, flags2)
    return bytes(data)
