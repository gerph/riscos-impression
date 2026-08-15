"""The object dictionary, and the master-dictionary offset table used to
locate story/picture data in single-file documents.

See docs/impression-documents.xml, "Object Dictionary".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from riscos_impression import binary

DICTSTR_SIZE = 56

#: Directory-mode documents keep master-page story/picture files here.
MASTER_CHAPTER_DIRECTORY = "MasterChap"


def chapter_directory_name(create_number: int) -> str:
    """The directory-mode directory name for the chapter whose Section
    record has the given create_number; see "Directory layout and story
    files"."""
    return f"Chapter{create_number}"


class DictionaryEntryType(Enum):
    PICTURE = 1  # DCPICT
    TEXT = 2  # DCTEXT
    SECTION = 3  # DCSECT
    BRANCH = 4  # DCBRANCH


class EmbeddedObjectType(Enum):
    EPS = "eps"
    DRAW = "draw"
    TABLEMATE = "tablemate"
    EQUASOR = "equasor"
    FORMULIX = "formulix"
    EUREKA = "eureka"
    DIAGRAMIT = "diagramit"
    TABCALC = "tabcalc"
    GRAPHMATE = "graphmate"
    ARTWORKS = "artworks"
    DATA = "data"


_DRAW_FAMILY = {
    0xFF9: EmbeddedObjectType.DRAW,
    0xC85: EmbeddedObjectType.DRAW,
    0xAFF: EmbeddedObjectType.DRAW,
    0xBCF: EmbeddedObjectType.TABLEMATE,
    0xD91: EmbeddedObjectType.EQUASOR,
    0xB98: EmbeddedObjectType.FORMULIX,
    0xC37: EmbeddedObjectType.EUREKA,
    0xB84: EmbeddedObjectType.DIAGRAMIT,
    0xB7D: EmbeddedObjectType.TABCALC,
    0xB83: EmbeddedObjectType.GRAPHMATE,
}


def classify_embedded_object(types: int) -> EmbeddedObjectType:
    """Classify a DCPICT dictionary entry's ``types`` word; see
    docs/impression-documents.xml, "Embedded object types"."""
    low12 = types & 0xFFF
    if low12 == 0xFF5:
        return EmbeddedObjectType.EPS
    if low12 == 0xAFF:
        sub = (types >> 12) & 0xFFF
        if sub == 0:
            sub = low12
        return _DRAW_FAMILY.get(sub, EmbeddedObjectType.DATA)
    if low12 == 0xD94:
        return EmbeddedObjectType.ARTWORKS
    return EmbeddedObjectType.DATA


@dataclass(frozen=True)
class DictionaryEntry:
    index: int
    type: DictionaryEntryType
    id: int
    types: int

    @classmethod
    def from_bytes(cls, data: bytes, offset: int, index: int) -> "DictionaryEntry":
        return cls(
            index=index,
            type=DictionaryEntryType(binary.u8(data, offset)),
            id=binary.u32(data, offset + 4),
            types=binary.u32(data, offset + 32),
        )

    @property
    def embedded_object_type(self) -> Optional[EmbeddedObjectType]:
        if self.type is not DictionaryEntryType.PICTURE:
            return None
        return classify_embedded_object(self.types)


def parse_dictionary(data: bytes, dict1: int, mdict1: int) -> list[DictionaryEntry]:
    """Decode the object dictionary: the array of dictstr records spanning
    dict1 (start) to mdict1 (end, exclusive) in the file header -- this
    boundary is confirmed directly from the conversion source, which walks
    the array that way; see the note above."""
    count = (mdict1 - dict1) // DICTSTR_SIZE
    return [
        DictionaryEntry.from_bytes(data, dict1 + i * DICTSTR_SIZE, i)
        for i in range(count)
    ]


def parse_master_dictionary(data: bytes, mdict1: int, entry_count: int) -> list[int]:
    """Decode the master dictionary: entry_count + 1 file-offset integers
    starting at mdict1, one per dictionary entry plus a trailing sentinel
    used to compute the last entry's length."""
    return [
        binary.u32(data, mdict1 + i * 4) for i in range(entry_count + 1)
    ]


def story_length(master_dictionary: list[int], index: int) -> int:
    """The byte length of dictionary entry *index*'s story/picture data,
    derived from the master dictionary's offset table."""
    return master_dictionary[index + 1] - master_dictionary[index]


def chapter_index_for(dictionary: Sequence[DictionaryEntry], index: int) -> Optional[int]:
    """Which chapter dictionary entry *index* belongs to, in directory
    mode: found by counting DCSECT entries preceding it. The dictionary
    always carries one DCSECT entry for the master pages themselves
    before any belonging to a real chapter, so exactly one preceding
    DCSECT entry means MasterChap (MASTER_CHAPTER_DIRECTORY), not zero;
    two or more preceding DCSECT entries give a zero-based index into
    the document's chapters (two -> chapter 0, three -> chapter 1, and
    so on). See "Directory layout and story files"; confirmed directly
    from the conversion source (getifp() and chapcn() in c/frames.c),
    and empirically against every document in a 46-document survey (in
    which zero preceding DCSECT entries never actually occurred)."""
    count = sum(
        1 for entry in dictionary[:index] if entry.type is DictionaryEntryType.SECTION
    )
    return None if count <= 1 else count - 2
