"""The document colour table, and the inline colour-value-word codec used
by frame and style records to reference a colour.

See docs/impression-documents.xml, "Colour Table".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from riscos_impression import binary

#: Unity (full-strength) value for a decoded colour channel.
MAXCV = 0x10000

#: Unity (full-strength, unmodified) tint amount.
MAX_TINT = 128

ICOLOURSTR_SIZE = 48


class ColourModel(Enum):
    RGB = "rgb"
    CMYK = "cmyk"
    HSV = "hsv"


@dataclass(frozen=True)
class Colour:
    """A single resolved colour: either an entry from the document's
    on-disk colour table, or one decoded directly from an inline colour
    value word.

    ``values`` holds the channel values for ``model`` -- (r, g, b) for
    RGB, (c, m, y, k) for CMYK, (h, s, v) for HSV -- each already scaled
    to the 0..MAXCV range used throughout this format (hue is the one
    exception: see docs/impression-documents.xml, "Colour channel
    encoding").
    """

    index: Optional[int]
    name: str
    model: ColourModel
    values: tuple[int, ...]
    process: bool
    overprint: bool
    palette_word: int


@dataclass(frozen=True)
class _RawColourEntry:
    """One on-disk icolourstr record, before tint resolution."""

    palword: int
    flags: int
    y: int
    c: int
    m: int
    k: int
    name: str

    @classmethod
    def from_bytes(cls, data: bytes, offset: int) -> "_RawColourEntry":
        return cls(
            palword=binary.u32(data, offset + 0),
            flags=binary.u32(data, offset + 4),
            y=binary.u8(data, offset + 8),
            c=binary.u8(data, offset + 9),
            m=binary.u8(data, offset + 10),
            k=binary.u8(data, offset + 11),
            name=binary.cstring(data, offset + 12, 24),
        )

    @property
    def is_tint(self) -> bool:
        return bool(self.flags & 0x2)

    @property
    def is_process(self) -> bool:
        # Confirmed only for (flags & 0x3) == 0 (spot) or == 1 (process);
        # the conversion source has no case for 2 or 3, in which case a
        # newly-created colour record's flags stay at their zeroed
        # default, which is also "process". See docs/impression-documents.xml.
        return (self.flags & 0x3) != 0

    @property
    def is_overprint(self) -> bool:
        return bool(self.flags & 0x80)

    @property
    def tint_base_index(self) -> int:
        return (self.y >> 2) | ((self.c & 0x3F) << 6)

    @property
    def tint_amount(self) -> int:
        return self.k


def _decode_base_channels(
    y: int, c: int, m: int, k: int
) -> tuple[ColourModel, tuple[int, ...]]:
    selector = y & 0x3
    if selector == 1:
        return ColourModel.CMYK, (
            (c * MAXCV) // 255,
            (m * MAXCV) // 255,
            ((y & 0xFC) * MAXCV) // 255,
            (k * MAXCV) // 255,
        )
    if selector == 2:
        return ColourModel.HSV, (
            ((k << 4) | (m >> 4)) * MAXCV,
            (c * MAXCV) // 255,
            (((y >> 4) | ((m & 0xF) << 4)) * MAXCV) // 255,
        )
    return ColourModel.RGB, (
        (c * MAXCV) // 255,
        (m * MAXCV) // 255,
        (k * MAXCV) // 255,
    )


def _apply_tint(
    model: ColourModel, values: tuple[int, ...], tint: int
) -> tuple[int, ...]:
    remainder = MAX_TINT - tint
    if model is ColourModel.RGB:
        return tuple((remainder * MAXCV + tint * v) // MAX_TINT for v in values)
    if model is ColourModel.CMYK:
        return tuple((tint * v) // MAX_TINT for v in values)
    h, s, v = values
    return (
        (tint * h) // MAX_TINT,
        (tint * s) // MAX_TINT,
        (remainder * MAXCV + tint * v) // MAX_TINT,
    )


def parse_colour_table(data: bytes, colour1: int, tints: int) -> list[Colour]:
    """Decode the on-disk colour table (colour1 .. tints in the file
    header) into a list of resolved Colour objects.

    A tint entry's model, process/spot, overprint, and palette word are
    all inherited from its base colour rather than read from its own
    record; see docs/impression-documents.xml, "Colour flags word".
    """
    count = (tints - colour1) // ICOLOURSTR_SIZE
    raw_entries = [
        _RawColourEntry.from_bytes(data, colour1 + i * ICOLOURSTR_SIZE)
        for i in range(count)
    ]

    resolved: list[Optional[Colour]] = [None] * count

    def resolve(index: int, seen: frozenset[int]) -> Optional[Colour]:
        if not (0 <= index < count):
            return None
        if resolved[index] is not None:
            return resolved[index]
        if index in seen:
            return None  # circular tint reference

        raw = raw_entries[index]
        if not raw.name:
            return None  # unused slot

        if raw.is_tint:
            base = resolve(raw.tint_base_index, seen | {index})
            if base is None:
                return None
            colour = Colour(
                index=index,
                name=raw.name,
                model=base.model,
                values=_apply_tint(base.model, base.values, raw.tint_amount),
                process=base.process,
                overprint=base.overprint,
                palette_word=base.palette_word,
            )
        else:
            model, values = _decode_base_channels(raw.y, raw.c, raw.m, raw.k)
            colour = Colour(
                index=index,
                name=raw.name,
                model=model,
                values=values,
                process=raw.is_process,
                overprint=raw.is_overprint,
                palette_word=raw.palword,
            )

        resolved[index] = colour
        return colour

    for i in range(count):
        resolve(i, frozenset())

    return [colour for colour in resolved if colour is not None]


def decode_colour_word(
    word: int, colours: Optional[Sequence[Colour]] = None
) -> Optional[Colour]:
    """Decode a 32-bit inline colour value word, as used by frame fields
    (backcolour, bordercolour) and style fields (textcolour1, textcolour2,
    underlinecolour) to reference a colour.

    *colours* is the document's resolved on-disk colour table (see
    parse_colour_table), needed only to resolve a named-colour reference
    (selector 3); if that reference can't be resolved -- no table given,
    or the index is out of range -- None is returned. Every other
    selector always returns a Colour.
    """
    b3 = word & 0xFF
    b2 = (word >> 8) & 0xFF
    b1 = (word >> 16) & 0xFF
    b0 = (word >> 24) & 0xFF
    selector = b3 & 0x3

    if selector == 3:
        index = (b2 << 6) | (b3 >> 2)
        if colours is None:
            return None
        for colour in colours:
            if colour.index == index:
                return colour
        return None

    if selector == 1:
        model, values = ColourModel.CMYK, (
            (b2 * MAXCV) // 255,
            (b1 * MAXCV) // 255,
            ((b3 & 0xFC) * MAXCV) // 0xFC,
            (b0 * MAXCV) // 255,
        )
    elif selector == 2:
        model, values = ColourModel.HSV, (
            ((b1 >> 4) | (b0 << 4)) * MAXCV,
            (((b2 >> 4) | ((b1 & 0xF) << 4)) * MAXCV) // 255,
            (((b3 >> 4) | ((b2 & 0xF) << 4)) * MAXCV) // 255,
        )
    else:
        model, values = ColourModel.RGB, (
            (b2 * MAXCV) // 255,
            (b1 * MAXCV) // 255,
            (b0 * MAXCV) // 255,
        )

    return Colour(
        index=None,
        name="",
        model=model,
        values=values,
        process=True,
        overprint=False,
        palette_word=0,
    )
