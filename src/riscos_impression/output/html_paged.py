"""Paged-media HTML5 output: one page-sized <div> per Impression page,
each frame absolutely positioned within it from its own decoded
geometry, styled so a browser can preview it as stacked pages and an
external paged-media renderer (Prince or WeasyPrint, if either is found
on PATH -- see _export_pdf) can export it straight to PDF.

Frame geometry uses the same page_origin/to_page_coordinates helpers
the OvProDDL converter does (output/base.py): CSS's own coordinate
system is top-left-origin, Y-down -- the same convention OvationPro's
own DDL format uses -- unlike the PDF converter, which needed no Y-flip
at all, since PDF's native page space is bottom-left, Y-up (matching
Impression's own coordinates directly).

This converter is deliberately simpler than the PDF one: a browser's
own block layout wraps text *within* a frame's sized <div> natively, so
none of pdfdoc.py's approximate-metrics per-line positioning is needed
for a story confined to a single frame -- it renders in full there,
clipped (CSS overflow: hidden) if it doesn't fit, with no attempt made
to measure whether it actually overflows.

A story spanning a real, chapter-anchored multi-frame chain is
different: a browser has no native way to flow text across several
separately-positioned, fixed-size boxes, so this converter estimates
how much of the story's own text belongs in each chain member using
the same approximate character-width metrics pdfdoc.py's own text flow
uses (a duplicate, self-contained copy -- see _approx_width and
_flow_chained_story/_flow_items_into_containers below), splitting only
at paragraph and PageBreakMark boundaries rather than pdfdoc.py's own
per-line granularity (a browser still does the actual within-frame
line-wrapping natively, once it knows which paragraphs/slices belong
in which frame). A frame_chain that doesn't resolve against its own
chapter's pages at all (independently-repeated master content, not a
real chain) falls back to the original single-frame-only handling.

Dynamic text repel around an obstacle picture (as pdfdoc.py does) is
not attempted; every frame is positioned independently via CSS
`position: absolute`, so an obstacle's box and a text frame's box can
visually overlap exactly as they do in the source document.

Paragraph-level formatting (left/right margin, first-line indent,
alignment, space before/after, line height) is applied as inline CSS on
each `<p>` via html_base.py's paragraph_css_properties, using the
enclosing frame's own real content width to sanity-check right_indent
the same way pdfdoc.py does (an oversized right_indent, authored for a
wider frame than it's actually used in, is dropped rather than
squeezing a frame's text into an unreadable column) -- meaningful here
because a frame's width in this converter matches the source document's
own geometry exactly, unlike scrolling HTML's reflowed, viewport-width
columns.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from riscos_impression.log import ConversionLog
from riscos_impression.model.dictionary import DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import BlankFrame, Frame, GroupFrame, GuideFrame, PictureFrame, TextFrame
from riscos_impression.model.numbering import NumberingStyle, resolve_number
from riscos_impression.model.story import (
    ChapterNumberMark,
    EmbedMark,
    HeadingNumberMark,
    MergeMark,
    PageBreakMark,
    PageNumberMark,
    Run,
    Story,
    TabMark,
)
from riscos_impression.model.styles import Style
from riscos_impression.output import font_metrics
from riscos_impression.output.base import page_origin, to_page_coordinates
from riscos_impression.output.html_base import (
    HTML5Converter,
    colour_to_css,
    css_style_attr,
    escape_html,
    paragraph_css_properties,
    paragraph_line_height_pt,
    style_css_properties,
)

#: Millipoints per CSS point; see docs/impression-documents.xml's note
#: under "Frame object common layout".
UNIT = 1000.0

#: Used only when a style carries no font_size at all, matching
#: pdfdoc.py's own _DEFAULT_FONT_SIZE_16THS (10pt).
_DEFAULT_FONT_SIZE_16THS = 160

#: Below this usable width (in points), a line is treated as having no
#: room at all -- matches pdfdoc.py's own _MIN_USABLE_WIDTH.
_MIN_USABLE_WIDTH = 10.0

#: RISC OS font name substring -> font_metrics.WIDTHS_256PT family, for
#: _approx_width's own estimate of how many lines a chained story's
#: paragraph will wrap to at a given frame width (see
#: _flow_chained_story). A *duplicate*, self-contained copy of
#: pdfdoc.py's own metrics font-selection, not shared with it (matching
#: this project's convention of independent converters) or with
#: html_base.py's differently-shaped, CSS-stack-oriented _FAMILY_HINTS.
_METRICS_FAMILY_HINTS = [
    ("trinity", "Times"),
    ("times", "Times"),
    ("homerton", "Helvetica"),
    ("corpus", "Courier"),
]
_AVERAGE_WIDTH_FACTOR = {"Helvetica": 0.52, "Times": 0.46, "Courier": 0.6}
_RISCOS_METRICS_FONT = {
    "Helvetica": {
        (False, False): "Homerton.Medium",
        (True, False): "Homerton.Bold",
        (False, True): "Homerton.Medium.Oblique",
        (True, True): "Homerton.Bold.Oblique",
    },
    "Times": {
        (False, False): "Trinity.Medium",
        (True, False): "Trinity.Bold",
        (False, True): "Trinity.Medium.Italic",
        (True, True): "Trinity.Bold.Italic",
    },
}


def _metrics_base_family(font_style_name: Optional[str]) -> str:
    name = (font_style_name or "").lower()
    for hint, family in _METRICS_FAMILY_HINTS:
        if hint in name:
            return family
    return "Helvetica"


def _approx_width(text: str, style: Style) -> float:
    """Estimated width (in points) of *text* set in *style* -- a
    duplicate, self-contained copy of pdfdoc.py's own _approx_width,
    used here only to estimate how many lines a chained story's
    paragraph will wrap to at a given frame width (see
    _flow_chained_story), never to position anything precisely (a
    browser does that natively once it knows which slice of text
    belongs in which frame)."""
    size = (style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
    family = _metrics_base_family(style.font_style_name)
    variants = _RISCOS_METRICS_FONT.get(family)
    if variants is not None:
        name = (style.font_style_name or "").lower()
        is_bold = bool(style.bold) or "bold" in name
        is_italic = bool(style.italic) or "italic" in name or "oblique" in name
        metrics_font = variants[(is_bold, is_italic)]
        total_per_mille = 0.0
        for ch in text:
            per_mille = font_metrics.char_width_per_mille(metrics_font, ch)
            if per_mille is None:
                break
            total_per_mille += per_mille
        else:
            return total_per_mille / 1000.0 * size
    return len(text) * size * _AVERAGE_WIDTH_FACTOR.get(family, 0.5)

_DOCUMENT_CSS = """\
@media print { .ro-page { margin: 0; box-shadow: none; page-break-after: always; } }
body { margin: 0; padding: 1em 0; background: #888888; }
.ro-page { position: relative; margin: 0 auto 1em auto; background: #ffffff; box-shadow: 0 0 8px rgba(0,0,0,0.4); overflow: hidden; }
.ro-frame { position: absolute; overflow: hidden; box-sizing: border-box; }
.ro-frame p { margin: 0 0 0.6em 0; }
"""


class PagedHTMLConverter(HTML5Converter):
    """Renders the decoded document model as CSS paged-media HTML: one
    page-sized <div> per Impression page, each frame absolutely
    positioned within it from its own decoded geometry. See the module
    docstring for what's simplified compared to the PDF converter."""

    def __init__(
        self,
        document,
        log=None,
        strict: bool = False,
        border_width_pt: float = 0.5,
        export_pdf: bool = True,
    ):
        super().__init__(document, log=log, strict=strict)
        self.border_width_pt = border_width_pt
        #: Whether to also try to export a PDF via Prince or WeasyPrint,
        #: if either is found on PATH; see _export_pdf.
        self.export_pdf = export_pdf

    # -- Top level -----------------------------------------------------------

    def begin_document(self) -> None:
        self._chapter_number = 0
        self._page_number = 0
        self._rendered_dictionary_indices: set[int] = set()
        self._chain_warned: set[int] = set()
        self._dictionary_by_index = {entry.index: entry for entry in self.document.dictionary}
        self._pages_html: list[str] = []
        #: dictionary_index -> {id(frame): html}, populated once (on the
        #: first frame encountered) by _flow_chained_story for a story
        #: with a real, chapter-anchored multi-frame chain; each later
        #: chain member's own _render_text_frame call just pops its own
        #: slice out. See _render_text_frame.
        self._chain_html: dict[int, dict[int, str]] = {}
        #: chapter id -> {id(frame): PageGroup}; see _frame_page_map.
        self._frame_page_maps: dict[int, dict] = {}

    def begin_chapter(self, chapter: Chapter) -> None:
        self._chapter_number += 1

    def begin_page(self, chapter: Chapter, page: PageGroup) -> None:
        self._page_number += 1
        self._origin = page_origin(page.page)
        w_pt = page.page.print_width / UNIT
        h_pt = page.page.print_height / UNIT
        self._page_parts = [f'<div class="ro-page" style="width: {w_pt:.2f}pt; height: {h_pt:.2f}pt;">\n']

        if page.master_page is not None:
            linked_indices = {f.master_index for f in page.frames if f.master}
            for mframe in page.master_page.frames:
                if mframe.master_index in linked_indices:
                    continue
                with self.catch(
                    "frame", location=f"chapter {chapter.section.create_number} master furniture"
                ):
                    self._page_parts.append(self._render_frame(mframe, chapter, page))

    def emit_frame(self, chapter: Chapter, page: PageGroup, frame: object) -> None:
        self._page_parts.append(self._render_frame(frame, chapter, page))

    def end_page(self, chapter: Chapter, page: PageGroup) -> None:
        self._page_parts.append("</div>\n")
        self._pages_html.append("".join(self._page_parts))

    def write(self, output_path: Path) -> None:
        html = self._wrap_document("".join(self._pages_html))
        output_path = Path(output_path)
        output_path.write_text(html, encoding="utf-8")
        if self.export_pdf:
            self._export_pdf(output_path)

    def _wrap_document(self, body_html: str) -> str:
        return (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            "<title>Impression document</title>\n"
            f"<style>\n{_DOCUMENT_CSS}</style>\n</head>\n<body>\n"
            f"{body_html}"
            "</body>\n</html>\n"
        )

    def _export_pdf(self, html_path: Path) -> None:
        pdf_path = html_path.with_suffix(".pdf")
        for tool, build_args in (
            ("prince", lambda exe: [exe, str(html_path), "-o", str(pdf_path)]),
            ("weasyprint", lambda exe: [exe, str(html_path), str(pdf_path)]),
        ):
            exe = shutil.which(tool)
            if exe is None:
                continue
            try:
                subprocess.run(build_args(exe), check=True, capture_output=True)
                self.log.info("export", f"exported {pdf_path.name} via {tool}")
            except Exception as e:  # noqa: BLE001 - PDF export is opportunistic, never fatal
                self.log.best_effort("export", f"{tool} PDF export failed: {e}")
            return
        self.log.info("export", "neither prince nor weasyprint found on PATH; PDF export skipped")

    # -- Frames -----------------------------------------------------------

    def _effective_frame(self, frame: Frame, page: PageGroup) -> Frame:
        """The Frame whose geometry/fill/border should actually be drawn
        for *frame*: itself, unless it's master-linked, in which case its
        appearance is inherited from the corresponding master-page frame
        (matching the PDF and OvProDDL converters' own choice -- the
        local record's own geometry isn't meaningful in that case)."""
        if not frame.master:
            return frame
        record = self.master_frame(page, frame)
        if record is not None and isinstance(record.value, Frame):
            return record.value
        self.log.error("frame", "master frame not found for a master-linked frame")
        return frame

    def _render_frame(self, frame: Frame, chapter: Chapter, page: PageGroup) -> str:
        if frame.embed_tag:
            # Anchored inline within a text story instead, at the
            # matching EmbedMark's own position (_render_embed) --
            # never drawn at its own raw position on the page in
            # normal front-to-back order; mirrors pdfdoc.py's own
            # _draw_frame check exactly. Drawing it here too, at its
            # raw (and often stale/irrelevant) box, was confirmed
            # against a real document (PCI_Spec from the local
            # examples/ corpus, whose DrawFile diagrams appeared
            # doubled and overlapping running text on one page) as the
            # direct cause -- newly visible once the multi-frame chain
            # flow fix let a story's own text actually reach the
            # matching EmbedMark, rather than never being rendered
            # far enough into the story to reach it at all.
            return ""
        if isinstance(frame, (GuideFrame, GroupFrame)):
            return ""  # non-printing / no visual content of its own

        appearance = self._effective_frame(frame, page)
        # A master-linked frame's appearance is substituted from the
        # master page, which keeps its own, entirely separate absolute
        # coordinate canvas (confirmed empirically while building the
        # PDF converter: content pages within one chapter share one
        # contiguous vertical canvas, but master pages live in a
        # different object-record stream with their own origin) -- so
        # its coordinates must be converted using the *master* page's
        # own origin, not the content page's.
        origin = self._origin if appearance is frame else page_origin(page.master_page.page)
        x0, y0 = to_page_coordinates(origin, appearance.x0, appearance.y1)
        x1, y1 = to_page_coordinates(origin, appearance.x1, appearance.y0)
        left, top = x0 / UNIT, y0 / UNIT
        width, height = (x1 - x0) / UNIT, (y1 - y0) / UNIT
        if width <= 0 or height <= 0:
            return ""

        styles = [f"left: {left:.2f}pt", f"top: {top:.2f}pt", f"width: {width:.2f}pt", f"height: {height:.2f}pt"]
        fill = appearance.fill_colour(self.document.colours) if appearance.filled else None
        fill_css = colour_to_css(fill)
        if fill_css:
            styles.append(f"background-color: {fill_css}")
        if appearance.has_border:
            # Each edge is independent (0xFF = absent; see
            # docs/impression-documents.xml, "Frame object common
            # layout" -- border0=top, border1=left, border2=right,
            # border3=bottom, confirmed empirically against a real
            # document), so a uniform `border` shorthand is wrong
            # whenever fewer than all four are set.
            border_css = colour_to_css(appearance.border_colour(self.document.colours)) or "#000000"
            border_spec = f"{self.border_width_pt:.1f}pt solid {border_css}"
            if appearance.border0 != 0xFF:
                styles.append(f"border-top: {border_spec}")
            if appearance.border1 != 0xFF:
                styles.append(f"border-left: {border_spec}")
            if appearance.border2 != 0xFF:
                styles.append(f"border-right: {border_spec}")
            if appearance.border3 != 0xFF:
                styles.append(f"border-bottom: {border_spec}")
        h_inset = max(0.0, appearance.hinset / UNIT)
        v_inset = max(0.0, appearance.vinset / UNIT)
        if h_inset or v_inset:
            styles.append(f"padding: {v_inset:.2f}pt {h_inset:.2f}pt")

        content = ""
        if isinstance(frame, PictureFrame):
            content = self._render_picture(frame)
        elif isinstance(frame, (TextFrame, BlankFrame)):
            content_width_pt = max(0.0, width - 2 * h_inset)
            content = self._render_text_frame(frame, chapter, content_width_pt)

        style_attr = "; ".join(styles)
        return f'<div class="ro-frame" style="{style_attr}">{content}</div>\n'

    def _render_picture(self, pict: PictureFrame) -> str:
        if pict.dictionary_index < 0:
            return ""
        entry = self._dictionary_by_index.get(pict.dictionary_index)
        if entry is None:
            self.log.error("picture", f"no dictionary entry for index {pict.dictionary_index}")
            return ""
        if entry.type is not DictionaryEntryType.PICTURE:
            return ""
        return self._picture_html(pict, entry)

    def _render_text_frame(self, frame, chapter: Chapter, content_width_pt: float) -> str:
        if frame.dictionary_index < 0:
            return ""
        entry = self._dictionary_by_index.get(frame.dictionary_index)
        if entry is None:
            return ""
        if entry.type is not DictionaryEntryType.TEXT:
            return ""  # a blank frame's link may resolve to a picture instead

        # A real chapter-anchored chain's full per-frame split was
        # already computed the first time any of its members was
        # visited (see below); every later member -- whether visited
        # before or after this one in page-walk order, since
        # resolve_frame_chain doesn't depend on that -- just pops its
        # own slice out here.
        cached_chain = self._chain_html.get(entry.index)
        if cached_chain is not None:
            return cached_chain.pop(id(frame), "")

        if entry.index in self._rendered_dictionary_indices:
            return ""

        story = None
        with self.catch("story", location=f"dictionary entry {entry.index}"):
            story = self.document.story(entry)
        if story is None:
            return ""

        if story.frame_chain:
            chain_html = None
            with self.catch("story", location=f"dictionary entry {entry.index}"):
                chain_html = self._flow_chained_story(story, entry.index, chapter, frame)
            if chain_html is not None:
                self._chain_html[entry.index] = chain_html
                return chain_html.pop(id(frame), "")
            # Not a real chain after all (e.g. independently-repeated
            # master content whose frame_chain is anchored to the master
            # page it's defined on, not this chapter -- see
            # _flow_chained_story) -- fall back to the single-frame
            # handling this converter always used before.
            if entry.index not in self._chain_warned:
                self._chain_warned.add(entry.index)
                self.log.best_effort(
                    "story",
                    "story's frame_chain doesn't resolve against this chapter's own "
                    "pages; rendered fresh in this one frame only, not flowed",
                    location=f"dictionary entry {entry.index}",
                )

        self._rendered_dictionary_indices.add(entry.index)
        return self._render_story(story, entry.index, chapter, content_width_pt)

    # -- Multi-frame chain flow -------------------------------------------

    def _frame_page_map(self, chapter: Chapter) -> dict[int, PageGroup]:
        """id(frame) -> the PageGroup it's on, across every page of
        *chapter*; mirrors pdfdoc.py's own _frame_page_map, needed to
        find a frame chain's other members' pages when computing
        _flow_chained_story (most of which generally aren't the page
        currently being walked)."""
        cached = self._frame_page_maps.get(id(chapter))
        if cached is not None:
            return cached
        mapping: dict[int, PageGroup] = {}
        for page in chapter.pages:
            for record in page.records:
                if record.value is not None:
                    mapping[id(record.value)] = page
        self._frame_page_maps[id(chapter)] = mapping
        return mapping

    def _content_box_pt_for(self, frame: Frame, page: PageGroup) -> Optional[tuple[float, float]]:
        """(content_width_pt, content_height_pt) for *frame* on *page*
        -- self-contained, mirroring _render_frame's own geometry
        computation exactly, but resolved fresh rather than relying on
        self._origin (only valid for the page currently being walked;
        a frame chain's other members generally aren't on it)."""
        appearance = self._effective_frame(frame, page)
        origin = page_origin(page.page) if appearance is frame else page_origin(page.master_page.page)
        x0, y0 = to_page_coordinates(origin, appearance.x0, appearance.y1)
        x1, y1 = to_page_coordinates(origin, appearance.x1, appearance.y0)
        width, height = (x1 - x0) / UNIT, (y1 - y0) / UNIT
        if width <= 0 or height <= 0:
            return None
        h_inset = max(0.0, appearance.hinset / UNIT)
        v_inset = max(0.0, appearance.vinset / UNIT)
        content_width = max(0.0, width - 2 * h_inset)
        content_height = max(0.0, height - 2 * v_inset)
        if content_width <= 0 or content_height <= 0:
            return None
        return content_width, content_height

    def _flow_chained_story(
        self, story: Story, dictionary_index: int, chapter: Chapter, first_frame: Frame
    ) -> Optional[dict[int, str]]:
        """Resolve *story*'s full frame chain and distribute its
        paragraphs across every member's own box in turn -- see the
        module docstring and _flow_items_into_containers for how.
        Returns None if the chain doesn't fully resolve against
        *chapter*'s own content pages: a master-linked frame repeated
        independently across several chapters shares one
        dictionary_index per occurrence just like a real chain does,
        but its frame_chain is anchored to the master page it's defined
        on, not this chapter -- mirrors pdfdoc.py's own
        _compute_chain_layout, including this same distinction."""
        if not story.frame_chain:
            return None
        saved_log = self.log
        self.log = ConversionLog()
        try:
            chain = self.resolve_frame_chain(story, chapter=chapter, master=False)
        finally:
            self.log = saved_log
        if len(chain) != len(story.frame_chain):
            return None

        chain_frames = [r.value for r in chain if isinstance(r.value, Frame)]
        if not any(f is first_frame for f in chain_frames):
            chain_frames = [first_frame] + chain_frames

        frame_page_map = self._frame_page_map(chapter)
        containers = []
        for cframe in chain_frames:
            member_page = frame_page_map.get(id(cframe))
            if member_page is None:
                continue
            box = self._content_box_pt_for(cframe, member_page)
            if box is None:
                continue
            containers.append((cframe, box[0], box[1]))
        if not containers:
            return {}
        return self._flow_items_into_containers(story.paragraphs, dictionary_index, chapter, containers)

    def _flow_items_into_containers(
        self, paragraphs: tuple, dictionary_index: int, chapter: Chapter, containers: list
    ) -> dict[int, str]:
        """Distribute *paragraphs* across *containers* (each a (frame,
        content_width_pt, content_height_pt) triple, in chain order),
        moving to the next container whenever the current one's
        estimated remaining height runs out. Estimates a slice's height
        via _approx_width's own word-wrap count (the module-level
        metrics helpers above, a duplicate of pdfdoc.py's own) rather
        than pdfdoc.py's own per-line positioning -- a browser lays out
        each frame's own <p> content natively once it knows which slice
        belongs there. Splits at paragraph AND PageBreakMark boundaries
        only (not mid-line, unlike pdfdoc.py): a paragraph too long for
        the CURRENT container's remaining space moves wholesale to the
        next one, which can leave more trailing blank space in a
        container than pdfdoc.py's own per-line flow would -- a
        deliberate simplification, since a browser -- not this
        converter -- does the actual per-line wrapping within each
        frame's own box. A slice that doesn't fit anywhere (the chain
        has run out of containers) is still placed in the last one,
        clipped by its own CSS overflow: hidden, rather than dropped."""
        assignments: dict[int, list[str]] = {id(f): [] for f, _, _ in containers}
        if not containers:
            return {}

        body_style = self.resolve_style([])
        container_index = 0
        frame, width_pt, height_pt = containers[0]
        used_height = 0.0

        def advance() -> bool:
            nonlocal container_index, frame, width_pt, height_pt, used_height
            container_index += 1
            if container_index >= len(containers):
                return False
            frame, width_pt, height_pt = containers[container_index]
            used_height = 0.0
            return True

        def place(items: list, para_style, is_continuation: bool) -> None:
            nonlocal used_height
            height = self._estimate_slice_height_pt(items, para_style, width_pt, chapter)
            if used_height > 0 and used_height + height > height_pt:
                advance()  # if this fails, still place it below -- see the docstring
            assignments[id(frame)].append(
                self._render_items(tuple(items), dictionary_index, chapter, para_style, width_pt, is_continuation)
            )
            used_height += height

        for paragraph in paragraphs:
            first_style_slots = next(
                (item.style_slots for item in paragraph.items if isinstance(item, (Run, EmbedMark))), None
            )
            para_style = self.resolve_style(first_style_slots) if first_style_slots is not None else body_style
            slice_items: list = []
            is_continuation = False
            for item in paragraph.items:
                if isinstance(item, PageBreakMark):
                    place(slice_items, para_style, is_continuation)
                    slice_items = []
                    if not advance():
                        return {k: "".join(v) for k, v in assignments.items()}
                    is_continuation = False
                    continue
                slice_items.append(item)
            place(slice_items, para_style, is_continuation)

        return {k: "".join(v) for k, v in assignments.items()}

    def _estimate_slice_height_pt(self, items: list, para_style, width_pt: float, chapter: Chapter) -> float:
        """Estimated total height (points) *items* (a whole paragraph,
        or a PageBreakMark-delimited slice of one) will occupy at
        *width_pt* -- counting wrapped lines via _approx_width the same
        way pdfdoc.py's own line-wrapping would; see
        _flow_items_into_containers.

        right_indent gets the same oversized-value fallback
        paragraph_css_properties applies when actually rendering (see
        its own docstring): dropped entirely rather than trusted
        verbatim if it wouldn't leave _MIN_USABLE_WIDTH of the frame's
        own width. Regression test: a real document (PCI_Spec from the
        local examples/ corpus) has a table/history-list style whose
        right_indent is authored for a much wider frame -- almost
        exactly equal to the actual frame's own width -- collapsing
        the *measured* width to nothing while the *rendered* CSS
        correctly dropped it and used the frame's own full width. That
        mismatch made every word estimate its own line, wildly
        inflating each row's estimated height and splitting a nine-row
        history table (which fits easily in one frame -- confirmed
        against the PDF converter's own output, all on one page) across
        three separate frames three pages apart, each pulling in
        unrelated pictures from whichever frame the overflow landed on."""
        line_height = paragraph_line_height_pt(para_style)
        left_indent = (para_style.left_indent or 0) / UNIT
        right_indent = (para_style.right_indent or 0) / UNIT
        first_indent = (para_style.first_indent or 0) / UNIT
        if right_indent and width_pt - left_indent - right_indent < _MIN_USABLE_WIDTH:
            right_indent = 0.0
        available = max(_MIN_USABLE_WIDTH, width_pt - left_indent - right_indent)

        x = first_indent
        line_count = 1
        embed_height = 0.0
        for item in items:
            if isinstance(item, Run):
                style = self.resolve_style(item.style_slots)
                for word in re.split(r"(\s+)", item.text):
                    if not word:
                        continue
                    w = _approx_width(word, style)
                    if word.isspace():
                        x += w
                        continue
                    if x > 0 and x + w > available:
                        line_count += 1
                        x = 0.0
                    x += w
            elif isinstance(item, EmbedMark):
                embed_height += self._embed_height_pt(item.embed_tag, chapter)
            elif isinstance(item, TabMark):
                x += 36.0  # a default tab pitch estimate, matching pdfdoc.py's own fallback
            elif isinstance(item, (PageNumberMark, ChapterNumberMark, HeadingNumberMark)):
                x += _approx_width("0000", para_style)

        height = line_count * line_height + embed_height
        if para_style.space_before:
            height += para_style.space_before / UNIT
        if para_style.space_after:
            height += para_style.space_after / UNIT
        return height

    def _embed_height_pt(self, embed_tag: int, chapter: Chapter) -> float:
        frame = self._embed_frame_for_tag(embed_tag, chapter)
        if frame is None:
            return 0.0
        return max(0.0, (frame.y1 - frame.y0) / UNIT)

    def _embed_frame_for_tag(self, embed_tag: int, chapter: Chapter) -> Optional[Frame]:
        for page in chapter.pages:
            for record in page.records:
                value = record.value
                if isinstance(value, Frame) and value.embed_tag == embed_tag:
                    return value
        return None

    def _render_story(self, story: Story, dictionary_index: int, chapter: Chapter, content_width_pt: float) -> str:
        return "".join(
            self._render_paragraph(p, dictionary_index, chapter, content_width_pt) for p in story.paragraphs
        )

    def _render_paragraph(self, paragraph, dictionary_index: int, chapter: Chapter, content_width_pt: float) -> str:
        # The style whose paragraph-level attributes (margins,
        # first-line indent, alignment, spacing) apply to the whole
        # block -- the paragraph's own first Run/EmbedMark's style,
        # mirroring pdfdoc.py's own para_style selection in
        # _paragraph_tokens (a leading mark with no style of its own,
        # e.g. a TabMark, must not fall back to body directly).
        first_style_slots = next(
            (item.style_slots for item in paragraph.items if isinstance(item, (Run, EmbedMark))), None
        )
        para_style = self.resolve_style(first_style_slots) if first_style_slots is not None else self.resolve_style([])
        return self._render_items(paragraph.items, dictionary_index, chapter, para_style, content_width_pt)

    def _render_items(
        self,
        items,
        dictionary_index: int,
        chapter: Chapter,
        para_style,
        content_width_pt: float,
        is_continuation: bool = False,
    ) -> str:
        """Render one paragraph's worth of story items as a single <p>.
        *items* is normally a whole Paragraph's own items tuple, but a
        chained story spanning multiple frames (see _flow_chained_story)
        calls this once per PageBreakMark-delimited slice instead, each
        slice rendered into its own frame -- *is_continuation* then
        suppresses the CSS properties that only make sense at a
        paragraph's true start (first-line indent, space-above), since
        a slice continuing in a later frame isn't a new paragraph."""
        props = paragraph_css_properties(para_style, max_width_pt=content_width_pt)
        if is_continuation:
            props.pop("text-indent", None)
            props.pop("margin-top", None)
        para_attr = css_style_attr(props)
        p_open = f'<p style="{para_attr}">' if para_attr else "<p>"

        spans: list[str] = []
        buffer: list[str] = []
        current_style = self.resolve_style([])

        def flush() -> None:
            if not buffer:
                return
            text = escape_html("".join(buffer))
            props = style_css_properties(current_style, self.document.colours)
            attr = css_style_attr(props)
            spans.append(f'<span style="{attr}">{text}</span>' if attr else text)
            buffer.clear()

        for item in items:
            if isinstance(item, Run):
                style = self.resolve_style(item.style_slots)
                if style != current_style:
                    flush()
                    current_style = style
                buffer.append(item.text)
            elif isinstance(item, PageNumberMark):
                buffer.append(str(self._page_number))
            elif isinstance(item, ChapterNumberMark):
                buffer.append(str(self._chapter_number))
            elif isinstance(item, HeadingNumberMark):
                buffer.append(self._resolve_number_text(item.tag, dictionary_index))
            elif isinstance(item, TabMark):
                flush()
                spans.append("&#9;")
            elif isinstance(item, PageBreakMark):
                pass  # a real chain's forced breaks are handled by _flow_chained_story; elsewhere, no page concept to act on
            elif isinstance(item, MergeMark):
                buffer.append(f"<<{item.field_name}>>")
            elif isinstance(item, EmbedMark):
                flush()
                spans.append(self._render_embed(item.embed_tag, chapter))
        flush()

        if not spans:
            return f"{p_open}&nbsp;</p>\n"
        return f"{p_open}{''.join(spans)}</p>\n"

    def _render_embed(self, embed_tag: int, chapter: Chapter) -> str:
        value = self._embed_frame_for_tag(embed_tag, chapter)
        if value is None:
            self.log.error("story", f"embedded frame tag {embed_tag} not found")
            return ""
        if isinstance(value, PictureFrame):
            return self._render_picture(value)
        self.log.best_effort(
            "story",
            "embedded text frame not rendered inline; only embedded pictures are reproduced",
        )
        return ""

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
