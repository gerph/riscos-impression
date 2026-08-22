"""Paragraph and heading numbering.

See docs/impression-documents.xml, "Paragraph and Heading Numbering".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from riscos_impression import binary

NUMBERSTR_SIZE = 12


class NumberingStyle(Enum):
    DECIMAL = 0
    ROMAN_UPPER = 1
    ROMAN_LOWER = 2
    ALPHA_UPPER = 3
    ALPHA_LOWER = 4
    BULLET = 5


def resolve_numbering_style(raw: int) -> Optional[NumberingStyle]:
    try:
        return NumberingStyle(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class NumberingRecord:
    index: int
    start: bool
    start_value: int
    raw_style: int
    style: Optional[NumberingStyle]
    tag: int
    dictionary_index: int

    @classmethod
    def from_bytes(cls, data: bytes, offset: int, index: int) -> "NumberingRecord":
        word0 = binary.u32(data, offset)
        word1 = binary.u32(data, offset + 4)
        raw_style = word1 & 0xFF
        return cls(
            index=index,
            start=binary.bit(word0, 0),
            start_value=binary.bits(word0, 1, 31),
            raw_style=raw_style,
            style=resolve_numbering_style(raw_style),
            tag=binary.bits(word1, 8, 24),
            dictionary_index=binary.s32(data, offset + 8),
        )


def parse_numbering_table(
    data: bytes, numbers: int, numbers_end: int
) -> list[NumberingRecord]:
    """Decode the numbering table spanning numbers (start) to numbers_end
    (end, exclusive) in the file header."""
    count = (numbers_end - numbers) // NUMBERSTR_SIZE
    return [
        NumberingRecord.from_bytes(data, numbers + i * NUMBERSTR_SIZE, i)
        for i in range(count)
    ]


#: Largest-first (value, symbol) pairs for the standard subtractive-
#: notation greedy Roman numeral algorithm.
_ROMAN_NUMERALS = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def _to_roman(value: int) -> str:
    parts = []
    for magnitude, symbol in _ROMAN_NUMERALS:
        count, value = divmod(value, magnitude)
        parts.append(symbol * count)
    return "".join(parts)


def _to_alpha(value: int) -> str:
    """Bijective base-26: 1=A, 2=B, ..., 26=Z, 27=AA, 28=AB, ... --
    matches the everyday "a, b, c, ..., z, aa, bb, ..." outline-list
    convention (and spreadsheet column letters), not a plain base-26
    positional encoding (which would have no letter for "0" and so
    couldn't represent 26 as anything other than a two-letter value
    starting over from A)."""
    parts = []
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        parts.append(chr(ord("A") + remainder))
    return "".join(reversed(parts))


def format_number(value: int, style: Optional[NumberingStyle]) -> str:
    """*value* (see resolve_number) formatted for *style*. DECIMAL, and
    any unrecognised/None style, is always a plain base-10 string --
    the same fallback the original conversion source's own decimal
    case used, and safe for any integer including zero/negative.

    Roman numerals and alphabetic style both need value >= 1: neither
    system has a representation for zero or negative numbers, so
    anything less falls back to plain decimal too, rather than raising
    or producing a nonsensical string.

    Bullet style ignores value entirely -- every item in a bulleted
    list gets the same bullet glyph, not a running count.

    Note: the original C conversion source (c/styles' own
    expandnumber()) recognised all of these style codes but left every
    non-decimal branch genuinely empty -- this is real, additional
    behaviour beyond what that reference tool ever did, not a port of
    existing logic."""
    if style is NumberingStyle.BULLET:
        return "•"
    if style is NumberingStyle.ROMAN_UPPER and value >= 1:
        return _to_roman(value)
    if style is NumberingStyle.ROMAN_LOWER and value >= 1:
        return _to_roman(value).lower()
    if style is NumberingStyle.ALPHA_UPPER and value >= 1:
        return _to_alpha(value)
    if style is NumberingStyle.ALPHA_LOWER and value >= 1:
        return _to_alpha(value).lower()
    return str(value)


def resolve_number(
    records: list[NumberingRecord], dictionary_index: int, tag: int
) -> Optional[int]:
    """The current running value of the numbering sequence identified by
    (dictionary_index, tag) at the point that tag occurs: found by
    locating the matching record, then scanning backwards through records
    belonging to the same story, summing one for each until a 'start'
    record is reached, whose start_value seeds the count. Mirrors
    expandnumber() in the conversion source. Returns None if no record
    matches (dictionary_index, tag)."""
    target = None
    for i, record in enumerate(records):
        if record.dictionary_index == dictionary_index and record.tag == tag:
            target = i
            break
    if target is None:
        return None

    n = -1
    i = target
    while i >= 0:
        record = records[i]
        if record.dictionary_index == dictionary_index:
            n += 1
            if record.start:
                n += record.start_value
                break
        i -= 1
    return n
