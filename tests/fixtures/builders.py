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


# One-byte style fields, in on-disk order: (bit in flags1, field name used
# as a key in build_style_record's `values`). Names matching model.styles.Style
# attributes carry that field's value; "xxxNNN" names are unconfirmed
# placeholders, and names starting with "_" are selectors superseded by
# later data (their value doesn't matter; only their presence does).
_STYLE_ONE_BYTE_FIELDS = [
    (0, "xxx100"),
    (1, "auto_indent"),
    (2, "xxx102"),
    (3, "xxx103"),
    (4, "xxx104"),
    (5, "xxx105"),
    (6, "xxx106"),
    (7, "font_name_selector0"),
    (8, "underline"),
    (9, "script"),
    (10, "strikeout"),
    (11, "alignment"),
    (12, "keep_single"),
    (13, "keep_multiple"),
    (14, "hyphenation"),
    (15, "xxx115"),
    (16, "decimal_tab"),
    (17, "keep_next"),
    (18, "_textcolour0_selector"),
    (19, "xxx119"),
    (20, "_underlinecolour_selector"),
    (21, "xxx121"),
    (22, "xxx122"),
    (23, "_textbackcolour_selector"),
    (24, "lock_to_grid"),
    (25, "rule_off_0"),
    (26, "italic"),
    (27, "bold"),
]

# Four-byte style fields, in on-disk order: (bit in flags2, field name).
_STYLE_FOUR_BYTE_FIELDS = [
    (0, "left_indent"),
    (1, "right_indent_raw"),
    (2, "first_indent_absolute"),
    (3, "script_offset"),
    (4, "script_size"),
    (5, "line_spacing_raw"),
    (6, "space_after"),
    (7, "space_before"),
    (8, "underline_offset_0"),
    (9, "underline_offset_1"),
    (10, "rule_vertical_width"),
    (11, "xxx211"),
    (12, "xxx212"),
    (13, "font_size"),
    (14, "font_aspect_ratio"),
    (15, "xxx215"),
    (16, "keep_together"),
    (17, "_leader"),
    (18, "xxx218"),
    (19, "xxx219"),
    (20, "xxx220"),
    (21, "_fontname1_selector"),
    (22, "tracking"),
    (23, "xxx223"),
    (24, "_textcolour1_selector"),
    (25, "_textcolour2_selector"),
    (26, "xxx226"),
    (27, "xxx227"),
    (28, "xxx228"),
    (29, "xxx229"),
    (30, "xxx230"),
    (31, "xxx231"),
]

STYLESTR_HEADER_SIZE = 56


def build_style_record(
    *,
    flags1: int = 0,
    flags2: int = 0,
    tabs: int = 0,
    xxx4: int = 0,
    xxx5: int = 0,
    key: int = 0,
    b1: int = 0,
    b2: int = 0,
    paragraph_apply: bool = False,
    is_contents_entry_style: bool = False,
    is_index_entry_style: bool = False,
    name: str = "",
    is_body_text: bool = False,
    values: "dict | None" = None,
    leader: bytes = b"\x00\x00\x00\x00",
    tab_words: "list[int] | None" = None,
    font_style_name: str = "",
    foreground_colour_word: int = 0,
    background_colour_word: int = 0,
    underline_colour_word: int = 0,
) -> bytes:
    """Build one stylestr record (56-byte fixed header plus its
    variable-length body), mirroring model.styles's decode order and
    presence rules field for field."""
    values = values or {}

    def present1(bit: int) -> bool:
        return bool(flags1 & (1 << bit)) or is_body_text

    def present2(bit: int) -> bool:
        return bool(flags2 & (1 << bit)) or is_body_text

    body = bytearray()
    for bit, fname in _STYLE_ONE_BYTE_FIELDS:
        if present1(bit):
            body.append(values.get(fname, 0) & 0xFF)
    if is_body_text:
        body += b"\x00"  # iseffect/xxx129/showonstylemenu/xxx131 phantom byte

    while len(body) % 4:
        body.append(0)

    for bit, fname in _STYLE_FOUR_BYTE_FIELDS:
        if present2(bit):
            if fname == "_leader":
                body += leader[:4].ljust(4, b"\x00")
            elif fname in ("line_spacing_raw", "space_before"):
                # Read as unsigned by the decoder (line_spacing_raw packs a
                # top-bit flag; space_before is masked to its low 24 bits).
                body += struct.pack("<I", values.get(fname, 0) & 0xFFFFFFFF)
            else:
                body += struct.pack("<i", values.get(fname, 0))

    tab_bits = [b for b in range(32) if (tabs & (1 << b)) or is_body_text]
    tab_words = tab_words or []
    for i in range(len(tab_bits)):
        word = tab_words[i] if i < len(tab_words) else 0
        body += struct.pack("<I", word)

    if present2(21):
        name_bytes = font_style_name.encode("latin-1")[:40]
        body += name_bytes + b"\x00" * (40 - len(name_bytes))

    if present2(24):
        body += struct.pack("<I", foreground_colour_word)
    if present2(25):
        body += struct.pack("<I", background_colour_word)
    if flags1 & (1 << 20):
        body += struct.pack("<I", underline_colour_word)

    header = bytearray(STYLESTR_HEADER_SIZE)
    struct.pack_into("<I", header, 0, flags1)
    struct.pack_into("<I", header, 4, flags2)
    struct.pack_into("<I", header, 8, tabs)
    struct.pack_into("<I", header, 12, xxx4)
    struct.pack_into("<I", header, 16, xxx5)
    key_word = (key & 0x1FF) | ((b1 & 0x7F) << 9) | ((b2 & 0xFF) << 16)
    if paragraph_apply:
        key_word |= 1 << 29
    if is_contents_entry_style:
        key_word |= 1 << 30
    if is_index_entry_style:
        key_word |= 1 << 31
    struct.pack_into("<I", header, 20, key_word)
    name_bytes = name.encode("latin-1")[:32]
    header[24 : 24 + len(name_bytes)] = name_bytes

    return bytes(header) + bytes(body)


def build_style_table(entries: "dict[int, bytes]") -> bytes:
    """Build a full style-table blob (255-entry offset array followed by
    the concatenated style records) from a slot-number -> record bytes
    mapping, as produced by build_style_record."""
    offsets_size = 255 * 4
    offsets = [0] * 255
    bodies = bytearray()
    for slot in sorted(entries):
        offsets[slot] = offsets_size + len(bodies)
        bodies += entries[slot]
    return b"".join(struct.pack("<I", o) for o in offsets) + bytes(bodies)


NUMBERSTR_SIZE = 12


def build_numbering_record(
    *,
    start: bool = False,
    start_value: int = 0,
    style: int = 0,
    tag: int = 0,
    dictionary_index: int = 0,
) -> bytes:
    data = bytearray(NUMBERSTR_SIZE)
    word0 = (1 if start else 0) | ((start_value & 0x7FFFFFFF) << 1)
    struct.pack_into("<I", data, 0, word0)
    word1 = (style & 0xFF) | ((tag & 0xFFFFFF) << 8)
    struct.pack_into("<I", data, 4, word1)
    struct.pack_into("<i", data, 8, dictionary_index)
    return bytes(data)


ILINESTR_SIZE = 8


def _pack_u24(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF])


def build_frame_reference_line(*, frame_offset: int) -> bytes:
    """Build one ilinestr-framed frame-reference (kind 0x2) line."""
    payload = struct.pack("<I", frame_offset)
    total_len = ILINESTR_SIZE + len(payload)
    header = bytearray(ILINESTR_SIZE)
    header[4] = 0x2
    header[5:8] = _pack_u24(total_len)
    return bytes(header) + payload


def build_text_content_line(content: bytes, *, preamble: bytes = b"\x00" * 16) -> bytes:
    """Build one ilinestr-framed text-content (kind 0x5) line from
    *content* (the control-code/text stream), with the always-skipped
    16-byte preamble prepended."""
    payload = preamble + content
    total_len = ILINESTR_SIZE + len(payload)
    header = bytearray(ILINESTR_SIZE)
    header[4] = 0x5
    header[5:8] = _pack_u24(total_len)
    return bytes(header) + payload


def build_story_bytes(lines: "list[bytes]") -> bytes:
    """Concatenate story lines and append the zero-length terminator
    record that ends a story."""
    return b"".join(lines) + bytes(ILINESTR_SIZE)


CTRL_E = 0x05
CTRL_G = 0x07
CTRL_H = 0x08
CTRL_K = 0x0B
CTRL_M = 0x0D
CTRL_N = 0x0E
CTRL_R = 0x12
CTRL_S = 0x13
CTRL_U = 0x15
CTRL_CLOSESQ = 0x1D
SEMBED = 0x1
SMERGE = 0x2


class ContentBuilder:
    """Builds a text-content line's control-code/text stream, handling
    the format's 4-byte alignment convention (relative to the start of
    the content, which itself always begins 4-byte aligned after the
    16-byte skipped preamble) so tests can compose control-code sequences
    without hand-computing padding."""

    def __init__(self) -> None:
        self.data = bytearray()

    def literal(self, text: str) -> "ContentBuilder":
        self.data += text.encode("latin-1")
        return self

    def _pad_after_control_byte(self) -> int:
        after = len(self.data) + 1
        aligned = (after + 3) & ~3
        return aligned - after

    def simple_ctrl(self, code: int) -> "ContentBuilder":
        """A control code with no payload and no alignment (CTRL_E,
        CTRL_M, CTRL_N)."""
        self.data.append(code)
        return self

    def ctrl(self, code: int, *ints: int) -> "ContentBuilder":
        """A control code whose payload is a 4-byte-aligned run of
        signed 32-bit integers."""
        pad = self._pad_after_control_byte()
        self.data.append(code)
        self.data += bytes(pad)
        for value in ints:
            self.data += struct.pack("<i", value)
        return self

    def ctrl_style(self, code: int, slots: "list[int]") -> "ContentBuilder":
        """CTRL_G/CTRL_H: an unused count word, then a zero-terminated
        list of style-table slot numbers."""
        pad = self._pad_after_control_byte()
        self.data.append(code)
        self.data += bytes(pad)
        self.data += struct.pack("<i", 0)  # count field; unused by the decoder
        for slot in slots:
            self.data += struct.pack("<i", slot & 0xFF)
        self.data += struct.pack("<i", 0)
        return self

    def ctrl_s_embed(self, embed_tag: int, *, xx2: int = 0, xxx: int = 0, xxy: int = 0) -> "ContentBuilder":
        pad = self._pad_after_control_byte()
        self.data.append(CTRL_S)
        self.data += bytes(pad)
        total = 4 * 6  # length field + embedstr's 5 fields
        self.data += struct.pack("<i", total)
        self.data += struct.pack("<i", SEMBED)
        self.data += struct.pack("<i", xx2)
        self.data += struct.pack("<i", embed_tag)
        self.data += struct.pack("<i", xxx)
        self.data += struct.pack("<i", xxy)
        return self

    def ctrl_s_merge(self, field_name: str, *, xx1: int = 0, xx2: int = 0) -> "ContentBuilder":
        pad = self._pad_after_control_byte()
        self.data.append(CTRL_S)
        self.data += bytes(pad)
        name_bytes = field_name.encode("latin-1") + b"\x00"
        total = 4 + 4 + 4 + 4 + len(name_bytes)
        self.data += struct.pack("<i", total)
        self.data += struct.pack("<i", SMERGE)
        self.data += struct.pack("<i", xx1)
        self.data += struct.pack("<i", xx2)
        self.data += name_bytes
        return self

    def bytes(self) -> bytes:
        return bytes(self.data)
