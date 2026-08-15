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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from riscos_impression import binary

AREA_HEADER_SIZE = 16
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
        too short to hold one."""
        if len(data) < AREA_HEADER_SIZE:
            return None
        sprite_count = binary.u32(data, 4)
        first_offset = binary.u32(data, 8)
        first = None
        if sprite_count > 0 and len(data) >= first_offset + SPRITE_HEADER_SIZE:
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
