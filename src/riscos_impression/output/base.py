"""Shared base for converting a decoded ImpressionDocument into an output
format: frame-chain resolution, master-frame/colour resolution, coordinate
transforms, and style-cascade resolution, plus a best-effort error
boundary backed by ConversionLog.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from riscos_impression.log import ConversionLog
from riscos_impression.model.colours import Colour
from riscos_impression.model.document import ImpressionDocument
from riscos_impression.model.document_tree import Chapter, PageGroup, find_master_frame
from riscos_impression.model.frames import Frame, ObjectRecord, Page
from riscos_impression.model.story import Story
from riscos_impression.model.styles import Style


def build_offset_index(page_groups: Sequence[PageGroup]) -> dict[int, ObjectRecord]:
    """Index every frame record across *page_groups* by its byte offset,
    for resolving a Story's frame_chain (see Converter.resolve_frame_chain)."""
    return {record.offset: record for page in page_groups for record in page.records}


@dataclasses.dataclass(frozen=True)
class PageOrigin:
    """A page's content-area origin (inside its bleed margin); matches
    orgx/orgy in the conversion source's ixpage()."""

    x: int
    y: int


def page_origin(page: Page) -> PageOrigin:
    return PageOrigin(x=page.x0 + page.bleed, y=page.y1 - page.bleed)


def to_page_coordinates(origin: PageOrigin, x: int, y: int) -> tuple[int, int]:
    """Convert an absolute document coordinate to one relative to a
    page's content origin, with Y flipped to increase downward from the
    page's top edge -- the convention the conversion source's own DDL
    output uses throughout."""
    return (x - origin.x, origin.y - y)


#: Style fields that identify or describe the record itself, rather than
#: participate in cascading (None-means-absent) inheritance.
#:
#: tab_stops is handled separately in resolve_style, not via this set's
#: usual "None means absent" cascade rule: its own absent-value sentinel
#: is an *empty tuple* (a style whose own `tabs` bitmask has no bits
#: set), not None, so folding it into the generic loop below would let
#: any such style wrongly wipe out an already-cascaded ruler from
#: further out the stack. `tabs` itself (the raw on-disk bitmask) stays
#: fully non-cascading -- nothing reads it once tab_stops is decoded.
_NON_CASCADING_STYLE_FIELDS = frozenset(
    {
        "index",
        "is_body_text",
        "name",
        "key",
        "paragraph_apply",
        "is_contents_entry_style",
        "is_index_entry_style",
        "is_effect",
        "shows_on_style_menu",
        "text_back_colour",
        "tabs",
        "tab_stops",
        "unknown",
    }
)


class Converter:
    """Base class for a document -> output-format converter.

    Provides the resolution and coordinate helpers every converter needs,
    and a best-effort error boundary (catch()) that logs rather than
    raises unless strict is set. Subclasses implement convert(); the
    optional begin_*/end_*/emit_frame hooks and the default convert()
    below are a convenience for converters that fit a simple chapter/
    page/frame walk -- override convert() entirely if a different
    structure fits the target format better.
    """

    def __init__(
        self,
        document: ImpressionDocument,
        log: Optional[ConversionLog] = None,
        strict: bool = False,
    ):
        self.document = document
        self.log = log if log is not None else ConversionLog()
        self.strict = strict

    @contextmanager
    def catch(self, area: str, location: Optional[str] = None):
        """Run a block that should work; if it raises and strict is not
        set, log the exception as an error and continue rather than
        aborting the whole conversion."""
        try:
            yield
        except Exception as e:  # noqa: BLE001 - the conversion-wide safety net
            if self.strict:
                raise
            self.log.error(area, str(e), location)

    # -- Colour / master-frame resolution --------------------------------

    def resolve_colour(self, word: int) -> Optional[Colour]:
        return self.document.resolve_colour_word(word)

    def master_frame(self, page: PageGroup, frame: Frame) -> Optional[ObjectRecord]:
        return find_master_frame(page, frame)

    # -- Style cascade ----------------------------------------------------

    def resolve_style(self, style_slots: Sequence[int]) -> Style:
        """The effective style for a run active under *style_slots* (in
        stack order, outermost first): the body style (slot 0), with
        each named style in turn overriding any field it actually
        specifies (a non-None value). Unknown slot numbers are ignored.

        tab_stops is the one field that cascades on its own, non-empty
        rule instead: a style whose own `tabs` bitmask has no bits set
        decodes to an empty tab_stops tuple, which means "this style
        doesn't define a ruler of its own", not "this style defines an
        empty ruler" -- so it must not override one already cascaded
        from further out the stack (see _NON_CASCADING_STYLE_FIELDS).

        line_spacing_raw gets a similar, narrower exception: the
        conversion source's own style emitter (c/styles) only ever
        writes an explicit `{leading ...}` for a style that has its own
        linespace bit set -- body text's is *always* set (its presence
        flag is forced), everything else only if the author actually
        chose one -- so body's own value is never meant to stand in for
        an unrelated style's leading. When it's left to fall through
        unchanged from body and that value is a FIXED (absolute-point,
        not proportional) leading frozen for body's own font size, and
        the resolved font_size differs from body's, keep it as "unset"
        instead so the renderer's own size-relative default applies.
        A real document (ForSimon3 from the local moreexamples/ corpus)
        had a 26pt heading style inherit body's fixed ~13pt leading
        verbatim, producing severely overlapping lines. Proportional
        leading is scale-invariant and is left to cascade normally."""
        styles_by_slot = {style.index: style for style in self.document.styles}
        body = styles_by_slot.get(0)
        if body is None:
            raise ValueError("document has no body style (slot 0)")

        values = {field.name: getattr(body, field.name) for field in dataclasses.fields(Style)}
        line_spacing_overridden = False
        for slot in style_slots:
            style = styles_by_slot.get(slot)
            if style is None:
                continue
            if style.tab_stops:
                values["tab_stops"] = style.tab_stops
            for field in dataclasses.fields(Style):
                if field.name in _NON_CASCADING_STYLE_FIELDS:
                    continue
                value = getattr(style, field.name)
                if value is not None:
                    values[field.name] = value
                    if field.name == "line_spacing_raw":
                        line_spacing_overridden = True

        raw_line_spacing = values["line_spacing_raw"]
        if (
            not line_spacing_overridden
            and raw_line_spacing is not None
            and raw_line_spacing & 0x80000000  # fixed, not proportional
            and values["font_size"] != body.font_size
        ):
            values["line_spacing_raw"] = None
        return Style(**values)

    # -- Frame-chain resolution -------------------------------------------

    def resolve_frame_chain(
        self, story: Story, *, chapter: Optional[Chapter] = None, master: bool = False
    ) -> list[ObjectRecord]:
        """Resolve a Story's frame_chain (raw byte offsets; see
        model.story.Story) to the actual object records they reference,
        in story order (records rather than bare Frame values, since a
        record's offset is usually what a caller needs next -- to build
        a DDL-style identifier, for example).

        A raw offset is never docdata-absolute by itself: the
        conversion source resolves it as masterpages1 + offset (for a
        master-page story) or mainpages2 + offset (for a main-document
        story) in single-file documents. In directory-mode documents, a
        main-document story's on-disk offset is instead stored relative
        to the chapter's own Section record, and getstory() adds that
        record's own (docdata-absolute) offset before the same
        mainpages2-relative resolution would apply -- which nets out to
        simply the chapter's absolute offset, with no mainpages2 term at
        all. *chapter* is required unless master=True. Any offset that
        doesn't resolve is logged and skipped."""
        header = self.document.header
        if master:
            index = build_offset_index(self.document.master_pages)
            adjustment = header.masterpages1
        else:
            if chapter is None:
                raise ValueError(
                    "chapter is required to resolve a main-document frame chain"
                )
            index = build_offset_index(chapter.pages)
            adjustment = (
                chapter.offset if self.document.source.directory_mode else header.mainpages2
            )

        records = []
        for raw_offset in story.frame_chain:
            record = index.get(raw_offset + adjustment)
            if record is not None and isinstance(record.value, Frame):
                records.append(record)
            else:
                self.log.error(
                    "story", f"frame chain offset {raw_offset} did not resolve to a frame"
                )
        return records

    # -- Default chapter/page/frame walk -----------------------------------

    def convert(self, output_path: Path) -> None:
        self.begin_document()
        for chapter in self.document.chapters:
            self.begin_chapter(chapter)
            for page in chapter.pages:
                self.begin_page(chapter, page)
                for frame in page.frames:
                    location = (
                        f"chapter {chapter.section.create_number} page @0x{page.offset:x}"
                    )
                    with self.catch("frame", location=location):
                        self.emit_frame(chapter, page, frame)
                self.end_page(chapter, page)
            self.end_chapter(chapter)
        self.end_document()
        self.write(output_path)

    def begin_document(self) -> None:
        pass

    def begin_chapter(self, chapter: Chapter) -> None:
        pass

    def begin_page(self, chapter: Chapter, page: PageGroup) -> None:
        pass

    def emit_frame(self, chapter: Chapter, page: PageGroup, frame: object) -> None:
        pass

    def end_page(self, chapter: Chapter, page: PageGroup) -> None:
        pass

    def end_chapter(self, chapter: Chapter) -> None:
        pass

    def end_document(self) -> None:
        pass

    def write(self, output_path: Path) -> None:
        raise NotImplementedError
