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
"""

from __future__ import annotations

from pathlib import Path

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
                pass  # page numbers are a paginated-layout concept; meaningless in a scrolling document
            elif isinstance(item, ChapterNumberMark):
                buffer.append(str(self._chapter_number))
            elif isinstance(item, HeadingNumberMark):
                buffer.append(self._resolve_number_text(item.tag, dictionary_index))
            elif isinstance(item, TabMark):
                flush()
                spans.append("&#9;")
            elif isinstance(item, PageBreakMark):
                pass  # no page concept in a scrolling document
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
