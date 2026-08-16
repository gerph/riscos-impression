"""Text story decoding: paragraph/run content and the frame-reference
chain, from a story's raw ilinestr-framed byte stream.

See docs/impression-documents.xml, "Text Story Data".

This module decodes a single story's already-located bytes; locating
those bytes (via the master dictionary in single-file documents, or by
name in directory-mode documents) is a document-assembly concern handled
in Stage 6 (io/reader.py), not here. Likewise, resolving frame_chain's
raw byte offsets into actual Frame objects, and building the
generation-numbered chains used for repeating frames, needs the whole
object-record tree and so is also Stage 6's job; this module only
extracts the raw offsets as stored.

The paragraph-break ("pending newline") handling below mirrors the
conversion source's txwritedata() exactly, including its specific quirks:
a pending break (from CTRL_M) is only turned into an actual paragraph
split at the start of the next line record or at a CTRL_G/CTRL_H style
change -- not simply "whenever more content follows" -- and CTRL_N (a
forced page break) silently discards a pending break rather than
flushing it first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from riscos_impression import binary, encoding

ILINESTR_SIZE = 8

CTRL_E = 0x05
CTRL_G = 0x07
CTRL_H = 0x08
CTRL_K = 0x0B
CTRL_M = 0x0D
CTRL_N = 0x0E
CTRL_R = 0x12
CTRL_S = 0x13
CTRL_U = 0x15
CTRL_CLOSESQ = 0x1D

#: CTRL_A, CTRL_B, CTRL_V, CTRL_W, CTRL_X, CTRL_Z: skipped zero-terminated
#: lists of undetermined meaning.
_SKIP_LIST_CODES = frozenset({0x01, 0x02, 0x16, 0x17, 0x18, 0x1A})

KPAGE = 0x1
KCHAP = 0x2
KNUMBER = 0x4

SEMBED = 0x1
SMERGE = 0x2

FRAME_REFERENCE_KIND = 0x2
TEXT_CONTENT_KIND = 0x5


@dataclass(frozen=True)
class Run:
    text: str
    style_slots: tuple[int, ...]


@dataclass(frozen=True)
class PageNumberMark:
    pass


@dataclass(frozen=True)
class ChapterNumberMark:
    pass


@dataclass(frozen=True)
class HeadingNumberMark:
    tag: int


@dataclass(frozen=True)
class TabMark:
    pass


@dataclass(frozen=True)
class PageBreakMark:
    pass


@dataclass(frozen=True)
class EmbedMark:
    embed_tag: int


@dataclass(frozen=True)
class MergeMark:
    field_name: str


ParagraphItem = Union[
    Run,
    PageNumberMark,
    ChapterNumberMark,
    HeadingNumberMark,
    TabMark,
    PageBreakMark,
    EmbedMark,
    MergeMark,
]


@dataclass(frozen=True)
class Paragraph:
    items: tuple[ParagraphItem, ...]


@dataclass(frozen=True)
class Story:
    #: Raw byte offsets from frame-reference lines, in story order; see
    #: the module docstring for why these aren't resolved to Frame
    #: objects here.
    frame_chain: tuple[int, ...]
    paragraphs: tuple[Paragraph, ...]


class _StoryBuilder:
    def __init__(self) -> None:
        self.frame_chain: list[int] = []
        self.paragraphs: list[Paragraph] = []
        self.current_items: list[ParagraphItem] = []
        self.text_buffer: list[str] = []
        self.style_stack: list[int] = []
        self.pending_paragraph_break = False

    def flush_text(self) -> None:
        if self.text_buffer:
            self.current_items.append(
                Run(text="".join(self.text_buffer), style_slots=tuple(self.style_stack))
            )
            self.text_buffer = []

    def flush_pending_paragraph_break(self) -> None:
        if self.pending_paragraph_break:
            self.paragraphs.append(Paragraph(items=tuple(self.current_items)))
            self.current_items = []
            self.pending_paragraph_break = False

    def finish(self) -> Story:
        self.flush_text()
        self.paragraphs.append(Paragraph(items=tuple(self.current_items)))
        return Story(
            frame_chain=tuple(self.frame_chain), paragraphs=tuple(self.paragraphs)
        )


def _decode_text_line(data: bytes, payload_start: int, line_end: int, builder: _StoryBuilder) -> None:
    # The first 16 bytes of a text-content line's payload are always
    # skipped without interpretation (undetermined purpose).
    i = payload_start + 16

    builder.flush_pending_paragraph_break()

    while i < line_end:
        c = binary.u8(data, i)

        if c >= 32:
            # RISC OS Latin1 (alphabet 101), not plain ISO-8859-1 -- see
            # docs/impression-documents.xml, "Text and character
            # encoding". Bytes below 0x80 are unaffected; this only
            # matters once c reaches the C1 range.
            builder.text_buffer.append(encoding.decode_byte(c))
            i += 1
            continue

        if c == CTRL_E:
            i += 1

        elif c == CTRL_K:
            i = (i + 1 + 3) & ~3
            code = binary.u32(data, i)
            i += 4
            kind = code & 0xFF
            if kind == KPAGE:
                builder.flush_text()
                builder.current_items.append(PageNumberMark())
            elif kind == KCHAP:
                builder.flush_text()
                builder.current_items.append(ChapterNumberMark())
            elif kind == KNUMBER:
                builder.flush_text()
                builder.current_items.append(HeadingNumberMark(tag=(code >> 8) & 0xFFFFFF))

        elif c == CTRL_M:
            builder.flush_text()
            builder.pending_paragraph_break = True
            i += 1

        elif c == CTRL_N:
            builder.flush_text()
            builder.current_items.append(PageBreakMark())
            builder.pending_paragraph_break = False
            i += 1

        elif c == CTRL_R:
            i = (i + 1 + 3) & ~3
            while True:
                code = binary.u32(data, i)
                i += 4
                if code == 0 or i >= line_end:
                    break
            builder.flush_text()
            builder.current_items.append(TabMark())

        elif c == CTRL_S:
            builder.flush_text()
            i = (i + 1 + 3) & ~3
            length_field = binary.u32(data, i)
            discriminator = binary.u32(data, i + 4)
            if discriminator == SEMBED:
                embed_tag = binary.u32(data, i + 4 + 8)
                builder.current_items.append(EmbedMark(embed_tag=embed_tag))
            elif discriminator == SMERGE:
                field_name, _ = binary.nul_string(data, i + 4 + 12)
                builder.current_items.append(MergeMark(field_name=field_name))
            i += length_field

        elif c == CTRL_U:
            i = (i + 1 + 3) & ~3
            i += 8  # two ints, read and skipped; purpose undetermined

        elif c == CTRL_CLOSESQ:
            i = (i + 1 + 3) & ~3
            i += 16  # four ints, read and skipped; kerning adjustments

        elif c in (CTRL_G, CTRL_H):
            builder.flush_text()
            builder.flush_pending_paragraph_break()
            i = (i + 1 + 3) & ~3
            i += 4  # count field; the scanner looks for a zero terminator instead
            new_stack = []
            while True:
                code = binary.u32(data, i)
                i += 4
                if code == 0:
                    break
                new_stack.append(code & 0xFF)
                if i >= line_end:
                    break
            builder.style_stack = new_stack

        elif c in _SKIP_LIST_CODES:
            i = (i + 1 + 3) & ~3
            while True:
                code = binary.u32(data, i)
                i += 4
                if code == 0 or i >= line_end:
                    break

        else:
            i += 1  # unhandled control code; consumed with no special meaning


def parse_story(data: bytes) -> Story:
    """Decode one story's raw ilinestr-framed byte stream."""
    builder = _StoryBuilder()
    pos = 0
    length = len(data)

    while pos + ILINESTR_SIZE <= length:
        xx2 = binary.u8(data, pos + 4)
        line_length = binary.u24(data, pos + 5)
        if line_length == 0:
            break
        line_end = pos + line_length
        if line_end > length:
            break  # truncated/malformed story data; stop rather than misread

        kind = xx2 & 0x7
        if kind == FRAME_REFERENCE_KIND:
            builder.frame_chain.append(binary.u32(data, pos + ILINESTR_SIZE))
        elif kind == TEXT_CONTENT_KIND:
            _decode_text_line(data, pos + ILINESTR_SIZE, line_end, builder)

        pos = line_end

    return builder.finish()
