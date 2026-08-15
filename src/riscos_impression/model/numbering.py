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
