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
own block layout wraps text within a frame's sized <div> natively, so
none of pdfdoc.py's approximate-metrics line-wrapping machinery is
needed here. Two things that follow from staying simple, both logged
rather than silently attempted:

* A story confined to a single frame renders in full there, clipped
  (CSS overflow: hidden) if it doesn't fit -- no attempt is made to
  measure whether it actually overflows, since that would need the
  same manual text-metrics work this format's own native wrapping is
  meant to avoid. A story spanning a real multi-frame chain only ever
  renders in its first frame -- the same limitation pdfdoc.py started
  with before chain flow was added for it; the equivalent follow-up
  here, if wanted, would look much the same.
* Dynamic text repel around an obstacle picture (as pdfdoc.py does) is
  not attempted; every frame is positioned independently via CSS
  `position: absolute`, so an obstacle's box and a text frame's box can
  visually overlap exactly as they do in the source document.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

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
from riscos_impression.output.base import page_origin, to_page_coordinates
from riscos_impression.output.html_base import HTML5Converter, colour_to_css, css_style_attr, escape_html, style_css_properties

#: Millipoints per CSS point; see docs/impression-documents.xml's note
#: under "Frame object common layout".
UNIT = 1000.0

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
            content = self._render_text_frame(frame, chapter)

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

    def _render_text_frame(self, frame, chapter: Chapter) -> str:
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
        if story.frame_chain and entry.index not in self._chain_warned:
            self._chain_warned.add(entry.index)
            self.log.best_effort(
                "story",
                "story spans a multi-frame chain; only its first frame's box is "
                "used, clipped -- this converter doesn't flow text across frames "
                "(unlike the PDF converter)",
                location=f"dictionary entry {entry.index}",
            )
        return self._render_story(story, entry.index, chapter)

    def _render_story(self, story: Story, dictionary_index: int, chapter: Chapter) -> str:
        return "".join(self._render_paragraph(p, dictionary_index, chapter) for p in story.paragraphs)

    def _render_paragraph(self, paragraph, dictionary_index: int, chapter: Chapter) -> str:
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

        for item in paragraph.items:
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
                pass  # content is already clipped to one frame's box; no page concept to act on here
            elif isinstance(item, MergeMark):
                buffer.append(f"<<{item.field_name}>>")
            elif isinstance(item, EmbedMark):
                flush()
                spans.append(self._render_embed(item.embed_tag, chapter))
        flush()

        if not spans:
            return "<p>&nbsp;</p>\n"
        return f"<p>{''.join(spans)}</p>\n"

    def _render_embed(self, embed_tag: int, chapter: Chapter) -> str:
        for page in chapter.pages:
            for record in page.records:
                value = record.value
                if isinstance(value, Frame) and value.embed_tag == embed_tag:
                    if isinstance(value, PictureFrame):
                        return self._render_picture(value)
                    self.log.best_effort(
                        "story",
                        "embedded text frame not rendered inline; only embedded pictures are reproduced",
                    )
                    return ""
        self.log.error("story", f"embedded frame tag {embed_tag} not found")
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
