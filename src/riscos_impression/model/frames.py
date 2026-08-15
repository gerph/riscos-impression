"""Object-record streams: pages, chapters (sections), branches, and every
kind of frame (text, picture, blank, guide, group).

See docs/impression-documents.xml, "Object-record streams" and "Object
Records".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from riscos_impression import binary
from riscos_impression.model.colours import Colour, decode_colour_word

OBJHDR_SIZE = 8
FRAME_COMMON_SIZE = 104
PICTSTR_SIZE = 160
SECTSTR_SIZE = 116
PAGESTR_SIZE = 68
BRANCHSTR_SIZE = 40

#: The bordercolour/backcolour sentinel meaning "no colour override".
NO_COLOUR = 0xFFFFFFFF


class ObjectType(Enum):
    PAGE = 0x1
    TEXT = 0x2
    PICTURE = 0x3
    BLANK = 0x4
    BRANCH = 0x6
    SECTION = 0x7
    GUIDE = 0xA
    GROUP = 0xB
    UNKNOWN = 0x10


def resolve_object_type(raw_type: int) -> Optional[ObjectType]:
    """Resolve an object record header's raw type byte to an ObjectType,
    folding the legacy 0xFF ("XXXXX") blank-frame byte value into BLANK,
    as the conversion source treats them identically everywhere. Returns
    None for a type code the format doesn't define (0x5, 0x8, 0x9,
    0xC-0xF)."""
    if raw_type == 0xFF:
        return ObjectType.BLANK
    try:
        return ObjectType(raw_type)
    except ValueError:
        return None


def _record_length(data: bytes, offset: int) -> int:
    return binary.u8(data, offset + 5) | (binary.u8(data, offset + 6) << 8)


# ---------------------------------------------------------------------------
# Frame object common layout (XTEXT, XBLANK, XGUIDE, XGROUP, and the base of
# XPICT)
# ---------------------------------------------------------------------------


def _parse_frame_common(data: bytes, offset: int) -> dict:
    flags = binary.u32(data, offset + 20)
    group_flags = binary.u32(data, offset + 72)
    return dict(
        x0=binary.s32(data, offset + 4),
        y0=binary.s32(data, offset + 8),
        x1=binary.s32(data, offset + 12),
        y1=binary.s32(data, offset + 16),
        selected=binary.bit(flags, 0),
        repel=binary.bit(flags, 1),
        filled=binary.bit(flags, 2),
        master=binary.bit(flags, 8),
        locked=binary.bit(flags, 9),
        grouped=binary.bit(flags, 21),
        repeating=binary.bit(flags, 22),
        level=binary.bits(flags, 24, 8),
        dictionary_index=binary.s32(data, offset + 24),
        exx0=binary.s32(data, offset + 28),
        exy0=binary.s32(data, offset + 32),
        exx1=binary.s32(data, offset + 36),
        exy1=binary.s32(data, offset + 40),
        master_index=binary.u8(data, offset + 45),
        fill_colour_word=binary.u32(data, offset + 48),
        hinset=binary.s32(data, offset + 52),
        vinset=binary.s32(data, offset + 56),
        border0=binary.u8(data, offset + 60),
        border1=binary.u8(data, offset + 61),
        border2=binary.u8(data, offset + 62),
        border3=binary.u8(data, offset + 63),
        border_colour_word=binary.u32(data, offset + 64),
        embed_tag=binary.u32(data, offset + 68),
        group_number=binary.u8(data, offset + 72),
        overprint=binary.bit(group_flags, 13),
    )


@dataclass(frozen=True)
class Frame:
    """Fields common to XTEXT, XBLANK, XGUIDE, and XGROUP object records,
    and shared by XPICT as the base of its own, extended layout. See
    docs/impression-documents.xml, "Frame object common layout"."""

    x0: int
    y0: int
    x1: int
    y1: int
    selected: bool
    repel: bool
    filled: bool
    master: bool
    locked: bool
    grouped: bool
    repeating: bool
    level: int
    dictionary_index: int
    exx0: int
    exy0: int
    exx1: int
    exy1: int
    master_index: int
    fill_colour_word: int
    hinset: int
    vinset: int
    border0: int
    border1: int
    border2: int
    border3: int
    border_colour_word: int
    embed_tag: int
    group_number: int
    overprint: bool

    @classmethod
    def from_bytes(cls, data: bytes, offset: int) -> "Frame":
        return cls(**_parse_frame_common(data, offset))

    @property
    def has_border(self) -> bool:
        return any(
            border != 0xFF
            for border in (self.border0, self.border1, self.border2, self.border3)
        )

    @property
    def has_story(self) -> bool:
        return self.dictionary_index >= 0

    def fill_colour(self, colours: Sequence[Colour]) -> Optional[Colour]:
        if not self.filled:
            return None
        return decode_colour_word(self.fill_colour_word, colours)

    def border_colour(self, colours: Sequence[Colour]) -> Optional[Colour]:
        if self.border_colour_word == NO_COLOUR:
            return None
        return decode_colour_word(self.border_colour_word, colours)


@dataclass(frozen=True)
class TextFrame(Frame):
    """XTEXT: a normal text frame; its story (dictionary_index) holds
    formatted text, decoded in model.story."""


@dataclass(frozen=True)
class BlankFrame(Frame):
    """XBLANK: an unlinked frame, not yet assigned as text or picture."""


@dataclass(frozen=True)
class GuideFrame(Frame):
    """XGUIDE: a non-printing page guide line. Only x0, y0, x1, y1,
    locked, and level are meaningful; a single record produces four
    independent guide lines (left, right, bottom, top edges)."""


@dataclass(frozen=True)
class GroupFrame(Frame):
    """XGROUP: marks the end of a run of grouped frames sharing the same
    level and group number; carries the group's own master-page linkage
    but does not itself describe a drawable frame."""


# ---------------------------------------------------------------------------
# Picture frame (XPICT)
# ---------------------------------------------------------------------------


class PathOpCode(Enum):
    END = 0
    MOVE = 2
    CLOSE = 5
    CURVE = 6
    DRAW = 8


_COORDINATE_OPS = {PathOpCode.MOVE, PathOpCode.DRAW}


@dataclass(frozen=True)
class PathOp:
    """One opcode of an irregular picture boundary (crop path). x and y,
    when present, are relative to the centre of the picture frame's outer
    box."""

    code: PathOpCode
    x: Optional[int] = None
    y: Optional[int] = None


def _find_irregular_boundary_offset(
    data: bytes, body_offset: int, body_length: int
) -> Optional[int]:
    """Scan an XPICT record's extension sub-records (the bytes beyond the
    fixed 160-byte body) for an irregular-boundary sub-record, returning
    the byte offset of its code word, or None if there isn't one."""
    if body_length <= PICTSTR_SIZE:
        return None
    pos = body_offset + PICTSTR_SIZE
    end = body_offset + body_length
    while pos + 4 <= end:
        code = binary.u32(data, pos)
        if (code & 0xFF) == 1:
            return pos
        advance = ((code >> 8) & 0xFFFFFF) - 4
        if advance <= 0:
            break  # malformed sub-record; stop rather than loop forever
        pos += advance
    return None


def _decode_boundary_path(data: bytes, code_offset: int) -> tuple[PathOp, ...]:
    code = binary.u32(data, code_offset)
    end = code_offset + ((code >> 8) & 0xFFFFFF) - 4
    pos = code_offset + 4

    ops: list[PathOp] = []
    while pos < end:
        raw_op = binary.u32(data, pos)
        pos += 4
        try:
            op_code = PathOpCode(raw_op)
        except ValueError:
            break  # unrecognised opcode; stop rather than misinterpret
        if op_code in _COORDINATE_OPS:
            ops.append(PathOp(op_code, binary.s32(data, pos), binary.s32(data, pos + 4)))
            pos += 8
        else:
            ops.append(PathOp(op_code))
        if op_code is PathOpCode.END:
            break
    return tuple(ops)


@dataclass(frozen=True)
class PictureFrame(Frame):
    """XPICT: a picture frame. Extends the common frame layout with
    scale/shift/rotation and optional PostScript halftone screen and
    irregular-boundary (crop path) data."""

    use_ps_screen: bool
    use_recommended_screen: bool
    xscale: int
    yscale: int
    xshift: int
    yshift: int
    angle: int
    lpi: int
    psscreen: int
    boundary: Optional[tuple[PathOp, ...]] = None

    @classmethod
    def from_bytes(cls, data: bytes, offset: int, body_length: int) -> "PictureFrame":
        common = _parse_frame_common(data, offset)
        flags = binary.u32(data, offset + 20)
        group_flags = binary.u32(data, offset + 72)
        # The byte order of the lpi/psscreen/psx0 word is not confirmed;
        # this follows the field's declaration order. See
        # docs/impression-documents.xml, "Picture frame (XPICT)".
        picture_word = binary.u32(data, offset + 124)

        boundary = None
        boundary_offset = _find_irregular_boundary_offset(data, offset, body_length)
        if boundary_offset is not None:
            boundary = _decode_boundary_path(data, boundary_offset)

        return cls(
            **common,
            use_ps_screen=binary.bit(flags, 16),
            use_recommended_screen=binary.bit(group_flags, 15),
            xscale=binary.s32(data, offset + 104),
            yscale=binary.s32(data, offset + 108),
            xshift=binary.s32(data, offset + 112),
            yshift=binary.s32(data, offset + 116),
            angle=binary.s32(data, offset + 120),
            psscreen=binary.bits(picture_word, 16, 8),
            lpi=binary.bits(picture_word, 24, 8),
            boundary=boundary,
        )


# ---------------------------------------------------------------------------
# Page (XPAGE)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """XPAGE: a page, either on a master page stream or a main-document
    stream."""

    x0: int
    y0: int
    x1: int
    y1: int
    bleed: int
    master_page_name: str

    @classmethod
    def from_bytes(cls, data: bytes, offset: int) -> "Page":
        return cls(
            x0=binary.s32(data, offset + 4),
            y0=binary.s32(data, offset + 8),
            x1=binary.s32(data, offset + 12),
            y1=binary.s32(data, offset + 16),
            bleed=binary.s32(data, offset + 28),
            master_page_name=binary.cstring(data, offset + 40, 28),
        )

    @property
    def print_width(self) -> int:
        return (self.x1 - self.x0) - 2 * self.bleed

    @property
    def print_height(self) -> int:
        return (self.y1 - self.y0) - 2 * self.bleed


# ---------------------------------------------------------------------------
# Section (XSECT) and Branch (XBRANCH)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """XSECT: marks the start of a chapter, and the master-page pair and
    page/chapter-numbering behaviour it uses."""

    create_number: int
    master_page_index: int
    start_page_number: int
    override_start_page: bool
    start_on_right: bool
    copy_previous: bool
    start_chapter_number: int
    override_start_chapter: bool

    @classmethod
    def from_bytes(cls, data: bytes, offset: int) -> "Section":
        flags1 = binary.u16(data, offset + 14)
        flags2 = binary.u16(data, offset + 82)
        return cls(
            create_number=binary.s32(data, offset + 4),
            master_page_index=binary.s32(data, offset + 8),
            start_page_number=binary.u16(data, offset + 12),
            override_start_page=binary.bit(flags1, 0),
            start_on_right=binary.bit(flags1, 1),
            copy_previous=binary.bit(flags1, 2),
            start_chapter_number=binary.u16(data, offset + 80),
            override_start_chapter=binary.bit(flags2, 0),
        )


@dataclass(frozen=True)
class Branch:
    """XBRANCH: a 40-byte body, entirely unidentified; see
    docs/impression-documents.xml, "Branch object (XBRANCH)"."""


# ---------------------------------------------------------------------------
# Object-record stream walking
# ---------------------------------------------------------------------------


def _parse_body(
    object_type: Optional[ObjectType], data: bytes, body_offset: int, body_length: int
):
    if object_type is ObjectType.PAGE:
        return Page.from_bytes(data, body_offset)
    if object_type is ObjectType.TEXT:
        return TextFrame.from_bytes(data, body_offset)
    if object_type is ObjectType.BLANK:
        return BlankFrame.from_bytes(data, body_offset)
    if object_type is ObjectType.GUIDE:
        return GuideFrame.from_bytes(data, body_offset)
    if object_type is ObjectType.GROUP:
        return GroupFrame.from_bytes(data, body_offset)
    if object_type is ObjectType.PICTURE:
        return PictureFrame.from_bytes(data, body_offset, body_length)
    if object_type is ObjectType.SECTION:
        return Section.from_bytes(data, body_offset)
    if object_type is ObjectType.BRANCH:
        return Branch()
    return None  # UNKNOWN (0x10), or a type code the format doesn't define


@dataclass(frozen=True)
class ObjectRecord:
    """One decoded record from an object-record stream."""

    offset: int  #: byte offset of this record's objhdr within the document data
    raw_type: int
    type: Optional[ObjectType]
    length: int  #: total record length, header included
    value: object  #: Page | Frame subclass | Section | Branch | None


def parse_object_stream(data: bytes, start: int, end: int) -> list[ObjectRecord]:
    """Walk one object-record stream from *start* up to (but not beyond)
    *end*, decoding each record's body according to its type. Stops at a
    zero-length record (the stream's own end-of-stream sentinel) even if
    that is before *end*."""
    records = []
    pos = start
    while pos < end:
        raw_type = binary.u8(data, pos)
        length = _record_length(data, pos)
        if length == 0:
            break
        object_type = resolve_object_type(raw_type)
        body_offset = pos + OBJHDR_SIZE
        body_length = length - OBJHDR_SIZE
        value = _parse_body(object_type, data, body_offset, body_length)
        records.append(
            ObjectRecord(
                offset=pos,
                raw_type=raw_type,
                type=object_type,
                length=length,
                value=value,
            )
        )
        pos += length
    return records
