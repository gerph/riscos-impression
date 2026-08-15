"""The document file header, and the top-level decoded document.

See docs/impression-documents.xml, "File Header".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from riscos_impression import binary

if TYPE_CHECKING:
    from riscos_impression.io.source import DocumentSource

#: Minimum format version this package will attempt to read. Matches the
#: original TransIMP converter's own MINVERSION check.
MIN_VERSION = 28

#: The fixed word found at offset 4 of every example document examined so
#: far. Not enforced (a document with a different value is simply
#: unconfirmed, not necessarily invalid), but recorded since it lines up
#: consistently enough to be a format signature rather than noise.
EXPECTED_MAGIC = 0x12345678


class ImpressionFormatError(Exception):
    """Raised when document data cannot be interpreted as (a supported
    version of) the Impression document format."""


@dataclass(frozen=True)
class FileHeader:
    """The fixed 380-byte header at the start of a document's data block."""

    SIZE: ClassVar[int] = 380

    v1: int
    magic: int
    version: int
    colour1: int
    colour2: int
    colour3: int
    tints: int
    stylebase: int
    x3: int
    x4: int
    x5: int
    numbers: int
    numbers_end: int
    x8: int
    x9: int
    x10: int
    dict1: int
    dict2: int
    mdict1: int
    mdict2: int
    masterpages1: int
    masterpages2: int
    mainpages1: int
    mainpages2: int
    contents1: int
    contents2: int
    oldname: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "FileHeader":
        if len(data) < cls.SIZE:
            raise ImpressionFormatError(
                f"document data is too short to hold a file header "
                f"({len(data)} bytes, need at least {cls.SIZE})"
            )

        version = binary.u32(data, 8)
        if version < MIN_VERSION:
            raise ImpressionFormatError(
                f"document format version {version} is older than the "
                f"minimum supported version {MIN_VERSION}"
            )

        return cls(
            v1=binary.u32(data, 0),
            magic=binary.u32(data, 4),
            version=version,
            colour1=binary.u32(data, 276),
            colour2=binary.u32(data, 280),
            colour3=binary.u32(data, 284),
            tints=binary.u32(data, 288),
            stylebase=binary.u32(data, 292),
            x3=binary.u32(data, 296),
            x4=binary.u32(data, 300),
            x5=binary.u32(data, 304),
            numbers=binary.u32(data, 308),
            numbers_end=binary.u32(data, 312),
            x8=binary.u32(data, 316),
            x9=binary.u32(data, 320),
            x10=binary.u32(data, 324),
            dict1=binary.u32(data, 328),
            dict2=binary.u32(data, 332),
            mdict1=binary.u32(data, 336),
            mdict2=binary.u32(data, 340),
            masterpages1=binary.u32(data, 344),
            masterpages2=binary.u32(data, 348),
            mainpages1=binary.u32(data, 352),
            mainpages2=binary.u32(data, 356),
            contents1=binary.u32(data, 360),
            contents2=binary.u32(data, 364),
            oldname=binary.cstring(data, 368, 12),
        )


class ImpressionDocument:
    """The full decoded contents of an Impression document.

    Construction is progressive: at this stage, only ``header`` is
    populated. Later stages (colours, styles, numbering, dictionary, pages,
    stories) add further attributes as they are implemented; see PLAN.md.
    """

    def __init__(self, source: "DocumentSource", header: FileHeader):
        self.source = source
        self.header = header
