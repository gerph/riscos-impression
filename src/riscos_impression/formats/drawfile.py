"""A stub decoder for embedded RISC OS DrawFile pictures.

Decodes only the file header (signature and bounding box); the vector,
text, and sprite objects the file contains are not parsed. This is a
general RISC OS file format, not part of the Impression-specific format
documented in docs/impression-documents.xml -- the header layout here is
standard, external RISC OS DrawFile knowledge, not something recovered
from the Impression conversion source.

This module has no ConversionLog dependency; a caller that renders a
DrawFile as a placeholder box using only this stub's bounding box (rather
than the file's actual vector content) is expected to log that itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from riscos_impression import binary

SIGNATURE = b"Draw"
HEADER_SIZE = 40
_BBOX_OFFSET = 24


@dataclass(frozen=True)
class BoundingBox:
    #: Draw units (1/256 OS unit).
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass(frozen=True)
class DrawFile:
    """A stub DrawFile decode: just its declared bounding box. Object
    content is not decoded."""

    bounds: BoundingBox

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["DrawFile"]:
        """Decode a DrawFile's header, or return None if *data* is too
        short or doesn't start with the DrawFile signature."""
        if len(data) < HEADER_SIZE or data[:4] != SIGNATURE:
            return None
        return cls(
            bounds=BoundingBox(
                x0=binary.s32(data, _BBOX_OFFSET),
                y0=binary.s32(data, _BBOX_OFFSET + 4),
                x1=binary.s32(data, _BBOX_OFFSET + 8),
                y1=binary.s32(data, _BBOX_OFFSET + 12),
            )
        )
