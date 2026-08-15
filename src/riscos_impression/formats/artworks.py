"""A full stub for embedded ArtWorks pictures.

ArtWorks is a third-party proprietary vector format; this package makes
no attempt to parse it, not even its bounding box. A fixed placeholder
size is used wherever one is needed. A caller using this stub is
expected to log that the picture could not be rendered (ConversionLog's
'unsupported' level fits better than 'best_effort' here, unlike
DrawFile/Sprite, since not even the real size is known).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from riscos_impression.formats.drawfile import BoundingBox

#: Used since this stub cannot determine an ArtWorks file's real size.
PLACEHOLDER_BOUNDS = BoundingBox(x0=0, y0=0, x1=256 * 1000, y1=256 * 1000)


@dataclass(frozen=True)
class ArtWorks:
    data: bytes
    bounds: BoundingBox = field(default=PLACEHOLDER_BOUNDS)

    @classmethod
    def from_bytes(cls, data: bytes) -> "ArtWorks":
        return cls(data=data)
