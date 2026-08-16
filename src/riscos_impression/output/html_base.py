"""Shared HTML5 output helpers: colour/style -> CSS mapping and picture
placeholder rendering, used by both the scrolling and paged-media HTML5
converters.

Unlike the PDF converter, HTML output does no line-wrapping or text
layout of its own at all -- a browser's own rendering engine handles
that natively from the CSS this module produces, so there is no
equivalent of pdfdoc.py's approximate-metrics wrapping concern here.
"""

from __future__ import annotations

import base64
import html as _html
from typing import Optional

from riscos_impression.formats.drawfile import DrawFile
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


def font_family_css(style: Style) -> str:
    name = (style.font_style_name or "").lower()
    for hint, stack in _FAMILY_HINTS:
        if hint in name:
            return stack
    return _DEFAULT_FONT_STACK


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
            if DrawFile.from_bytes(data) is not None:
                self.log.best_effort(
                    "picture",
                    "DrawFile picture rendered as a placeholder box; vector content "
                    "is not decoded by this converter",
                )
                return self._placeholder_img("Draw", width_pt, height_pt)
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
