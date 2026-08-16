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

* Text layout uses real per-character advance-width metrics
  (font_metrics.py, reproduced from real RISC OS Trinity/Homerton font
  data -- see that module's own docstring) for Helvetica/Times-mapped
  text, and an exact flat 0.6em for Courier (genuinely fixed-pitch,
  confirmed against the same source data). Only a font this converter
  can't map to either of those two families at all (Symbol,
  ZapfDingbats) or a character with no RISC OS Latin1 representation
  falls back to a flat per-family average width instead. Actual glyph
  rendering is exact regardless (it uses the PDF viewer's own built-in
  standard-14 font program), so any residual approximation only
  affects where lines break and how justified/aligned spacing lands,
  not what any individual glyph looks like.
* A story's text flows across its whole frame chain (resolved via
  Converter.resolve_frame_chain), moving on to the next chain member
  whenever the current one fills up and re-wrapping the remainder for
  its own width. Text also repels dynamically around any repel-flagged
  frame (a picture, or another text frame such as an address block) on
  the same page, narrowing each line's available width around whatever
  obstacles intersect its own Y-band, from whichever side has less
  room. Real documents sometimes combine both techniques -- chaining a
  narrow frame beside an obstacle into a full-width one below it --
  rather than relying purely on dynamic repel; a later same-page chain
  member whose box overlaps an earlier one skips drawing its own
  fill/border (which would paint over already-placed text) and never
  starts its own content higher up than the earlier member's bottom
  edge. Only running out of frames in the chain entirely -- text that
  still doesn't fit anywhere -- is logged and clipped. A forced page
  break within a story (CTRL_N/PageBreakMark -- "force to next" in the
  conversion source) jumps straight to the START of the next chain
  member regardless of how much room is left in the current one, the
  same as running out of room does; it doesn't just insert a blank
  line in place.
* DrawFile pictures are rendered as real PDF vector content (paths --
  fill/stroke colour, width, winding rule -- and single-line text, via
  formats/drawfile.py's object decoder), at the picture's own true
  size (native DrawFile bounds, scaled by the frame's own declared
  display scale -- pict.xscale/yscale; see _draw_drawfile_picture),
  centred within the target frame's box and clipped to it -- NOT
  stretched to fill the frame's own box, which gave a visibly wrong
  size/aspect whenever the frame wasn't sized to exactly match the
  picture's own native bounds at 100% scale (confirmed against a real
  document and the user's own reading of Impression's picture info
  dialog). The frame's own declared xshift/yshift position and angle
  aren't applied -- see that method's own docstring for why (a
  plausible-looking xshift/yshift interpretation was tried and
  rejected: it clipped away real, visible picture content in every
  real example checked) -- centring is used instead as a safer
  default until the real anchor convention is confirmed. Dash patterns
  are parsed but not honoured (lines render solid); join styles aren't
  honoured either (PDF's own default mitred join is used regardless).
  Triangular caps (the mechanism real Draw files use for arrowhead/
  pointer line ends -- confirmed against the actual RISC OS DrawFile
  module's own rendering implementation, not just the PRM's field
  descriptions) ARE honoured: PDF has no native triangular line-cap
  style, so one is drawn as an explicit filled triangle at the
  subpath's own start/end instead (see _draw_triangular_caps); plain
  butt/round caps are not distinguished from each other (both render
  as PDF's own default butt cap). A Sprite object embedded *within* a
  DrawFile,
  and any other undecoded object type (text area, options, transformed
  text/sprite), still falls back to a labelled placeholder box for just
  that object, logged once per picture. A picture that isn't a valid
  DrawFile at all -- Sprite pictures, and anything else -- still
  renders as a labelled placeholder box (formats/sprite.py is a stub
  bounding-box reader only; there's no pixel data available to
  rasterise). ArtWorks pictures are a full stub (formats/artworks.py)
  and always render as a placeholder. EPS pictures also render as a
  placeholder box, but with
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
* A picture frame with a non-zero embed_tag is anchored inline within
  a text story (at the point a matching EmbedMark occurs), not drawn
  independently at its own raw page position -- see
  docs/impression-documents.xml, "Frame object common layout"
  (embedtag). It's laid out as its own block: it ends the current
  line, at the frame's own real, intended size (confirmed against a
  real document and the user's own reading of Impression's picture
  info dialog -- the frame's box IS the picture's true on-page size,
  not something to be rederived from the paragraph's own column
  width), shrinking only if that doesn't fit the current column at all
  (preserving aspect ratio), and pushes every following line down
  below it, rather than being placed at a fixed box that could overlay
  already-flowing text wherever it
  happened to intersect it.
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

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from riscos_impression.formats.drawfile import (
    CAP_TRIANGULAR,
    DrawFile,
    DrawGroup,
    DrawPath,
    DrawPathOpCode,
    DrawSprite,
    DrawTagged,
    DrawText,
    colour_rgb,
)
from riscos_impression.formats.eps import EPSObject
from riscos_impression.formats.sprite import SpriteArea
from riscos_impression.log import ConversionLog
from riscos_impression.output import font_metrics
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

#: Draw units (1/256 OS unit, itself 1/180 inch) to PDF points.
_DRAW_UNIT_TO_PT = 72.0 / (180.0 * 256.0)

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
    """A PDF literal string for *text*. Every text font this converter
    declares, other than Symbol/ZapfDingbats (which use their own
    built-in glyph encoding, not a text-encoding table -- see
    STANDARD_FONTS' /Encoding setup), is declared as WinAnsiEncoding,
    so *text* is transcoded through the equivalent Windows-1252 codec
    here rather than left to the content stream's own final latin-1
    encode step (see end_page) -- which would otherwise silently
    replace any character above U+00FF with '?', including every RISC
    OS smart quote/dash/ligature (see encoding.py; these decode to
    real Unicode code points, not raw Latin-1 ones)."""
    escaped = _pdf_escape(text)
    raw = escaped.encode("cp1252", errors="replace")
    return "(" + raw.decode("latin-1") + ")"


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


def _standard_font_for(font_style_name: Optional[str], bold: bool, italic: bool) -> str:
    family = _base_family(font_style_name)
    if family in ("Symbol", "ZapfDingbats"):
        return family
    name = (font_style_name or "").lower()
    is_bold = bold or "bold" in name
    is_italic = italic or "italic" in name or "oblique" in name
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


def choose_standard_font(style: Style) -> str:
    """Best-effort mapping from a resolved style's RISC OS font name
    (plus its bold/italic override flags) to one of the 14 standard PDF
    fonts; see the module docstring."""
    return _standard_font_for(style.font_style_name, bool(style.bold), bool(style.italic))


#: family -> the four Homerton/Trinity weight/slant table names in
#: font_metrics.WIDTHS_256PT, indexed by (is_bold, is_italic). Courier
#: isn't here (see _approx_width -- it's genuinely fixed-pitch, exact
#: via _AVERAGE_WIDTH_FACTOR already) and neither are Symbol/
#: ZapfDingbats (no real metrics table available for them at all).
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


def _riscos_metrics_font_name(style: Style) -> Optional[str]:
    family = _base_family(style.font_style_name)
    variants = _RISCOS_METRICS_FONT.get(family)
    if variants is None:
        return None
    name = (style.font_style_name or "").lower()
    is_bold = bool(style.bold) or "bold" in name
    is_italic = bool(style.italic) or "italic" in name or "oblique" in name
    return variants[(is_bold, is_italic)]


def _approx_width(text: str, style: Style) -> float:
    size = (style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
    metrics_font = _riscos_metrics_font_name(style)
    if metrics_font is not None:
        total_per_mille = 0.0
        for ch in text:
            per_mille = font_metrics.char_width_per_mille(metrics_font, ch)
            if per_mille is None:
                break
            total_per_mille += per_mille
        else:
            return total_per_mille / 1000.0 * size
        # A character fell outside the metrics table (rare -- anything
        # not representable in RISC OS Latin1 at all); fall through to
        # the flat per-family average below for the *whole* string,
        # same as when there's no metrics table for this font at all.
    family = choose_standard_font(style).split("-")[0]
    return len(text) * size * _AVERAGE_WIDTH_FACTOR.get(family, 0.5)


def _line_height_pt(style: Style) -> float:
    size = (style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
    if style.line_spacing_raw is not None:
        if style.line_spacing_is_fixed:
            # A FIXED leading value is millipoints, stored verbatim from
            # whatever font size was in effect when it was last set --
            # confirmed against a real document (Telegraph from the
            # local moreexamples/ corpus, cross-checked against a real
            # OvationPro-native DDF export the user supplied): its "Main
            # Heading" style (28pt) carries a fixed leading of 19.66pt
            # (raw word 0x80014ccc), a leftover snapshot from some
            # earlier, smaller font size that never got updated when the
            # style's own font_size was later increased for visual
            # impact -- the DDF export independently confirms the style
            # is actually meant to use 130% proportional leading, and
            # 19.66pt visibly collided the heading's own two wrapped
            # lines. Same underlying failure as the cross-style cascade
            # case fixed in Converter.resolve_style, just happening
            # within a single style's own record via an inconsistent
            # edit instead of via inheritance -- so never let a fixed
            # value produce LESS spacing than the natural single-line
            # default below; it can still widen (loosen) spacing when
            # genuinely larger than that, respecting an intentionally
            # loose author choice.
            return max(abs(style.line_spacing) / UNIT, size * 1.2)
        # The proportional value is stored as percent x100 (e.g. 12000 =
        # 120.00%), not a literal percent -- confirmed empirically, not
        # from the conversion source (docs/impression-documents.xml's
        # own note just says "the remaining 24 bits directly", which is
        # exactly what the original converter's DDL emission does too:
        # it passes the raw value straight through to OvationPro's own
        # `{leading 1 N}` DDL directive unscaled, per c/styles in the
        # sibling riscos-source repo -- so OvationPro's own DDL
        # interpreter is where the real x100 scaling actually happens,
        # not anything reproducible from this converter's own source).
        # Taking the raw value as a literal percent instead produced a
        # 100x-too-tall line height (1728pt for a 12pt style, seen on a
        # style shared by a real corporate document template across at
        # least 14 of the 48 local example documents), which silently
        # dropped a whole story's remaining content once the first
        # oversized line overflowed its frame -- see PLAN.md's Stage 9
        # addenda.
        percent = (style.line_spacing / 100.0) if style.line_spacing else 100
        return size * 1.2 * (percent / 100.0)
    return size * 1.2


#: Adobe's own standard AFM Ascender values (per 1000 units of em) for
#: the three families this converter maps text on to; Symbol/
#: ZapfDingbats fall back to Helvetica's, which is close enough for the
#: rare case either is actually used for a whole line's worth of text.
_ASCENT_PER_MILLE = {"Helvetica": 718, "Times": 683, "Courier": 629}


def _ascent_pt(style: Style) -> float:
    """A line's own font ascent -- the distance from a text box's top
    edge down to its *first* line's baseline, as opposed to
    _line_height_pt's full ascent+descent+leading figure (correct for
    the gap *between* two baselines, but too large for the gap between
    a box's top edge and its first baseline: using it there pushes
    every frame's text down by roughly the descent+leading amount,
    visibly low compared to the real document -- confirmed against a
    real page image the user supplied for PCI_Spec)."""
    size = (style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
    family = choose_standard_font(style).split("-")[0]
    return size * _ASCENT_PER_MILLE.get(family, _ASCENT_PER_MILLE["Helvetica"]) / 1000.0


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


def _draw_rgb_op(word: int, stroke: bool) -> str:
    """A `rg`/`RG` operator for a raw Draw palette word (see
    formats/drawfile.py's colour_rgb) -- DrawFile colours are always
    plain RGB, unlike the document's own colour table."""
    r, g, b = colour_rgb(word)
    op = "RG" if stroke else "rg"
    return f"{_fmt(r / 255)} {_fmt(g / 255)} {_fmt(b / 255)} {op}\n"


# ---------------------------------------------------------------------------
# DrawFile triangular caps (arrowheads)
# ---------------------------------------------------------------------------


def _unit_vector(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0, 0.0
    return dx / length, dy / length


def _subpath_cap_directions(
    ops: list, to_pt
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """For each *open* subpath in *ops* (i.e. not closed by
    CLOSE_LINE/CLOSE_GAP), the (start_point, start_outward_dir,
    end_point, end_outward_dir) needed to draw a triangular cap at
    either end -- all already in the final PDF point space (via
    *to_pt*, so a non-uniform x/y scale is accounted for correctly: a
    direction is computed by transforming both of the two points it's
    derived from and taking their difference, not by transforming a
    doc-space unit vector directly, which a non-uniform scale would
    distort). A closed subpath has no real start/end to cap and is
    skipped entirely. CURVE control points are ignored for direction
    purposes -- only the curve's own end point is used as an ordinary
    vertex, a reasonable approximation for the tangent at a subpath's
    own start/end; no real document found during development used a
    curve immediately there."""
    subpaths: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    closed = False

    def flush() -> None:
        if len(current) >= 2 and not closed:
            subpaths.append(list(current))
        current.clear()

    for op in ops:
        if op.code in (DrawPathOpCode.MOVE, DrawPathOpCode.MOVE_INTERNAL):
            flush()
            closed = False
            current.append((op.x, op.y))
        elif op.code in (DrawPathOpCode.LINE, DrawPathOpCode.GAP, DrawPathOpCode.CURVE):
            current.append((op.x, op.y))
        elif op.code in (DrawPathOpCode.CLOSE_LINE, DrawPathOpCode.CLOSE_GAP):
            closed = True
    flush()

    results = []
    for verts in subpaths:
        p0 = to_pt(*verts[0])
        p1 = to_pt(*verts[1])
        pn = to_pt(*verts[-1])
        pn1 = to_pt(*verts[-2])
        start_dir = _unit_vector(p0[0] - p1[0], p0[1] - p1[1])
        end_dir = _unit_vector(pn[0] - pn1[0], pn[1] - pn1[1])
        results.append((p0, start_dir, pn, end_dir))
    return results


def _triangular_cap_polygon(
    point: tuple[float, float], direction: tuple[float, float], width_pt: float, length_pt: float
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """The three vertices of a triangular cap at *point*: a base,
    *width_pt* wide and centred on *point*, perpendicular to
    *direction*, and an apex *length_pt* further out along
    *direction*."""
    px, py = point
    dx, dy = direction
    nx, ny = -dy, dx  # perpendicular to (dx, dy)
    half = width_pt / 2.0
    base_left = (px + nx * half, py + ny * half)
    base_right = (px - nx * half, py - ny * half)
    apex = (px + dx * length_pt, py + dy * length_pt)
    return base_left, apex, base_right


# ---------------------------------------------------------------------------
# Text layout
# ---------------------------------------------------------------------------


@dataclass
class _Token:
    kind: str  #: "word" | "space" | "tab" | "break" | "embed"
    text: str
    style: Style
    #: Only set for kind="embed": the PictureFrame anchored inline at
    #: this point in the story; see _paragraph_tokens.
    embed_frame: Optional[Frame] = None


def _strip_trailing_space(line: list[_Token]) -> None:
    while line and line[-1].kind == "space":
        line.pop()


def _next_tab_stop(x_pt: float, tab_base_x: float, style: Style) -> tuple[float, int]:
    """The next tab stop at or after *x_pt* -- (position, kind) -- from
    the style's own tab ruler if it has one (positions are relative to
    *tab_base_x* -- the frame's own left content edge, not any per-line
    indent, matching a tab ruler's usual frame-relative meaning), else
    a fixed default pitch, left-kind (0). A stop whose kind isn't one
    of 0=left/1=centre/2=right/3=decimal (see model.styles.TabStop) is
    a rule-line marker, not a real stop, and is skipped."""
    if style.tab_stops:
        stops = sorted((ts.position, ts.kind) for ts in style.tab_stops if ts.kind in (0, 1, 2, 3))
        for pos, kind in stops:
            candidate = tab_base_x + pos / UNIT
            if candidate > x_pt + 0.5:
                return candidate, kind
    default_pitch = 36.0  # half an inch; used only when the style defines no tab ruler
    step = int((x_pt - tab_base_x) / default_pitch) + 1
    return tab_base_x + step * default_pitch, 0


def _segment_width(tokens: list["_Token"]) -> float:
    """Approx width of a run of tokens up to (not including) the next
    tab or break. A centre/right/decimal tab stop's position describes
    where this following segment should *end* (or sit at the middle
    of), not where it starts, so its actual start position can't be
    resolved without knowing this width first -- unlike a left tab,
    which never needs to look ahead at all."""
    total = 0.0
    for tok in tokens:
        if tok.kind in ("tab", "break"):
            break
        total += _approx_width(tok.text, tok.style)
    return total


def _tab_target_x(stop: float, kind: int, x: float, segment_width: float) -> float:
    """Where the segment following a tab should actually start, given
    the stop it landed on (see _next_tab_stop) and that stop's own
    kind: left (0) starts the segment AT the stop; centre (1) and
    right (2) instead work backward from the stop by half, or all, of
    the segment's own width, so the segment ends up centred on, or
    ending at, the stop -- decimal (3) is simplified to the same as
    right (this converter doesn't track where a decimal point falls
    within a not-yet-placed segment). Never moves backward past the
    tab's own position *x*: a segment too wide to fit even at its own
    natural stop just starts immediately after the tab instead, the
    same "unreachable target is a no-op" fallback used when the stop
    itself is past the line's right edge entirely (see _tab_advance) --
    a style's tab ruler can be set up for a much wider frame than the
    one it's actually used in (styles are shared across frames of any
    size), so a stop being reachable at all doesn't guarantee a wide
    segment aimed at it will actually fit."""
    if kind == 1:
        target = stop - segment_width / 2.0
    elif kind in (2, 3):
        target = stop - segment_width
    else:
        target = stop
    return max(x, target)


def _tab_advance(
    x: float, tab_base_x: float, style: Style, right_edge: float, segment_width: float = 0.0
) -> float:
    """The tab's effective new X position (see _tab_target_x), unless
    even the stop itself would exceed *right_edge*, in which case the
    tab is treated as a no-op."""
    stop, kind = _next_tab_stop(x, tab_base_x, style)
    if stop > right_edge:
        return x
    return _tab_target_x(stop, kind, x, segment_width)


def _wrap_one_line(
    tokens: list[_Token], tab_base_x: float, line_start: float, right_edge: float
) -> tuple[list[_Token], list[_Token]]:
    """Consume tokens (in order) to build ONE line fitting within
    [line_start, right_edge], tracking the actual absolute X position
    (not just an abstract running width) so a tab's real jump distance
    -- which can be far larger than any single word, if the style's tab
    ruler was set up for a wider frame than this one -- is accounted
    for: a tab whose target would land past *right_edge* forces a line
    break first, rather than being allowed to carry the rest of the line
    off past the frame's (and possibly the page's) edge. Also stops
    early at a forced "break" token (a PageBreakMark -- CTRL_N in the
    story data, "force to next" in the conversion source, which emits
    a DDL {newpage} for it) and at an "embed" token (an inline
    picture), WITHOUT consuming either -- the caller has its own,
    separate handling for both (forcing an actual container/frame
    advance for "break"; placing the picture as its own block for
    "embed"; see _flow_paragraphs_into_containers) and needs to see
    the token itself to run it. Returns (line_tokens, remaining_tokens);
    at least one token is always consumed when *tokens* is non-empty
    and doesn't start with a break or an embed (an over-wide single
    word or tab still gets its own line rather than being dropped)."""
    line: list[_Token] = []
    x = line_start
    for i, tok in enumerate(tokens):
        if tok.kind in ("break", "embed"):
            _strip_trailing_space(line)
            return line, tokens[i:]
        if tok.kind == "tab":
            stop, _kind = _next_tab_stop(x, tab_base_x, tok.style)
            if stop > right_edge and line:
                _strip_trailing_space(line)
                return line, tokens[i:]
            line.append(tok)
            segment_width = _segment_width(tokens[i + 1 :])
            x = _tab_advance(x, tab_base_x, tok.style, right_edge, segment_width)
            continue
        width = _approx_width(tok.text, tok.style)
        if tok.kind == "word" and line and x + width > right_edge:
            _strip_trailing_space(line)
            return line, tokens[i:]
        line.append(tok)
        x += width
    _strip_trailing_space(line)
    return line, []


#: Below this usable width (in points), a line's Y-band is treated as
#: fully blocked by a repel obstacle rather than attempting to place
#: anything in the sliver that's left; see _narrow_for_obstacles.
_MIN_USABLE_WIDTH = 10.0


def _narrow_for_obstacles(
    left: float,
    right: float,
    y_top: float,
    y_bottom: float,
    obstacles: list[tuple[float, float, float, float]],
) -> tuple[float, float]:
    """Shrink [left, right] around any obstacle box (ox0,oy0,ox1,oy1)
    whose Y-range intersects this line's [y_bottom, y_top] band and
    whose X-range overlaps the current usable range, pushing in from
    whichever side has less room -- dynamic text repel around a picture
    or other repel-flagged frame; see _repel_obstacles_for_page and the
    module docstring."""
    for ox0, oy0, ox1, oy1 in obstacles:
        if oy1 <= y_bottom or oy0 >= y_top:
            continue
        if ox1 <= left or ox0 >= right:
            continue
        if (ox0 - left) <= (right - ox1):
            left = max(left, ox1)
        else:
            right = min(right, ox0)
    return left, right


def _wrap_tokens(
    tokens: list[_Token], tab_base_x: float, line_start_first: float, line_start_normal: float, right_edge: float
) -> list[list[_Token]]:
    """Greedy word-wrap of the whole of *tokens* at a single, fixed
    *right_edge*, via repeated _wrap_one_line calls. *line_start_first*
    is the first wrapped line's left edge (for first-line/hanging
    indents), *line_start_normal* every line after it."""
    lines: list[list[_Token]] = []
    remaining = tokens
    line_start = line_start_first
    while remaining or not lines:
        line, remaining = _wrap_one_line(remaining, tab_base_x, line_start, right_edge)
        lines.append(line)
        line_start = line_start_normal
        if not remaining:
            break
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
        #: dictionary_index -> {id(frame): [render_line() call-arg tuples]},
        #: computed once per story (across its whole frame chain, if any)
        #: the first time any of its frames is encountered; see
        #: _compute_chain_layout.
        self._story_layouts: dict[int, dict] = {}
        #: chapter id -> {id(frame): PageGroup}, for locating a chain
        #: member's own page (needed to resolve its geometry) when it
        #: isn't the frame currently being drawn; see _frame_page_map.
        self._frame_page_maps: dict[int, dict] = {}
        #: id(page) -> [(x0,y0,x1,y1), ...] repel-obstacle boxes on that
        #: page, in PDF points; see _repel_obstacles_for_page.
        self._repel_obstacles: dict[int, list] = {}
        #: chapter id -> {embed_tag: Frame}, for resolving an inline
        #: EmbedMark to the frame it anchors; see _embed_frame_map.
        self._embed_frame_maps: dict[int, dict[int, Frame]] = {}
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
        #: dictionary_index values whose box has already been drawn once
        #: on this page; see _draw_frame's use of it.
        self._dictionary_seen_this_page: set[int] = set()
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

    def _frame_appearance_and_origin(
        self, frame: Frame, page: PageGroup, default_origin: tuple[int, int]
    ) -> tuple[Frame, tuple[int, int]]:
        """The (appearance, origin) pair _draw_frame and the chain-layout
        precompute both need: *frame*'s own geometry with *default_origin*
        (the content page's own canvas for a normal page frame, or the
        master page's own separate canvas for a piece of master
        furniture), unless *frame* is master-linked, in which case its
        appearance and origin are both substituted from the master page
        (_effective_frame) -- master pages keep entirely their own
        absolute coordinate space, unrelated to the content pages that
        use them."""
        appearance = self._effective_frame(frame, page)
        if appearance is frame:
            return appearance, default_origin
        master_page_box = page.master_page.page
        return appearance, (master_page_box.x0 + master_page_box.bleed, master_page_box.y0 + master_page_box.bleed)

    def _repel_obstacles_for_page(
        self, page: PageGroup, exclude: Optional[Frame] = None
    ) -> list[tuple[float, float, float, float]]:
        """Every repel-flagged frame's own repel box (exx0..exy1 -- not
        its plain outer box; see "Frame object common layout" in
        docs/impression-documents.xml) visible on *page*: its own
        local/master-linked frames, plus any unlinked master furniture
        (the same set _draw_frame's master-furniture pass in begin_page
        draws). In final PDF points, relative to *page*'s own origin.
        The (frame, rect) pairs are cached per page; *exclude*, if
        given, is filtered out of the returned rects by identity -- a
        frame that's itself repel-flagged (e.g. a text frame meant to
        push *other* frames' text away from it) must not obstruct its
        own text when it's the one currently being laid out. Used by
        _flow_paragraphs_into_containers to shrink a text line's
        available width around an obstacle (dynamic text repel)."""
        cached = self._repel_obstacles.get(id(page))
        if cached is None:
            default_origin = (page.page.x0 + page.page.bleed, page.page.y0 + page.page.bleed)
            pairs: list[tuple[Frame, tuple[float, float, float, float]]] = []

            def add(frame: Frame) -> None:
                if not frame.repel:
                    return
                appearance, (ox, oy) = self._frame_appearance_and_origin(frame, page, default_origin)
                x0 = (appearance.exx0 - ox) / UNIT
                y0 = (appearance.exy0 - oy) / UNIT
                x1 = (appearance.exx1 - ox) / UNIT
                y1 = (appearance.exy1 - oy) / UNIT
                if x1 > x0 and y1 > y0:
                    pairs.append((frame, (x0, y0, x1, y1)))

            for frame in page.frames:
                add(frame)
            if page.master_page is not None:
                linked_indices = {f.master_index for f in page.frames if f.master}
                for mframe in page.master_page.frames:
                    if mframe.master_index not in linked_indices:
                        add(mframe)

            cached = pairs
            self._repel_obstacles[id(page)] = cached

        if exclude is None:
            return [rect for _frame, rect in cached]
        return [rect for frame, rect in cached if frame is not exclude]

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
        *source_origin*'s canvas. See _frame_appearance_and_origin for
        what changes when *frame* is master-linked."""
        if frame.embed_tag:
            # Anchored inline within a text story instead, at the
            # matching EmbedMark's own position -- never drawn at its
            # own raw position on the page in normal front-to-back
            # order; see docs/impression-documents.xml, "Frame object
            # common layout" (embedtag) and _embed_frame_map. Drawing
            # it here too, at its raw (and often stale/irrelevant) box,
            # was the direct cause of inline pictures visually
            # overlaying running text instead of the text flowing
            # around them.
            return
        if isinstance(frame, GuideFrame):
            return  # non-printing
        if isinstance(frame, GroupFrame):
            self.log.best_effort(
                "frame",
                "group frame membership not reproduced in PDF output; the group "
                "marker itself has no visual representation",
            )
            return

        draw_box = True
        if dictionary_dedupe and isinstance(frame, (TextFrame, BlankFrame)) and frame.dictionary_index >= 0:
            if frame.dictionary_index in self._dictionary_seen_this_page:
                # A later frame in the same story's chain, on this same
                # page, as an earlier one (real documents do this to
                # hand-emulate text flowing around an obstacle picture,
                # using two frames instead of dynamic repel, which isn't
                # implemented -- see the module docstring). Its box can
                # spatially overlap or fully enclose the earlier member's
                # box, so drawing its own fill/border here would paint
                # over already-placed text; skip the box (but still flow
                # this frame's own share of the story's text into it,
                # below).
                draw_box = False
            else:
                self._dictionary_seen_this_page.add(frame.dictionary_index)

        appearance, appearance_origin = self._frame_appearance_and_origin(frame, page, source_origin)

        saved_origin = self._origin
        self._origin = appearance_origin
        try:
            boundary = appearance.boundary if isinstance(appearance, PictureFrame) else None
            if boundary and draw_box:
                self._content.append("q\n")
                self._content.append(self._boundary_clip_path(appearance, boundary))

            if draw_box:
                self._draw_box(appearance)
            if isinstance(frame, PictureFrame):
                self._draw_picture(frame, appearance)
            elif isinstance(frame, (TextFrame, BlankFrame)):
                self._draw_story(frame, appearance, chapter, page, dictionary_dedupe=dictionary_dedupe)

            if boundary and draw_box:
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
            # Each edge is independent (0xFF = absent, anything else
            # present; see docs/impression-documents.xml, "Frame object
            # common layout" -- border0=top, border1=left, border2=
            # right, border3=bottom, confirmed empirically against a
            # real document and the user's own reading of Impression's
            # ruler dialog), so a full rectangle stroke is wrong
            # whenever fewer than all four are set -- confirmed against
            # a real document (PCI_Spec's own footer frame, top+bottom
            # only) where it drew two extra, entirely fictional side
            # borders.
            border_colour = frame.border_colour(self.document.colours)
            self._content.append(_stroke_colour_op(border_colour))
            self._content.append(f"{_fmt(self.border_width_pt)} w\n")
            edges = []
            if frame.border0 != 0xFF:  # top
                edges.append(f"{_fmt(x0)} {_fmt(y1)} m {_fmt(x1)} {_fmt(y1)} l S\n")
            if frame.border1 != 0xFF:  # left
                edges.append(f"{_fmt(x0)} {_fmt(y0)} m {_fmt(x0)} {_fmt(y1)} l S\n")
            if frame.border2 != 0xFF:  # right
                edges.append(f"{_fmt(x1)} {_fmt(y0)} m {_fmt(x1)} {_fmt(y1)} l S\n")
            if frame.border3 != 0xFF:  # bottom
                edges.append(f"{_fmt(x0)} {_fmt(y0)} m {_fmt(x1)} {_fmt(y0)} l S\n")
            self._content.append("".join(edges))

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
        x0, y0 = self._to_pt(appearance.x0, appearance.y0)
        x1, y1 = self._to_pt(appearance.x1, appearance.y1)
        self._draw_picture_at(pict, x0, y0, x1, y1)

    def _draw_embedded_picture(self, pict: PictureFrame, x0: float, y0: float, x1: float, y1: float) -> None:
        """As _draw_picture, but for a picture anchored inline within a
        text story (a non-zero embed_tag; see _embed_frame_map) rather
        than one drawn at its own raw position on the page: *x0..y1*
        are already the picture's own computed inline placement in the
        current frame's local point space (see
        _flow_paragraphs_into_containers), not derived from the
        frame's own stored box at all. Deliberately does not draw the
        frame's own fill/border (unlike a page-positioned picture) --
        those are defined relative to the frame's own raw box, which
        has no correspondence to this recomputed inline position."""
        self._draw_picture_at(pict, x0, y0, x1, y1)

    def _draw_picture_at(self, pict: PictureFrame, x0: float, y0: float, x1: float, y1: float) -> None:
        if pict.dictionary_index < 0:
            return
        entry = self._dictionary_by_index.get(pict.dictionary_index)
        if entry is None:
            self.log.error("picture", f"no dictionary entry for index {pict.dictionary_index}")
            return
        if entry.type is not DictionaryEntryType.PICTURE:
            return
        if x1 <= x0 or y1 <= y0:
            return

        with self.catch("picture", location=f"dictionary entry {entry.index}"):
            data = self.document.picture_bytes(entry)
            self._draw_picture_content(data, entry, x0, y0, x1, y1, pict)

    def _draw_picture_content(
        self, data: bytes, entry, x0: float, y0: float, x1: float, y1: float, pict: PictureFrame
    ) -> None:
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
                self._draw_drawfile_picture(draw, x0, y0, x1, y1, pict)
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

    # -- DrawFile pictures -----------------------------------------------------

    def _draw_drawfile_picture(
        self, draw: DrawFile, x0: float, y0: float, x1: float, y1: float, pict: PictureFrame
    ) -> None:
        """Render a decoded DrawFile's objects directly as PDF vector
        content, using the picture frame's own declared display scale
        (pict.xscale/yscale) to size the DrawFile's own native-size
        content, then centring it within the frame's box
        [x0,y0,x1,y1] -- NOT stretched to fill it. Confirmed against a
        real document and the user's own reading of Impression's
        picture info dialog: a picture frame is a clip window onto its
        content, sized independently of that content's own true size,
        not a box the content is stretched to fill (stretching gave a
        visibly, sometimes drastically, wrong size and aspect ratio
        whenever the frame's own box didn't happen to exactly match
        the picture's native bounds at 100% scale).

        pict.xscale/yscale are stored as the *inverse* of the picture's
        displayed scale (confirmed against the real document: a raw
        value of 0x20000, i.e. 2.0, is a genuine 50% display scale --
        matches ovprodll.py's own _tr_setscale, which this project's
        DDL output already relies on for the same fields).

        pict.xshift/yshift and pict.angle are NOT applied here.
        xshift/yshift looked, from their on-disk field names and
        ovprodll.py's own DDL emission, like a millipoint offset of the
        content's own bottom-left corner from the frame's bottom-left
        -- but every real inline picture checked (three, in one real
        document) had a yshift value that, applied that way, clipped
        away a real, visible part of the picture (confirmed against
        the user's own reference image) rather than just repositioning
        it within an otherwise-empty margin; the true anchor/sign
        convention needs the real OvationPro DDL "picturedata bottomleft"
        semantics to pin down properly, not a guess against one
        (possibly misleading) data point. Centring instead guarantees
        the whole scaled picture stays visible -- a safer default than
        risking silently cropping real content -- until that's
        confirmed. Rotation is a separate, unimplemented piece of work
        regardless; a non-zero angle is logged once rather than
        silently ignored. See the module docstring for what else is
        approximated (dash patterns, caps/joins) versus what's a
        genuine placeholder (Sprite objects embedded within the file,
        and any other undecoded object type)."""
        bounds = draw.bounds
        display_scale_x = (0x10000 / pict.xscale) if pict.xscale else 1.0
        display_scale_y = (0x10000 / pict.yscale) if pict.yscale else 1.0
        sx = _DRAW_UNIT_TO_PT * display_scale_x
        sy = _DRAW_UNIT_TO_PT * display_scale_y
        displayed_w = bounds.width * sx
        displayed_h = bounds.height * sy
        origin_x = x0 + max(0.0, ((x1 - x0) - displayed_w) / 2.0)
        origin_y = y0 + max(0.0, ((y1 - y0) - displayed_h) / 2.0)

        def to_pt(dx: int, dy: int) -> tuple[float, float]:
            return origin_x + (dx - bounds.x0) * sx, origin_y + (dy - bounds.y0) * sy

        self._content.append("q\n")
        self._content.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(x1 - x0)} {_fmt(y1 - y0)} re W n\n")
        notes: list[str] = []
        if pict.angle:
            notes.append("a DrawFile picture's own rotation is not applied; it is drawn unrotated")
        for obj in draw.objects:
            self._draw_drawfile_object(obj, draw.fonts, to_pt, (sx, sy), notes)
        self._content.append("Q\n")
        for note in dict.fromkeys(notes):  # de-duplicate, keep first-seen order
            self.log.best_effort("picture", note)

    def _draw_drawfile_object(self, obj, fonts: dict, to_pt, scale: tuple[float, float], notes: list[str]) -> None:
        if isinstance(obj, DrawPath):
            self._draw_drawfile_path(obj, to_pt, scale)
            if obj.dashed:
                notes.append("dashed DrawFile path lines are rendered solid; dash patterns are not reproduced")
        elif isinstance(obj, DrawText):
            self._draw_drawfile_text(obj, fonts, to_pt, scale)
        elif isinstance(obj, DrawGroup):
            for child in obj.objects:
                self._draw_drawfile_object(child, fonts, to_pt, scale, notes)
        elif isinstance(obj, DrawTagged):
            if obj.inner is not None:
                self._draw_drawfile_object(obj.inner, fonts, to_pt, scale, notes)
        elif isinstance(obj, DrawSprite):
            sx0, sy0 = to_pt(obj.bounds.x0, obj.bounds.y0)
            sx1, sy1 = to_pt(obj.bounds.x1, obj.bounds.y1)
            self._draw_placeholder(min(sx0, sx1), min(sy0, sy1), max(sx0, sx1), max(sy0, sy1), "Sprite")
            notes.append(
                "a Sprite object embedded within a DrawFile picture is drawn as a "
                "placeholder box; pixel data is not decoded"
            )
        else:  # DrawUnknown -- text area, options, transformed text/sprite, or unrecognised
            notes.append(
                "one or more DrawFile object types (e.g. text area, options, transformed "
                "text/sprite) within a picture were not decoded and are omitted"
            )

    def _draw_drawfile_path(self, path: DrawPath, to_pt, scale: tuple[float, float]) -> None:
        has_fill = path.fill_colour is not None
        has_stroke = path.stroke_colour is not None
        if (not has_fill and not has_stroke) or not path.ops:
            return

        path_parts = []
        for op in path.ops:
            if op.code in (DrawPathOpCode.MOVE, DrawPathOpCode.MOVE_INTERNAL, DrawPathOpCode.GAP):
                x, y = to_pt(op.x, op.y)
                path_parts.append(f"{_fmt(x)} {_fmt(y)} m\n")
            elif op.code is DrawPathOpCode.LINE:
                x, y = to_pt(op.x, op.y)
                path_parts.append(f"{_fmt(x)} {_fmt(y)} l\n")
            elif op.code is DrawPathOpCode.CURVE:
                cx1, cy1 = to_pt(op.cx1, op.cy1)
                cx2, cy2 = to_pt(op.cx2, op.cy2)
                ex, ey = to_pt(op.x, op.y)
                path_parts.append(f"{_fmt(cx1)} {_fmt(cy1)} {_fmt(cx2)} {_fmt(cy2)} {_fmt(ex)} {_fmt(ey)} c\n")
            elif op.code is DrawPathOpCode.CLOSE_LINE:
                path_parts.append("h\n")
            # CLOSE_GAP: no direct PDF equivalent needed -- the next MOVE/GAP
            # starts a fresh subpath anyway.
        if not path_parts:
            return

        style_parts = []
        width_pt = 0.0
        if has_fill:
            style_parts.append(_draw_rgb_op(path.fill_colour, stroke=False))
        if has_stroke:
            style_parts.append(_draw_rgb_op(path.stroke_colour, stroke=True))
            # scale[i] is already "target points per source Draw unit"
            # (the same ratio to_pt uses for coordinates), so it
            # converts line_width (in Draw units) to points directly --
            # no separate _DRAW_UNIT_TO_PT factor needed here (unlike
            # _draw_drawfile_text's size_pt, which starts from a value
            # already in points and so needs the opposite correction).
            line_scale = (abs(scale[0]) + abs(scale[1])) / 2.0
            width_pt = path.line_width * line_scale if path.line_width else 0.3
            style_parts.append(f"{_fmt(max(0.1, width_pt))} w\n")

        op_code = {(True, True): "B", (True, False): "f", (False, True): "S"}[(has_fill, has_stroke)]
        if has_fill and path.even_odd:
            op_code += "*"

        self._content.append("".join(style_parts) + "".join(path_parts) + f"{op_code}\n")

        if has_stroke and (path.start_cap == CAP_TRIANGULAR or path.end_cap == CAP_TRIANGULAR):
            self._draw_triangular_caps(path, to_pt, width_pt)

    def _draw_triangular_caps(self, path: DrawPath, to_pt, width_pt: float) -> None:
        """Draw a filled arrowhead-style triangle at the start and/or
        end of each open subpath in *path*, matching the cap style the
        real RISC OS DrawFile module applies via Draw_Stroke's own
        triangular cap feature -- confirmed against that module's own
        rendering implementation, not just the PRM's field
        descriptions (see formats/drawfile.py's own module docstring).
        PDF's line-cap styles (butt/round/square) have no triangular
        option, so this draws the exact same shape Draw_Stroke itself
        would produce as an explicit filled triangle instead: a
        *width*-wide base sitting at the path's own endpoint, an apex
        extending *length* further out along the path's own tangent
        direction there (both stored in the file as 1/16ths of the
        stroke's own line width -- see DrawPath.triangle_cap_width/
        length -- scaled here via *width_pt*, the stroke's own already-
        computed on-page width). A closed subpath (one ending in
        CLOSE_LINE/CLOSE_GAP) has no real start/end to cap and is
        skipped."""
        for start_pt, start_dir, end_pt, end_dir in _subpath_cap_directions(path.ops, to_pt):
            if path.start_cap == CAP_TRIANGULAR:
                self._draw_one_triangular_cap(path, start_pt, start_dir, width_pt)
            if path.end_cap == CAP_TRIANGULAR:
                self._draw_one_triangular_cap(path, end_pt, end_dir, width_pt)

    def _draw_one_triangular_cap(
        self, path: DrawPath, point: tuple[float, float], direction: tuple[float, float], width_pt: float
    ) -> None:
        if direction == (0.0, 0.0):
            return  # a zero-length subpath has no direction to point the cap in
        cap_width_pt = (path.triangle_cap_width / 16.0) * width_pt
        cap_length_pt = (path.triangle_cap_length / 16.0) * width_pt
        if cap_width_pt <= 0 or cap_length_pt <= 0:
            return
        (x0, y0), (x1, y1), (x2, y2) = _triangular_cap_polygon(point, direction, cap_width_pt, cap_length_pt)
        self._content.append(_draw_rgb_op(path.stroke_colour, stroke=False))
        self._content.append(f"{_fmt(x0)} {_fmt(y0)} m {_fmt(x1)} {_fmt(y1)} l {_fmt(x2)} {_fmt(y2)} l h f\n")

    def _draw_drawfile_text(self, text: DrawText, fonts: dict, to_pt, scale: tuple[float, float]) -> None:
        if not text.text.strip() or text.size_y <= 0:
            return
        sx, sy = scale
        # text.size_y is already in points (1/640 point, per the
        # format), unlike a path's Draw-unit-denominated line_width --
        # so scaling it by sy directly (a points-per-Draw-unit ratio)
        # would be a unit mismatch, giving a font size smaller than
        # intended by roughly the same factor sy is smaller than 1pt-
        # per-Draw-unit (confirmed against a real document: this
        # produced a ~0.01pt font size, rendering the text completely,
        # invisibly small rather than visibly missing). Dividing by
        # _DRAW_UNIT_TO_PT first turns sy into the dimensionless
        # magnification the picture is actually being drawn at.
        size_pt = (text.size_y / 640.0) * (abs(sy) / _DRAW_UNIT_TO_PT)
        if size_pt <= 0.01:
            return
        # The DrawFile's own x/y font-size ratio, composed with the
        # picture's own (possibly non-uniform) sx/sy scale, becomes a
        # single PDF horizontal-scaling percentage (Tz) -- always set
        # explicitly, never left to inherit from a previous text run,
        # since Tz is graphics-state and would otherwise leak into
        # whatever text is drawn next within this picture's own q/Q.
        hscale_pct = 100.0 * (text.size_x * sx) / (text.size_y * sy) if sy else 100.0
        pdf_font = _standard_font_for(fonts.get(text.font_number), bold=False, italic=False)
        x, y = to_pt(text.baseline_x, text.baseline_y)
        colour_op = _draw_rgb_op(text.colour, stroke=False) if text.colour is not None else "0 0 0 rg\n"
        self._content.append(
            f"{colour_op}BT {_fmt(hscale_pct)} Tz /{self._font_resource_name[pdf_font]} {_fmt(size_pt)} Tf "
            f"{_fmt(x)} {_fmt(y)} Td {_pdf_str(text.text)} Tj ET\n"
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

        box = self._inset_box_pt(appearance)
        if box is None:
            return
        x0, y0, x1, y1 = box

        if dictionary_dedupe and frame.dictionary_index in self._story_layouts:
            lines = self._story_layouts[frame.dictionary_index].get(id(frame), [])
        elif dictionary_dedupe:
            # Flowing a story across its whole frame chain needs to happen
            # once, globally, the first time any of its frames is
            # encountered -- not per frame -- since later chain members
            # only receive whatever didn't fit in earlier ones. A story
            # that isn't genuinely a content-page chain (see
            # _compute_chain_layout) isn't cached here at all -- each
            # occurrence is independent and gets its own fresh copy
            # instead.
            story = None
            with self.catch("story", location=f"dictionary entry {entry.index}"):
                story = self.document.story(entry)
            if story is None:
                return
            layout = self._compute_chain_layout(story, entry, chapter, frame, page)
            if layout is not None:
                self._story_layouts[frame.dictionary_index] = layout
                lines = layout.get(id(frame), [])
            else:
                obstacles_by_key = {id(frame): self._repel_obstacles_for_page(page, exclude=frame)}
                assignments = self._flow_paragraphs_into_containers(
                    story.paragraphs, entry.index, [(id(frame), id(page), x0, y0, x1, y1)], chapter, obstacles_by_key
                )
                lines = assignments.get(id(frame), [])
        else:
            # Master furniture: rendered fresh, independently, on every
            # page that uses it (not deduped/chain-flowed -- each page
            # needs its own copy, e.g. so {pageno}-style marks evaluate
            # correctly per page).
            story = None
            with self.catch("story", location=f"dictionary entry {entry.index}"):
                story = self.document.story(entry)
            if story is None:
                return
            obstacles_by_key = {id(frame): self._repel_obstacles_for_page(page, exclude=frame)}
            assignments = self._flow_paragraphs_into_containers(
                story.paragraphs, entry.index, [(id(frame), id(page), x0, y0, x1, y1)], chapter, obstacles_by_key
            )
            lines = assignments.get(id(frame), [])

        if not lines:
            return
        self._content.append("q\n")
        self._content.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(x1 - x0)} {_fmt(y1 - y0)} re W n\n")
        for entry in lines:
            if entry[0] == "embed":
                _, pict, ex0, ey0, ex1, ey1 = entry
                self._draw_embedded_picture(pict, ex0, ey0, ex1, ey1)
            else:
                self._render_line(*entry)
        self._content.append("Q\n")

    def _inset_box_pt(self, frame: Frame) -> Optional[tuple[float, float, float, float]]:
        """*frame*'s content box (outer box minus hinset/vinset) in PDF
        points, using the currently-active origin (self._origin, as set
        up by _draw_frame's origin swap for whichever frame is currently
        being drawn)."""
        x0, y0 = self._to_pt(frame.x0 + frame.hinset, frame.y0 + frame.vinset)
        x1, y1 = self._to_pt(frame.x1 - frame.hinset, frame.y1 - frame.vinset)
        if x1 <= x0 or y1 <= y0:
            return None
        return x0, y0, x1, y1

    def _inset_box_pt_for(self, frame: Frame, page: PageGroup) -> Optional[tuple[float, float, float, float]]:
        """As _inset_box_pt, but self-contained: resolves *frame*'s own
        appearance/origin (via _frame_appearance_and_origin) rather than
        relying on self._origin already being set for it -- needed for a
        frame chain's OTHER members, which aren't the frame currently
        being drawn."""
        default_origin = (page.page.x0 + page.page.bleed, page.page.y0 + page.page.bleed)
        appearance, (ox, oy) = self._frame_appearance_and_origin(frame, page, default_origin)
        x0 = (appearance.x0 + appearance.hinset - ox) / UNIT
        y0 = (appearance.y0 + appearance.vinset - oy) / UNIT
        x1 = (appearance.x1 - appearance.hinset - ox) / UNIT
        y1 = (appearance.y1 - appearance.vinset - oy) / UNIT
        if x1 <= x0 or y1 <= y0:
            return None
        return x0, y0, x1, y1

    def _frame_page_map(self, chapter: Chapter) -> dict[int, PageGroup]:
        """id(frame) -> the PageGroup it's on, across every page of
        *chapter*; built once per chapter and cached, for locating a
        frame chain's other members' pages in _compute_chain_layout."""
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

    def _embed_frame_map(self, chapter: Chapter) -> dict[int, Frame]:
        """embed_tag -> the Frame it's carried by, across every content
        page of *chapter*; built once per chapter and cached. A frame
        with a non-zero embed_tag is anchored inline within a text
        story at the point a matching EmbedMark occurs, instead of
        being drawn at its own raw position on the page in normal
        front-to-back order -- see docs/impression-documents.xml,
        "Frame object common layout" (embedtag) and "Embedded and
        merge markers"."""
        cached = self._embed_frame_maps.get(id(chapter))
        if cached is not None:
            return cached
        mapping: dict[int, Frame] = {}
        for page in chapter.pages:
            for record in page.records:
                frame = record.value
                if isinstance(frame, Frame) and frame.embed_tag:
                    mapping[frame.embed_tag] = frame
        self._embed_frame_maps[id(chapter)] = mapping
        return mapping

    def _resolve_content_chain_quietly(self, story: Story, chapter: Chapter) -> list:
        """Like Converter.resolve_frame_chain(chapter=chapter, master=False),
        but without logging a failed offset as an error -- used only to
        test whether a story's frame_chain is genuinely anchored to
        *chapter*'s own content pages (a real, flowing chain) versus a
        master page (independently-repeated content linked onto several
        chapters, which isn't a chain at all -- see
        _compute_chain_layout). A real failure to resolve gets its
        chance to log for real when this comes back negative and the
        caller falls back to single-frame handling instead."""
        saved_log = self.log
        self.log = ConversionLog()
        try:
            return self.resolve_frame_chain(story, chapter=chapter, master=False)
        finally:
            self.log = saved_log

    def _compute_chain_layout(
        self, story: Story, entry, chapter: Chapter, fallback_frame: Frame, fallback_page: PageGroup
    ) -> Optional[dict[int, list]]:
        """Resolve *story*'s full frame chain (in reading order) and flow
        its whole text across every member's own box in turn, moving on
        to the next member whenever one fills up. Returns the same
        id(frame) -> [render_line() call-arg tuples] mapping
        _flow_paragraphs_into_containers does; a later chain member not
        yet reached by the page walk just isn't drawn until its own
        _draw_story call looks its entry up here.

        Returns None if *story*'s frame_chain doesn't fully resolve
        against *chapter*'s own content pages: a master-linked frame
        repeated independently across several chapters (e.g. a running
        header/footer) shares one dictionary_index per occurrence just
        like a real chain does, but its frame_chain data (when it has
        any at all) is anchored to the master page it's defined on, not
        to any particular chapter -- so it isn't a flow this converter
        should try to distribute text across at all; the caller falls
        back to laying each occurrence out fresh and independently."""
        if not story.frame_chain:
            return None
        chain = self._resolve_content_chain_quietly(story, chapter)
        if len(chain) != len(story.frame_chain):
            return None

        chain_frames = [record.value for record in chain if isinstance(record.value, Frame)]
        if not any(f is fallback_frame for f in chain_frames):
            chain_frames = [fallback_frame] + chain_frames

        frame_page_map = self._frame_page_map(chapter)
        containers = []
        obstacles_by_key: dict[int, list] = {}
        for cframe in chain_frames:
            member_page = fallback_page if cframe is fallback_frame else frame_page_map.get(id(cframe))
            if member_page is None:
                continue
            box = self._inset_box_pt_for(cframe, member_page)
            if box is None:
                continue
            containers.append((id(cframe), id(member_page), *box))
            obstacles_by_key[id(cframe)] = self._repel_obstacles_for_page(member_page, exclude=cframe)

        if not containers:
            return {}
        return self._flow_paragraphs_into_containers(
            story.paragraphs, entry.index, containers, chapter, obstacles_by_key
        )

    def _paragraph_tokens(
        self, paragraph, dictionary_index: int, body_style: Style, chapter: Chapter
    ) -> tuple[list[_Token], Style]:
        tokens: list[_Token] = []
        # A mark (most commonly a leading TabMark -- real documents use
        # one to right-align a list's own number column, before any
        # Run) appearing before the paragraph's first Run/EmbedMark has
        # no style of its own to inherit otherwise; falling back to the
        # document's body style instead of the paragraph's own applied
        # style silently used the wrong tab ruler for it (confirmed
        # against a real document's numbered contents list: a leading
        # tab landed on the body style's own ruler while every later
        # tab in the same line correctly used the paragraph's own,
        # producing inconsistent alignment from row to row depending on
        # how each row's own text happened to interact with the wrong
        # ruler). EmbedMark counts too, alongside Run -- a paragraph
        # consisting of nothing but a single embedded picture (a real,
        # confirmed case) has no Run at all for a style to otherwise
        # come from.
        first_style_slots = next(
            (item.style_slots for item in paragraph.items if isinstance(item, (Run, EmbedMark))), None
        )
        current_style = self.resolve_style(first_style_slots) if first_style_slots is not None else body_style
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
                # Like Run, an EmbedMark carries its own style_slots
                # (e.g. a "Centre" alignment effect applied around it,
                # confirmed against a real document) -- update
                # current_style the same way a Run does, both for its
                # own token and for whatever paragraph content follows.
                current_style = self.resolve_style(item.style_slots)
                frame = self._embed_frame_map(chapter).get(item.embed_tag)
                if isinstance(frame, PictureFrame) and frame.x1 > frame.x0 and frame.y1 > frame.y0:
                    tokens.append(_Token("embed", "", current_style, embed_frame=frame))
                else:
                    # Not a picture (format doc: "frames" in general can
                    # carry an embed_tag, but every real example found
                    # is a PictureFrame -- see module docstring), or no
                    # matching frame at all, or a degenerate zero-size
                    # box; there's nothing sensible to lay out inline,
                    # so the reference is silently skipped, same as
                    # before this was implemented at all.
                    if frame is not None:
                        self.log.best_effort(
                            "story",
                            "an embedded frame that isn't a picture is not placed inline",
                            location=f"dictionary entry {dictionary_index}",
                        )
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

    def _flow_paragraphs_into_containers(
        self,
        paragraphs: tuple,
        dictionary_index: int,
        containers: list[tuple[int, int, float, float, float, float]],
        chapter: Chapter,
        obstacles_by_key: Optional[dict[int, list]] = None,
    ) -> dict[int, list]:
        """Lay out *paragraphs* across *containers* in order -- each a
        (key, page_key, x0, y0, x1, y1) box in PDF points -- moving on to
        the next container whenever the current one fills up mid-paragraph
        (a paragraph's remaining, not-yet-placed tokens are re-wrapped for
        the new container, since chain members can have different
        widths). Lines are placed one at a time (not a whole paragraph at
        once), since *obstacles_by_key* -- each container's own key ->
        repel-flagged frames' boxes on its page (from
        _repel_obstacles_for_page, with that container's own frame
        excluded from its own obstacle list) -- can narrow a line's
        available width differently band by band as Y descends (dynamic
        text repel around a picture or other obstacle). Returns key ->
        [entry, ...], where each entry is either a render_line() call-arg
        tuple, or an inline picture placement ("embed", frame, x0, y0,
        x1, y1) -- see _draw_story's own dispatch on entry[0]. A single-
        container caller (master furniture, or a story confined to one
        frame) just gets one key back. Logs a best_effort note, once, if
        the paragraphs run out of containers before they run out of
        content.

        An inline picture (a non-zero-embed_tag PictureFrame referenced
        by an EmbedMark; see _paragraph_tokens/_embed_frame_map) is laid
        out as its own block: it ends the current line (if any text has
        already been placed on it), is scaled to the paragraph's own
        current usable width preserving its own aspect ratio, and pushes
        every following line down below it -- rather than being drawn
        independently at its own raw, page-relative box (which, since
        nothing here ever accounted for its size, could overlay already-
        placed running text instead of displacing it).

        Real documents sometimes emulate text flowing around an obstacle
        (rather than relying purely on dynamic repel) by chaining two
        frames on the *same* page, where the second's box geometrically
        overlaps the first's. Starting the second at its own top edge in
        that case would visually collide with content already placed by
        the first, so a container never starts higher than the lowest
        point reached by an earlier container on the same page whose
        own X-range actually overlaps its own (see advance_container) --
        deliberately NOT any earlier same-page container regardless of
        position: side-by-side grid cells (e.g. several frames chained
        across a page laid out two-by-two, each getting its own quarter
        of the page -- confirmed against a real document) never
        visually overlap, so an earlier cell's leftover Y position must
        not constrain a later one that merely happens to share a page."""
        obstacles_by_key = obstacles_by_key or {}
        assignments: dict[int, list] = {key: [] for key, *_ in containers}
        if not containers:
            return assignments

        body_style = self.resolve_style([])
        #: Lowest Y each container's own content has reached so far,
        #: keyed by container key (NOT page_key) -- see advance_container.
        container_floor: dict[int, float] = {}
        container_index = 0
        key, page_key, cx0, cy0, cx1, cy1 = containers[0]
        y_cursor = cy1
        #: Set whenever a container is fresh (just started, or just
        #: advanced into) -- its very first line drops by that line's
        #: own ascent (see _ascent_pt), not a full line_height, since
        #: y_cursor here means "the box's own top edge", not "the
        #: previous line's baseline". Every later line, in any
        #: paragraph, drops by the normal full line_height.
        first_line_pending = True

        def advance_container() -> bool:
            nonlocal container_index, key, page_key, cx0, cy0, cx1, cy1, y_cursor, first_line_pending
            container_index += 1
            if container_index >= len(containers):
                return False
            key, page_key, cx0, cy0, cx1, cy1 = containers[container_index]
            # Only a genuinely earlier container on the SAME page whose
            # own X-range actually overlaps this one's (not just meets
            # at a shared edge) can constrain where this one starts --
            # see the docstring's "narrow frame beside an obstacle
            # chaining into a full-width one below it" case. Without
            # this check, side-by-side grid cells on one page (sharing
            # a page_key but never visually overlapping, confirmed
            # against a real document: ForDad's four caption frames,
            # laid out two-by-two) would wrongly inherit an unrelated
            # neighbour's own leftover Y position, starving them of
            # most of their own height and, in turn, triggering a
            # second, unintended container advance for content that
            # would otherwise have fit perfectly in the one right
            # beside it.
            floor = cy1
            for prior_key, prior_page_key, pcx0, _pcy0, pcx1, _pcy1 in containers[:container_index]:
                if prior_page_key != page_key or pcx1 <= cx0 or pcx0 >= cx1:
                    continue
                prior_floor = container_floor.get(prior_key)
                if prior_floor is not None:
                    floor = min(floor, prior_floor)
            y_cursor = min(cy1, floor)
            first_line_pending = True
            return True

        for paragraph in paragraphs:
            tokens, para_style = self._paragraph_tokens(paragraph, dictionary_index, body_style, chapter)
            left_indent = (para_style.left_indent or 0) / UNIT
            right_indent = (para_style.right_indent or 0) / UNIT
            first_indent = (para_style.first_indent or 0) / UNIT
            line_height = _line_height_pt(para_style)
            is_first_line = True
            placed_any_line = False

            # space_before (DDL "spaceabove") was never applied anywhere
            # in this converter -- confirmed against a real document
            # (Telegraph from the local moreexamples/ corpus, alongside
            # its "Main Heading" style's own leading bug above): its
            # heading style sets spaceabove 20pt, but the gap between
            # the preceding paragraph and the heading was missing
            # entirely in the PDF. Skipped at the very top of a
            # container (first_line_pending here means "nothing placed
            # in this container yet", whether it's the very first one or
            # one just advanced into) -- there's no preceding paragraph
            # in this container to space away from, matching how
            # ordinary DTP layout suppresses space-before at the top of
            # a column/frame.
            if not first_line_pending and para_style.space_before:
                y_cursor -= para_style.space_before / UNIT

            while tokens or not placed_any_line:
                if tokens and tokens[0].kind == "break":
                    # CTRL_N ("force to next") forces the flow to jump
                    # to the START of the next frame in the chain, not
                    # just to a new line in the same one -- confirmed
                    # against the conversion source (c/styles,
                    # txwritedata's CTRL_N case: "force to next",
                    # emitting a DDL {newpage}). A real document
                    # (ForSimon3 from the local moreexamples/ corpus)
                    # has two consecutive PageBreakMarks after its body
                    # text, meant to skip a page and land its final
                    # heading on the third frame of a three-frame
                    # chain; treating them as plain blank lines instead
                    # left the heading one frame too early, with the
                    # actual final frame sitting empty.
                    tokens = tokens[1:]
                    placed_any_line = True
                    first_line_pending = False
                    is_first_line = False
                    if not advance_container():
                        self.log.best_effort(
                            "story",
                            "text overflowed the available frame(s) and was clipped",
                            location=f"dictionary entry {dictionary_index}",
                        )
                        return assignments
                    continue
                if tokens and tokens[0].kind == "embed":
                    pict = tokens[0].embed_frame
                    # The frame's own box is the picture's real,
                    # intended on-page size (confirmed against a real
                    # document and the user's own reading of
                    # Impression's picture info dialog: a 77.08mm x
                    # 51.14mm frame in the document matched a 77.08mm x
                    # 51.14mm frame box here exactly) -- not something
                    # to be re-derived from the paragraph's own column
                    # width, which is usually much wider. Only shrunk,
                    # preserving aspect, if it doesn't fit the current
                    # column at all; never enlarged to fill it.
                    frame_width_pt = (pict.x1 - pict.x0) / UNIT
                    frame_height_pt = (pict.y1 - pict.y0) / UNIT
                    while True:
                        indent = (left_indent + first_indent) if is_first_line else left_indent
                        avail_x0 = cx0 + indent
                        avail_x1 = cx1 - right_indent
                        if avail_x1 - avail_x0 < _MIN_USABLE_WIDTH:
                            avail_x0, avail_x1 = cx0, cx1
                        avail_width = avail_x1 - avail_x0
                        if frame_width_pt <= 0 or frame_width_pt <= avail_width:
                            embed_width = frame_width_pt
                            embed_height = frame_height_pt
                        else:
                            embed_width = avail_width
                            embed_height = avail_width * (frame_height_pt / frame_width_pt)
                        # When the picture's own real size is narrower
                        # than the column, its horizontal position
                        # within that column follows the paragraph's
                        # own alignment -- confirmed against a real
                        # document: the exact paragraph carrying an
                        # inline picture had a "Centre" alignment
                        # effect applied to it, and the picture was
                        # indeed centred within its column, not left-
                        # aligned. Matches _render_line's own alignment
                        # handling for text (1=centre, 2=right; see the
                        # module docstring for why those two are an
                        # unconfirmed-but-conventional guess).
                        spare = avail_width - embed_width
                        if para_style.alignment == 1:
                            embed_x0 = avail_x0 + spare / 2.0
                        elif para_style.alignment == 2:
                            embed_x0 = avail_x0 + spare
                        else:
                            embed_x0 = avail_x0
                        embed_x1 = embed_x0 + embed_width
                        if y_cursor - embed_height >= cy0:
                            break
                        if not advance_container():
                            self.log.best_effort(
                                "story",
                                "text overflowed the available frame(s) and was clipped",
                                location=f"dictionary entry {dictionary_index}",
                            )
                            return assignments
                    embed_y1 = y_cursor
                    embed_y0 = y_cursor - embed_height
                    assignments[key].append(("embed", pict, embed_x0, embed_y0, embed_x1, embed_y1))
                    y_cursor = embed_y0
                    container_floor[key] = min(container_floor.get(key, y_cursor), y_cursor)
                    placed_any_line = True
                    first_line_pending = False
                    is_first_line = False
                    tokens = tokens[1:]
                    continue

                # The very first line dropped into a fresh container
                # only needs to clear its own font ascent below the
                # box's top edge, not a full line_height (that figure
                # includes descent/leading, which belongs *between*
                # lines, not above the first one) -- see _ascent_pt.
                drop = _ascent_pt(para_style) if first_line_pending else line_height
                line_top = y_cursor
                line_bottom = y_cursor - drop
                if line_bottom < cy0:
                    if not advance_container():
                        self.log.best_effort(
                            "story",
                            "text overflowed the available frame(s) and was clipped",
                            location=f"dictionary entry {dictionary_index}",
                        )
                        return assignments
                    continue

                indent = (left_indent + first_indent) if is_first_line else left_indent
                line_start = cx0 + indent
                right_edge = cx1 - right_indent
                if right_edge - line_start < _MIN_USABLE_WIDTH:
                    # The paragraph's own indent settings alone (no
                    # obstacle involved) already eliminate all usable
                    # width in this container. Two different real
                    # causes need two different fixes here:
                    #  - right_indent alone (with a normal/zero left
                    #    indent) can be set up for a much wider frame
                    #    than the one it's actually used in (styles are
                    #    shared across frames of any size; compare the
                    #    tab-ruler note in _next_tab_stop for the same
                    #    class of issue) -- falling back to the
                    #    container's own full width is the safe, general
                    #    fix.
                    #  - right_indent can *also* be set to (near) the
                    #    frame's own full width deliberately, on a
                    #    paragraph whose real content is short and
                    #    tab-terminated (a label before a value column,
                    #    say -- confirmed against a real document's
                    #    title-block style, whose "left bound" the user
                    #    could read directly off Impression's own ruler
                    #    dialog). Resetting line_start back to the
                    #    container's own left edge as well, on top of
                    #    dropping the right margin, wiped out that
                    #    paragraph's real, intentional hanging first-
                    #    line indent -- confirmed against the user's own
                    #    reference image, where the label column should
                    #    start well right of the frame's own edge, not
                    #    flush against it.
                    # Preferring to drop only the right margin (keeping
                    # line_start as computed) handles both: if
                    # line_start alone already leaves no usable room
                    # either, the full container width is still the
                    # fallback -- the "obstacle leaves no room" skip
                    # below would otherwise burn through this container,
                    # then the whole chain, without ever placing this
                    # paragraph, silently dropping every later paragraph
                    # in the story too, not just this one.
                    if cx1 - line_start >= _MIN_USABLE_WIDTH:
                        right_edge = cx1
                    else:
                        line_start, right_edge = cx0, cx1
                obstacles = obstacles_by_key.get(key)
                if obstacles:
                    line_start, right_edge = _narrow_for_obstacles(
                        line_start, right_edge, line_top, line_bottom, obstacles
                    )
                if right_edge - line_start < _MIN_USABLE_WIDTH:
                    # An obstacle leaves no usable room on this Y-band;
                    # skip past it rather than place a near-empty line.
                    y_cursor -= drop
                    first_line_pending = False
                    continue

                line, tokens = _wrap_one_line(tokens, cx0, line_start, right_edge)
                placed_any_line = True
                y_cursor -= drop
                first_line_pending = False
                container_floor[key] = min(container_floor.get(key, y_cursor), y_cursor)
                assignments[key].append(
                    (line, line_start, cx0, right_edge, y_cursor, bool(tokens), para_style.alignment)
                )
                is_first_line = False

            if para_style.space_after:
                y_cursor -= para_style.space_after / UNIT

        return assignments

    def _render_line(
        self,
        tokens: list[_Token],
        start_x: float,
        tab_base_x: float,
        right_edge: float,
        y: float,
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
        for idx, tok in enumerate(tokens):
            if tok.kind == "tab":
                segment_width = _segment_width(tokens[idx + 1 :])
                x = _tab_advance(x, tab_base_x, tok.style, right_edge, segment_width)
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
