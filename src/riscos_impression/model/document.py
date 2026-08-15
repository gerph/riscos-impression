"""The document file header, and the top-level decoded document.

See docs/impression-documents.xml, "File Header".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Optional

from riscos_impression import binary
from riscos_impression.model.colours import Colour, decode_colour_word
from riscos_impression.model.dictionary import (
    MASTER_CHAPTER_DIRECTORY,
    DictionaryEntry,
    DictionaryEntryType,
    chapter_directory_name,
    chapter_index_for,
    story_length,
)
from riscos_impression.model.document_tree import Chapter, PageGroup, find_master_frame
from riscos_impression.model.frames import Frame, ObjectRecord
from riscos_impression.model.numbering import NumberingRecord
from riscos_impression.model.story import Story, parse_story
from riscos_impression.model.styles import Style

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
    """The full decoded contents of an Impression document: its header,
    colour and style tables, paragraph numbering, object dictionary,
    master pages, and chapters (each with its own pages and frames,
    already linked to whichever master page applies). See
    io.reader.load_document(), which builds one of these."""

    def __init__(
        self,
        source: "DocumentSource",
        header: FileHeader,
        colours: list[Colour],
        styles: list[Style],
        numbering: list[NumberingRecord],
        dictionary: list[DictionaryEntry],
        master_dictionary: list[int],
        master_pages: list[PageGroup],
        chapters: list[Chapter],
    ):
        self.source = source
        self.header = header
        self.colours = colours
        self.styles = styles
        self.numbering = numbering
        self.dictionary = dictionary
        self.master_dictionary = master_dictionary
        self.master_pages = master_pages
        self.chapters = chapters

    def resolve_colour_word(self, word: int) -> Optional[Colour]:
        return decode_colour_word(word, self.colours)

    def master_frame(self, page: PageGroup, frame: Frame) -> Optional[ObjectRecord]:
        """The record on *page*'s master page whose master_index matches
        *frame*'s, if any; see model.document_tree.find_master_frame."""
        return find_master_frame(page, frame)

    def _chapter_directory_for(self, entry: DictionaryEntry) -> str:
        chapter_index = chapter_index_for(self.dictionary, entry.index)
        if chapter_index is None:
            return MASTER_CHAPTER_DIRECTORY
        return chapter_directory_name(self.chapters[chapter_index].section.create_number)

    def story_bytes(self, entry: DictionaryEntry) -> bytes:
        """The raw bytes for a DCTEXT or DCPICT dictionary entry, resolved
        via the master dictionary (single-file mode) or by name within the
        document directory (directory mode)."""
        if not self.source.directory_mode:
            offset = self.master_dictionary[entry.index]
            length = story_length(self.master_dictionary, entry.index)
            return self.source.docdata[offset : offset + length]

        chapter_directory = self._chapter_directory_for(entry)
        if entry.type is DictionaryEntryType.PICTURE:
            return self.source.read_picture_file(chapter_directory, entry.id)
        if entry.type is DictionaryEntryType.TEXT:
            return self.source.read_text_chunk(chapter_directory, entry.id)
        raise ValueError(
            f"dictionary entry {entry.index} is a {entry.type}, not a story or picture"
        )

    def story(self, entry: DictionaryEntry) -> Story:
        if entry.type is not DictionaryEntryType.TEXT:
            raise ValueError(f"dictionary entry {entry.index} is not a text story")
        return parse_story(self.story_bytes(entry))

    def picture_bytes(self, entry: DictionaryEntry) -> bytes:
        if entry.type is not DictionaryEntryType.PICTURE:
            raise ValueError(f"dictionary entry {entry.index} is not a picture")
        return self.story_bytes(entry)
