"""Scrolling HTML5 output: a linear reflow of the document's own text
and pictures in reading order.

Unlike the PDF converter, this format has no geometry or pagination of
its own -- a browser lays out and wraps the content natively from the
CSS this module produces. That makes the frame-chain and text-repel
concerns pdfdoc.py has to solve itself irrelevant here: a story's whole
text is rendered once, as a single run of paragraphs, wherever its
first frame is encountered in reading order (chapter by chapter, page
by page, frame by frame), with no need to work out which physical frame
would have held which portion. Page furniture -- page/chapter numbering
as a *visual* concept, absolute frame position, master-page background
frames -- is dropped entirely; this converter never even visits
document.master_pages, only each chapter's own content pages, so
master-only furniture is naturally never seen. A master-*linked*
frame's own dictionary_index is still honoured normally, the same way
every other frame's is.

Paragraph-level formatting (left margin, first-line indent, alignment,
space before/after, line height) is applied as inline CSS on each `<p>`
via html_base.py's paragraph_css_properties -- a POSITIVE right_indent
(an offset from the *original* frame's own left edge, not an inset
from its right; see Style.right_indent_is_delta and
paragraph_css_properties' own docstring) is the only exception, left
out entirely here: resolving it to a CSS margin-right needs the
frame's own real width, which this format deliberately never tracks
(see above). A negative right_indent (a genuine inset from the right
edge) maps directly to CSS margin-right regardless, and is applied
normally (unlike html_paged.py, which additionally has the frame's
real width available for the positive case too).

A tab, unlike right_indent, does NOT need the frame's own real width
to position at all -- a style's own declared tab stops are absolute
point offsets from the paragraph's own left margin, independent of
frame or viewport width -- so tabs still jump to their own declared
stop here (an ordinary, in-flow inline-block spacer, sized by
measuring the upcoming segment's width ahead of time for a centre/
right/decimal stop; mirrors html_paged.py's own, already-validated
mechanism and pdfdoc.py's _segment_width/_tab_target_x exactly). Only
the actual visual column alignment across rows of different widths is
approximate here, same as everywhere else in this format, since a
browser -- not this converter -- does the real text measurement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from riscos_impression.model.dictionary import DictionaryEntryType
from riscos_impression.model.document_tree import Chapter
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
from riscos_impression.output.html_base import (
    HTML5Converter,
    css_style_attr,
    escape_html,
    paragraph_css_properties,
    style_css_properties,
)

_DOCUMENT_CSS = """\
body { margin: 2em auto; max-width: 40em; font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; line-height: 1.4; }
p { margin: 0 0 1em 0; }
section.chapter { margin-bottom: 3em; }
img { vertical-align: middle; }
"""

#: Millipoints per CSS point; see docs/impression-documents.xml's note
#: under "Frame object common layout".
UNIT = 1000.0

#: Used only when a style carries no font_size at all, matching
#: pdfdoc.py's own _DEFAULT_FONT_SIZE_16THS (10pt).
_DEFAULT_FONT_SIZE_16THS = 160

#: RISC OS font name substring -> font_metrics.WIDTHS_256PT family, for
#: _approx_width's own estimate of an upcoming tab segment's width. A
#: *duplicate*, self-contained copy of pdfdoc.py's/html_paged.py's own
#: metrics font-selection, not shared with either (matching this
#: project's convention of independent converters).
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
    duplicate, self-contained copy of pdfdoc.py's/html_paged.py's own
    _approx_width, used here only to size an in-flow tab spacer, never
    to position anything with pixel precision (a browser does the real
    text measurement natively)."""
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


class ScrollingHTMLConverter(HTML5Converter):
    """Renders the decoded document model as one linear, scrolling HTML5
    page. See the module docstring for what's deliberately dropped
    (page furniture, absolute geometry) versus what's still honoured
    (styled text runs, embedded pictures, merge/numbering marks)."""

    def convert(self, output_path: Path) -> None:
        self._chapter_number = 0
        self._rendered_dictionary_indices: set[int] = set()
        self._dictionary_by_index = {entry.index: entry for entry in self.document.dictionary}

        body_parts = []
        for chapter in self.document.chapters:
            self._chapter_number += 1
            with self.catch("chapter", location=f"chapter {chapter.section.create_number}"):
                body_parts.append(self._render_chapter(chapter))

        Path(output_path).write_text(self._wrap_document("".join(body_parts)), encoding="utf-8")

    def _wrap_document(self, body_html: str) -> str:
        return (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            "<title>Impression document</title>\n"
            f"<style>\n{_DOCUMENT_CSS}</style>\n</head>\n<body>\n"
            f"{body_html}"
            "</body>\n</html>\n"
        )

    def _render_chapter(self, chapter: Chapter) -> str:
        parts = [f'<section class="chapter" data-chapter="{self._chapter_number}">\n']
        for page in chapter.pages:
            for frame in page.frames:
                if not isinstance(frame, Frame):
                    continue
                with self.catch(
                    "frame", location=f"chapter {chapter.section.create_number}"
                ):
                    parts.append(self._render_frame(frame, chapter))
        parts.append("</section>\n")
        return "".join(parts)

    def _render_frame(self, frame: Frame, chapter: Chapter) -> str:
        if frame.embed_tag:
            # Anchored inline within a text story instead, at the
            # matching EmbedMark's own position (_render_embed) --
            # never drawn again at its own top-level position in the
            # page's frame list; mirrors html_paged.py's own
            # _render_frame check (and pdfdoc.py's _draw_frame) exactly.
            # Confirmed against a real document (PCI_Spec): 3 DrawFile
            # diagrams embedded inline in running text were rendered
            # once inline (correct) and a second time at their own raw
            # frame position (this converter has no page geometry, so
            # that raw position just falls wherever the frame happens
            # to sit in the page's frame list -- in this document, at
            # the very end).
            return ""
        if isinstance(frame, (GuideFrame, GroupFrame)):
            return ""  # non-printing / no visual content of its own
        if isinstance(frame, PictureFrame):
            return self._render_picture_frame(frame)
        if isinstance(frame, (TextFrame, BlankFrame)):
            return self._render_text_frame(frame, chapter)
        return ""

    def _render_picture_frame(self, pict: PictureFrame) -> str:
        if pict.dictionary_index < 0:
            return ""
        entry = self._dictionary_by_index.get(pict.dictionary_index)
        if entry is None:
            self.log.error("picture", f"no dictionary entry for index {pict.dictionary_index}")
            return ""
        if entry.type is not DictionaryEntryType.PICTURE:
            return ""
        return f"<p>{self._picture_html(pict, entry)}</p>\n"

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
        return self._render_story(story, entry.index, chapter)

    def _render_story(self, story: Story, dictionary_index: int, chapter: Chapter) -> str:
        return "".join(self._render_paragraph(p, dictionary_index, chapter) for p in story.paragraphs)

    def _render_paragraph(self, paragraph, dictionary_index: int, chapter: Chapter) -> str:
        spans: list[str] = []
        buffer: list[str] = []
        current_style = self.resolve_style([])

        # The style whose paragraph-level attributes (margins,
        # first-line indent, alignment, spacing) apply to the whole
        # block -- the paragraph's own first Run/EmbedMark's style,
        # mirroring pdfdoc.py's own para_style selection in
        # _paragraph_tokens (a leading mark with no style of its own,
        # e.g. a TabMark, must not fall back to body directly).
        first_style_slots = next(
            (item.style_slots for item in paragraph.items if isinstance(item, (Run, EmbedMark))), None
        )
        para_style = self.resolve_style(first_style_slots) if first_style_slots is not None else current_style
        para_attr = css_style_attr(paragraph_css_properties(para_style))
        p_open = f'<p style="{para_attr}">' if para_attr else "<p>"

        items = paragraph.items
        tab_stops = sorted(para_style.tab_stops, key=lambda ts: ts.position) if para_style.tab_stops else []
        left_indent_pt = (para_style.left_indent or 0) / UNIT
        first_indent_pt = (para_style.first_indent or 0) / UNIT
        cursor_pt = left_indent_pt + first_indent_pt

        def flush() -> None:
            if not buffer:
                return
            text = escape_html("".join(buffer))
            props = style_css_properties(current_style, self.document.colours)
            attr = css_style_attr(props)
            spans.append(f'<span style="{attr}">{text}</span>' if attr else text)
            buffer.clear()

        def append_measured(text: str) -> None:
            nonlocal cursor_pt
            buffer.append(text)
            cursor_pt += _approx_width(text, current_style)

        def segment_width(start_index: int, base_style) -> float:
            # Approx width of the run of items from *start_index* up to
            # (not including) the next TabMark or the end of the
            # paragraph -- needed to work out where a centre/right/
            # decimal tab's segment should *start*, since its target
            # stop describes where the segment ends (or its middle
            # sits), not where it begins. Mirrors html_paged.py's own,
            # already-validated segment_width (and pdfdoc.py's own
            # _segment_width) exactly.
            total = 0.0
            style = base_style
            for later in items[start_index:]:
                if isinstance(later, TabMark):
                    break
                if isinstance(later, Run):
                    style = self.resolve_style(later.style_slots)
                    total += _approx_width(later.text, style)
                elif isinstance(later, PageNumberMark):
                    pass  # meaningless in a scrolling document; contributes no width here either
                elif isinstance(later, ChapterNumberMark):
                    total += _approx_width(str(self._chapter_number), style)
                elif isinstance(later, HeadingNumberMark):
                    total += _approx_width(self._resolve_number_text(later.tag, dictionary_index), style)
                elif isinstance(later, EmbedMark):
                    frame = self._embed_frame_for_tag(later.embed_tag, chapter)
                    if frame is not None:
                        total += max(0.0, (frame.x1 - frame.x0) / UNIT)
            return total

        for idx, item in enumerate(items):
            if isinstance(item, Run):
                style = self.resolve_style(item.style_slots)
                if style != current_style:
                    flush()
                    current_style = style
                append_measured(item.text)
            elif isinstance(item, PageNumberMark):
                pass  # page numbers are a paginated-layout concept; meaningless in a scrolling document
            elif isinstance(item, ChapterNumberMark):
                append_measured(str(self._chapter_number))
            elif isinstance(item, HeadingNumberMark):
                append_measured(self._resolve_number_text(item.tag, dictionary_index))
            elif isinstance(item, TabMark):
                # A tab jumps to the FIRST declared stop past the
                # current cursor position (mirrors html_paged.py's own,
                # already-validated tab handling and pdfdoc.py's own
                # _next_tab_stop exactly) via an ordinary, in-flow
                # inline-block spacer -- a literal tab character would
                # otherwise collapse to nothing under HTML's default
                # whitespace handling. A centre/right/decimal stop
                # measures its own upcoming segment's width ahead of
                # time so the spacer can land that segment centred on,
                # or ending at, the stop rather than starting there.
                flush()
                stop_pt, kind = None, 0
                for ts in tab_stops:
                    candidate = ts.position / UNIT
                    if candidate > cursor_pt + 0.5:
                        stop_pt, kind = candidate, ts.kind
                        break
                if stop_pt is None:
                    stop_pt = (int(cursor_pt / 36.0) + 1) * 36.0  # half-inch default pitch
                if kind == 0:
                    target_pt = stop_pt
                else:
                    width = segment_width(idx + 1, current_style)
                    target_pt = stop_pt - (width / 2.0 if kind == 1 else width)  # 1=centre, 2/3=right/decimal
                target_pt = max(target_pt, cursor_pt)
                spacer_width = target_pt - cursor_pt
                if spacer_width > 0.005:
                    spans.append(f'<span style="display:inline-block;width:{spacer_width:.2f}pt"></span>')
                cursor_pt = target_pt
            elif isinstance(item, PageBreakMark):
                pass  # no page concept in a scrolling document
            elif isinstance(item, MergeMark):
                buffer.append(f"<<{item.field_name}>>")
            elif isinstance(item, EmbedMark):
                flush()
                spans.append(self._render_embed(item.embed_tag, chapter))
                frame = self._embed_frame_for_tag(item.embed_tag, chapter)
                if frame is not None:
                    cursor_pt += max(0.0, (frame.x1 - frame.x0) / UNIT)
        flush()

        if not spans:
            return f"{p_open}&nbsp;</p>\n"
        return f"{p_open}{''.join(spans)}</p>\n"

    def _embed_frame_for_tag(self, embed_tag: int, chapter: Chapter) -> Optional[Frame]:
        for page in chapter.pages:
            for record in page.records:
                value = record.value
                if isinstance(value, Frame) and value.embed_tag == embed_tag:
                    return value
        return None

    def _render_embed(self, embed_tag: int, chapter: Chapter) -> str:
        for page in chapter.pages:
            for record in page.records:
                value = record.value
                if isinstance(value, Frame) and value.embed_tag == embed_tag:
                    if isinstance(value, PictureFrame):
                        if value.dictionary_index < 0:
                            return ""
                        entry = self._dictionary_by_index.get(value.dictionary_index)
                        if entry is None or entry.type is not DictionaryEntryType.PICTURE:
                            return ""
                        return self._picture_html(value, entry)
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
