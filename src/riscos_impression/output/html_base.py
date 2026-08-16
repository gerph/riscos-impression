"""Shared HTML5 output helpers: colour/style -> CSS mapping, DrawFile
picture rendering, and placeholder rendering, used by both the
scrolling and paged-media HTML5 converters.

Unlike the PDF converter, HTML output does no line-wrapping or text
layout of its own at all -- a browser's own rendering engine handles
that natively from the CSS this module produces, so there is no
equivalent of pdfdoc.py's approximate-metrics wrapping concern here.

DrawFile pictures are rendered as an inline SVG fragment (paths --
fill/stroke colour, width, winding rule -- and single-line text, via
formats/drawfile.py's object decoder), mapping the DrawFile's own
bounding box on to the picture's own width/height. This mirrors
pdfdoc.py's own DrawFile rendering closely, with two differences: SVG's
Y-down coordinate convention needs an explicit flip (PDF's own
convention already matches Draw's Y-up one directly), and text with a
non-square x/y font-size ratio is rendered at its plain y-based size
rather than reproduced (pdfdoc.py can do this cheaply via PDF's `Tz`
horizontal-scaling operator; SVG has no equally direct equivalent
without first knowing the glyphs' own natural width). Dash patterns and
precise cap/join styles are parsed but not honoured, matching
pdfdoc.py; a Sprite object embedded *within* a DrawFile, and any other
undecoded object type, still falls back to a placeholder for just that
object. A picture that isn't a valid DrawFile at all still falls back
to the labelled placeholder image below.
"""

from __future__ import annotations

import base64
import html as _html
from typing import Optional

from riscos_impression.formats.drawfile import (
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
_FAMILY_HINTS = [
    ("courier", '"Courier New", Courier, monospace'),
    ("corpus", '"Courier New", Courier, monospace'),
    ("system", '"Courier New", Courier, monospace'),
    ("mono", '"Courier New", Courier, monospace'),
    ("times", 'Times, "Times New Roman", serif'),
    ("trinity", 'Times, "Times New Roman", serif'),
    ("serif", 'Times, "Times New Roman", serif'),
]
_DEFAULT_FONT_STACK = '"Helvetica Neue", Helvetica, Arial, sans-serif'


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
            return self._picture_html_for_data(data, entry, width_pt, height_pt)
        return self._placeholder_img("data", width_pt, height_pt)

    def _picture_html_for_data(self, data: bytes, entry, width_pt: float, height_pt: float) -> str:
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
                return self._drawfile_svg(draw, width_pt, height_pt)
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

    def _drawfile_svg(self, draw: DrawFile, width_pt: float, height_pt: float) -> str:
        """A decoded DrawFile's objects as an inline SVG fragment,
        mapping the DrawFile's own bounding box on to the picture's own
        width/height (stretch to fit, matching PDFConverter's own
        DrawFile rendering and the DrawFile format's own "a Sprite
        object fills its bounding box" convention). See the module
        docstring and pdfdoc.py's own DrawFile section for what's
        approximated versus a genuine placeholder."""
        bounds = draw.bounds
        sx = width_pt / bounds.width if bounds.width else 1.0
        sy = height_pt / bounds.height if bounds.height else 1.0

        def to_svg(dx: int, dy: int) -> tuple[float, float]:
            # SVG is Y-down from the top-left; Draw is Y-up from the
            # bottom-left, so the Y axis needs flipping (unlike
            # pdfdoc.py, which shares PDF's own Y-up convention).
            return (dx - bounds.x0) * sx, height_pt - (dy - bounds.y0) * sy

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_pt:.1f}pt" '
            f'height="{height_pt:.1f}pt" viewBox="0 0 {width_pt:.1f} {height_pt:.1f}">'
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
