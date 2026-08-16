"""Best-effort Markdown output: serialises each story's text as plain
paragraphs, inferring heading levels and attempting to recognise a
grid of bordered frames as a table. This is a deliberately lossy
format -- there is no confirmed "this is a heading" or "this is a
table" concept anywhere in the Impression document model, so both are
judgement calls made from indirect evidence, not confirmed document
facts:

* A paragraph is treated as a heading candidate only if its dominant
  style is paragraph-scoped (`paragraph_apply`) and not the body style;
  its level (H1-H5) is then picked from its font size relative to the
  body style's own -- a bigger jump in size ranks as a bigger (lower-
  numbered) heading. There is no such thing as an "H6" here: anything
  that doesn't clear the smallest threshold is treated as an ordinary
  paragraph instead of guessing a level for it.
* A page's bordered TextFrame/BlankFrame records are recognised as a
  Markdown table only when they form a clean, consistent grid: at
  least two rows and two columns, and every row sharing the same set
  of column X-positions (within a small tolerance). Anything less
  regular is left as ordinary paragraphs instead of guessing at a
  table structure that isn't really there.

Pictures are left as simple bracketed placeholders (e.g. `[draw]`);
Markdown has no meaningful inline image support here (no rasterised
image data exists to embed even if it did).
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from riscos_impression.model.dictionary import DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import BlankFrame, Frame, ObjectRecord, PictureFrame, TextFrame
from riscos_impression.model.numbering import NumberingStyle, resolve_number
from riscos_impression.model.story import (
    ChapterNumberMark,
    EmbedMark,
    HeadingNumberMark,
    MergeMark,
    PageBreakMark,
    PageNumberMark,
    Run,
    TabMark,
)
from riscos_impression.output.base import Converter

#: Millipoints; frames within this of each other are treated as
#: sharing a row/column boundary when detecting a table grid.
_GRID_TOLERANCE = 2000

#: font_size ratios (relative to the body style's own) that pick a
#: heading level; see the module docstring for why these are a
#: judgement call, not a confirmed document fact.
_HEADING_RATIO_LEVELS = [
    (1.8, 1),
    (1.5, 2),
    (1.3, 3),
    (1.15, 4),
    (1.05, 5),
]

_MD_SPECIAL = re.compile(r"([\\`*_\[\]])")


def _escape_markdown(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


class MarkdownConverter(Converter):
    """Renders the decoded document model as best-effort Markdown. See
    the module docstring for the heading and table heuristics."""

    def __init__(self, document, log=None, strict: bool = False):
        super().__init__(document, log=log, strict=strict)

    def convert(self, output_path: Path) -> None:
        self._chapter_number = 0
        self._rendered_dictionary_indices: set[int] = set()
        self._dictionary_by_index = {entry.index: entry for entry in self.document.dictionary}
        self._logged_picture_note = False
        self._body_font_size = self._resolve_body_font_size()

        chapter_parts = []
        for chapter in self.document.chapters:
            self._chapter_number += 1
            with self.catch("chapter", location=f"chapter {chapter.section.create_number}"):
                chapter_parts.append(self._render_chapter(chapter))

        text = ("\n---\n\n".join(part for part in chapter_parts if part)) + "\n"
        Path(output_path).write_text(text, encoding="utf-8")

    # -- Chapters / pages -----------------------------------------------------

    def _render_chapter(self, chapter: Chapter) -> str:
        parts = []
        for page in chapter.pages:
            grid, consumed_offsets = self._detect_table(page)
            if grid:
                parts.append(self._render_table(grid))
            for record in page.records:
                if record.offset in consumed_offsets:
                    continue
                frame = record.value
                if not isinstance(frame, Frame):
                    continue
                with self.catch(
                    "frame", location=f"chapter {chapter.section.create_number} @0x{record.offset:x}"
                ):
                    parts.append(self._render_frame(frame))
        return "\n".join(part for part in parts if part)

    # -- Table detection -----------------------------------------------------

    def _detect_table(self, page: PageGroup) -> tuple[Optional[list[list[ObjectRecord]]], set[int]]:
        candidates = [
            r for r in page.records
            if isinstance(r.value, (TextFrame, BlankFrame)) and r.value.has_border
        ]
        if len(candidates) < 4:
            return None, set()

        rows: dict[int, list[ObjectRecord]] = defaultdict(list)
        for record in candidates:
            key = next((y for y in rows if abs(y - record.value.y0) <= _GRID_TOLERANCE), record.value.y0)
            rows[key].append(record)
        if len(rows) < 2:
            return None, set()

        # Top row first (Y increases upward), left-to-right within a row.
        grid = [sorted(members, key=lambda r: r.value.x0) for _, members in sorted(rows.items(), key=lambda kv: -kv[0])]
        col_count = len(grid[0])
        if col_count < 2 or any(len(row) != col_count for row in grid):
            return None, set()  # an inconsistent number of cells per row; not a clean grid

        for col_index in range(col_count):
            x0s = [row[col_index].value.x0 for row in grid]
            if max(x0s) - min(x0s) > _GRID_TOLERANCE:
                return None, set()  # columns don't line up consistently; don't guess

        consumed = {record.offset for row in grid for record in row}
        return grid, consumed

    def _render_table(self, grid: list[list[ObjectRecord]]) -> str:
        self.log.best_effort(
            "table",
            f"recognised a {len(grid)}x{len(grid[0])} grid of bordered frames as a "
            "table (a layout-based guess, not a confirmed table concept in the format)",
        )
        rows_md = []
        for row_index, row in enumerate(grid):
            cells = [self._cell_text(record) for record in row]
            rows_md.append("| " + " | ".join(cells) + " |")
            if row_index == 0:
                rows_md.append("| " + " | ".join("---" for _ in row) + " |")
        return "\n".join(rows_md) + "\n"

    def _cell_text(self, record: ObjectRecord) -> str:
        frame = record.value
        if frame.dictionary_index < 0:
            return ""
        entry = self._dictionary_by_index.get(frame.dictionary_index)
        if entry is None or entry.type is not DictionaryEntryType.TEXT:
            return ""
        self._rendered_dictionary_indices.add(entry.index)
        story = None
        with self.catch("story", location=f"dictionary entry {entry.index}"):
            story = self.document.story(entry)
        if story is None:
            return ""
        pieces = []
        for paragraph in story.paragraphs:
            text = "".join(getattr(item, "text", "") for item in paragraph.items)
            if text.strip():
                pieces.append(text.strip())
        return _escape_markdown(" ".join(pieces)).replace("|", "\\|")

    # -- Frames -----------------------------------------------------------

    def _render_frame(self, frame: Frame) -> str:
        if isinstance(frame, PictureFrame):
            return self._render_picture(frame)
        if isinstance(frame, (TextFrame, BlankFrame)):
            return self._render_text_frame(frame)
        return ""

    def _render_picture(self, pict: PictureFrame) -> str:
        if pict.dictionary_index < 0:
            return ""
        entry = self._dictionary_by_index.get(pict.dictionary_index)
        if entry is None or entry.type is not DictionaryEntryType.PICTURE:
            return ""
        if not self._logged_picture_note:
            self._logged_picture_note = True
            self.log.info(
                "picture",
                "pictures are left as bracketed placeholders; Markdown output has no inline image support",
            )
        kind = entry.embedded_object_type
        label = kind.value if kind is not None else "picture"
        return f"[{label}]\n"

    def _render_text_frame(self, frame) -> str:
        if frame.dictionary_index < 0:
            return ""
        entry = self._dictionary_by_index.get(frame.dictionary_index)
        if entry is None:
            return ""
        if entry.type is not DictionaryEntryType.TEXT:
            return ""  # a blank frame's link may resolve to a picture instead
        if entry.index in self._rendered_dictionary_indices:
            return ""
        self._rendered_dictionary_indices.add(entry.index)

        story = None
        with self.catch("story", location=f"dictionary entry {entry.index}"):
            story = self.document.story(entry)
        if story is None:
            return ""
        return "\n".join(part for part in (self._render_paragraph(p, entry.index) for p in story.paragraphs) if part)

    def _render_paragraph(self, paragraph, dictionary_index: int) -> str:
        text_parts: list[str] = []
        #: The first non-blank Run's own style_slots -- *not* a resolved
        #: Style, since resolve_style()'s cascade always reports
        #: is_body_text=True/paragraph_apply=False regardless of which
        #: named style was actually applied (both are non-cascading
        #: fields, inherited from the body style by construction); an
        #: empty tuple here means no named style was applied at all
        #: (plain body text), which is the only reliable non-heading
        #: signal available.
        dominant_slots: Optional[tuple] = None

        for item in paragraph.items:
            if isinstance(item, Run):
                if dominant_slots is None and item.text.strip():
                    dominant_slots = item.style_slots
                text_parts.append(item.text)
            elif isinstance(item, PageNumberMark):
                pass  # meaningless outside of a paginated layout
            elif isinstance(item, ChapterNumberMark):
                text_parts.append(str(self._chapter_number))
            elif isinstance(item, HeadingNumberMark):
                text_parts.append(self._resolve_number_text(item.tag, dictionary_index))
            elif isinstance(item, TabMark):
                text_parts.append("\t")
            elif isinstance(item, PageBreakMark):
                pass  # no page concept in a flat Markdown document
            elif isinstance(item, MergeMark):
                text_parts.append(f"<<{item.field_name}>>")
            elif isinstance(item, EmbedMark):
                pass  # embedded frames aren't followed inline in Markdown output

        text = "".join(text_parts).strip()
        if not text:
            return ""
        text = _escape_markdown(text)
        level = self._heading_level(dominant_slots)
        if level:
            return f"{'#' * level} {text}\n"
        return f"{text}\n"

    def _heading_level(self, style_slots: Optional[tuple]) -> Optional[int]:
        if not style_slots:
            return None  # plain body text (or an empty/mark-only paragraph) is never a heading
        style = self.resolve_style(style_slots)
        body_size = self._body_font_size
        size = style.font_size or body_size
        ratio = size / body_size if body_size else 1.0
        for threshold, level in _HEADING_RATIO_LEVELS:
            if ratio >= threshold:
                return level
        return None

    def _resolve_body_font_size(self) -> int:
        """The body style's own font_size, for the heading-ratio
        calculation -- or a plausible default (10pt, matching every
        other converter's own fallback) for the edge case of a
        document with no styles at all (e.g. one with no chapters,
        never actually reaching a heading decision either way)."""
        try:
            return self.resolve_style([]).font_size or 160
        except ValueError:
            return 160

    def _resolve_number_text(self, tag: int, dictionary_index: int) -> str:
        record = next(
            (r for r in self.document.numbering if r.dictionary_index == dictionary_index and r.tag == tag),
            None,
        )
        if record is None:
            self.log.error("numbering", f"no numbering record for tag {tag}")
            return ""
        if record.style is not NumberingStyle.DECIMAL:
            self.log.unsupported(
                "numbering",
                f"{record.style.name if record.style else record.raw_style} numbering "
                "style not implemented; only decimal is (matches the conversion "
                "source's own gap, not just this converter's)",
            )
            return ""
        return str(resolve_number(self.document.numbering, dictionary_index, tag))
