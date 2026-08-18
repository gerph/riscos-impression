"""``extract``: pulls every story and picture out of a document as
separate, standalone files, for reuse outside Impression entirely --
unlike the other converters, this isn't a single rendered document but
a directory of independent pieces, one per object-dictionary entry
(see model.dictionary.DictionaryEntry), keyed by that entry's own
index:

* ``text/NNNN.txt``    -- a text story's own plain text, stripped of
  all styling and layout.
* ``html/NNNN.html``   -- the same story as a standalone HTML document,
  with inline CSS approximating the source style table (font family/
  size/weight, colour, paragraph alignment/indent/spacing) via the
  same style_css_properties/paragraph_css_properties html_base.py's
  own HTML converters use -- not pixel-accurate (no attempt at line
  wrapping, tab-stop measurement, or frame-chain flow; see html_base.py
  and html_scrolling.py for that fuller machinery), just a readable,
  reasonably-styled approximation good enough to carry the document's
  general look into another tool.
* ``images/NNNN,<filetype>`` or ``images/NNNN.bin`` -- a picture's own
  raw embedded bytes, as-is (EPS is the one exception: its own wrapper
  header is stripped, leaving genuinely standalone PostScript -- see
  formats/eps.py). When recognised, the name carries the picture's
  real RISC OS filetype using the standard comma-suffix convention --
  ``,aff`` (DrawFile), ``,ff9`` (Sprite), ``,ff5`` (EPS), ``,d94``
  (ArtWorks) -- not a made-up "friendly" extension; unrecognised data
  falls back to a plain ``.bin``.
* ``svg/NNNN.svg``     -- a DrawFile picture's own content re-rendered
  as a standalone SVG file, at its own native size (no picture-frame
  scale/xshift/yshift/rotation applied -- unlike html_base.py's own
  _drawfile_svg, which renders one specific frame's own placement of a
  picture, a dictionary entry has no single owning frame in general
  (the same picture can be placed by more than one frame, each with
  its own scale/shift), so this renders the artwork itself instead of
  any one placement of it). Reuses HTML5Converter's own per-object SVG
  emitters (_drawfile_svg_object and everything under it) directly,
  just with a different, frame-free coordinate mapping.

Every story/picture is extracted independently, via best-effort
(catch()) per entry -- one broken entry doesn't stop the rest.
"""

from __future__ import annotations

from pathlib import Path

from riscos_impression.formats.drawfile import DrawFile
from riscos_impression.formats.eps import EPSObject
from riscos_impression.formats.sprite import SpriteArea
from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType, EmbeddedObjectType
from riscos_impression.model.story import (
    EmbedMark,
    HeadingNumberMark,
    MergeMark,
    PageBreakMark,
    PageNumberMark,
    ChapterNumberMark,
    Run,
    Story,
    TabMark,
)
from riscos_impression.output.html_base import (
    HTML5Converter,
    _DRAW_UNIT_TO_PT,
    css_style_attr,
    escape_html,
    paragraph_css_properties,
    style_css_properties,
)

#: DCPICT EmbeddedObjectType -> RISC OS filetype comma-suffix for the
#: raw dump, when not further narrowed by an actual decode attempt
#: (see _extract_picture). ArtWorks' own RISC OS filetype is &D94, so
#: its comma-suffix is "d94" (not "aff" -- that filetype, &AFF,
#: belongs to DrawFile); every other, undecoded companion-app type in
#: model.dictionary's own _DRAW_FAMILY (Tablemate, Equasor, Formulix,
#: Eureka, DiagramIT, TabCalc, GraphMate) has no known filetype either,
#: so falls through to plain ".bin" like undecodable DATA.
_FILETYPE_SUFFIX_BY_TYPE = {
    EmbeddedObjectType.ARTWORKS: "d94",
}


class ExtractConverter(HTML5Converter):
    """Extracts every story/picture in the document to its own file
    under an output directory -- see the module docstring for the
    directory layout and what each file contains."""

    def extract(self, output_dir: Path) -> None:
        output_dir = Path(output_dir)
        text_dir = output_dir / "text"
        html_dir = output_dir / "html"
        images_dir = output_dir / "images"
        svg_dir = output_dir / "svg"

        text_entries = [e for e in self.document.dictionary if e.type is DictionaryEntryType.TEXT]
        picture_entries = [e for e in self.document.dictionary if e.type is DictionaryEntryType.PICTURE]
        if text_entries:
            text_dir.mkdir(parents=True, exist_ok=True)
            html_dir.mkdir(parents=True, exist_ok=True)
        if picture_entries:
            images_dir.mkdir(parents=True, exist_ok=True)

        for entry in text_entries:
            with self.catch("text", location=f"dictionary entry {entry.index}"):
                self._extract_text(entry, text_dir, html_dir)
        for entry in picture_entries:
            with self.catch("picture", location=f"dictionary entry {entry.index}"):
                self._extract_picture(entry, images_dir, svg_dir)

    # -- Text -----------------------------------------------------------

    def _extract_text(self, entry: DictionaryEntry, text_dir: Path, html_dir: Path) -> None:
        story = self.document.story(entry)
        name = f"{entry.index:04d}"
        (text_dir / f"{name}.txt").write_text(self._story_plain_text(story), encoding="utf-8")
        (html_dir / f"{name}.html").write_text(self._story_html(story), encoding="utf-8")

    @staticmethod
    def _story_plain_text(story: Story) -> str:
        paragraphs = []
        for paragraph in story.paragraphs:
            parts = []
            for item in paragraph.items:
                if isinstance(item, Run):
                    parts.append(item.text)
                elif isinstance(item, TabMark):
                    parts.append("\t")
                elif isinstance(item, MergeMark):
                    parts.append(f"«{item.field_name}»")
                elif isinstance(item, EmbedMark):
                    parts.append("[picture]")
                elif isinstance(item, (PageNumberMark, ChapterNumberMark, HeadingNumberMark, PageBreakMark)):
                    pass  # page/chapter-relative; not meaningful outside the source document's own layout
            paragraphs.append("".join(parts))
        return "\n\n".join(paragraphs) + "\n"

    def _story_html(self, story: Story) -> str:
        body_parts = [self._paragraph_html(p) for p in story.paragraphs]
        return (
            "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<title>Extracted story</title>\n</head>\n<body>\n" + "".join(body_parts) + "</body>\n</html>\n"
        )

    def _paragraph_html(self, paragraph) -> str:
        first_style_slots = next(
            (item.style_slots for item in paragraph.items if isinstance(item, (Run, EmbedMark))), None
        )
        para_style = self.resolve_style(first_style_slots if first_style_slots is not None else ())
        para_attr = css_style_attr(paragraph_css_properties(para_style))
        p_open = f'<p style="{para_attr}">' if para_attr else "<p>"

        spans = []
        for item in paragraph.items:
            if isinstance(item, Run):
                style = self.resolve_style(item.style_slots)
                attr = css_style_attr(style_css_properties(style, self.document.colours))
                text = escape_html(item.text)
                spans.append(f'<span style="{attr}">{text}</span>' if attr else text)
            elif isinstance(item, TabMark):
                spans.append("&#9;")
            elif isinstance(item, MergeMark):
                spans.append(f"&#171;{escape_html(item.field_name)}&#187;")
            elif isinstance(item, EmbedMark):
                spans.append("[picture]")
            # Page/chapter/heading numbers and forced page breaks are
            # page-relative -- not meaningful once pulled out of the
            # source document's own layout, so simply dropped.
        if not spans:
            return f"{p_open}&nbsp;</p>\n"
        return f"{p_open}{''.join(spans)}</p>\n"

    # -- Pictures ---------------------------------------------------------

    def _extract_picture(self, entry: DictionaryEntry, images_dir: Path, svg_dir: Path) -> None:
        data = self.document.picture_bytes(entry)
        name = f"{entry.index:04d}"
        kind = entry.embedded_object_type

        if kind is EmbeddedObjectType.EPS:
            eps = EPSObject.from_bytes(data)
            (images_dir / f"{name},ff5").write_bytes(eps.data)
            return

        if kind is EmbeddedObjectType.DRAW:
            draw = DrawFile.from_bytes(data)
            if draw is not None:
                (images_dir / f"{name},aff").write_bytes(data)
                svg_dir.mkdir(parents=True, exist_ok=True)
                (svg_dir / f"{name}.svg").write_text(self._drawfile_native_svg(draw), encoding="utf-8")
                return
            if SpriteArea.from_bytes(data) is not None:
                (images_dir / f"{name},ff9").write_bytes(data)
                return
            self.log.error(
                "picture", f"dictionary entry {entry.index} classified as a drawable format "
                "but decoded as neither DrawFile nor Sprite; dumped raw"
            )
            (images_dir / f"{name}.bin").write_bytes(data)
            return

        suffix = _FILETYPE_SUFFIX_BY_TYPE.get(kind)
        if suffix is not None:
            (images_dir / f"{name},{suffix}").write_bytes(data)
        else:
            (images_dir / f"{name}.bin").write_bytes(data)

    def _drawfile_native_svg(self, draw: DrawFile) -> str:
        """*draw*'s own content at its own native size (100% scale, no
        shift or rotation) -- see the module docstring for why this
        can't reuse html_base.py's own frame-specific _drawfile_svg."""
        bounds = self._drawfile_effective_bounds(draw)
        width_pt = max(0.0, bounds.width * _DRAW_UNIT_TO_PT)
        height_pt = max(0.0, bounds.height * _DRAW_UNIT_TO_PT)

        def to_svg(dx: int, dy: int) -> tuple[float, float]:
            return (dx - bounds.x0) * _DRAW_UNIT_TO_PT, height_pt - (dy - bounds.y0) * _DRAW_UNIT_TO_PT

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_pt:.1f}pt" '
            f'height="{height_pt:.1f}pt" viewBox="0 0 {width_pt:.1f} {height_pt:.1f}">'
        ]
        notes: list[str] = []
        for obj in draw.objects:
            self._drawfile_svg_object(obj, draw.fonts, to_svg, (_DRAW_UNIT_TO_PT, _DRAW_UNIT_TO_PT), parts, notes)
        parts.append("</svg>")
        for note in dict.fromkeys(notes):  # de-duplicate, keep first-seen order
            self.log.best_effort("picture", note)
        return "".join(parts)
