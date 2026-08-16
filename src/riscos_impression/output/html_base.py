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
own declared display scale (pict.xscale/yscale) and centred within the
picture's own width/height -- NOT stretched to fill it (see
_drawfile_svg's own docstring). This mirrors pdfdoc.py's own DrawFile
rendering closely, with two differences: SVG's
Y-down coordinate convention needs an explicit flip (PDF's own
convention already matches Draw's Y-up one directly), and text with a
non-square x/y font-size ratio is rendered at its plain y-based size
rather than reproduced (pdfdoc.py can do this cheaply via PDF's `Tz`
horizontal-scaling operator; SVG has no equally direct equivalent
without first knowing the glyphs' own natural width). Dash patterns and
join styles are parsed but not honoured, matching pdfdoc.py. Triangular
caps (the mechanism real Draw files use for arrowhead/pointer line
ends) ARE honoured, the same way pdfdoc.py does: SVG has no triangular
`stroke-linecap` option either, so one is drawn as an explicit,
separately-filled triangle path at the subpath's own start/end instead
(see _draw_svg_triangular_caps). A Sprite object embedded *within* a
DrawFile, and any other undecoded object type, still falls back to a
placeholder for just that object. A picture that isn't a valid
DrawFile at all still falls back to the labelled placeholder image
below.
"""

from __future__ import annotations

import base64
import html as _html
import math
from typing import Optional

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
from riscos_impression.model.colours import MAXCV, Colour, ColourModel
from riscos_impression.model.dictionary import EmbeddedObjectType
from riscos_impression.model.styles import Style
from riscos_impression.output.base import Converter

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
        # "Colour channel encoding"); normalise it back to a 0..1 fraction.
        r, g, b = _hsv_to_rgb((h / MAXCV) / 255.0, s / MAXCV, v / MAXCV)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


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

#: Below this usable width (in points), a right_indent is treated as
#: implausible for its own frame rather than trusted -- matches
#: pdfdoc.py's own _MIN_USABLE_WIDTH threshold and reasoning.
_MIN_USABLE_WIDTH_PT = 10.0


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
    actually known and CSS-relevant, i.e. paged HTML output, whose
    frames are sized to match the original document exactly. right_indent
    is dropped whenever it would leave less than _MIN_USABLE_WIDTH_PT of
    that box (after left_indent too), rather than trusted verbatim --
    mirrors pdfdoc.py's own oversized-right-indent fallback, confirmed
    against the same real document (PCI_Spec): a title-block style's
    right_indent was authored for a much wider frame than it's actually
    used in, and applying it verbatim there would squeeze that frame's
    own text into an unreadably narrow (here, potentially negative)
    column. Left at the default None (scrolling HTML has no frame width
    of its own to check against at all -- see that converter's module
    docstring), right_indent is left out entirely instead, rather than
    risk an oversized page-layout-specific margin landing on a reflowed,
    viewport-width column it was never designed for."""
    props: dict[str, str] = {}
    if style.left_indent:
        props["margin-left"] = f"{style.left_indent / UNIT:.2f}pt"
    if style.right_indent and max_width_pt is not None:
        left_indent_pt = (style.left_indent / UNIT) if style.left_indent else 0.0
        right_indent_pt = style.right_indent / UNIT
        if max_width_pt - left_indent_pt - right_indent_pt >= _MIN_USABLE_WIDTH_PT:
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
    converter -- DrawFile/Sprite get a labelled placeholder, since both
    decoders are stub bounding-box readers with no pixel/vector data to
    rasterise; ArtWorks is a full stub and always renders as a
    placeholder; EPS gets a placeholder too, with a note that its raw
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
            else:
                self.log.best_effort(
                    "picture", "Sprite picture rendered as a placeholder box; pixel data is not decoded by this converter"
                )
            return self._placeholder_img("Sprite", width_pt, height_pt)

        if kind is EmbeddedObjectType.ARTWORKS:
            self.log.unsupported(
                "picture", "ArtWorks picture rendered as a placeholder box; this format is not decoded at all by this converter"
            )
            return self._placeholder_img("ArtWorks", width_pt, height_pt)

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

    # -- DrawFile pictures -----------------------------------------------------

    def _drawfile_svg(self, draw: DrawFile, pict, width_pt: float, height_pt: float) -> str:
        """A decoded DrawFile's objects as an inline SVG fragment, using
        the picture frame's own declared display scale (pict.xscale/
        yscale) to size the DrawFile's own native-size content, then
        centring it within the picture's own box [width_pt, height_pt]
        -- NOT stretched to fill it. Mirrors pdfdoc.py's own
        _draw_drawfile_picture exactly (including its xscale/yscale
        sign convention and its reasoning for centring rather than
        applying xshift/yshift; see that method's own docstring for the
        full explanation). A real document (PCI_Spec from the local
        examples/ corpus) showed DrawFile pictures at visibly, sometimes
        drastically, wrong size, with their own text badly misplaced --
        the same stretch-to-fit failure mode already found and fixed
        for the PDF converter, just never carried across to this one.
        The SVG viewport clips anything the centred content overflows,
        the same as pdfdoc.py's own explicit clip rectangle. See the
        module docstring and pdfdoc.py's own DrawFile section for what
        else is approximated versus a genuine placeholder."""
        bounds = draw.bounds
        display_scale_x = (0x10000 / pict.xscale) if pict.xscale else 1.0
        display_scale_y = (0x10000 / pict.yscale) if pict.yscale else 1.0
        sx = _DRAW_UNIT_TO_PT * display_scale_x
        sy = _DRAW_UNIT_TO_PT * display_scale_y
        displayed_w = bounds.width * sx
        displayed_h = bounds.height * sy
        origin_x = max(0.0, (width_pt - displayed_w) / 2.0)
        origin_y = max(0.0, (height_pt - displayed_h) / 2.0)

        def to_svg(dx: int, dy: int) -> tuple[float, float]:
            # SVG is Y-down from the top-left; Draw is Y-up from the
            # bottom-left, so the Y axis needs flipping (unlike
            # pdfdoc.py, which shares PDF's own Y-up convention) --
            # measured from the top of the CENTRED content, not the
            # picture's own top edge.
            return origin_x + (dx - bounds.x0) * sx, origin_y + (bounds.y1 - dy) * sy

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_pt:.1f}pt" '
            f'height="{height_pt:.1f}pt" viewBox="0 0 {width_pt:.1f} {height_pt:.1f}" '
            f'style="overflow: hidden;">'
        ]
        notes: list[str] = []
        if pict.angle:
            notes.append("a DrawFile picture's own rotation is not applied; it is drawn unrotated")
        for obj in draw.objects:
            self._drawfile_svg_object(obj, draw.fonts, to_svg, (sx, sy), parts, notes)
        parts.append("</svg>")
        for note in dict.fromkeys(notes):  # de-duplicate, keep first-seen order
            self.log.best_effort("picture", note)
        return "".join(parts)

    def _drawfile_svg_object(self, obj, fonts: dict, to_svg, scale, parts: list[str], notes: list[str]) -> None:
        if isinstance(obj, DrawPath):
            self._drawfile_svg_path(obj, to_svg, scale, parts)
            if obj.dashed:
                notes.append("dashed DrawFile path lines are rendered solid; dash patterns are not reproduced")
        elif isinstance(obj, DrawText):
            self._drawfile_svg_text(obj, fonts, to_svg, scale, parts)
        elif isinstance(obj, DrawGroup):
            for child in obj.objects:
                self._drawfile_svg_object(child, fonts, to_svg, scale, parts, notes)
        elif isinstance(obj, DrawTagged):
            if obj.inner is not None:
                self._drawfile_svg_object(obj.inner, fonts, to_svg, scale, parts, notes)
        elif isinstance(obj, DrawSprite):
            px0, py0 = to_svg(obj.bounds.x0, obj.bounds.y0)
            px1, py1 = to_svg(obj.bounds.x1, obj.bounds.y1)
            rx0, rx1 = sorted((px0, px1))
            ry0, ry1 = sorted((py0, py1))
            parts.append(
                f'<rect x="{rx0:.1f}" y="{ry0:.1f}" width="{rx1 - rx0:.1f}" height="{ry1 - ry0:.1f}" '
                f'fill="none" stroke="#999999" stroke-width="1"/>'
                f'<text x="{(rx0 + rx1) / 2:.1f}" y="{(ry0 + ry1) / 2:.1f}" font-size="9" fill="#666666" '
                f'text-anchor="middle" dominant-baseline="middle">[Sprite]</text>'
            )
            notes.append(
                "a Sprite object embedded within a DrawFile picture is drawn as a "
                "placeholder box; pixel data is not decoded"
            )
        else:  # DrawUnknown -- text area, options, transformed text/sprite, or unrecognised
            notes.append(
                "one or more DrawFile object types (e.g. text area, options, transformed "
                "text/sprite) within a picture were not decoded and are omitted"
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
