"""Shared HTML5 output helpers: colour/style -> CSS mapping, DrawFile
picture rendering, and placeholder rendering, used by both the
scrolling and paged-media HTML5 converters.

Unlike the PDF converter, HTML output does no line-wrapping or text
layout of its own at all -- a browser's own rendering engine handles
that natively from the CSS this module produces, so there is no
equivalent of pdfdoc.py's approximate-metrics wrapping concern here.

DrawFile pictures are rendered as an inline SVG fragment (paths --
fill/stroke colour, width, winding rule -- and single-line text, via
formats/drawfile.py's object decoder), sized from the picture frame's
own declared display scale (pict.xscale/yscale) and positioned exactly
the way pdfdoc.py's own _draw_drawfile_picture is: xshift/yshift anchor
the drawfile's own native (0, 0) origin at the frame's own left/bottom
edge (adjusted by xshift/yshift and the frame's own hinset), rotated
about that same origin by pict.angle first if non-zero, falling back to
centred (shrunk to fit first if it doesn't fit natively) whenever the
shift would leave under 5% of the smaller of the frame/content
overlapping -- see _drawfile_svg's own docstring for the differences
from pdfdoc.py's version (there aren't many; SVG's Y-down coordinate
convention needs an explicit flip PDF doesn't, applied once at the very
end rather than threaded through the whole derivation, since PDF's own
convention already matches Draw's Y-up one directly). This project's
own convention is duplicated, self-contained logic per converter rather
than a shared implementation (see pdfdoc.py's own docstring for that
formula's full derivation and calibration history; not reproduced
again here). Text with a non-square x/y font-size ratio is rendered at
its plain y-based size rather than reproduced (pdfdoc.py can do this
cheaply via PDF's `Tz` horizontal-scaling operator; SVG has no equally
direct equivalent without first knowing the glyphs' own natural width).
Dash patterns and join styles are parsed but not honoured, matching
pdfdoc.py. Triangular caps (the mechanism real Draw files use for
arrowhead/pointer line ends) ARE honoured, the same way pdfdoc.py does:
SVG has no triangular `stroke-linecap` option either, so one is drawn
as an explicit, separately-filled triangle path at the subpath's own
start/end instead (see _draw_svg_triangular_caps). A Sprite object
embedded *within* a DrawFile, and any other undecoded object type,
still falls back to a placeholder for just that object. A picture that
isn't a valid DrawFile at all still falls back to the labelled
placeholder image below.
"""

from __future__ import annotations

import base64
import html as _html
import math
from typing import Optional

from riscos_impression.formats.drawfile import (
    CAP_TRIANGULAR,
    OPTIONS_TYPE,
    BoundingBox,
    DrawFile,
    DrawGroup,
    DrawJPEG,
    DrawPath,
    DrawPathOpCode,
    DrawSprite,
    DrawTagged,
    DrawText,
    colour_rgb,
)
from riscos_impression.formats.eps import EPSObject
from riscos_impression.formats.sprite import SpriteArea, wrap_single_sprite_as_area
from riscos_impression.formats.sprite_png import sprite_area_to_png
from riscos_impression.model.colours import MAXCV, Colour, ColourModel
from riscos_impression.model.dictionary import EmbeddedObjectType
from riscos_impression.model.styles import Style
from riscos_impression.output.base import Converter

try:
    # riscos_artworks is only installed via this project's own optional
    # "artworks" extra (see pyproject.toml) -- ArtWorks pictures fall
    # back to the usual labelled placeholder, exactly like any other
    # undecoded picture kind, when it isn't available.
    from riscos_artworks import ArtWorks
    from riscos_impression.formats.artworks_svg import ARTWORKS_UNIT_TO_USER_UNITS, artworks_svg_fragment
except ImportError:  # pragma: no cover - exercised by CI without the extra
    ArtWorks = None
    artworks_svg_fragment = None
    ARTWORKS_UNIT_TO_USER_UNITS = None

#: Millipoints per CSS point; see docs/impression-documents.xml's note
#: under "Frame object common layout" (the same confirmed unit pdfdoc.py
#: uses).
UNIT = 1000.0

#: Draw units (1/256 OS unit, itself 1/180 inch) to CSS points.
_DRAW_UNIT_TO_PT = 72.0 / (180.0 * 256.0)


def escape_html(text: str) -> str:
    return _html.escape(text, quote=False)


# ---------------------------------------------------------------------------
# DrawFile triangular caps (arrowheads) -- see pdfdoc.py's own
# equivalent for the full explanation; duplicated here rather than
# shared, matching this project's convention of independent output
# converters.
# ---------------------------------------------------------------------------


def _unit_vector(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0, 0.0
    return dx / length, dy / length


def _subpath_cap_directions(
    ops: list, to_svg
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """For each open subpath in *ops*, (start_point, start_outward_dir,
    end_point, end_outward_dir) in the final SVG point space; see
    pdfdoc.py's own _subpath_cap_directions for the full rationale."""
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
        p0 = to_svg(*verts[0])
        p1 = to_svg(*verts[1])
        pn = to_svg(*verts[-1])
        pn1 = to_svg(*verts[-2])
        start_dir = _unit_vector(p0[0] - p1[0], p0[1] - p1[1])
        end_dir = _unit_vector(pn[0] - pn1[0], pn[1] - pn1[1])
        results.append((p0, start_dir, pn, end_dir))
    return results


def _triangular_cap_polygon(
    point: tuple[float, float], direction: tuple[float, float], width_pt: float, length_pt: float
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    px, py = point
    dx, dy = direction
    nx, ny = -dy, dx
    half = width_pt / 2.0
    base_left = (px + nx * half, py + ny * half)
    base_right = (px - nx * half, py - ny * half)
    apex = (px + dx * length_pt, py + dy * length_pt)
    return base_left, apex, base_right


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    i = int(h * 6.0) % 6
    f = h * 6.0 - int(h * 6.0)
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


def colour_to_css(colour: Optional[Colour]) -> Optional[str]:
    """A CSS "#rrggbb" colour for *colour*, or None if *colour* is None
    (the caller should omit the CSS property entirely in that case,
    rather than guess at a default)."""
    if colour is None:
        return None
    if colour.model is ColourModel.CMYK:
        c, m, y, k = (v / MAXCV for v in colour.values)
        r, g, b = 1 - min(1.0, c + k), 1 - min(1.0, m + k), 1 - min(1.0, y + k)
    elif colour.model is ColourModel.RGB:
        r, g, b = (v / MAXCV for v in colour.values)
    else:
        h, s, v = colour.values
        # h carries no /255 scaling (see docs/impression-documents.xml,
        # "Colour channel encoding") because it isn't a byte-range
        # value like every other channel -- it's an angle, 0-360
        # degrees, packed into the same on-disk slot. See pdfdoc.py's
        # own _to_rgb for the real-document confirmation (268 degrees,
        # h/MAXCV == 268.0 exactly, matching the user's own colour
        # picker dialog) that /255 (the earlier, wrong assumption) sent
        # the resolved colour round the wheel more than once.
        r, g, b = _hsv_to_rgb((h / MAXCV) / 360.0, s / MAXCV, v / MAXCV)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


#: Impression's own "Border 4"/"Border 5" fill-band colours (see
#: pdfdoc.py's _SHADOW_COLOUR_RGB/_BORDER5_COLOUR_RGB for how these were
#: measured against a reference image), reused as fixed CSS colours
#: here since neither HTML converter has a way to draw a separate
#: filled band outside a div's own box -- see border_css_declarations.
_BORDER4_GREY_CSS = "#787878"
_BORDER5_GREY_CSS = "#e2e2e2"

#: Border 6/7's own shadow-band width, matching pdfdoc.py's own
#: _SHADOW_WIDTH_PT exactly -- see border_css_declarations's own
#: box-shadow approximation for these two styles.
_SHADOW_WIDTH_PT = 5669 / UNIT


def _border_css_for_style(style: int, border_css: str, border_width_pt: float) -> str:
    """A CSS `border-<edge>` value for one edge of one of Impression's
    ten "Border 1".."Border 10" UI styles (style is the 0-based stored
    byte -- see model.frames.Frame.has_border). Used per-edge by
    border_css_declarations whenever a frame's border0..3 don't all
    share one style (a whole rounded-rectangle outline, a shadow's own
    offset band, and a mitred picture-frame moulding all inherently
    need to know about more than one edge at once, so none of those
    can be expressed edge-by-edge at all -- border_css_declarations
    handles styles 5/6/9 specially when uniform across all four edges,
    or per shared corner for 9, falling back to this plain per-edge
    line otherwise). A lower-fidelity approximation than pdfdoc.py's
    own per-style rendering even there (see that converter's
    _draw_border_band/_draw_notched_shadow_edge/_draw_border_ring_line/
    _draw_rounded_border for the pixel measurements this is based on
    and the full set of styles) -- this converter has no way to draw a
    separate filled band outside a div's own box at all, so styles 3-4
    fall back to a plain, correspondingly-coloured line even when
    uniform; CSS's own `double` border style is used natively for 7-8
    rather than hand-building two lines (both approximated as meeting
    cleanly at every corner, matching pdfdoc.py's own current
    behaviour, since CSS's border corners always mitre this way
    natively)."""
    thin = border_width_pt
    med = thin * 2.0
    thick = thin * 7.0
    if style == 1:  # Border 2
        return f"{med:.1f}pt solid {border_css}"
    if style == 2:  # Border 3
        return f"{thick:.1f}pt solid {border_css}"
    if style == 3:  # Border 4: mid-grey (best-effort: plain line, no separate band)
        return f"{thick:.1f}pt solid {_BORDER4_GREY_CSS}"
    if style == 4:  # Border 5: light-grey (best-effort: plain line, no separate band)
        return f"{thick:.1f}pt solid {_BORDER5_GREY_CSS}"
    if style in (5, 6):  # Border 6/7: offset shadow (best-effort: plain thick line)
        return f"{thick:.1f}pt solid {border_css}"
    if style == 7:  # Border 8: thin + thicker line
        return f"{med * 2.0:.1f}pt double {border_css}"
    if style == 8:  # Border 9: two thicker lines, wider gap
        return f"{med * 3.0:.1f}pt double {border_css}"
    if style == 9:  # Border 10 mixed with other styles: no whole-outline rounding possible
        return f"{thick:.1f}pt solid {border_css}"
    return f"{thin:.1f}pt solid {border_css}"  # Border 1, or an unrecognised style byte


def border_css_declarations(frame, colours, border_width_pt: float) -> list[str]:
    """CSS declarations (`"border-top: ..."`, `"outline: ..."`,
    `"border-radius: ..."`, and similar) implementing *frame*'s own
    border0..3 -- shared by both HTML converters (html_paged.py and
    html_scrolling.py), unlike most of this project's own converter
    logic, since generating CSS text is presentation-mapping code like
    colour_to_css/style_css_properties above, not a page-geometry
    concern the two HTML formats need to solve differently.

    Each edge is independent (0xFF = absent; see
    docs/impression-documents.xml, "Frame object common layout" --
    border0=top, border1=left, border2=right, border3=bottom), so a
    uniform `border` shorthand is wrong whenever fewer than all four
    are set -- confirmed empirically against a real document (PCI_Spec,
    top+bottom only) whose footer frame otherwise grew two extra,
    entirely fictional side borders. Two whole-frame special cases,
    each needing to know about more than one edge at once so they
    can't be expressed by _border_css_for_style's own per-edge value:

    * All four edges Border 10 (style 9): drawn with `outline`, not
      `border` -- unlike `border`, an outline sits outside the div's
      own layout box without affecting it (no encroachment into the
      frame's own content area, matching pdfdoc.py's own
      non-encroaching offset), and `outline-offset` gives Border 10's
      own visible gap from the frame's boundary directly -- both a
      closer native match than `border`+`border-radius` (which
      encroaches and sits flush with no gap). When only *some* edges
      are Border 10 (or Border 10 mixed with other styles), `outline`
      can't be used (it has no per-edge variant), so each edge falls
      back to _border_css_for_style's plain line -- but any *corner*
      whose own two adjoining edges are BOTH Border 10 still gets its
      own `border-<corner>-radius`, so two Border-10 edges that
      actually meet still curve into each other even when a third edge
      is a different style or missing entirely (confirmed against
      TestDoc-Real2Border10.png's own "Border 10, no left" reference
      frame: the two corners where both adjoining edges are present
      round in full, only the two corners adjoining the missing edge
      don't).
    * All four edges Border 6 or Border 7 (style 5/6): approximated
      with a CSS box-shadow -- not pixel-matched to pdfdoc.py's own
      _draw_notched_shadow_edge (no mitred/notched geometry, no
      separate thin outline showing through a gap), but a much closer
      visual impression than a plain line for the common case of one
      style applied to a whole frame. A per-edge mix of styles (rare)
      still falls back to the plain-line approximation, since CSS
      box-shadow can't be edge-specific either."""
    if not frame.has_border:
        return []
    border_css = colour_to_css(frame.border_colour(colours)) or "#000000"
    thick = border_width_pt * 7.0
    edges = (
        (frame.border0, "top"),
        (frame.border1, "left"),
        (frame.border2, "right"),
        (frame.border3, "bottom"),
    )
    present = [(style, edge) for style, edge in edges if style != 0xFF]
    if not present:
        return []
    uniform_style = present[0][0] if len({s for s, _ in present}) == 1 else None

    if uniform_style == 9 and len(present) == 4:
        return [
            f"outline: {thick:.1f}pt solid {border_css}",
            f"outline-offset: {thick * 3.0:.1f}pt",
            f"border-radius: {thick * 1.8:.1f}pt",
        ]
    if uniform_style in (5, 6) and len(present) == 4:
        sw = _SHADOW_WIDTH_PT
        dx = sw if uniform_style == 5 else -sw
        return [
            f"border: {border_width_pt:.1f}pt solid {border_css}",
            f"box-shadow: {dx:.1f}pt {-sw:.1f}pt 0 0 {border_css}",
        ]

    styles = [f"border-{edge}: {_border_css_for_style(style, border_css, border_width_pt)}" for style, edge in present]
    style_by_edge = {edge: style for style, edge in present}
    radius = thick * 1.8
    for corner, (edge_a, edge_b) in (
        ("top-left", ("top", "left")),
        ("top-right", ("top", "right")),
        ("bottom-left", ("bottom", "left")),
        ("bottom-right", ("bottom", "right")),
    ):
        if style_by_edge.get(edge_a) == 9 and style_by_edge.get(edge_b) == 9:
            styles.append(f"border-{corner}-radius: {radius:.1f}pt")
    return styles


#: Substrings of a RISC OS font name that identify its family for CSS
#: purposes; matched case-insensitively, first match wins. Homerton
#: (and anything unrecognised) falls through to the sans-serif stack --
#: see choose_standard_font in pdfdoc.py for the same mapping used
#: there for the PDF converter's standard-14 fonts.
#: Single-quoted, not double -- these values are embedded inside a
#: double-quoted HTML/SVG style="..." attribute (see css_style_attr and
#: _drawfile_svg_text); a literal " here would terminate that attribute
#: early and corrupt everything after it. Confirmed against a real
#: document (PCI_Spec from the local examples/ corpus): every property
#: following a quoted font name in the same style attribute (bold,
#: italic, colour, and -- in SVG -- font-size) was silently lost,
#: since the browser stopped parsing the attribute at the embedded ".
_FAMILY_HINTS = [
    ("courier", "'Courier New', Courier, monospace"),
    ("corpus", "'Courier New', Courier, monospace"),
    ("system", "'Courier New', Courier, monospace"),
    ("mono", "'Courier New', Courier, monospace"),
    ("times", "Times, 'Times New Roman', serif"),
    ("trinity", "Times, 'Times New Roman', serif"),
    ("serif", "Times, 'Times New Roman', serif"),
]
_DEFAULT_FONT_STACK = "'Helvetica Neue', Helvetica, Arial, sans-serif"


def _font_family_css_for_name(font_style_name: Optional[str]) -> str:
    name = (font_style_name or "").lower()
    for hint, stack in _FAMILY_HINTS:
        if hint in name:
            return stack
    return _DEFAULT_FONT_STACK


def font_family_css(style: Style) -> str:
    return _font_family_css_for_name(style.font_style_name)


def _draw_colour_to_css(word: Optional[int]) -> Optional[str]:
    """A CSS "#rrggbb" colour for a raw Draw palette word (see
    formats/drawfile.py's colour_rgb), or None for "no colour"."""
    if word is None:
        return None
    r, g, b = colour_rgb(word)
    return f"#{r:02x}{g:02x}{b:02x}"


def style_css_properties(style: Style, colours) -> dict[str, str]:
    """The inline CSS properties for a resolved Style: font family/size/
    weight/style, underline/strikeout, and foreground/background colour
    (each only when the style actually carries it)."""
    props: dict[str, str] = {"font-family": font_family_css(style)}
    if style.font_size is not None:
        props["font-size"] = f"{style.font_size / 16:.2f}pt"
    if style.bold:
        props["font-weight"] = "bold"
    if style.italic:
        props["font-style"] = "italic"
    decorations = []
    if style.underline:
        decorations.append("underline")
    if style.strikeout:
        decorations.append("line-through")
    if decorations:
        props["text-decoration"] = " ".join(decorations)

    fg_css = colour_to_css(style.foreground_colour(colours)) if style.foreground_colour_word is not None else None
    if fg_css:
        props["color"] = fg_css
    bg_css = colour_to_css(style.background_colour(colours))
    if bg_css:
        props["background-color"] = bg_css
    return props


#: Used only when a style carries no font_size at all, matching
#: pdfdoc.py's own _DEFAULT_FONT_SIZE_16THS (10pt).
_DEFAULT_FONT_SIZE_16THS = 160

#: DDL/Style alignment codes -> CSS text-align keywords; 0 (left) needs
#: no explicit property at all, matching the browser's own default.
_ALIGNMENT_CSS = {1: "center", 2: "right", 3: "justify"}


def paragraph_line_height_pt(style: Style) -> float:
    """CSS line-height (in points) for a resolved paragraph Style --
    ported from pdfdoc.py's own _line_height_pt, kept independently
    here rather than imported (matching this project's convention of
    self-contained converters; see the module docstring). Includes the
    same fixed-value floor pdfdoc.py uses: a FIXED line spacing value
    smaller than the style's own natural 120% default is a stale
    snapshot from some earlier, smaller font size the style was last
    edited at (confirmed against a real document, Telegraph from the
    local moreexamples/ corpus, whose "Main Heading" style's own frozen
    leading was barely 70% of its current font size) and must not be
    trusted verbatim -- it can still widen spacing when genuinely
    larger than the natural default, just never shrink it into overlap."""
    size = (style.font_size or _DEFAULT_FONT_SIZE_16THS) / 16.0
    if style.line_spacing_raw is not None:
        if style.line_spacing_is_fixed:
            return max(abs(style.line_spacing) / UNIT, size * 1.2)
        percent = (style.line_spacing / 100.0) if style.line_spacing else 100
        return size * 1.2 * (percent / 100.0)
    return size * 1.2


def paragraph_css_properties(style: Style, max_width_pt: Optional[float] = None) -> dict[str, str]:
    """The block-level CSS properties for a resolved paragraph Style:
    left/right margin, first-line indent, alignment, space before/
    after, and line height -- the paragraph-level counterpart to
    style_css_properties' run-level font/colour properties, applied to
    a <p> element as a whole rather than to individual <span>s within
    it. Confirmed missing entirely from both HTML converters against a
    real document (PCI_Spec from the local examples/ corpus): its
    styles' left/right margins and first-line indents never appeared in
    the HTML output at all, since only style_css_properties (which
    never touched any of these fields) was ever applied.

    Uses whichever style the paragraph's own first Run/EmbedMark
    carries (see each converter's _render_paragraph, which mirrors
    pdfdoc.py's own para_style selection in _paragraph_tokens) --
    not necessarily the same style every individual run within the
    paragraph uses, but the one whose paragraph-level attributes
    (margins, alignment, spacing) actually apply to the whole block,
    matching how Impression's own paragraph formatting works.

    *max_width_pt*, when given, is the paragraph's own frame's real
    available (content) width -- only meaningful where that width is
    actually known, i.e. paged HTML output, whose frames are sized to
    match the original document exactly.

    right_indent_raw's own sign selects one of two entirely different
    placements for the column's right edge (see Style.right_indent_is_
    delta and docs/impression-documents.xml's "Paragraph ruler
    fields"): a POSITIVE raw value (right_indent_is_delta True; DDL
    kind 2, INDENTOFFSET in the conversion source) is an offset from
    the frame's own LEFT edge, not an inset from its right -- confirmed
    against a real document (PCI_Spec from the local examples/ corpus)
    and a real DDF export the user supplied, generated by Impression
    itself: its base style declares leftmargin 19.8pt, rightmargin
    510.2pt on a frame that's itself 510.2pt wide, which only makes
    sense read as an offset from the left (landing almost exactly at
    the frame's own right edge). This project originally treated every
    right_indent as an inset from the right (a non-positive raw value,
    DDL kind 0, which maps directly to CSS margin-right), which read
    against a wide frame's own width squeezed the column to almost
    nothing. Resolving the offset case to a CSS margin-right (itself an
    inset from the box's own right edge) needs the box's own real
    width, which scrolling HTML deliberately never tracks (see that
    converter's own module docstring) -- so it's left out there,
    rather than guess."""
    props: dict[str, str] = {}
    if style.left_indent:
        props["margin-left"] = f"{style.left_indent / UNIT:.2f}pt"
    if style.right_indent:
        right_indent_pt = style.right_indent / UNIT
        if style.right_indent_is_delta:
            if max_width_pt is not None:
                margin_right_pt = max_width_pt - right_indent_pt
                if margin_right_pt > 0:
                    props["margin-right"] = f"{margin_right_pt:.2f}pt"
        else:
            props["margin-right"] = f"{right_indent_pt:.2f}pt"
    if style.first_indent:
        props["text-indent"] = f"{style.first_indent / UNIT:.2f}pt"
    alignment_css = _ALIGNMENT_CSS.get(style.alignment)
    if alignment_css:
        props["text-align"] = alignment_css
    if style.space_before:
        props["margin-top"] = f"{style.space_before / UNIT:.2f}pt"
    if style.space_after:
        props["margin-bottom"] = f"{style.space_after / UNIT:.2f}pt"
    props["line-height"] = f"{paragraph_line_height_pt(style):.2f}pt"
    return props


def css_style_attr(props: dict[str, str]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in props.items())


def picture_placeholder_data_uri(label: str, width_pt: float, height_pt: float) -> str:
    """A minimal placeholder image (an outlined box with a diagonal
    cross and a text label, matching the PDF converter's own
    placeholder style) for an undecoded picture, as a self-contained
    data: URI -- no external image file or library needed."""
    w = max(1.0, width_pt)
    h = max(1.0, height_pt)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}" height="{h:.1f}" '
        f'viewBox="0 0 {w:.1f} {h:.1f}">'
        f'<rect x="0.5" y="0.5" width="{w - 1:.1f}" height="{h - 1:.1f}" '
        f'fill="none" stroke="#999999" stroke-width="1"/>'
        f'<line x1="0" y1="0" x2="{w:.1f}" y2="{h:.1f}" stroke="#999999" stroke-width="1"/>'
        f'<line x1="0" y1="{h:.1f}" x2="{w:.1f}" y2="0" stroke="#999999" stroke-width="1"/>'
        f'<text x="{w / 2:.1f}" y="{h / 2:.1f}" font-size="10" fill="#666666" '
        f'text-anchor="middle" dominant-baseline="middle">[{escape_html(label)}]</text>'
        f"</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


class HTML5Converter(Converter):
    """Shared base for the scrolling and paged-media HTML5 converters:
    picture rendering (dispatched by embedded type exactly like the PDF
    converter -- Sprite gets a labelled placeholder, since its decoder
    is a stub bounding-box reader with no pixel data to rasterise;
    ArtWorks is rendered as a nested `<svg>` via the optional
    riscos_artworks decoder and formats/artworks_svg.py -- see
    _artworks_svg -- falling back to the usual labelled placeholder
    when that extra isn't installed or the picture fails to decode/
    render; EPS gets a placeholder too, with a note that its raw
    content isn't embedded in HTML output at all, unlike the PDF
    converter's embedded-file attachment -- HTML has no equivalent
    mechanism)."""

    def _picture_html(self, pict, entry) -> str:
        width_pt = (pict.x1 - pict.x0) / UNIT
        height_pt = (pict.y1 - pict.y0) / UNIT
        with self.catch("picture", location=f"dictionary entry {entry.index}"):
            data = self.document.picture_bytes(entry)
            return self._picture_html_for_data(data, entry, pict, width_pt, height_pt)
        return self._placeholder_img("data", width_pt, height_pt)

    def _picture_html_for_data(self, data: bytes, entry, pict, width_pt: float, height_pt: float) -> str:
        kind = entry.embedded_object_type

        if kind is EmbeddedObjectType.EPS:
            eps = EPSObject.from_bytes(data)
            self.log.best_effort(
                "picture",
                f"EPS picture '{eps.name}' rendered as a placeholder box; its raw "
                "content is not embedded in HTML output (unlike the PDF converter's "
                "embedded-file attachment, HTML has no equivalent mechanism)",
            )
            return self._placeholder_img("EPS", width_pt, height_pt)

        if kind is EmbeddedObjectType.DRAW:
            draw = DrawFile.from_bytes(data)
            if draw is not None:
                return self._drawfile_svg(draw, pict, width_pt, height_pt)
            if SpriteArea.from_bytes(data) is None:
                self.log.error(
                    "picture", "picture classified as a drawable format but decoded as neither DrawFile nor Sprite"
                )
                return self._placeholder_img("Sprite", width_pt, height_pt)
            png = sprite_area_to_png(data)
            if png is not None:
                return self._image_data_uri_img("png", png, width_pt, height_pt)
            self.log.best_effort(
                "picture", "Sprite picture rendered as a placeholder box; the optional 'sprites' "
                "extra (riscos_sprites) is not installed, or the sprite failed to decode"
            )
            return self._placeholder_img("Sprite", width_pt, height_pt)

        if kind is EmbeddedObjectType.ARTWORKS:
            if artworks_svg_fragment is None:
                self.log.unsupported(
                    "picture", "ArtWorks picture rendered as a placeholder box; the optional "
                    "'artworks' extra (riscos_artworks) is not installed"
                )
                return self._placeholder_img("ArtWorks", width_pt, height_pt)
            try:
                artwork = ArtWorks.from_buffer(data)
            except Exception as e:
                # A real, current riscos_artworks decoder gap (not this
                # project's own bug) -- e.g. some real ArtWorks files
                # embed a SpriteRecord shape its decoder doesn't yet
                # parse -- so this is a known limitation, not an
                # unexpected failure.
                self.log.best_effort(
                    "picture", f"ArtWorks picture rendered as a placeholder box; failed to decode ({e})"
                )
                return self._placeholder_img("ArtWorks", width_pt, height_pt)
            return self._artworks_svg(artwork, pict, width_pt, height_pt)

        label = kind.value if kind is not None else "data"
        self.log.best_effort("picture", f"{label} picture rendered as a placeholder box; not decoded by this converter")
        return self._placeholder_img(label, width_pt, height_pt)

    def _placeholder_img(self, label: str, width_pt: float, height_pt: float) -> str:
        uri = picture_placeholder_data_uri(label, width_pt, height_pt)
        return (
            f'<img src="{uri}" alt="[{escape_html(label)} picture, not rendered]" '
            f'width="{width_pt:.1f}" height="{height_pt:.1f}" '
            f'style="width: {width_pt:.1f}pt; height: {height_pt:.1f}pt;">'
        )

    def _image_data_uri_img(self, image_format: str, image_bytes: bytes, width_pt: float, height_pt: float) -> str:
        """A decoded raster image (currently only real Sprite pixel
        data, via formats/sprite_png.py) embedded as a self-contained
        `<img>` data: URI, sized to the picture frame's own box."""
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return (
            f'<img src="data:image/{image_format};base64,{encoded}" alt="[picture]" '
            f'width="{width_pt:.1f}" height="{height_pt:.1f}" '
            f'style="width: {width_pt:.1f}pt; height: {height_pt:.1f}pt;">'
        )

    # -- DrawFile pictures -----------------------------------------------------

    @staticmethod
    def _drawfile_effective_bounds(draw: DrawFile) -> BoundingBox:
        """A duplicate, self-contained copy of pdfdoc.py's own method of
        the same name (matching this project's convention of
        independent converters, not shared code) -- see that copy's own
        docstring for the full rationale (a real document's own
        DrawText object extending beyond the file header's own declared
        bounds, and DrawSprite/DrawUnknown objects carrying meaningless
        dummy bounds that must NOT be unioned in)."""
        x0, y0, x1, y1 = draw.bounds.x0, draw.bounds.y0, draw.bounds.x1, draw.bounds.y1

        def visit(obj) -> None:
            nonlocal x0, y0, x1, y1
            if isinstance(obj, (DrawPath, DrawText)):
                b = obj.bounds
                x0, y0 = min(x0, b.x0), min(y0, b.y0)
                x1, y1 = max(x1, b.x1), max(y1, b.y1)
            elif isinstance(obj, DrawGroup):
                for child in obj.objects:
                    visit(child)
            elif isinstance(obj, DrawTagged) and obj.inner is not None:
                visit(obj.inner)

        for obj in draw.objects:
            visit(obj)
        return BoundingBox(x0, y0, x1, y1)

    def _drawfile_svg(self, draw: DrawFile, pict, width_pt: float, height_pt: float) -> str:
        """A decoded DrawFile's objects as an inline SVG fragment, using
        the picture frame's own declared display scale (pict.xscale/
        yscale) to size the DrawFile's own native-size content, and
        positioned within the picture's own box [width_pt, height_pt]
        exactly the way pdfdoc.py's own _draw_drawfile_picture is --
        see that method's own docstring for the full derivation and
        calibration history (xshift/yshift anchor the drawfile's own
        native (0, 0) origin at the frame's own left/bottom edge, pict
        .angle rotates about that same origin first, and an implausible
        -- under 5% overlap of the smaller of the frame/content -- shift
        falls back to centred, shrunk to fit first if the content
        doesn't fit natively). This project's own convention is
        duplicated, self-contained logic per converter (see
        _drawfile_effective_bounds above) rather than a shared
        implementation, so this is a deliberate copy of that formula,
        not a call into it.

        The frame's own box is used as [0, 0]-[width_pt, height_pt] in
        Y-up pt space here (identical to pdfdoc.py's own page-absolute
        [x0, y0]-[x1, y1], just frame-relative rather than
        page-relative, since this fragment is placed within its own
        `<svg>` sized to exactly that box) -- SVG's Y-down convention
        only needs flipping once, right at the end, in *to_svg* itself
        (`height_pt - py`), rather than threading a flipped convention
        through the whole derivation the way the version this
        supersedes did (which additionally only ever centred, never
        applying xshift/yshift at all). *apply_shift* has no
        pdfdoc.py-style False case here: this module has no equivalent
        of pdfdoc.py's own _draw_embedded_picture (a *recomputed* inline
        box that xshift/yshift isn't declared relative to) -- every
        caller of this method already uses the picture's own real,
        stored frame box (_picture_html's width_pt/height_pt), matching
        pdfdoc.py's page-positioned case unconditionally.

        The SVG viewport clips anything the shifted/centred content
        overflows, the same as pdfdoc.py's own explicit clip rectangle.
        See the module docstring and pdfdoc.py's own DrawFile section
        for what else is approximated versus a genuine placeholder."""
        bounds = self._drawfile_effective_bounds(draw)
        display_scale_x = (0x10000 / pict.xscale) if pict.xscale else 1.0
        display_scale_y = (0x10000 / pict.yscale) if pict.yscale else 1.0
        sx = _DRAW_UNIT_TO_PT * display_scale_x
        sy = _DRAW_UNIT_TO_PT * display_scale_y
        displayed_w = bounds.width * sx
        displayed_h = bounds.height * sy

        x0, y0, x1, y1 = 0.0, 0.0, width_pt, height_pt
        x_anchor = x0 + pict.hinset / UNIT
        shifted_x = x_anchor - pict.xshift / UNIT + bounds.x0 * sx
        shifted_y = y0 - pict.yshift / UNIT + bounds.y0 * sy
        overlap_w = max(0.0, min(x1, shifted_x + displayed_w) - max(x0, shifted_x))
        overlap_h = max(0.0, min(y1, shifted_y + displayed_h) - max(y0, shifted_y))
        frame_area = (x1 - x0) * (y1 - y0)
        content_area = displayed_w * displayed_h
        reference_area = min(frame_area, content_area)
        shifted = None
        if reference_area <= 0 or (overlap_w * overlap_h) / reference_area >= 0.05:
            shifted = (shifted_x, shifted_y)

        if shifted is not None:
            origin_x, origin_y = shifted
        else:
            frame_w, frame_h = x1 - x0, y1 - y0
            if displayed_w > frame_w or displayed_h > frame_h:
                fit_scale = min(
                    frame_w / displayed_w if displayed_w else 1.0,
                    frame_h / displayed_h if displayed_h else 1.0,
                )
                sx *= fit_scale
                sy *= fit_scale
                displayed_w *= fit_scale
                displayed_h *= fit_scale
            origin_x = x0 + max(0.0, (frame_w - displayed_w) / 2.0)
            origin_y = y0 + max(0.0, (frame_h - displayed_h) / 2.0)

        # See pdfdoc.py's own _draw_drawfile_picture docstring for the
        # full derivation: a standard mathematical (counter-clockwise)
        # rotation about the drawfile's own native (0, 0) origin,
        # applied before the rest of this transform.
        angle_rad = math.radians(pict.angle / 65536.0) if pict.angle else 0.0
        cos_a, sin_a = (math.cos(angle_rad), math.sin(angle_rad)) if angle_rad else (1.0, 0.0)

        def to_svg(dx: int, dy: int) -> tuple[float, float]:
            # SVG is Y-down from the top-left; Draw (and this method's
            # own Y-up working space above) is Y-up from the bottom-left
            # -- flipped here, once, against the frame's own height, not
            # threaded through the derivation above (unlike pdfdoc.py,
            # which shares PDF's own Y-up convention throughout and
            # never needs this).
            if angle_rad:
                dx, dy = dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a
            px = origin_x + (dx - bounds.x0) * sx
            py = origin_y + (dy - bounds.y0) * sy
            return px, height_pt - py

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_pt:.1f}pt" '
            f'height="{height_pt:.1f}pt" viewBox="0 0 {width_pt:.1f} {height_pt:.1f}" '
            f'style="overflow: hidden;">'
        ]
        notes: list[str] = []
        for obj in draw.objects:
            self._drawfile_svg_object(obj, draw.fonts, to_svg, (sx, sy), parts, notes)
        parts.append("</svg>")
        for note in dict.fromkeys(notes):  # de-duplicate, keep first-seen order
            self.log.best_effort("picture", note)
        return "".join(parts)

    def _drawfile_svg_object(self, obj, fonts: dict, to_svg, scale, parts: list[str], notes: list[str]) -> None:
        if isinstance(obj, DrawPath):
            self._drawfile_svg_path(obj, to_svg, scale, parts)
        elif isinstance(obj, DrawText):
            self._drawfile_svg_text(obj, fonts, to_svg, scale, parts)
        elif isinstance(obj, DrawGroup):
            for child in obj.objects:
                self._drawfile_svg_object(child, fonts, to_svg, scale, parts, notes)
        elif isinstance(obj, DrawTagged):
            if obj.inner is not None:
                self._drawfile_svg_object(obj.inner, fonts, to_svg, scale, parts, notes)
        elif isinstance(obj, DrawJPEG):
            self._drawfile_svg_jpeg(obj, to_svg, parts, notes)
        elif isinstance(obj, DrawSprite):
            self._drawfile_svg_sprite(obj, to_svg, parts, notes)
        elif obj.type != OPTIONS_TYPE:  # DrawUnknown -- text area, transformed text/sprite, or unrecognised
            notes.append(
                "one or more DrawFile object types (e.g. text area, transformed "
                "text/sprite) within a picture were not decoded and are omitted"
            )
        # else: an Options object -- no rendering component of its own, so
        # nothing was actually omitted; not worth logging (see OPTIONS_TYPE).

    def _drawfile_svg_jpeg(self, jpeg: DrawJPEG, to_svg, parts: list[str], notes: list[str]) -> None:
        """A JPEG's own bytes are already a complete, standalone JPEG
        file (see formats/drawfile.py's DrawJPEG) -- embedded directly
        as a base64 data: URI, no re-encoding needed. Positioned/sized
        from the object's own bounding box the same way a DrawSprite
        placeholder is; the object's own transform matrix (a/b/c/d/e/f)
        isn't applied beyond that -- every real file seen so far has an
        identity a/d (1.0) and zero b/c (no rotation/shear), matching
        the bounding box exactly, so this is only a simplification for
        the (currently unobserved) rotated/sheared case, not a gap in
        the common one."""
        px0, py0 = to_svg(jpeg.bounds.x0, jpeg.bounds.y0)
        px1, py1 = to_svg(jpeg.bounds.x1, jpeg.bounds.y1)
        rx0, rx1 = sorted((px0, px1))
        ry0, ry1 = sorted((py0, py1))
        encoded = base64.b64encode(jpeg.data).decode("ascii")
        parts.append(
            f'<image x="{rx0:.2f}" y="{ry0:.2f}" width="{rx1 - rx0:.2f}" height="{ry1 - ry0:.2f}" '
            f'preserveAspectRatio="none" href="data:image/jpeg;base64,{encoded}"/>'
        )
        _a, b, c, _d, _e, _f = jpeg.matrix
        if b or c:
            notes.append(
                "a JPEG image with a rotated/sheared transform is rendered axis-aligned "
                "to its own bounding box; rotation/shear is not reproduced"
            )

    def _drawfile_svg_sprite(self, sprite: DrawSprite, to_svg, parts: list[str], notes: list[str]) -> None:
        """A Sprite object's own body is a single native sprite record
        with no area wrapper of its own (see DrawSprite's own
        docstring) -- wrapped via wrap_single_sprite_as_area before
        handing it to the same optional riscos_sprites conversion a
        top-level Sprite picture uses. Falls back to the original
        placeholder box (with the same label/note) when that isn't
        installed or the sprite fails to decode."""
        px0, py0 = to_svg(sprite.bounds.x0, sprite.bounds.y0)
        px1, py1 = to_svg(sprite.bounds.x1, sprite.bounds.y1)
        rx0, rx1 = sorted((px0, px1))
        ry0, ry1 = sorted((py0, py1))
        png = sprite_area_to_png(wrap_single_sprite_as_area(sprite.data)) if sprite.data else None
        if png is not None:
            encoded = base64.b64encode(png).decode("ascii")
            parts.append(
                f'<image x="{rx0:.2f}" y="{ry0:.2f}" width="{rx1 - rx0:.2f}" height="{ry1 - ry0:.2f}" '
                f'preserveAspectRatio="none" href="data:image/png;base64,{encoded}"/>'
            )
            return
        parts.append(
            f'<rect x="{rx0:.1f}" y="{ry0:.1f}" width="{rx1 - rx0:.1f}" height="{ry1 - ry0:.1f}" '
            f'fill="none" stroke="#999999" stroke-width="1"/>'
            f'<text x="{(rx0 + rx1) / 2:.1f}" y="{(ry0 + ry1) / 2:.1f}" font-size="9" fill="#666666" '
            f'text-anchor="middle" dominant-baseline="middle">[Sprite]</text>'
        )
        notes.append(
            "a Sprite object embedded within a DrawFile picture is drawn as a "
            "placeholder box; the optional 'sprites' extra (riscos_sprites) is not "
            "installed, or the sprite failed to decode"
        )

    def _drawfile_svg_path(self, path: DrawPath, to_svg, scale, parts: list[str]) -> None:
        has_fill = path.fill_colour is not None
        has_stroke = path.stroke_colour is not None
        if (not has_fill and not has_stroke) or not path.ops:
            return

        d_parts = []
        for op in path.ops:
            if op.code in (DrawPathOpCode.MOVE, DrawPathOpCode.MOVE_INTERNAL, DrawPathOpCode.GAP):
                x, y = to_svg(op.x, op.y)
                d_parts.append(f"M {x:.2f} {y:.2f}")
            elif op.code is DrawPathOpCode.LINE:
                x, y = to_svg(op.x, op.y)
                d_parts.append(f"L {x:.2f} {y:.2f}")
            elif op.code is DrawPathOpCode.CURVE:
                cx1, cy1 = to_svg(op.cx1, op.cy1)
                cx2, cy2 = to_svg(op.cx2, op.cy2)
                ex, ey = to_svg(op.x, op.y)
                d_parts.append(f"C {cx1:.2f} {cy1:.2f} {cx2:.2f} {cy2:.2f} {ex:.2f} {ey:.2f}")
            elif op.code is DrawPathOpCode.CLOSE_LINE:
                d_parts.append("Z")
            # CLOSE_GAP: no direct SVG equivalent needed -- the next M starts a fresh subpath.
        if not d_parts:
            return

        fill = _draw_colour_to_css(path.fill_colour) if has_fill else "none"
        stroke = _draw_colour_to_css(path.stroke_colour) if has_stroke else "none"
        attrs = [f'd="{" ".join(d_parts)}"', f'fill="{fill}"', f'stroke="{stroke}"']
        width_pt = 0.0
        if has_stroke:
            # scale[i] is already "target points per source Draw unit"
            # (see pdfdoc.py's own _draw_drawfile_path for the same
            # calculation and why no separate _DRAW_UNIT_TO_PT factor
            # belongs here).
            line_scale = (abs(scale[0]) + abs(scale[1])) / 2.0
            width_pt = path.line_width * line_scale if path.line_width else 0.3
            attrs.append(f'stroke-width="{max(0.1, width_pt):.2f}"')
            if path.dashed and path.dash_elements:
                # line_scale converts Draw units -> pt the same way
                # width_pt above does; SVG's own stroke-dasharray takes
                # a plain list of on/off lengths in the current
                # coordinate system, so no odd-element-count sense-
                # inversion handling is needed here (SVG already
                # repeats/alternates the array itself the same way).
                dasharray = " ".join(f"{e * line_scale:.2f}" for e in path.dash_elements)
                attrs.append(f'stroke-dasharray="{dasharray}"')
                if path.dash_offset:
                    attrs.append(f'stroke-dashoffset="{path.dash_offset * line_scale:.2f}"')
        if has_fill and path.even_odd:
            attrs.append('fill-rule="evenodd"')
        parts.append(f"<path {' '.join(attrs)}/>")

        if has_stroke and (path.start_cap == CAP_TRIANGULAR or path.end_cap == CAP_TRIANGULAR):
            self._draw_svg_triangular_caps(path, to_svg, width_pt, parts)

    def _draw_svg_triangular_caps(self, path: DrawPath, to_svg, width_pt: float, parts: list[str]) -> None:
        """As pdfdoc.py's own _draw_triangular_caps: PDF has no
        triangular line-cap style, and neither does SVG (`stroke-
        linecap` is only butt/round/square), so a triangular cap is
        drawn here the same way -- as an explicit filled triangle at
        the subpath's own start/end, matching the real RISC OS
        DrawFile module's own Draw_Stroke-based rendering."""
        fill = _draw_colour_to_css(path.stroke_colour)
        for start_pt, start_dir, end_pt, end_dir in _subpath_cap_directions(path.ops, to_svg):
            if path.start_cap == CAP_TRIANGULAR:
                self._append_svg_triangular_cap(start_pt, start_dir, path, width_pt, fill, parts)
            if path.end_cap == CAP_TRIANGULAR:
                self._append_svg_triangular_cap(end_pt, end_dir, path, width_pt, fill, parts)

    def _append_svg_triangular_cap(
        self,
        point: tuple[float, float],
        direction: tuple[float, float],
        path: DrawPath,
        width_pt: float,
        fill: Optional[str],
        parts: list[str],
    ) -> None:
        if direction == (0.0, 0.0):
            return
        cap_width_pt = (path.triangle_cap_width / 16.0) * width_pt
        cap_length_pt = (path.triangle_cap_length / 16.0) * width_pt
        if cap_width_pt <= 0 or cap_length_pt <= 0:
            return
        (x0, y0), (x1, y1), (x2, y2) = _triangular_cap_polygon(point, direction, cap_width_pt, cap_length_pt)
        parts.append(
            f'<path d="M {x0:.2f} {y0:.2f} L {x1:.2f} {y1:.2f} L {x2:.2f} {y2:.2f} Z" '
            f'fill="{fill}" stroke="none"/>'
        )

    def _drawfile_svg_text(self, text: DrawText, fonts: dict, to_svg, scale, parts: list[str]) -> None:
        if not text.text.strip() or text.size_y <= 0:
            return
        _sx, sy = scale
        # Unlike pdfdoc.py's Tz-based approach, this ignores any x/y
        # font-size skew the DrawFile itself declares (a rare case, and
        # SVG has no equally direct equivalent without first knowing
        # the glyphs' own natural width) -- a deliberate simplification.
        #
        # text.size_y is already in points (1/640 point); dividing by
        # _DRAW_UNIT_TO_PT turns sy (points per Draw unit) into the
        # dimensionless magnification the picture is actually being
        # drawn at -- see pdfdoc.py's own _draw_drawfile_text for the
        # real-document bug this fixes (font size ~100x too small).
        size_pt = (text.size_y / 640.0) * (abs(sy) / _DRAW_UNIT_TO_PT)
        if size_pt <= 0.5:
            return
        x, y = to_svg(text.baseline_x, text.baseline_y)
        font_name = fonts.get(text.font_number)
        name_lower = (font_name or "").lower()
        style_bits = [f"font-family:{_font_family_css_for_name(font_name)}", f"font-size:{size_pt:.2f}pt"]
        if "bold" in name_lower:
            style_bits.append("font-weight:bold")
        if "italic" in name_lower or "oblique" in name_lower:
            style_bits.append("font-style:italic")
        style_bits.append(f"fill:{_draw_colour_to_css(text.colour) or '#000000'}")
        parts.append(f'<text x="{x:.2f}" y="{y:.2f}" style="{"; ".join(style_bits)}">{escape_html(text.text)}</text>')

    # -- ArtWorks pictures -------------------------------------------------

    def _artworks_svg(self, artwork: "ArtWorks", pict, width_pt: float, height_pt: float) -> str:
        """Embed *artwork*'s own content (via formats/artworks_svg.py's
        artworks_svg_fragment(), the same renderer output/extract.py
        uses), using the picture frame's own xshift/yshift/xscale/
        yscale placement -- the same formula _drawfile_svg uses (see
        that method's own docstring for the full derivation and
        calibration history), adapted for ArtWorks' own native units
        (ARTWORKS_UNIT_TO_USER_UNITS in place of _DRAW_UNIT_TO_PT) and
        a viewBox-derived bounding box in place of DrawFile's own
        decoded BoundingBox. This superseded an earlier version that
        only ever centred the content (via the nested SVG's own
        preserveAspectRatio="xMidYMid meet"), never applying
        xshift/yshift at all -- confirmed wrong by the user against a
        real document, where a picture reusing the same ArtWorks
        content at two different placements (once shifted/scaled,
        once not) rendered identically instead of showing its own
        distinct placement.

        Unlike _drawfile_svg's own to_svg, which rotates each point
        individually before scaling, artworks_svg_fragment()'s own
        `inner` is opaque pre-rendered markup (with its own internal
        `scale(1,-1)` Y-flip already baked in) -- there's no per-point
        hook to rotate through, so pict.angle isn't applied here yet;
        a non-zero angle is logged once rather than silently ignored,
        matching this project's own convention for a known, deferred
        gap. The translate+scale composed below accounts for that
        pre-existing internal flip (translate_y includes native max_y,
        not min_y, precisely to compensate for it) -- see the two
        inline comments below for the derivation."""
        with self.catch("picture", location="ArtWorks rendering"):
            def sprite_to_png(sprite_data: bytes) -> Optional[bytes]:
                return sprite_area_to_png(wrap_single_sprite_as_area(sprite_data))
            viewbox, _native_width_pt, _native_height_pt, inner = artworks_svg_fragment(
                artwork, sprite_to_png)
            min_x, neg_max_y, width, height = (float(v) for v in viewbox.split())
            min_y = -neg_max_y - height

            display_scale_x = (0x10000 / pict.xscale) if pict.xscale else 1.0
            display_scale_y = (0x10000 / pict.yscale) if pict.yscale else 1.0
            sx = ARTWORKS_UNIT_TO_USER_UNITS * display_scale_x
            sy = ARTWORKS_UNIT_TO_USER_UNITS * display_scale_y
            displayed_w = width * sx
            displayed_h = height * sy

            x0, y0, x1, y1 = 0.0, 0.0, width_pt, height_pt
            x_anchor = x0 + pict.hinset / UNIT
            shifted_x = x_anchor - pict.xshift / UNIT + min_x * sx
            shifted_y = y0 - pict.yshift / UNIT + min_y * sy
            overlap_w = max(0.0, min(x1, shifted_x + displayed_w) - max(x0, shifted_x))
            overlap_h = max(0.0, min(y1, shifted_y + displayed_h) - max(y0, shifted_y))
            frame_area = (x1 - x0) * (y1 - y0)
            content_area = displayed_w * displayed_h
            reference_area = min(frame_area, content_area)
            shifted = None
            if reference_area <= 0 or (overlap_w * overlap_h) / reference_area >= 0.05:
                shifted = (shifted_x, shifted_y)

            if shifted is not None:
                origin_x, origin_y = shifted
            else:
                frame_w, frame_h = x1 - x0, y1 - y0
                if displayed_w > frame_w or displayed_h > frame_h:
                    fit_scale = min(
                        frame_w / displayed_w if displayed_w else 1.0,
                        frame_h / displayed_h if displayed_h else 1.0,
                    )
                    sx *= fit_scale
                    sy *= fit_scale
                    displayed_w *= fit_scale
                    displayed_h *= fit_scale
                origin_x = x0 + max(0.0, (frame_w - displayed_w) / 2.0)
                origin_y = y0 + max(0.0, (frame_h - displayed_h) / 2.0)

            if pict.angle:
                self.log.best_effort(
                    "picture", "an ArtWorks picture's own rotation angle is not applied in HTML output"
                )

            # origin_x/origin_y are in the same Y-up (frame-bottom-
            # relative) working space _drawfile_svg's own version uses;
            # top_y converts to this fragment's own Y-down (frame-top-
            # relative) space. translate_y then additionally accounts
            # for inner's own internal scale(1,-1): that flip already
            # negates the native Y coordinate before this transform
            # ever sees it, so the translation must anchor against the
            # native max_y (== min_y + height), not min_y, to land the
            # content's own top edge at top_y -- see the module's own
            # sibling formula in pdfdoc.py's _draw_artworks_picture for
            # the equivalent, flip-free (PDF is Y-up throughout) case.
            top_y = height_pt - origin_y - displayed_h
            translate_x = origin_x - min_x * sx
            translate_y = top_y + (min_y + height) * sy
            return (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_pt:.2f}pt" '
                f'height="{height_pt:.2f}pt" viewBox="0 0 {width_pt:.2f} {height_pt:.2f}" '
                f'style="overflow: hidden;">'
                f'<g transform="translate({translate_x:.4f},{translate_y:.4f}) '
                f'scale({sx:.6f},{sy:.6f})">{inner}</g></svg>'
            )
        return self._placeholder_img("ArtWorks", width_pt, height_pt)
