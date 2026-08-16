"""Native PDF output: a minimal, dependency-free PDF 1.4 writer driving
the same decoded document model the OvProDDL converter uses.

Coordinate unit: every Impression coordinate is in millipoints (see
docs/impression-documents.xml's note under "Frame object common
layout"), and PDF's native unit is exactly a point, so the conversion
is a plain divide-by-1000 -- no unit-guessing needed. Impression's
frame/page geometry is already bottom-left-origin, Y-up (x0/y0 are the
"left"/"bottom" edge, x1/y1 the "right"/"top" edge), which is also
PDF's native page coordinate system, so frame placement needs no Y-flip
either (unlike the OvProDDL converter, which flips Y to match
OvationPro's own top-down box convention).

Several things here are necessarily best-effort, logged via
ConversionLog rather than guessed at silently:

* Text layout uses an approximate (non-AFM) average character width
  per standard font, not real per-glyph metrics -- Courier's width is
  exact (it's genuinely fixed-pitch, 0.6em per Adobe's own metrics),
  but Helvetica/Times line-wrapping and justification are
  approximations. Actual glyph rendering is exact regardless (it uses
  the PDF viewer's own built-in standard-14 font program), so this
  only affects where lines break and how justified spacing lands, not
  what any individual glyph looks like.
* A story's text is rendered in full only in the first frame of its
  chain that this converter encounters, clipped to that frame's inset
  box; multi-frame text flow across a chain (the way Impression itself
  reflows a long story across several linked frames) is not
  reproduced. Overflow within that one frame is silently clipped
  (logged once per occurrence).
* DrawFile and Sprite pictures are drawn as a labelled placeholder box
  (this package's decoders for both are stub bounding-box readers, see
  formats/drawfile.py and formats/sprite.py; there's no pixel or
  vector data available to actually rasterise). ArtWorks pictures are
  a full stub (formats/artworks.py) and always render as a
  placeholder. EPS pictures also render as a placeholder box, but with
  their raw PostScript content attached to the page as an embedded
  file annotation -- PDF has no reliable native mechanism to render
  arbitrary embedded EPS/PostScript (the legacy "PS XObject" facility
  is deprecated and unsupported by most viewers), so a lossless
  attachment plus a visible placeholder is the best available
  compromise.
* A picture frame's irregular boundary (crop path) clips both its own
  fill/border and its content -- a good, direct use of the
  already-decoded path opcodes (MOVE/DRAW/CLOSE/END; CURVE is
  recognised but not decoded, the same limitation as the OvProDDL
  converter).
* Master-page furniture (frames placed only on a master page, with no
  corresponding locally-linked frame on a given content page) is drawn
  as a background layer behind that page's own content, once per
  content page that uses the master. This is a PDF-specific design
  choice this converter has to make for itself: DDL's output doesn't
  need it, since OvationPro's own layout engine merges master and page
  content when it renders the DDL, but a self-contained PDF has no
  such external renderer to rely on.
* Paragraph alignment codes beyond 0 (left) and 3 (justified) are not
  confirmed by the conversion source (see docs/impression-documents.xml,
  "alignment"); 1 is assumed centre and 2 is assumed right, following
  the common word-processor convention, not a confirmed OvationPro DDL
  fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from riscos_impression.formats.drawfile import DrawFile
from riscos_impression.formats.eps import EPSObject
from riscos_impression.formats.sprite import SpriteArea
from riscos_impression.model.colours import MAXCV, Colour, ColourModel
from riscos_impression.model.dictionary import DictionaryEntryType, EmbeddedObjectType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import (
    BlankFrame,
    Frame,
    GroupFrame,
    GuideFrame,
    PathOpCode,
    PictureFrame,
    TextFrame,
)
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
from riscos_impression.output.base import Converter

#: Millipoints per PDF point; see the module docstring.
UNIT = 1000.0

_DEFAULT_FONT_SIZE_16THS = 160  # 10pt, used when a style carries no font_size at all.


# ---------------------------------------------------------------------------
# Low-level PDF object writer
# ---------------------------------------------------------------------------


class _PDFWriter:
    """A minimal incremental PDF object table: allocate object numbers
    (optionally before their content is known, for forward references
    like /Pages -> its Kids), set their body bytes, then render the
    whole file (header, objects, xref table, trailer)."""

    def __init__(self) -> None:
        self._objects: list[Optional[bytes]] = [None]  # index 0 is unused (object numbers are 1-based)

    def reserve(self) -> int:
        self._objects.append(None)
        return len(self._objects) - 1

    def set(self, number: int, body: bytes) -> None:
        self._objects[number] = body

    def add(self, body: bytes) -> int:
        number = self.reserve()
        self.set(number, body)
        return number

    def render(self, root_obj: int) -> bytes:
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0] * len(self._objects)
        for i in range(1, len(self._objects)):
            body = self._objects[i]
            if body is None:
                raise ValueError(f"PDF object {i} was reserved but never given a body")
            offsets[i] = len(out)
            out += f"{i} 0 obj\n".encode("latin-1")
            out += body
            out += b"\nendobj\n"
        xref_offset = len(out)
        out += f"xref\n0 {len(self._objects)}\n".encode("latin-1")
        out += b"0000000000 65535 f \n"
        for i in range(1, len(self._objects)):
            out += f"{offsets[i]:010d} 00000 n \n".encode("latin-1")
        out += b"trailer\n"
        out += f"<< /Size {len(self._objects)} /Root {root_obj} 0 R >>\n".encode("latin-1")
        out += f"startxref\n{xref_offset}\n%%EOF".encode("latin-1")
        return bytes(out)


def _stream_obj(content: bytes, extra: str = "") -> bytes:
    return f"<< /Length {len(content)} {extra}>>\nstream\n".encode("latin-1") + content + b"\nendstream"


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_str(text: str) -> str:
    return f"({_pdf_escape(text)})"


def _fmt(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


# ---------------------------------------------------------------------------
# Standard-14 fonts
# ---------------------------------------------------------------------------

STANDARD_FONTS = [
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
    "Symbol", "ZapfDingbats",
]

#: Substrings of a RISC OS font name that identify its family; matched
#: case-insensitively, first match wins. Homerton (and anything
#: unrecognised) falls through to Helvetica.
_FAMILY_HINTS = [
    ("courier", "Courier"),
    ("corpus", "Courier"),
    ("system", "Courier"),
    ("mono", "Courier"),
    ("times", "Times"),
    ("trinity", "Times"),
    ("serif", "Times"),
    ("symbol", "Symbol"),
    ("dingbat", "ZapfDingbats"),
    ("wingding", "ZapfDingbats"),
]

#: Average width, as a fraction of the font size (em), used only for
#: line-wrap/justification decisions -- see the module docstring.
#: Courier's is exact (Adobe's own AFM gives every Courier glyph a
#: width of exactly 0.6em); the others are approximations.
_AVERAGE_WIDTH_FACTOR = {
    "Helvetica": 0.52,
    "Times": 0.46,
    "Courier": 0.6,
    "Symbol": 0.5,
    "ZapfDingbats": 0.7,
}


def _base_family(font_style_name: Optional[str]) -> str:
    name = (font_style_name or "").lower()
    for hint, family in _FAMILY_HINTS:
        if hint in name:
            return family
    return "Helvetica"


def choose_standard_font(style: Style) -> str:
    """Best-effort mapping from a resolved style's RISC OS font name
    (plus its bold/italic override flags) to one of the 14 standard PDF
    fonts; see the module docstring."""
    family = _base_family(style.font_style_name)
    if family in ("Symbol", "ZapfDingbats"):
        return family
    name = (style.font_style_name or "").lower()
    is_bold = bool(style.bold) or "bold" in name
    is_italic = bool(style.italic) or "italic" in name or "oblique" in name
    if family == "Times":
        if is_bold and is_italic:
            return "Times-BoldItalic"
        if is_bold:
            return "Times-Bold"
        if is_italic:
            return "Times-Italic"
        return "Times-Roman"
    suffix = {
        (False, False): "",
        (True, False): "-Bold",
        (False, True): "-Oblique",
        (True, True): "-BoldOblique",
    }[(is_bold, is_italic)]
    return family + suffix


def _approx_width(text: str, style: Style) -> float:
    size = (style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
    family = choose_standard_font(style).split("-")[0]
    return len(text) * size * _AVERAGE_WIDTH_FACTOR.get(family, 0.5)


def _line_height_pt(style: Style) -> float:
    size = (style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
    if style.line_spacing_raw is not None:
        if style.line_spacing_is_fixed:
            return abs(style.line_spacing) / UNIT
        percent = style.line_spacing if style.line_spacing else 100
        return size * 1.2 * (percent / 100.0)
    return size * 1.2


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    i = int(h * 6.0) % 6
    f = h * 6.0 - int(h * 6.0)
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


def _to_rgb(colour: Colour) -> tuple[float, float, float]:
    if colour.model is ColourModel.RGB:
        r, g, b = colour.values
        return r / MAXCV, g / MAXCV, b / MAXCV
    if colour.model is ColourModel.HSV:
        h, s, v = colour.values
        # h carries no /255 scaling (see docs/impression-documents.xml,
        # "Colour channel encoding"); normalise it back to a 0..1 fraction.
        return _hsv_to_rgb((h / MAXCV) / 255.0, s / MAXCV, v / MAXCV)
    raise ValueError(f"unexpected colour model {colour.model}")  # CMYK has its own operator; see _fill_colour_op


def _fill_colour_op(colour: Optional[Colour]) -> str:
    if colour is None:
        return "0 0 0 rg\n"
    if colour.model is ColourModel.CMYK:
        c, m, y, k = (v / MAXCV for v in colour.values)
        return f"{_fmt(c)} {_fmt(m)} {_fmt(y)} {_fmt(k)} k\n"
    r, g, b = _to_rgb(colour)
    return f"{_fmt(r)} {_fmt(g)} {_fmt(b)} rg\n"


def _stroke_colour_op(colour: Optional[Colour]) -> str:
    if colour is None:
        return "0 0 0 RG\n"
    if colour.model is ColourModel.CMYK:
        c, m, y, k = (v / MAXCV for v in colour.values)
        return f"{_fmt(c)} {_fmt(m)} {_fmt(y)} {_fmt(k)} K\n"
    r, g, b = _to_rgb(colour)
    return f"{_fmt(r)} {_fmt(g)} {_fmt(b)} RG\n"


# ---------------------------------------------------------------------------
# Text layout
# ---------------------------------------------------------------------------


@dataclass
class _Token:
    kind: str  #: "word" | "space" | "tab" | "break"
    text: str
    style: Style


def _strip_trailing_space(line: list[_Token]) -> None:
    while line and line[-1].kind == "space":
        line.pop()


def _next_tab_stop(x_pt: float, tab_base_x: float, style: Style) -> float:
    """The next tab position at or after *x_pt*, from the style's own
    tab ruler if it has one (positions are relative to *tab_base_x* --
    the frame's own left content edge, not any per-line indent, matching
    a tab ruler's usual frame-relative meaning), else a fixed default
    pitch."""
    if style.tab_stops:
        positions = sorted(ts.position for ts in style.tab_stops)
        for pos in positions:
            candidate = tab_base_x + pos / UNIT
            if candidate > x_pt + 0.5:
                return candidate
    default_pitch = 36.0  # half an inch; used only when the style defines no tab ruler
    step = int((x_pt - tab_base_x) / default_pitch) + 1
    return tab_base_x + step * default_pitch


def _tab_advance(x: float, tab_base_x: float, style: Style, right_edge: float) -> float:
    """The tab's effective new X position: the next tab stop, unless even
    that would exceed *right_edge*, in which case the tab is treated as a
    no-op -- a style's tab ruler can be set up for a much wider frame
    than the one it's actually used in (styles are shared across frames
    of any size), so its stops aren't always reachable here."""
    target = _next_tab_stop(x, tab_base_x, style)
    return target if target <= right_edge else x


def _wrap_tokens(
    tokens: list[_Token], tab_base_x: float, line_start_first: float, line_start_normal: float, right_edge: float
) -> list[list[_Token]]:
    """Greedy word-wrap using each token's approximate width (see
    _approx_width), tracking the actual absolute X position (not just an
    abstract running width) so a tab's real jump distance -- which can be
    far larger than any single word, if the style's tab ruler was set up
    for a wider frame than this one -- is accounted for: a tab whose
    target would land past *right_edge* forces a line wrap first, rather
    than being allowed to carry the rest of the line off past the frame's
    (and possibly the page's) edge. *line_start_first* is the first
    wrapped line's left edge (for first-line/hanging indents),
    *line_start_normal* every line after it."""
    lines: list[list[_Token]] = []
    current: list[_Token] = []
    line_start = line_start_first
    x = line_start

    def wrap_line() -> None:
        nonlocal current, line_start, x
        _strip_trailing_space(current)
        lines.append(current)
        current = []
        line_start = line_start_normal
        x = line_start

    for tok in tokens:
        if tok.kind == "break":
            wrap_line()
            continue
        if tok.kind == "tab":
            if _next_tab_stop(x, tab_base_x, tok.style) > right_edge and current:
                wrap_line()  # give the tab a fresh line before giving up on it entirely
            current.append(tok)
            x = _tab_advance(x, tab_base_x, tok.style, right_edge)
            continue
        width = _approx_width(tok.text, tok.style)
        if tok.kind == "word" and current and x + width > right_edge:
            wrap_line()
        current.append(tok)
        x += width
    if current or not lines:
        _strip_trailing_space(current)
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


class PDFConverter(Converter):
    """Renders the decoded document model directly as PDF page content
    streams. See the module docstring for the best-effort areas this
    converter logs rather than guesses at."""

    def __init__(
        self,
        document,
        log=None,
        strict: bool = False,
        border_width_pt: float = 0.5,
    ):
        super().__init__(document, log=log, strict=strict)
        self.border_width_pt = border_width_pt

    # -- Top level -----------------------------------------------------------

    def begin_document(self) -> None:
        self._writer = _PDFWriter()
        self._pages_obj = self._writer.reserve()
        self._page_objs: list[int] = []
        self._chapter_number = 0
        self._page_number = 0
        self._rendered_stories: set[int] = set()
        self._dictionary_by_index = {entry.index: entry for entry in self.document.dictionary}
        self._font_resource_name: dict[str, str] = {}
        font_parts = []
        for i, base in enumerate(STANDARD_FONTS, start=1):
            extra = "" if base in ("Symbol", "ZapfDingbats") else "/Encoding /WinAnsiEncoding "
            obj = self._writer.add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} {extra}>>".encode("latin-1"))
            resource_name = f"F{i}"
            self._font_resource_name[base] = resource_name
            font_parts.append(f"/{resource_name} {obj} 0 R")
        self._font_resource_obj = self._writer.add(("<< " + " ".join(font_parts) + " >>").encode("latin-1"))

    def begin_chapter(self, chapter: Chapter) -> None:
        self._chapter_number += 1

    def begin_page(self, chapter: Chapter, page: PageGroup) -> None:
        self._page = page
        self._page_number += 1
        self._content: list[str] = []
        self._page_annots: list[str] = []
        self._page_origin = (page.page.x0 + page.page.bleed, page.page.y0 + page.page.bleed)
        self._origin = self._page_origin
        self._page_size = (page.page.print_width / UNIT, page.page.print_height / UNIT)
        self._content.append(f"0 0 {_fmt(self._page_size[0])} {_fmt(self._page_size[1])} re W n\n")

        if page.master_page is not None:
            master_page_box = page.master_page.page
            master_origin = (master_page_box.x0 + master_page_box.bleed, master_page_box.y0 + master_page_box.bleed)
            linked_indices = {f.master_index for f in page.frames if f.master}
            for mframe in page.master_page.frames:
                if mframe.master_index in linked_indices:
                    continue
                with self.catch(
                    "frame", location=f"chapter {chapter.section.create_number} master furniture"
                ):
                    self._draw_frame(
                        mframe, chapter, page, dictionary_dedupe=False, source_origin=master_origin
                    )

    def emit_frame(self, chapter: Chapter, page: PageGroup, frame: object) -> None:
        self._draw_frame(frame, chapter, page, dictionary_dedupe=True, source_origin=self._page_origin)

    def end_page(self, chapter: Chapter, page: PageGroup) -> None:
        content_bytes = "".join(self._content).encode("latin-1", errors="replace")
        content_obj = self._writer.add(_stream_obj(content_bytes))
        w_pt, h_pt = self._page_size
        annots = f"/Annots [{' '.join(self._page_annots)}]\n" if self._page_annots else ""
        page_dict = (
            f"<< /Type /Page /Parent {self._pages_obj} 0 R "
            f"/MediaBox [0 0 {_fmt(w_pt)} {_fmt(h_pt)}] "
            f"/Resources << /Font {self._font_resource_obj} 0 R >> "
            f"/Contents {content_obj} 0 R\n{annots}>>"
        )
        self._page_objs.append(self._writer.add(page_dict.encode("latin-1")))

    def end_document(self) -> None:
        kids = " ".join(f"{n} 0 R" for n in self._page_objs)
        self._writer.set(
            self._pages_obj,
            f"<< /Type /Pages /Kids [{kids}] /Count {len(self._page_objs)} >>".encode("latin-1"),
        )
        self._catalog_obj = self._writer.add(f"<< /Type /Catalog /Pages {self._pages_obj} 0 R >>".encode("latin-1"))

    def write(self, output_path: Path) -> None:
        Path(output_path).write_bytes(self._writer.render(self._catalog_obj))

    # -- Coordinates -----------------------------------------------------------

    def _to_pt(self, x_doc: int, y_doc: int) -> tuple[float, float]:
        ox, oy = self._origin
        return (x_doc - ox) / UNIT, (y_doc - oy) / UNIT

    # -- Frame dispatch ----------------------------------------------------------

    def _effective_frame(self, frame: Frame, page: PageGroup) -> Frame:
        """The Frame whose geometry/fill/border should actually be drawn
        for *frame*: itself, unless it's master-linked, in which case its
        appearance is inherited from the corresponding master-page frame
        (matching the OvProDDL converter's own {master ...}{local 0}
        choice -- the local record's own geometry isn't meaningful in
        that case)."""
        if not frame.master:
            return frame
        record = self.master_frame(page, frame)
        if record is not None and isinstance(record.value, Frame):
            return record.value
        self.log.error("frame", "master frame not found for a master-linked frame")
        return frame

    def _draw_frame(
        self,
        frame: Frame,
        chapter: Chapter,
        page: PageGroup,
        *,
        dictionary_dedupe: bool,
        source_origin: tuple[int, int],
    ) -> None:
        """Draw *frame*, whose own coordinates are relative to
        *source_origin*'s canvas (the content page's own canvas for a
        normal page frame, or the master page's own separate canvas for
        a piece of master furniture -- master pages keep entirely their
        own absolute coordinate space, unrelated to the content pages
        that use them). If *frame* is itself master-linked, its
        appearance is substituted from the master page (_effective_frame),
        whose coordinates are then relative to the master page's own
        canvas regardless of *source_origin*."""
        if isinstance(frame, GuideFrame):
            return  # non-printing
        if isinstance(frame, GroupFrame):
            self.log.best_effort(
                "frame",
                "group frame membership not reproduced in PDF output; the group "
                "marker itself has no visual representation",
            )
            return

        if (
            dictionary_dedupe
            and isinstance(frame, (TextFrame, BlankFrame))
            and frame.dictionary_index >= 0
            and frame.dictionary_index in self._rendered_stories
        ):
            # Another frame earlier in this same page's stream already
            # rendered this story's text (see _draw_story's dedupe) --
            # this frame is a later member of that story's chain. Since a
            # chain member's box can (and, in real documents, does)
            # spatially overlap or fully enclose an earlier member's box,
            # drawing its own fill/border here would paint over -- or sit
            # uncomfortably beside -- text that's already been placed;
            # skip it entirely rather than risk hiding real content.
            return

        appearance = self._effective_frame(frame, page)
        if appearance is frame:
            appearance_origin = source_origin
        else:
            master_page_box = page.master_page.page
            appearance_origin = (
                master_page_box.x0 + master_page_box.bleed,
                master_page_box.y0 + master_page_box.bleed,
            )

        saved_origin = self._origin
        self._origin = appearance_origin
        try:
            boundary = appearance.boundary if isinstance(appearance, PictureFrame) else None
            if boundary:
                self._content.append("q\n")
                self._content.append(self._boundary_clip_path(appearance, boundary))

            self._draw_box(appearance)
            if isinstance(frame, PictureFrame):
                self._draw_picture(frame, appearance)
            elif isinstance(frame, (TextFrame, BlankFrame)):
                self._draw_story(frame, appearance, chapter, page, dictionary_dedupe=dictionary_dedupe)

            if boundary:
                self._content.append("Q\n")
        finally:
            self._origin = saved_origin

    # -- Fill / border -----------------------------------------------------------

    def _draw_box(self, frame: Frame) -> None:
        x0, y0 = self._to_pt(frame.x0, frame.y0)
        x1, y1 = self._to_pt(frame.x1, frame.y1)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return

        fill = frame.fill_colour(self.document.colours) if frame.filled else None
        if fill is not None:
            self._content.append(_fill_colour_op(fill))
            self._content.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(w)} {_fmt(h)} re f\n")

        if frame.has_border:
            border_colour = frame.border_colour(self.document.colours)
            self._content.append(_stroke_colour_op(border_colour))
            self._content.append(f"{_fmt(self.border_width_pt)} w\n")
            self._content.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(w)} {_fmt(h)} re S\n")

    def _boundary_clip_path(self, pict: PictureFrame, boundary) -> str:
        cx_doc = (pict.x0 + pict.x1) // 2
        cy_doc = (pict.y0 + pict.y1) // 2
        parts = []
        for op in boundary:
            if op.code is PathOpCode.MOVE:
                x, y = self._to_pt(cx_doc + op.x, cy_doc + op.y)
                parts.append(f"{_fmt(x)} {_fmt(y)} m\n")
            elif op.code is PathOpCode.DRAW:
                x, y = self._to_pt(cx_doc + op.x, cy_doc + op.y)
                parts.append(f"{_fmt(x)} {_fmt(y)} l\n")
            elif op.code is PathOpCode.CLOSE:
                parts.append("h\n")
            elif op.code is PathOpCode.END:
                break
            # CURVE is recognised but not decoded; see model.frames.PathOpCode.
        parts.append("W n\n")
        return "".join(parts)

    # -- Pictures -----------------------------------------------------------

    def _draw_picture(self, pict: PictureFrame, appearance: PictureFrame) -> None:
        if pict.dictionary_index < 0:
            return
        entry = self._dictionary_by_index.get(pict.dictionary_index)
        if entry is None:
            self.log.error("picture", f"no dictionary entry for index {pict.dictionary_index}")
            return
        if entry.type is not DictionaryEntryType.PICTURE:
            return

        x0, y0 = self._to_pt(appearance.x0, appearance.y0)
        x1, y1 = self._to_pt(appearance.x1, appearance.y1)
        if x1 <= x0 or y1 <= y0:
            return

        with self.catch("picture", location=f"dictionary entry {entry.index}"):
            data = self.document.picture_bytes(entry)
            self._draw_picture_content(data, entry, x0, y0, x1, y1)

    def _draw_picture_content(self, data: bytes, entry, x0: float, y0: float, x1: float, y1: float) -> None:
        kind = entry.embedded_object_type

        if kind is EmbeddedObjectType.EPS:
            eps = EPSObject.from_bytes(data)
            self._draw_placeholder(x0, y0, x1, y1, "EPS")
            self._attach_eps_file(eps, x0, y0, x1, y1)
            self.log.best_effort(
                "picture",
                f"EPS picture '{eps.name}' drawn as a placeholder box; the raw EPS "
                "content is attached to the page as an embedded file rather than "
                "rendered (PDF has no reliable native EPS rendering mechanism)",
            )
            return

        if kind is EmbeddedObjectType.DRAW:
            draw = DrawFile.from_bytes(data)
            if draw is not None:
                self._draw_placeholder(x0, y0, x1, y1, "Draw")
                self.log.best_effort(
                    "picture",
                    "DrawFile picture rendered as a placeholder box; vector "
                    "content is not decoded by this converter",
                )
                return
            sprite = SpriteArea.from_bytes(data)
            self._draw_placeholder(x0, y0, x1, y1, "Sprite")
            if sprite is None:
                self.log.error("picture", "picture classified as a drawable format but decoded as neither DrawFile nor Sprite")
            else:
                self.log.best_effort(
                    "picture",
                    "Sprite picture rendered as a placeholder box; pixel data is "
                    "not decoded by this converter",
                )
            return

        if kind is EmbeddedObjectType.ARTWORKS:
            self._draw_placeholder(x0, y0, x1, y1, "ArtWorks")
            self.log.unsupported(
                "picture",
                "ArtWorks picture rendered as a placeholder box; this format is "
                "not decoded at all by this converter",
            )
            return

        label = kind.value if kind is not None else "data"
        self._draw_placeholder(x0, y0, x1, y1, label)
        self.log.best_effort(
            "picture", f"{label} picture rendered as a placeholder box; not decoded by this converter"
        )

    def _draw_placeholder(self, x0: float, y0: float, x1: float, y1: float, label: str) -> None:
        w, h = x1 - x0, y1 - y0
        parts = ["0.6 0.6 0.6 RG\n0.4 w\n"]
        parts.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(w)} {_fmt(h)} re S\n")
        parts.append(f"{_fmt(x0)} {_fmt(y0)} m {_fmt(x1)} {_fmt(y1)} l S\n")
        parts.append(f"{_fmt(x0)} {_fmt(y1)} m {_fmt(x1)} {_fmt(y0)} l S\n")
        text = f"[{label}]"
        size = max(6.0, min(10.0, h * 0.15)) if h > 0 else 6.0
        tx = x0 + max(1.0, (w - len(text) * size * 0.5) / 2.0)
        ty = (y0 + y1) / 2.0
        parts.append("0.35 0.35 0.35 rg\n")
        parts.append(
            f"BT /{self._font_resource_name['Helvetica']} {_fmt(size)} Tf "
            f"{_fmt(tx)} {_fmt(ty)} Td {_pdf_str(text)} Tj ET\n"
        )
        self._content.append("".join(parts))

    def _attach_eps_file(self, eps: EPSObject, x0: float, y0: float, x1: float, y1: float) -> None:
        ef_obj = self._writer.add(_stream_obj(eps.data, extra="/Type /EmbeddedFile "))
        name = eps.name or "picture.eps"
        filespec_obj = self._writer.add(
            f"<< /Type /Filespec /F {_pdf_str(name)} /EF << /F {ef_obj} 0 R >> >>".encode("latin-1")
        )
        self._page_annots.append(
            f"<< /Type /Annot /Subtype /FileAttachment /Rect [{_fmt(x0)} {_fmt(y0)} {_fmt(x1)} {_fmt(y1)}] "
            f"/FS {filespec_obj} 0 R /Name /Paperclip /Contents {_pdf_str(name)} >>"
        )

    # -- Text stories -----------------------------------------------------------

    def _draw_story(
        self, frame: Union[TextFrame, BlankFrame], appearance: Frame, chapter: Chapter, page: PageGroup, *, dictionary_dedupe: bool
    ) -> None:
        if frame.dictionary_index < 0:
            return
        entry = self._dictionary_by_index.get(frame.dictionary_index)
        if entry is None:
            return
        if entry.type is not DictionaryEntryType.TEXT:
            return  # a blank frame's link may resolve to a picture instead; nothing to draw here
        if dictionary_dedupe:
            if entry.index in self._rendered_stories:
                return
            self._rendered_stories.add(entry.index)

        story = None
        with self.catch("story", location=f"dictionary entry {entry.index}"):
            story = self.document.story(entry)
        if story is None:
            return

        x0, y0 = self._to_pt(appearance.x0 + appearance.hinset, appearance.y0 + appearance.vinset)
        x1, y1 = self._to_pt(appearance.x1 - appearance.hinset, appearance.y1 - appearance.vinset)
        if x1 <= x0 or y1 <= y0:
            return

        self._content.append("q\n")
        self._content.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(x1 - x0)} {_fmt(y1 - y0)} re W n\n")
        self._render_story_text(story, x0, y0, x1, y1, entry.index)
        self._content.append("Q\n")

    def _paragraph_tokens(self, paragraph, dictionary_index: int, body_style: Style) -> tuple[list[_Token], Style]:
        tokens: list[_Token] = []
        current_style = body_style
        for item in paragraph.items:
            if isinstance(item, Run):
                current_style = self.resolve_style(item.style_slots)
                for chunk in re.split(r"(\s+)", item.text):
                    if not chunk:
                        continue
                    tokens.append(_Token("space" if chunk.isspace() else "word", chunk, current_style))
            elif isinstance(item, PageNumberMark):
                tokens.append(_Token("word", str(self._page_number), current_style))
            elif isinstance(item, ChapterNumberMark):
                tokens.append(_Token("word", str(self._chapter_number), current_style))
            elif isinstance(item, HeadingNumberMark):
                tokens.append(_Token("word", self._resolve_number_text(item.tag, dictionary_index), current_style))
            elif isinstance(item, TabMark):
                tokens.append(_Token("tab", "", current_style))
            elif isinstance(item, PageBreakMark):
                tokens.append(_Token("break", "", current_style))
            elif isinstance(item, MergeMark):
                tokens.append(_Token("word", f"<<{item.field_name}>>", current_style))
            elif isinstance(item, EmbedMark):
                continue  # drawn separately as its own page frame; see module docstring
        return tokens, (tokens[0].style if tokens else body_style)

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

    def _render_story_text(self, story: Story, x0: float, y0: float, x1: float, y1: float, dictionary_index: int) -> None:
        body_style = self.resolve_style([])
        y_cursor = y1
        overflowed = False

        for paragraph in story.paragraphs:
            if overflowed:
                break
            tokens, para_style = self._paragraph_tokens(paragraph, dictionary_index, body_style)

            left_indent = (para_style.left_indent or 0) / UNIT
            right_indent = (para_style.right_indent or 0) / UNIT
            first_indent = (para_style.first_indent or 0) / UNIT
            right_edge = x1 - right_indent
            line_start_normal = x0 + left_indent
            line_start_first = x0 + left_indent + first_indent
            line_height = _line_height_pt(para_style)

            lines = _wrap_tokens(tokens, x0, line_start_first, line_start_normal, right_edge)
            for index, line in enumerate(lines):
                if y_cursor - line_height < y0:
                    self.log.best_effort(
                        "story",
                        "text overflowed its frame and was clipped; multi-frame text "
                        "flow across a chain is not reproduced",
                        location=f"dictionary entry {dictionary_index}",
                    )
                    overflowed = True
                    break
                y_cursor -= line_height
                is_first = index == 0
                is_last = index == len(lines) - 1
                start_x = line_start_first if is_first else line_start_normal
                self._render_line(
                    line, start_x, x0, right_edge, y_cursor, justify=not is_last, alignment=para_style.alignment
                )

            if not overflowed and para_style.space_after:
                y_cursor -= para_style.space_after / UNIT

    def _render_line(
        self,
        tokens: list[_Token],
        start_x: float,
        tab_base_x: float,
        right_edge: float,
        y: float,
        *,
        justify: bool,
        alignment: Optional[int],
    ) -> None:
        if not tokens:
            return
        line_width = sum(0.0 if t.kind == "tab" else _approx_width(t.text, t.style) for t in tokens)
        available = max(0.0, right_edge - start_x)
        extra_space = 0.0
        space_count = sum(1 for t in tokens if t.kind == "space")

        origin_x = start_x
        if alignment == 3 and justify and space_count:  # justified, not the paragraph's last line
            extra_space = max(0.0, (available - line_width) / space_count)
        elif alignment == 2:  # right (unconfirmed; see module docstring)
            origin_x = start_x + max(0.0, available - line_width)
        elif alignment == 1:  # centre (unconfirmed; see module docstring)
            origin_x = start_x + max(0.0, (available - line_width) / 2.0)

        parts = ["BT\n"]
        current_font = None
        current_size = None
        current_colour_key = None
        x = origin_x
        for tok in tokens:
            if tok.kind == "tab":
                x = _tab_advance(x, tab_base_x, tok.style, right_edge)
                continue
            font = choose_standard_font(tok.style)
            size = (tok.style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
            parts.append(f"1 0 0 1 {_fmt(x)} {_fmt(y)} Tm\n")
            if font != current_font or size != current_size:
                parts.append(f"/{self._font_resource_name[font]} {_fmt(size)} Tf\n")
                current_font, current_size = font, size
            colour = (
                tok.style.foreground_colour(self.document.colours)
                if tok.style.foreground_colour_word is not None
                else None
            )
            colour_key = colour.values if colour is not None else None
            if colour_key != current_colour_key:
                parts.append(_fill_colour_op(colour))
                current_colour_key = colour_key
            word_space = extra_space if tok.kind == "space" else 0.0
            parts.append(f"{_fmt(word_space)} Tw\n")
            parts.append(f"{_pdf_str(tok.text)} Tj\n")
            x += _approx_width(tok.text, tok.style) + (word_space if tok.kind == "space" else 0.0)
        parts.append("ET\n")
        self._content.append("".join(parts))
