"""Hand-built byte fixtures for the Computer Concepts ArtWorks format
(see the third-party riscos_artworks decoder), shared by the HTML
converters' tests. Kept minimal and self-contained rather than
depending on the sibling riscos_artworks checkout's own private test
fixtures, or on real .d94 files outside this repository -- just enough
of the on-disk layout (confirmed against real files while building
formats/artworks_svg.py) to decode as one single-object document.
"""

from __future__ import annotations

import struct

HEADER_SIZE = 0x80

FILLED = 0x80000000  # bit 31 set on a path's first element's tag -- ArtWorks' own "is filled" flag


def build_single_path_document(*, bbox: tuple[int, int, int, int] = (0, 0, 1000, 1000),
                                filled: bool = True) -> bytes:
    """The smallest ArtWorks document riscos_artworks.ArtWorks.from_buffer()
    will decode: a 128-byte header, one top-level list, and one filled
    square PathRecord with no fill/stroke attributes of its own (the
    renderer falls back to its own ambient default in that case -- see
    formats/artworks_svg.py's _DEFAULT_STYLE)."""
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<4sI8s", header, 0, b"Top!", 9, b"TopDraw\0")
    struct.pack_into("<6i", header, 16, 0, HEADER_SIZE, 0, 0, 0, 0)  # values[1] = body_offset
    struct.pack_into("<iii", header, 40, -1, -1, 0)  # undo, sprite, unknown_48
    struct.pack_into("<iii", header, 52, 0, 0, -1)  # american w/h, palette (-1 = none)

    tag_move = 2 | (FILLED if filled else 0)
    path = struct.pack("<Iii", tag_move, 0, 0)
    for x, y in ((1000, 0), (1000, 1000), (0, 1000)):
        path += struct.pack("<Iii", 8, x, y)
    path += struct.pack("<I", 5)  # close
    path += struct.pack("<I", 0)  # end

    record_type_word = 0x02  # RECORD_02_PATH
    control_word = 2  # bit 1 (visible) set
    record_body = struct.pack("<II4i", record_type_word, control_word, *bbox) + path
    record = struct.pack("<ii", 0, 0) + record_body  # record pointer: next=0 (last/only)

    list_pointer = struct.pack("<ii", 0, 0)  # previous=0, next=0 (only list)

    return bytes(header) + list_pointer + record
