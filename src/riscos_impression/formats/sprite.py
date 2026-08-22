"""A stub decoder for embedded RISC OS Sprite pictures.

Decodes only the sprite area and first sprite headers (name and raw
dimension fields); pixel data is not decoded, and converting the raw
width-in-words/mode fields to a final pixel width needs a mode-to-bits-
per-pixel table this stub does not implement. This is general RISC OS
Sprite format knowledge, not something recovered from the Impression
conversion source.

This module has no ConversionLog dependency; a caller that renders a
sprite as a placeholder box using only this stub is expected to log
that itself.

Real pixel decoding (to PNG) is a separate, optional concern -- the
third-party riscos_sprites decoder (see the "sprites" extra in
pyproject.toml) does that; this module stays a dependency-free stub so
`SpriteArea.from_bytes` keeps working (as a cheap "is this recognisably
a sprite area at all" check, used e.g. by html_base.py/pdfdoc.py to
distinguish a Sprite picture from a corrupt DrawFile) even when that
extra isn't installed. See wrap_single_sprite_as_area() below for the
one piece of format knowledge a riscos_sprites caller needs from here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from riscos_impression import binary

AREA_HEADER_SIZE = 12
SPRITE_HEADER_SIZE = 44


@dataclass(frozen=True)
class Sprite:
    """One sprite's raw header fields, decoded only as far as this stub
    goes."""

    name: str
    width_words: int  #: width in words, minus one, as stored on disk
    height: int  #: height in pixels, minus one, as stored on disk
    first_bit_used: int
    last_bit_used: int
    mode: int


@dataclass(frozen=True)
class SpriteArea:
    """A stub decode of a sprite area (or single-sprite file, which uses
    the same area-header wrapper): the sprite count, and the first
    sprite's header if there is one."""

    sprite_count: int
    first: Optional[Sprite]

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["SpriteArea"]:
        """Decode a sprite area's header, or return None if *data* is
        too short to hold one.

        On disk (a ,ff9 file, or a sprite-typed PICTURE dictionary
        entry's own raw bytes -- confirmed against a real file,
        riscos-dumpsprites' own test fixture sprites/manysprites,ff9:
        offset 0 held 43, the file's own real sprite count), the area
        header is 12 bytes: sprite_count, first_sprite_offset,
        free_offset, with no leading word before them. The *in-memory*
        control-block convention used e.g. by OS_SpriteOp instead has
        an extra 4-byte "size of area" word first, shifting every
        field along by 4 -- easy to conflate the two, and this method
        used to (reading sprite_count at offset 4): fixed here. Also
        note first_sprite_offset/free_offset are themselves 4 bytes
        *larger* than their own real file offset (an artefact of that
        same in-memory convention, baked into the field's own meaning
        even on disk) -- subtract 4 to get an actual byte offset into
        *data*."""
        if len(data) < AREA_HEADER_SIZE:
            return None
        sprite_count = binary.u32(data, 0)
        first_offset = binary.u32(data, 4) - 4
        first = None
        if sprite_count > 0 and 0 <= first_offset and len(data) >= first_offset + SPRITE_HEADER_SIZE:
            first = _decode_sprite_header(data, first_offset)
        return cls(sprite_count=sprite_count, first=first)


def _decode_sprite_header(data: bytes, offset: int) -> Sprite:
    return Sprite(
        name=binary.cstring(data, offset + 4, 12),
        width_words=binary.u32(data, offset + 16),
        height=binary.u32(data, offset + 20),
        first_bit_used=binary.u32(data, offset + 24),
        last_bit_used=binary.u32(data, offset + 28),
        mode=binary.u32(data, offset + 40),
    )


def wrap_single_sprite_as_area(sprite_record: bytes) -> bytes:
    """Synthesise a minimal, valid sprite-area byte blob (the 12-byte
    header described in SpriteArea.from_bytes's own docstring) wrapping
    just *sprite_record* -- a single native sprite header+pixel record
    (SPRITE_HEADER_SIZE=44 bytes, then its image/mask data) with no
    area header of its own.

    Needed because a DrawFile's own Sprite object body is exactly that
    bare kind of record (confirmed empirically -- see DrawSprite's own
    docstring in formats/drawfile.py), but a full sprite-file decoder
    like riscos_sprites.SpriteFile expects the wrapped, multi-sprite
    area form every real ,ff9 file/Impression PICTURE dictionary entry
    actually has. Reuses the same 12-byte header field meanings
    SpriteArea.from_bytes documents (offsets stored 4 bytes larger
    than their own real file position)."""
    first_offset = AREA_HEADER_SIZE + 4
    free_offset = first_offset + len(sprite_record)
    return struct.pack("<III", 1, first_offset, free_offset) + sprite_record
