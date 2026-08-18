"""Renders a decoded Computer Concepts ArtWorks document (via the
third-party ``riscos_artworks`` decoder -- a structural decoder only,
with no rendering of its own) as a standalone SVG document.

Deliberately self-contained: this module imports nothing from the rest
of riscos_impression, only from ``riscos_artworks`` and the standard
library, so it can be lifted into the ``riscos_artworks`` package
itself later with no changes beyond the import path -- see PLAN.md for
why this lives here first, on the ``artworks-experiment`` branch,
rather than going straight into that package.

The rendering algorithm (record-list traversal with a scoped style
stack, path/fill/stroke/gradient mapping, unit scale, and the
Y-flip-via-viewBox trick) is ported from ``riscos-artworks-js``'s own
reference SVG mapper (`src/mapper/`, particularly `processor/index.js`
and `mapper/svg/*.js`) -- the JavaScript project riscos_artworks's own
record coverage is itself kept in step with (see that package's own
README) -- not derived independently. Notable behaviour carried across
from that reference implementation, not just guessed at:

* Style-setting records (stroke colour/width, fill colour, join style,
  start/end line cap, winding rule, dash pattern) are siblings that
  mutate the *current* scope's style state for whichever siblings
  follow them, not children of the object they style -- confirmed by
  that project's own "attribute-propagation" example corpus (e.g.
  "when two fills occur before a path then the second is used", "when
  a fill occurs after a path then it is not used later"). Descending
  into a record's own child_lists (a group, a layer, or a path's own
  nested attribute overrides) happens with a *duplicated* copy of the
  current style scope, popped back to the parent's own state again
  once that sub-list finishes -- so a style change nested inside a
  group/path never leaks back out to the group's own later siblings.
* A path/rectangle/ellipse/rounded-rectangle's own child_lists (if it
  has any -- typically attribute records overriding just this one
  object) are processed *before* the object itself is drawn, using the
  same duplicate/pop scoping.
* An object is skipped entirely when its own control word's bit 1
  ("visible") is clear.
* An object is drawn unfilled -- regardless of whatever fill the
  current style scope carries -- unless its own path's first element's
  tag has bit 31 set (ArtWorks' own per-path "is filled" flag, distinct
  from the propagated fill *colour*).
* Coordinates are emitted in ArtWorks' own native integer units,
  unscaled, for every path/gradient coordinate -- the unit scale
  (ArtWorks units -> the SVG's own declared width/height, matching
  riscos-artworks-js's own ARTWORKS_UNITS_TO_USER_UNITS = (1/640) *
  (4/3), which is exactly (4/3) of this project's own Draw-unit-to-
  point scale elsewhere -- ArtWorks units are 4/3 of a Draw unit) is
  applied only once, to the outer `<svg>` element's own width/height;
  the `viewBox` stays in native units, and an inner `<g
  transform="scale(1,-1)">` flips Y (ArtWorks, like Draw, is Y-up;
  SVG is Y-down) without needing every coordinate individually negated.

Not yet handled fully (best-effort gaps): text (CharacterRecord/
TextRecord are structural only here, the same as in
riscos-artworks-js's own SVG mapper -- no glyph outlines are decoded by
`riscos_artworks` itself), and distortion/perspective envelopes
(recursed into structurally, the distortion itself not applied).

Checking a real file (corpus/TestDoc,bc5's own "Shit Creek" picture,
in riscos-impression) against its own known appearance -- rather than
trusting "matches the reference" to mean "looks right" -- found that
file relies heavily on blends for shading (sky gradient, rounded
building shadow): with every blend left entirely unrendered
(riscos-artworks-js's own behaviour: recursed into structurally, never
drawn), almost nothing but flat *backing* rectangles remained visible
underneath the missing shading, reading as "the whole picture rendered
solid black" rather than "shading is missing here". BlendPathRecord (a
blend's own start/end keyframe shape, carrying an identical `.path`
field to a plain PathRecord) is now drawn the same way a PathRecord is
-- a real gap remains even so: ArtWorks marks a blend's own keyframes
invisible in the file itself (its control word's own visibility bit is
clear on both ends, confirmed directly against this same file), since
a correct renderer is expected to synthesise the *interpolated*
in-between shapes instead of showing the keyframes -- so this change
alone doesn't yet recover that file's own missing shading; genuine
blend interpolation (walking blend_steps, interpolating both
geometry and colour between the two keyframe paths) remains a real
follow-up, not attempted here.

Sprites are deliberately out of scope here too: a SpriteRecord falls
through to the generic default case below (recursed into structurally,
drawing nothing of its own) rather than getting a placeholder -- a
separate project is expected to provide sprite handling, so this
module doesn't attempt even a placeholder for one."""

from __future__ import annotations

from typing import Optional

from riscos_artworks import (
    ArtWorks,
    BoundingBox,
    CapStyle,
    ColourIndex,
    EllipseRecord,
    FillColourRecord,
    FillType,
    JoinStyle,
    PathRecord,
    Record,
    RecordList,
    RectangleRecord,
    RoundedRectangleRecord,
    BlendPathRecord,
    StartCapRecord,
    EndCapRecord,
    StrokeColourRecord,
    StrokeWidthRecord,
    JoinStyleRecord,
    WindingRule,
    WindingRuleRecord,
    DashPatternRecord,
)

#: ArtWorks' own native unit -> "user units" in the emitted SVG's own
#: declared width/height (the coordinate system inside the SVG's own
#: viewBox stays in native units throughout; see the module docstring).
#: Matches riscos-artworks-js's own ARTWORKS_UNITS_TO_USER_UNITS exactly.
ARTWORKS_UNIT_TO_USER_UNITS = (1.0 / 640.0) * (4.0 / 3.0)

_JOIN_CSS = {JoinStyle.MITRE: "miter", JoinStyle.ROUND: "round", JoinStyle.BEVEL: "bevel"}
_CAP_CSS = {CapStyle.BUTT: "butt", CapStyle.ROUND: "round", CapStyle.SQUARE: "square", CapStyle.TRIANGLE: "butt"}
_WINDING_CSS = {WindingRule.NON_ZERO: "nonzero", WindingRule.EVEN_ODD: "evenodd"}

#: A path element's own masked tag (bits 0-7 of its raw tag word); see
#: riscos_artworks.model's own PathElement.masked_tag.
_TAG_END = 0
_TAG_MOVE = 2
_TAG_CLOSE = 5
_TAG_BEZIER = 6
_TAG_LINE = 8

#: Default render state, matching riscos-artworks-js's own
#: DEFAULT_RENDER_STATE exactly: solid black hairline stroke, no fill,
#: bevel joins, butt caps, even-odd winding, no dash.
_DEFAULT_STYLE = {
    "stroke": ColourIndex(0x01000000),
    "stroke_width": 160,
    "fill_type": FillType.FLAT,
    "fill_colour": ColourIndex(0xFFFFFFFF),
    "gradient_line": None,
    "fill_start": None,
    "fill_end": None,
    "join": JoinStyle.BEVEL,
    "cap_start": CapStyle.BUTT,
    "cap_end": CapStyle.BUTT,
    "winding": WindingRule.EVEN_ODD,
    "dash_offset": 0,
    "dash_elements": (),
}

#: BlendPathRecord (a blend's own start/end keyframe shape) is included
#: here deliberately, beyond what riscos-artworks-js's own reference
#: mapper does (it recurses into a blend's own structure but never
#: draws it at all): a real file (corpus/TestDoc,bc5's own "Shit
#: Creek" picture, in riscos-impression) uses blends heavily for
#: shading (sky gradient, rounded building shadows), and with them
#: entirely unrendered, only the flat backing shapes beneath that
#: shading were visible -- drawing each blend keyframe path with
#: whatever fill/stroke is active at that point (the same as a normal
#: PathRecord; BlendPathRecord carries an identical `.path` field) is
#: not a real gradient/interpolation between the two keyframes, but is
#: a much closer approximation than showing nothing at all.
_GEOMETRY_TYPES = (PathRecord, RectangleRecord, EllipseRecord, RoundedRectangleRecord, BlendPathRecord)


def _colour_css(bgr: Optional[int]) -> str:
    """A CSS colour for a resolved BGR-packed colour word (bits 0-7
    red, 8-15 green, 16-23 blue -- matching riscos-artworks-js's own
    mapColour), or "none" for a transparent/unresolved colour."""
    if bgr is None:
        return "none"
    r, g, b = bgr & 0xFF, (bgr >> 8) & 0xFF, (bgr >> 16) & 0xFF
    return f"rgb({r},{g},{b})"


def _fmt(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


class _SvgBuilder:
    def __init__(self, artwork: ArtWorks):
        self.artwork = artwork
        self.objects: list[str] = []
        self.definitions: dict[str, str] = {}
        self._next_fill_id = 1
        self._bbox: Optional[list[float]] = None

    # -- Top level -----------------------------------------------------------

    def build(self) -> str:
        self.process_lists(self.artwork.record_lists, dict(_DEFAULT_STYLE))
        return self._wrap()

    def _wrap(self) -> str:
        min_x, min_y, max_x, max_y = self._bbox or [0, 0, 0, 0]
        width = max(max_x - min_x, 1)
        height = max(max_y - min_y, 1)
        width_pt = width * ARTWORKS_UNIT_TO_USER_UNITS
        height_pt = height * ARTWORKS_UNIT_TO_USER_UNITS
        defs = "".join(self.definitions.values())
        objects = "".join(self.objects)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(width_pt)}pt" '
            f'height="{_fmt(height_pt)}pt" '
            f'viewBox="{_fmt(min_x)} {_fmt(-max_y)} {_fmt(width)} {_fmt(height)}">'
            f"<defs>{defs}</defs>"
            f'<g transform="scale(1,-1)">{objects}</g>'
            "</svg>"
        )

    def _merge_bbox(self, box: BoundingBox) -> None:
        if self._bbox is None:
            self._bbox = [box.min_x, box.min_y, box.max_x, box.max_y]
            return
        self._bbox[0] = min(self._bbox[0], box.min_x)
        self._bbox[1] = min(self._bbox[1], box.min_y)
        self._bbox[2] = max(self._bbox[2], box.max_x)
        self._bbox[3] = max(self._bbox[3], box.max_y)

    # -- Traversal -----------------------------------------------------------

    def process_lists(self, lists: tuple[RecordList, ...], style: dict) -> None:
        # *style* is shared (and mutated in place) across every list in
        # *lists*, not reset per list: riscos-artworks-js's own
        # processLists() likewise makes a single call to processList()
        # per list using its one shared, class-level RenderState stack
        # -- scoping (a fresh, popped-afterwards copy) happens once per
        # *caller* of this method (process_record's own default case,
        # and process_geometry), not once per list within it. Getting
        # this wrong (an earlier version of this method copied *style*
        # inside this loop) meant a real multi-list document-defaults
        # sequence -- individual single-record lists each setting one
        # attribute, immediately followed by the actual content list --
        # silently discarded every one of those defaults before the
        # content list was ever reached, and every object in a real
        # ArtWorks file (corpus/TestDoc,bc5's own embedded pictures)
        # rendered with no fill at all.
        for record_list in lists:
            self.process_list(record_list.records, style)

    def process_list(self, records: tuple[Record, ...], style: dict) -> None:
        # *style* is mutated in place as sibling attribute records are
        # encountered, so later siblings in this same list see earlier
        # ones' own changes -- see the module docstring.
        for record in records:
            self.process_record(record, style)

    def process_record(self, record: Record, style: dict) -> None:
        if isinstance(record, _GEOMETRY_TYPES):
            self.process_geometry(record, style)
        elif isinstance(record, StrokeColourRecord):
            style["stroke"] = record.colour
        elif isinstance(record, StrokeWidthRecord):
            style["stroke_width"] = record.width
        elif isinstance(record, FillColourRecord):
            style["fill_type"] = record.fill_type_enum or FillType.FLAT
            style["fill_colour"] = record.colour
            style["gradient_line"] = record.gradient_line
            style["fill_start"] = record.start_colour
            style["fill_end"] = record.end_colour
        elif isinstance(record, JoinStyleRecord):
            style["join"] = record.join_style_enum or JoinStyle.MITRE
        elif isinstance(record, StartCapRecord):
            style["cap_start"] = record.cap_style_enum or CapStyle.BUTT
        elif isinstance(record, EndCapRecord):
            style["cap_end"] = record.cap_style_enum or CapStyle.BUTT
        elif isinstance(record, WindingRuleRecord):
            style["winding"] = record.winding_rule_enum or WindingRule.NON_ZERO
        elif isinstance(record, DashPatternRecord):
            style["dash_offset"] = record.offset or 0
            style["dash_elements"] = record.elements
        else:
            # Group/layer/blend/distortion/sprite/text and anything
            # else not drawn directly: descend into its own children
            # with a scoped copy of the current style, matching
            # riscos-artworks-js's own default case. Sprites are
            # deliberately skipped rather than given a placeholder --
            # see the module docstring: a separate project is expected
            # to provide sprite handling.
            self.process_lists(record.child_lists, dict(style))

    def process_geometry(self, record: Record, style: dict) -> None:
        child_style = dict(style)
        self.process_lists(record.child_lists, child_style)
        self._emit(record, child_style)

    # -- Drawing -----------------------------------------------------------

    def _emit(self, record: Record, style: dict) -> None:
        if not (record.control_word >> 1) & 1:
            return  # bit 1 clear: object marked not visible
        path = record.path
        if not path:
            return
        if not (path[0].tag >> 31) & 1:
            # ArtWorks' own per-path "is filled" flag is clear: drawn
            # unfilled regardless of whatever fill colour is in scope.
            style = dict(style)
            style["fill_type"] = FillType.FLAT
            style["fill_colour"] = ColourIndex(0xFFFFFFFF)
        self._merge_bbox(record.bounding_box)
        d = self._path_d(path)
        attrs = self._style_attrs(style)
        self.objects.append(f'<path d="{d}"{attrs}/>')

    @staticmethod
    def _path_d(path) -> str:
        parts = []
        for element in path:
            masked = element.tag & 0xFF
            if masked == _TAG_MOVE:
                parts.append(f"M{_fmt(element.point.x)},{_fmt(element.point.y)}")
            elif masked == _TAG_LINE:
                parts.append(f"L{_fmt(element.point.x)},{_fmt(element.point.y)}")
            elif masked == _TAG_BEZIER:
                parts.append(
                    f"C{_fmt(element.control_1.x)},{_fmt(element.control_1.y)} "
                    f"{_fmt(element.control_2.x)},{_fmt(element.control_2.y)} "
                    f"{_fmt(element.end.x)},{_fmt(element.end.y)}"
                )
            elif masked == _TAG_CLOSE:
                parts.append("Z")
            # TAG_END and any unrecognised tag contribute nothing (the
            # end marker isn't itself a drawing command).
        return "".join(parts)

    def _style_attrs(self, style: dict) -> str:
        attrs = [f' fill="{self._fill_css(style)}"', f' stroke="{self._stroke_css(style)}"']

        width = style["stroke_width"]
        if width:
            attrs.append(f' stroke-width="{_fmt(width)}"')
        else:
            # A zero stroke width is ArtWorks' own "thinnest possible
            # line" convention; matches riscos-artworks-js's own
            # comment on why a hairline needs an explicit vector-effect
            # here rather than plain "0" (which SVG treats as no
            # stroke at all).
            attrs.append(' stroke-width="0.5px" vector-effect="non-scaling-stroke"')

        join_css = _JOIN_CSS.get(style["join"])
        if join_css and style["join"] != JoinStyle.MITRE:
            attrs.append(f' stroke-linejoin="{join_css}"')
        cap_css = _CAP_CSS.get(style["cap_start"])
        if cap_css and style["cap_start"] != CapStyle.BUTT:
            attrs.append(f' stroke-linecap="{cap_css}"')
        winding_css = _WINDING_CSS.get(style["winding"])
        if winding_css and style["winding"] != WindingRule.NON_ZERO:
            attrs.append(f' fill-rule="{winding_css}"')
        if style["dash_offset"]:
            attrs.append(f' stroke-dashoffset="{_fmt(style["dash_offset"])}"')
        if style["dash_elements"]:
            elements = " ".join(_fmt(e) for e in style["dash_elements"])
            attrs.append(f' stroke-dasharray="{elements}"')
        return "".join(attrs)

    def _stroke_css(self, style: dict) -> str:
        return _colour_css(self.artwork.resolve_colour(style["stroke"]))

    def _fill_css(self, style: dict) -> str:
        fill_type = style["fill_type"]
        if fill_type == FillType.FLAT:
            return _colour_css(self.artwork.resolve_colour(style["fill_colour"]) if style["fill_colour"] else None)
        if fill_type in (FillType.LINEAR, FillType.RADIAL):
            return self._gradient_fill(style, radial=fill_type == FillType.RADIAL)
        return "none"

    def _gradient_fill(self, style: dict, *, radial: bool) -> str:
        gradient_line = style["gradient_line"]
        if gradient_line is None:
            return "none"
        fill_id = f"{'radial' if radial else 'linear'}-gradient-{self._next_fill_id}"
        self._next_fill_id += 1
        start = _colour_css(self._resolve_optional(style["fill_start"]))
        end = _colour_css(self._resolve_optional(style["fill_end"]))
        p1, p2 = gradient_line
        if radial:
            dx, dy = p2.x - p1.x, p2.y - p1.y
            r = (dx * dx + dy * dy) ** 0.5
            body = (
                f'<radialGradient id="{fill_id}" gradientUnits="userSpaceOnUse" '
                f'cx="{_fmt(p1.x)}" cy="{_fmt(p1.y)}" fx="{_fmt(p1.x)}" fy="{_fmt(p1.y)}" r="{_fmt(r)}">'
                f'<stop offset="0%" stop-color="{start}"/><stop offset="100%" stop-color="{end}"/>'
                "</radialGradient>"
            )
        else:
            body = (
                f'<linearGradient id="{fill_id}" gradientUnits="userSpaceOnUse" '
                f'x1="{_fmt(p1.x)}" y1="{_fmt(p1.y)}" x2="{_fmt(p2.x)}" y2="{_fmt(p2.y)}">'
                f'<stop offset="0%" stop-color="{start}"/><stop offset="100%" stop-color="{end}"/>'
                "</linearGradient>"
            )
        self.definitions[fill_id] = body
        return f"url(#{fill_id})"

    def _resolve_optional(self, colour: Optional[ColourIndex]):
        return self.artwork.resolve_colour(colour) if colour is not None else None


def artworks_to_svg(artwork: ArtWorks) -> str:
    """A standalone SVG document reproducing *artwork*'s own visible
    content -- see the module docstring for the rendering algorithm and
    its known gaps."""
    return _SvgBuilder(artwork).build()
