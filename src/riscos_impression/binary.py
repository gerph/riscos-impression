"""Low-level helpers for reading the little-endian binary structures used
throughout the Impression document format.

Field names and offsets used elsewhere in this package follow
``docs/impression-documents.xml`` in the repository root.
"""

from __future__ import annotations

import struct

from riscos_impression import encoding


def u8(data: bytes, offset: int) -> int:
    return data[offset]


def s8(data: bytes, offset: int) -> int:
    return struct.unpack_from("<b", data, offset)[0]


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def s16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<h", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def s32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def u24(data: bytes, offset: int) -> int:
    """A 3-byte little-endian unsigned integer, as used by ilinestr's
    prevlen/currlen fields."""
    return data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)


def bit(word: int, index: int) -> bool:
    """Extract a single bit from a word, numbered from 0 as the least
    significant bit, matching the bit numbering used throughout the format
    documentation."""
    return bool((word >> index) & 1)


def bits(word: int, index: int, width: int) -> int:
    """Extract a *width*-bit field starting at bit *index* (least
    significant bit first) from a word."""
    return (word >> index) & ((1 << width) - 1)


def cstring(data: bytes, offset: int, length: int) -> str:
    """Read a fixed-length RISC OS character-string field: text terminated
    by whichever comes first of a NUL or CR byte, or the field's own
    length, with any trailing padding discarded. Decoded as RISC OS
    Latin1 (see encoding.py), not plain ISO-8859-1."""
    raw = data[offset : offset + length]
    for terminator in (0x00, 0x0D):
        index = raw.find(terminator)
        if index != -1:
            raw = raw[:index]
    return encoding.decode(raw)


def nul_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read a NUL-terminated string starting at *offset*, decoded as
    RISC OS Latin1 (see encoding.py), not plain ISO-8859-1.

    Returns the decoded string and the offset of the byte following the
    terminator.
    """
    end = data.index(0x00, offset)
    return encoding.decode(data[offset:end]), end + 1
