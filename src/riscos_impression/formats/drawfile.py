"""A decoder for embedded RISC OS DrawFile pictures.

Decodes the file header and walks the object stream: font tables, paths
(fill/stroke colour, width, winding rule, and their move/line/curve/close
elements), single-line text, groups, and tagged objects (recursing into
both). Sprite objects, and any other object type (text area, options,
transformed text/sprite, or anything unrecognised), are captured only as
a bounding box -- there is no pixel data or further structure decoded
for them.

This is general RISC OS DrawFile knowledge, not something recovered from
the Impression conversion source; the on-disk layout here is verified
against the official PRM file-formats reference
(https://www.riscos.com/support/developers/prm/fileformats.html), not
just remembered/assumed -- see the `riscos-output` skill's
`drawfile-format.md` for the equivalent general-purpose reference (kept
in step with this module where practical).

This module has no ConversionLog dependency; a caller that falls back to
a placeholder for anything this module doesn't decode (a Sprite object,
an unrecognised object type, a dash pattern that won't be honoured) is
expected to log that itself.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union

from riscos_impression import binary

SIGNATURE = b"Draw"
HEADER_SIZE = 40
_BBOX_OFFSET = 24
_OBJECT_HEADER_SIZE = 24

#: The DrawFile "no colour" sentinel word (used for both fill and
#: outline colour): -1 as an unsigned 32-bit word.
_NO_COLOUR = 0xFFFFFFFF

#: Errors a corrupt/truncated object stream can raise while being
#: decoded; caught wherever an object body is parsed so a single bad
#: object stops the walk (returning what was already found) rather than
#: raising out of from_bytes entirely.
_PARSE_ERRORS = (struct.error, ValueError, IndexError)


@dataclass(frozen=True)
class BoundingBox:
    #: Draw units (1/256 OS unit).
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


class DrawPathOpCode(Enum):
    """Draw path element types that matter for rendering; see the Draw
    module's path element table (`draw.md` in the `riscos-output`
    skill). Element types 0 (end), 1 (continuation pointer) aren't
    represented here -- they terminate parsing rather than becoming an
    op (see _parse_path_ops)."""

    MOVE = 2  #: starts a new subpath, affects winding
    MOVE_INTERNAL = 3  #: starts a new subpath, doesn't affect winding
    CLOSE_GAP = 4  #: close without a connecting line
    CLOSE_LINE = 5  #: close with a line back to the subpath's start
    CURVE = 6  #: cubic bezier to (x, y) via control points (cx1,cy1)/(cx2,cy2)
    GAP = 7  #: move to (x, y) without starting a new subpath (dash use)
    LINE = 8


@dataclass(frozen=True)
class DrawPathOp:
    code: DrawPathOpCode
    x: int = 0
    y: int = 0
    #: Only meaningful for CURVE; x/y above is the curve's end point.
    cx1: int = 0
    cy1: int = 0
    cx2: int = 0
    cy2: int = 0


#: Cap/join style codes (path style word bits 0-1 join, 2-3 end/
#: "trailing" cap, 4-5 start/"leading" cap; confirmed against the
#: RISC OS DrawFile module's own real rendering implementation, not
#: just the PRM's field descriptions).
CAP_BUTT = 0
CAP_ROUND = 1
CAP_TRIANGULAR = 3


@dataclass(frozen=True)
class DrawPath:
    bounds: BoundingBox
    #: A raw &BBGGRR00 palette word, or None for "do not paint" (the
    #: file's own -1 sentinel); see colour_rgb() to decode one.
    fill_colour: Optional[int]
    stroke_colour: Optional[int]
    line_width: int  #: Draw units; 0 = hairline
    even_odd: bool  #: winding rule: False = non-zero, True = even-odd
    dashed: bool  #: a dash pattern is present but not decoded further
    join_style: int = 0  #: 0=mitred, 1=round, 2=bevelled; not decoded further than the raw code
    start_cap: int = CAP_BUTT  #: the path's own first point ("leading" cap)
    end_cap: int = CAP_BUTT  #: the path's own last point ("trailing" cap)
    #: In 1/16ths of line_width (the file's own on-disk unit -- see
    #: colour_rgb's sibling note on other odd units in this format);
    #: only meaningful when start_cap/end_cap is CAP_TRIANGULAR.
    triangle_cap_width: int = 0
    triangle_cap_length: int = 0
    ops: list[DrawPathOp] = field(default_factory=list)


@dataclass(frozen=True)
class DrawText:
    bounds: BoundingBox
    colour: Optional[int]
    font_number: int  #: looked up in the enclosing DrawFile's `fonts` map; 0 = system font
    size_x: int  #: 1/640 point -- NOT Draw units, unlike every coordinate field here
    size_y: int  #: 1/640 point
    baseline_x: int
    baseline_y: int
    text: str


@dataclass(frozen=True)
class DrawSprite:
    """A Sprite object's bounding box only; the pixel data itself isn't
    decoded (matching formats/sprite.py's own stub scope) -- a Sprite
    embedded *within* a DrawFile is rarer than a document's picture
    being a Sprite outright, and no more valuable to decode here."""

    bounds: BoundingBox


@dataclass(frozen=True)
class DrawGroup:
    bounds: BoundingBox
    name: str
    objects: list["DrawObject"]


@dataclass(frozen=True)
class DrawTagged:
    bounds: BoundingBox
    tag: int
    inner: Optional["DrawObject"]


@dataclass(frozen=True)
class DrawUnknown:
    """A recognised-but-undecoded object type (text area, text column,
    options, transformed text/sprite) or a genuinely unknown one. Only
    its bounding box is kept."""

    bounds: BoundingBox
    type: int


DrawObject = Union[DrawPath, DrawText, DrawSprite, DrawGroup, DrawTagged, DrawUnknown]


def colour_rgb(word: int) -> tuple[int, int, int]:
    """Decode a Draw palette word (&BBGGRR00) into 0-255 (r, g, b). Byte
    0 (the low byte) is a flags/tint byte the DrawFile format doesn't
    use; bytes 1-3 are R, G, B in that order."""
    r = (word >> 8) & 0xFF
    g = (word >> 16) & 0xFF
    b = (word >> 24) & 0xFF
    return r, g, b


def _decode_colour_word(word: int) -> Optional[int]:
    return None if word == _NO_COLOUR else word


@dataclass(frozen=True)
class DrawFile:
    """A decoded DrawFile: its own declared bounding box, the top-level
    object stream (with Group/Tagged objects already recursed into),
    and a merged font-number -> font-name map gathered from every Font
    table object found anywhere in the stream (the format only requires
    one to precede the text objects that use it; scanning the whole
    stream rather than tracking scope precisely is simpler and never
    wrong in practice, since font numbers aren't reused for different
    names within one file)."""

    bounds: BoundingBox
    objects: list[DrawObject] = field(default_factory=list)
    fonts: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["DrawFile"]:
        """Decode a DrawFile, or return None if *data* is too short or
        doesn't start with the DrawFile signature. A corrupt/truncated
        object stream after a valid header doesn't raise -- parsing
        just stops at the first object it can't make sense of, and
        whatever was already decoded is returned."""
        if len(data) < HEADER_SIZE or data[:4] != SIGNATURE:
            return None
        bounds = BoundingBox(
            x0=binary.s32(data, _BBOX_OFFSET),
            y0=binary.s32(data, _BBOX_OFFSET + 4),
            x1=binary.s32(data, _BBOX_OFFSET + 8),
            y1=binary.s32(data, _BBOX_OFFSET + 12),
        )
        objects, fonts = _parse_objects(data, HEADER_SIZE, len(data))
        return cls(bounds=bounds, objects=objects, fonts=fonts)


# ---------------------------------------------------------------------------
# Object stream walking
# ---------------------------------------------------------------------------


def _parse_objects(data: bytes, start: int, end: int) -> tuple[list[DrawObject], dict[int, str]]:
    objects: list[DrawObject] = []
    fonts: dict[int, str] = {}
    pos = start
    while pos + _OBJECT_HEADER_SIZE <= end:
        obj_type = binary.u32(data, pos)
        size = binary.u32(data, pos + 4)
        if size < _OBJECT_HEADER_SIZE or pos + size > end:
            break  # malformed size; stop rather than mis-walk the rest of the stream
        bounds = BoundingBox(
            x0=binary.s32(data, pos + 8),
            y0=binary.s32(data, pos + 12),
            x1=binary.s32(data, pos + 16),
            y1=binary.s32(data, pos + 20),
        )
        obj, obj_fonts = _parse_object(obj_type, data, bounds, pos + _OBJECT_HEADER_SIZE, pos + size)
        fonts.update(obj_fonts)
        if obj is not None:
            objects.append(obj)
        pos += size  # objects are always word-aligned; size is a multiple of 4 by construction
    return objects, fonts


def _parse_object(
    obj_type: int, data: bytes, bounds: BoundingBox, start: int, end: int
) -> tuple[Optional[DrawObject], dict[int, str]]:
    try:
        if obj_type == 0:
            return None, _parse_font_table(data, start, end)
        if obj_type == 1:
            return _parse_text(data, bounds, start, end), {}
        if obj_type == 2:
            return _parse_path(data, bounds, start, end), {}
        if obj_type == 5:
            return DrawSprite(bounds=bounds), {}
        if obj_type == 6:
            name = binary.cstring(data, start, 12)
            children, fonts = _parse_objects(data, start + 12, end)
            return DrawGroup(bounds=bounds, name=name, objects=children), fonts
        if obj_type == 7:
            tag = binary.u32(data, start)
            inner_objects, fonts = _parse_objects(data, start + 4, end)
            return DrawTagged(bounds=bounds, tag=tag, inner=inner_objects[0] if inner_objects else None), fonts
    except _PARSE_ERRORS:
        pass
    return DrawUnknown(bounds=bounds, type=obj_type), {}


def _parse_font_table(data: bytes, start: int, end: int) -> dict[int, str]:
    fonts: dict[int, str] = {}
    pos = start
    try:
        while pos < end:
            font_number = data[pos]
            pos += 1
            if font_number == 0:
                break  # 0 is the system font and is never listed; a 0 here means garbage/padding
            name, pos = binary.nul_string(data, pos)
            fonts[font_number] = name
    except _PARSE_ERRORS:
        pass
    return fonts


def _parse_path(data: bytes, bounds: BoundingBox, start: int, end: int) -> DrawPath:
    fill_word = binary.u32(data, start)
    stroke_word = binary.u32(data, start + 4)
    line_width = binary.u32(data, start + 8)
    style = binary.u32(data, start + 12)
    join_style = binary.bits(style, 0, 2)
    end_cap = binary.bits(style, 2, 2)
    start_cap = binary.bits(style, 4, 2)
    even_odd = binary.bit(style, 6)
    dashed = binary.bit(style, 7)
    triangle_cap_width = binary.bits(style, 16, 8)
    triangle_cap_length = binary.bits(style, 24, 8)
    data_start = start + 16
    if dashed:
        # Dash pattern block: 4-byte start offset + 4-byte element count +
        # one 4-byte word per element; skip over it to reach the path data.
        element_count = binary.u32(data, data_start + 4)
        data_start += 8 + element_count * 4
    return DrawPath(
        bounds=bounds,
        fill_colour=_decode_colour_word(fill_word),
        stroke_colour=_decode_colour_word(stroke_word),
        line_width=line_width,
        even_odd=even_odd,
        dashed=dashed,
        join_style=join_style,
        start_cap=start_cap,
        end_cap=end_cap,
        triangle_cap_width=triangle_cap_width,
        triangle_cap_length=triangle_cap_length,
        ops=_parse_path_ops(data, data_start, end),
    )


def _parse_path_ops(data: bytes, start: int, end: int) -> list[DrawPathOp]:
    ops: list[DrawPathOp] = []
    pos = start
    while pos + 4 <= end:
        op_type = binary.u32(data, pos) & 0xFF
        if op_type == 0 or op_type == 1:
            break  # end of path, or a continuation pointer (not followed)
        if op_type in (2, 3):
            if pos + 12 > end:
                break
            ops.append(
                DrawPathOp(
                    DrawPathOpCode.MOVE if op_type == 2 else DrawPathOpCode.MOVE_INTERNAL,
                    x=binary.s32(data, pos + 4),
                    y=binary.s32(data, pos + 8),
                )
            )
            pos += 12
        elif op_type == 4:
            ops.append(DrawPathOp(DrawPathOpCode.CLOSE_GAP))
            pos += 4
        elif op_type == 5:
            ops.append(DrawPathOp(DrawPathOpCode.CLOSE_LINE))
            pos += 4
        elif op_type == 6:
            if pos + 28 > end:
                break
            ops.append(
                DrawPathOp(
                    DrawPathOpCode.CURVE,
                    cx1=binary.s32(data, pos + 4),
                    cy1=binary.s32(data, pos + 8),
                    cx2=binary.s32(data, pos + 12),
                    cy2=binary.s32(data, pos + 16),
                    x=binary.s32(data, pos + 20),
                    y=binary.s32(data, pos + 24),
                )
            )
            pos += 28
        elif op_type in (7, 8):
            if pos + 12 > end:
                break
            ops.append(
                DrawPathOp(
                    DrawPathOpCode.GAP if op_type == 7 else DrawPathOpCode.LINE,
                    x=binary.s32(data, pos + 4),
                    y=binary.s32(data, pos + 8),
                )
            )
            pos += 12
        else:
            break  # unrecognised element type; stop rather than mis-walk the rest
    return ops


def _parse_text(data: bytes, bounds: BoundingBox, start: int, end: int) -> DrawText:
    colour_word = binary.u32(data, start)
    # +4 background colour hint: an anti-aliasing rendering hint, not
    # meaningful to a vector re-renderer; not decoded.
    font_style = binary.u32(data, start + 8)
    size_x = binary.u32(data, start + 12)
    size_y = binary.u32(data, start + 16)
    baseline_x = binary.s32(data, start + 20)
    baseline_y = binary.s32(data, start + 24)
    text, _ = binary.nul_string(data, start + 28)
    return DrawText(
        bounds=bounds,
        colour=_decode_colour_word(colour_word),
        font_number=font_style & 0xFF,
        size_x=size_x,
        size_y=size_y,
        baseline_x=baseline_x,
        baseline_y=baseline_y,
        text=text,
    )
