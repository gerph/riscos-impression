"""Grouping the flat object-record streams (see model.frames) into pages,
master-page pairs, and chapters, and resolving a main-document frame to
its master-page counterpart.

See docs/impression-documents.xml, "Object-record streams" and "Section
object (XSECT)".
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from riscos_impression.model.frames import Frame, ObjectRecord, ObjectType, Page, Section

#: Object types that belong to whichever page most recently preceded them
#: in a stream.
_PAGE_MEMBER_TYPES = frozenset(
    {
        ObjectType.TEXT,
        ObjectType.PICTURE,
        ObjectType.BLANK,
        ObjectType.GUIDE,
        ObjectType.GROUP,
        ObjectType.UNKNOWN,
        None,  # an unrecognised type code
    }
)


@dataclass(frozen=True)
class PageGroup:
    """A page and the frame records that follow it, up to the next page
    or a chapter/document boundary."""

    page: Page
    offset: int
    records: tuple[ObjectRecord, ...]
    #: The master page this page's frames should be linked against (see
    #: "Section object (XSECT)" for the left/right alternation rule).
    #: None for master pages themselves, and for main-document pages in
    #: a chapter with no master page resolved.
    master_page: Optional["PageGroup"] = None

    @property
    def frames(self) -> tuple[object, ...]:
        """The decoded value of each frame record on this page (Frame
        subclasses; excludes any record whose type wasn't recognised),
        in *drawing* order -- stably sorted by each frame's own `level`
        (see docs/impression-documents.xml, "Frame flags word": "Front-
        to-back stacking level of the frame (0 upwards)"), not raw
        object-record stream order. A real document's own stream order
        doesn't have to match its visual stacking at all: a picture can
        (and, confirmed against a real document and the user's own
        reference image, does) sit at a *higher* level than a filled
        text frame that comes later in the stream, meaning the text
        frame's own opaque fill must be painted first and the picture
        drawn afterwards, on top of it -- the reverse of stream order.
        Frames sharing a level keep their relative stream order (a
        stable sort), which is what makes same-level chain/group
        members still resolve consistently."""
        return tuple(
            sorted(
                (r.value for r in self.records if r.value is not None),
                key=lambda frame: getattr(frame, "level", 0),
            )
        )


@dataclass(frozen=True)
class Chapter:
    section: Section
    offset: int
    master_page_1: Optional[PageGroup]
    master_page_2: Optional[PageGroup]
    pages: tuple[PageGroup, ...]


def group_pages(records: Sequence[ObjectRecord]) -> list[PageGroup]:
    """Group a flat run of object records into pages: each XPAGE record
    starts a new PageGroup, owning every following frame record up to the
    next XPAGE (or the end of *records*)."""
    groups: list[PageGroup] = []
    current_page: Optional[Page] = None
    current_offset = 0
    current_records: list[ObjectRecord] = []

    def flush() -> None:
        if current_page is not None:
            groups.append(
                PageGroup(page=current_page, offset=current_offset, records=tuple(current_records))
            )

    for record in records:
        if record.type is ObjectType.PAGE:
            flush()
            current_page = record.value
            current_offset = record.offset
            current_records = []
        elif current_page is not None and record.type in _PAGE_MEMBER_TYPES:
            current_records.append(record)
    flush()

    return groups


def split_into_chapters(records: Sequence[ObjectRecord]) -> list[tuple[Section, int, list[ObjectRecord]]]:
    """Split the main-document object-record stream into
    (section, section_offset, records) groups, one per XSECT. An XBRANCH
    record ends the current chapter without starting a new one."""
    chapters: list[tuple[Section, int, list[ObjectRecord]]] = []
    current_section: Optional[Section] = None
    current_offset = 0
    current_records: list[ObjectRecord] = []

    def flush() -> None:
        if current_section is not None:
            chapters.append((current_section, current_offset, current_records))

    for record in records:
        if record.type is ObjectType.SECTION:
            flush()
            current_section = record.value
            current_offset = record.offset
            current_records = []
        elif record.type is ObjectType.BRANCH:
            flush()
            current_section = None
            current_records = []
        elif current_section is not None:
            current_records.append(record)
    flush()

    return chapters


def find_master_page_pair(
    master_page_groups: Sequence[PageGroup], master_page_index: int
) -> tuple[Optional[PageGroup], Optional[PageGroup]]:
    """The pair of master pages a chapter's master_page_index selects: the
    master_page_index'th master PageGroup, plus a second one if the next
    master PageGroup shares the same top edge (y1)."""
    if not (0 <= master_page_index < len(master_page_groups)):
        return None, None
    primary = master_page_groups[master_page_index]
    secondary = None
    next_index = master_page_index + 1
    if next_index < len(master_page_groups):
        candidate = master_page_groups[next_index]
        if candidate.page.y1 == primary.page.y1:
            secondary = candidate
    return primary, secondary


def assign_master_pages(
    pages: Sequence[PageGroup],
    master_page_1: Optional[PageGroup],
    master_page_2: Optional[PageGroup],
    start_on_right: bool,
) -> tuple[PageGroup, ...]:
    """Assign each of a chapter's main-document pages the master page its
    frames should be linked against, alternating strictly through the
    page sequence (see "Section object (XSECT)")."""
    if master_page_1 is None:
        return tuple(pages)

    current = master_page_2 if (start_on_right and master_page_2 is not None) else master_page_1
    result = []
    for page in pages:
        result.append(dataclasses.replace(page, master_page=current))
        if master_page_2 is not None and current is master_page_1:
            current = master_page_2
        else:
            current = master_page_1
    return tuple(result)


def build_chapters(
    main_records: Sequence[ObjectRecord], master_page_groups: Sequence[PageGroup]
) -> list[Chapter]:
    """Assemble the full chapter/page/master-page structure from the
    main-document object-record stream and the already-grouped master
    pages (see group_pages)."""
    chapters = []
    for section, offset, records in split_into_chapters(main_records):
        pages = group_pages(records)
        master_page_1, master_page_2 = find_master_page_pair(
            master_page_groups, section.master_page_index
        )
        pages = assign_master_pages(
            pages, master_page_1, master_page_2, section.start_on_right
        )
        chapters.append(
            Chapter(
                section=section,
                offset=offset,
                master_page_1=master_page_1,
                master_page_2=master_page_2,
                pages=pages,
            )
        )
    return chapters


def find_master_frame(page: PageGroup, frame: Frame) -> Optional[ObjectRecord]:
    """The record on *page*'s master page whose own master_index equals
    *frame*'s, or None if *frame* isn't master-linked, the page has no
    master page, or no match is found. See "Frame object common layout"
    for why this is an equality match, not a positional lookup."""
    if not frame.master or page.master_page is None:
        return None
    for record in page.master_page.records:
        value = record.value
        if isinstance(value, Frame) and value.master_index == frame.master_index:
            return record
    return None
