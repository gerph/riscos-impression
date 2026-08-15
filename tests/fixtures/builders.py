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
