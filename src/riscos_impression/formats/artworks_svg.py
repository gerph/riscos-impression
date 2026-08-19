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

* ``artworks_to_svg`` runs ``riscos_artworks.denormalise()`` on
  *artwork* before walking it (see that function's own docstring).
  The decoder reads exactly what's on disk: a flat run of sibling
  records, e.g. a shape immediately followed by its own local fill
  override. That flat form is not what gets rendered -- ArtWorks (and
  riscos-artworks-js, whose own SVG mapper always calls
  ``Artworks.denormalise()`` first) re-nests every such run so each
  subsequent sibling becomes the sole child of the one before it.
  Skipping this step was an earlier, real bug here: confirmed via
  AWDocs/TestDocs/TestDocs/BlueRect,d94 (a single rectangle with one
  local fill-colour override after it) rendering solid black instead
  of blue, since the override was read as an inert trailing sibling
  rather than the rectangle's own child.
* Style-setting records (stroke colour/width, fill colour, join style,
  start/end line cap, winding rule, dash pattern) are siblings that
  mutate the *current* scope's style state for whichever siblings
  follow them, not children of the object they style -- true only
  *after* denormalisation nests any object-local override under the
  object it styles; before that step every style-setting record is a
  flat sibling regardless of what it was meant to affect. Descending
  into a record's own child_lists (a group, a layer, or a path's own
  nested attribute overrides, now correctly populated by
  denormalise()) happens with a *duplicated* copy of the current style
  scope, popped back to the parent's own state again once that
  sub-list finishes -- so a style change nested inside a group/path
  never leaks back out to the group's own later siblings.
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

Text (TextRecord/CharacterRecord) is rendered as real SVG `<text>`
glyphs, one per CharacterRecord -- beyond what riscos-artworks-js's
own SVG mapper does (structural only there; `riscos_artworks` itself
decodes no glyph outlines at all, so this is CSS/browser-font text,
not a trace of ArtWorks' own rendering). Reverse-engineered against a
real file (corpus/TestDoc,bc5's own "Shit Creek" picture) since
neither the SDK manual nor riscos-artworks-js documents these fields:
* TextRecord.unknown_values = (flags, x, y, char_count, insertion_point,
  angle) -- x/y matched the first CharacterRecord's own position
  exactly; char_count matched the number of CharacterRecord children
  exactly; angle (65536ths of a degree) matched a visibly rotated
  text object's own non-axis-aligned selection rectangle.
* CharacterRecord.unknown_values = (x, y, x_offset, y_offset) -- each
  character's own (x, y) already reflects the *cumulative* advance
  along the text's own baseline (straight or, for rotated text,
  diagonal) -- x[n+1] == x[n] + x_offset[n] held exactly across every
  character checked, so characters are placed independently rather
  than needing this code to accumulate advances itself.
* CharacterRecord.character_code's low byte is the actual character
  code (`& 0xFF`); everything above that varies per character in ways
  not fully understood (control/kerning flags?) and is discarded.
Font *name* only drives a coarse bold/italic/monospace CSS guess (see
_font_family_css_for_name) -- RISC OS outline font names obviously
don't exist as installed fonts in a browser. Only font_size's own y
component is used (matching html_base.py's own DrawFile text
simplification, for the same reason: SVG has no direct equivalent of
PDF's `Tz` horizontal-scaling operator to reproduce an x/y size skew
cheaply). No word-wrap, justification, or kerning-pair-table lookups
are attempted -- each glyph is placed exactly where its own
CharacterRecord says, nothing more.

FontSizeRecord.x_size/y_size need converting via FONT_SIZE_TO_NATIVE_UNITS
before use -- see that constant's own docstring for the empirical
derivation (RISC OS's own "1/16th of a point" font-size convention,
already used elsewhere in this project for Impression's own unrelated
Style.font_size field). Treating y_size as already being in native
units was a real, shipped bug: every glyph rendered roughly 20-40x too
small, invisible in a picture whose own frame wasn't huge (a CD-cover
picture in a thumbnail-sized frame, reported by the user against a
real document) and merely small enough to go unremarked in one whose
frame happened to be large (a road-sign picture in a full-page frame).

Not yet handled fully (best-effort gaps): distortion/perspective envelopes
(recursed into structurally, the distortion itself not applied).

Checking a real file (corpus/TestDoc,bc5's own "Shit Creek" picture,
in riscos-impression) against its own known appearance -- rather than
trusting "matches the reference" to mean "looks right" -- surfaced the
missing-denormalise() bug above: what first looked like "the whole
picture rendered solid black" turned out to be a large object that
should have been a layer's own genuine first (backmost) child instead
rendering as a stray top-level sibling drawn last (i.e. on top of
everything), inheriting the ambient default fill (black) because its
own local override was similarly stranded as an inert sibling. Once
fixed, that file also relies heavily on blends for shading (sky
gradient, rounded building shadow); this is now handled -- see
process_blend_group.

process_blend_group (dispatched for BlendGroupRecord in process_record)
implements genuine blend interpolation for the one case confirmed
against real data (corpus/TestDoc,bc5's own blend groups, all with
matching start/end point counts and segment types): after
denormalise() (see above), a BlendGroupRecord's own child_lists always
split cleanly into exactly three -- one holding a BlendOptionsRecord
(giving blend_steps), and two holding one keyframe PathRecord each (in
file order: start, then end) -- confirmed against every one of that
file's own 8 real blend groups, not just guessed from
riscos-artworks-js's own createSimpleBlendGroup() test-fixture builder
(examples/simple-blend-group.js), which independently confirms the
same three-list shape. `blend_steps + 1` shapes are drawn, at
`t = i / blend_steps` for `i` in `0..blend_steps` inclusive (so the
first and last drawn shapes exactly reproduce the two keyframes, not
approximations of them) -- geometry is linearly interpolated
element-by-element (_interpolate_path), stroke colour and width
continuously (matching riscos-artworks-js's own blend-groups research
notes, docs/blend-groups/README.md: "stroke attributes ... interpolated
linearly"), join/cap/winding/dash discretely switched over at the
halfway point (same source, "these have like a discrete attribute").
Flat-to-flat fill colour is interpolated the same way stroke colour is;
any other fill combination (a gradient on either end) falls back to a
discrete halfway switchover of the whole fill definition rather than
attempting the partially-understood linear/radial cross-blending that
document's own README describes as not fully working even in AWViewer
itself. When a real blend group's own two keyframe paths don't share
the same point count or segment-type sequence, this code does not
attempt AWViewer's own point-insertion algorithm for unequal path
shapes -- by that same document's own admission ("It's not fully
understood how !AWViewer blends geometry..."), matching a general
unequal-point-count algorithm from black-box observation alone is an
open research problem for that sibling project, not something to
guess at here -- both keyframes are drawn as-is instead (closer to the
real appearance than drawing nothing, the previous behaviour).

Sprites are deliberately out of scope here too: a SpriteRecord falls
through to the generic default case below (recursed into structurally,
drawing nothing of its own) rather than getting a placeholder -- a
separate project is expected to provide sprite handling, so this
module doesn't attempt even a placeholder for one."""

from __future__ import annotations

from typing import Optional

from riscos_artworks import (
    ArtWorks,
    BezierElement,
    BlendGroupRecord,
    BlendOptionsRecord,
    BoundingBox,
    CapStyle,
    CloseElement,
    ColourIndex,
    EllipseRecord,
    EndElement,
    FillColourRecord,
    FillType,
    JoinStyle,
    LineElement,
    MoveElement,
    PathRecord,
    Point,
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
    TextRecord,
    CharacterRecord,
    FontNameRecord,
    FontSizeRecord,
    denormalise,
)

#: ArtWorks' own native unit -> "user units" in the emitted SVG's own
#: declared width/height (the coordinate system inside the SVG's own
#: viewBox stays in native units throughout; see the module docstring).
#: Matches riscos-artworks-js's own ARTWORKS_UNITS_TO_USER_UNITS exactly.
ARTWORKS_UNIT_TO_USER_UNITS = (1.0 / 640.0) * (4.0 / 3.0)

#: FontSizeRecord.x_size/y_size -> native ArtWorks coordinate units
#: (the same space every other geometry field, including
#: CharacterRecord's own bounding_box, already lives in -- neither the
#: SDK manual nor riscos-artworks-js documents this field's own unit,
#: same as TextRecord/CharacterRecord's own unknown_values; see the
#: module docstring). Confirmed empirically against two real pictures
#: (corpus/TestDoc,bc5's own CD-cover and "Shit Creek" pictures, in
#: riscos-impression): treating y_size as already being in native units
#: (the assumption this code made before this constant existed)
#: under-sized every glyph by roughly 20-40x -- invisible in a small
#: frame (the CD cover, a thumbnail-sized picture), merely small enough
#: to go unnoticed in a large one ("Shit Creek", whose own frame
#: happened to be big enough to make even a ~30x-undersized glyph
#: nominally legible). Real font sizes on RISC OS are conventionally
#: expressed in 1/16ths of a point (matching Font_SetFont's own R1/R2
#: units) -- dividing every observed y_size in the corpus (512, 320,
#: 728, 480, 576, 832, 352) by 16 gives a consistent set of ordinary,
#: round-ish point sizes (32, 20, 45.5, 30, 36, 52, 22), rather than
#: the sub-point sizes a "already native units" or a "1/640 point"
#: (DrawFile's own text-size convention) reading would give. Combined
#: with ARTWORKS_UNIT_TO_USER_UNITS's own native-units-per-point factor
#: (1 / ARTWORKS_UNIT_TO_USER_UNITS = 480), this gives
#: 480 / 16 = 30 native units per FontSizeRecord unit -- cross-checked
#: against real CharacterRecord.bounding_box/TextRecord.bounding_box
#: heights at several different font sizes across both pictures, and
#: landing consistently within the range a font's own cap-height
#: (~70-100% of em-size) and full ascent+descent line-height
#: (~115-135% of em-size) would be expected to fall in.
FONT_SIZE_TO_NATIVE_UNITS = 480.0 / 16.0

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
    "font_name": None,
    "font_size": 160,
    "text_angle": 0.0,
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


def _lerp_point(a: Point, b: Point, t: float) -> Point:
    return Point(x=round(a.x + (b.x - a.x) * t), y=round(a.y + (b.y - a.y) * t))


def _interpolate_path(start_path: tuple, end_path: tuple, t: float) -> Optional[tuple]:
    """Linearly interpolates two blend keyframe paths element-by-
    element, or returns None when they can't be safely paired up this
    way (a different number of elements, or a segment-type mismatch at
    some position) -- see process_blend_group's own docstring for why
    that case isn't handled by attempting AWViewer's own point-
    insertion algorithm here."""
    if len(start_path) != len(end_path):
        return None
    result = []
    for start_element, end_element in zip(start_path, end_path):
        if start_element.masked_tag != end_element.masked_tag:
            return None
        if isinstance(start_element, MoveElement) and isinstance(end_element, MoveElement):
            result.append(MoveElement(tag=start_element.tag, point=_lerp_point(start_element.point, end_element.point, t)))
        elif isinstance(start_element, LineElement) and isinstance(end_element, LineElement):
            result.append(LineElement(tag=start_element.tag, point=_lerp_point(start_element.point, end_element.point, t)))
        elif isinstance(start_element, BezierElement) and isinstance(end_element, BezierElement):
            result.append(
                BezierElement(
                    tag=start_element.tag,
                    control_1=_lerp_point(start_element.control_1, end_element.control_1, t),
                    control_2=_lerp_point(start_element.control_2, end_element.control_2, t),
                    end=_lerp_point(start_element.end, end_element.end, t),
                )
            )
        elif isinstance(start_element, (CloseElement, EndElement)):
            result.append(start_element)
        else:
            return None
    return tuple(result)


def _interpolate_colour_index(bgr_a: Optional[int], bgr_b: Optional[int], t: float) -> Optional[ColourIndex]:
    """Linearly interpolates two resolved BGR colour words in RGB
    space (matching riscos-artworks-js's own blend-groups research
    notes: colours "appear to be linearly interpolated in RGB space"),
    re-wrapped as a direct-colour ColourIndex (bit 24 set, per that
    class's own docstring) ready to feed back through the normal
    style/_colour_css machinery. None (fully transparent/unresolved) on
    either end can't be meaningfully blended towards a colour, so
    returns None -- callers fall back to a discrete keyframe choice."""
    if bgr_a is None or bgr_b is None:
        return None
    ra, ga, ba = bgr_a & 0xFF, (bgr_a >> 8) & 0xFF, (bgr_a >> 16) & 0xFF
    rb, gb, bb = bgr_b & 0xFF, (bgr_b >> 8) & 0xFF, (bgr_b >> 16) & 0xFF
    r = round(ra + (rb - ra) * t)
    g = round(ga + (gb - ga) * t)
    b = round(ba + (bb - ba) * t)
    return ColourIndex(0x01000000 | (b << 16) | (g << 8) | r)


def _escape_xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _font_family_css_for_name(font_name: Optional[str]) -> str:
    """A coarse CSS font-family guess from a RISC OS outline font's own
    name (e.g. "Homerton.Bold.Oblique", "Trinity.Medium") -- these
    fonts don't exist as installed fonts in a browser, so this is only
    ever a fallback shape/weight/style guess, not a real font match."""
    lower = (font_name or "").lower()
    if "mono" in lower or "corpus" in lower or "courier" in lower:
        return "monospace"
    if any(name in lower for name in ("homerton", "arial", "helvetica", "swiss", "sans")):
        return "sans-serif"
    return "serif"


class _SvgBuilder:
    def __init__(self, artwork: ArtWorks):
        self.artwork = artwork
        self.objects: list[str] = []
        self.definitions: dict[str, str] = {}
        self._next_fill_id = 1
        self._bbox: Optional[list[float]] = None

    # -- Top level -----------------------------------------------------------

    def build(self) -> str:
        viewbox, width_pt, height_pt, inner = self.build_fragment()
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_pt}pt" '
            f'height="{height_pt}pt" viewBox="{viewbox}">{inner}</svg>'
        )

    def build_fragment(self) -> tuple[str, str, str, str]:
        """Like build(), but returns (viewbox, width_pt, height_pt,
        inner_markup) separately rather than a single standalone `<svg>`
        document -- for embedding inside a caller's own differently
        sized/positioned outer `<svg>` (see artworks_svg_fragment)."""
        self.process_lists(self.artwork.record_lists, dict(_DEFAULT_STYLE))
        min_x, min_y, max_x, max_y = self._bbox or [0, 0, 0, 0]
        width = max(max_x - min_x, 1)
        height = max(max_y - min_y, 1)
        width_pt = width * ARTWORKS_UNIT_TO_USER_UNITS
        height_pt = height * ARTWORKS_UNIT_TO_USER_UNITS
        defs = "".join(self.definitions.values())
        objects = "".join(self.objects)
        viewbox = f"{_fmt(min_x)} {_fmt(-max_y)} {_fmt(width)} {_fmt(height)}"
        inner = f"<defs>{defs}</defs>" f'<g transform="scale(1,-1)">{objects}</g>'
        return viewbox, _fmt(width_pt), _fmt(height_pt), inner

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
        elif isinstance(record, FontNameRecord):
            style["font_name"] = record.font_name.text
        elif isinstance(record, FontSizeRecord):
            style["font_size"] = record.y_size
        elif isinstance(record, TextRecord):
            self.process_text(record, style)
        elif isinstance(record, CharacterRecord):
            self._emit_character(record, style)
        elif isinstance(record, BlendGroupRecord):
            self.process_blend_group(record, style)
        else:
            # Group/layer/blend/distortion/sprite and anything else
            # not drawn directly: descend into its own children with a
            # scoped copy of the current style, matching
            # riscos-artworks-js's own default case. Sprites are
            # deliberately skipped rather than given a placeholder --
            # see the module docstring: a separate project is expected
            # to provide sprite handling.
            self.process_lists(record.child_lists, dict(style))

    def process_text(self, record: Record, style: dict) -> None:
        # A TextRecord draws nothing of its own -- its own child_lists
        # (font name/size, fill/stroke colour, and one CharacterRecord
        # per glyph, in that order) do all the actual work, each seen
        # in turn as *this* text object's own scoped style; see the
        # module docstring for how unknown_values was reverse-engineered.
        if not (record.control_word >> 1) & 1:
            return  # bit 1 clear: object marked not visible
        child_style = dict(style)
        angle_raw = record.unknown_values[5] if len(record.unknown_values) > 5 else 0
        child_style["text_angle"] = angle_raw / 65536.0
        self._merge_bbox(record.bounding_box)
        self.process_lists(record.child_lists, child_style)

    def process_geometry(self, record: Record, style: dict) -> None:
        child_style = dict(style)
        self.process_lists(record.child_lists, child_style)
        self._emit(record, child_style)

    def process_blend_group(self, group: Record, style: dict) -> None:
        # See the module docstring for how the post-denormalise() shape
        # of a BlendGroupRecord's own child_lists was confirmed against
        # real data. Skip entirely -- including the interpolated draws
        # -- when the group's own visibility bit is clear, matching
        # every other geometry-bearing record's own convention.
        if not (group.control_word >> 1) & 1:
            return
        blend_steps = 1
        keyframe_lists: list[tuple] = []
        for child_list in group.child_lists:
            options = next((r for r in child_list.records if isinstance(r, BlendOptionsRecord)), None)
            if options is not None:
                blend_steps = max(options.blend_steps, 1)
            else:
                keyframe_lists.append(child_list.records)
        if len(keyframe_lists) != 2:
            # Not the two-keyframe shape this code understands (e.g. a
            # malformed or differently-structured file) -- fall back to
            # drawing whatever's structurally there, the pre-blend-
            # support behaviour, rather than guessing.
            self.process_lists(group.child_lists, dict(style))
            return
        start_path, start_style = self._capture_blend_keyframe(keyframe_lists[0], style)
        end_path, end_style = self._capture_blend_keyframe(keyframe_lists[1], style)
        if start_path is None or end_path is None:
            self.process_lists(group.child_lists, dict(style))
            return
        self._merge_bbox(group.bounding_box)
        if _interpolate_path(start_path, end_path, 0.0) is None:
            # Geometry can't be safely interpolated (different point
            # counts or mismatched segment types -- see the module
            # docstring on why AWViewer's own point-insertion algorithm
            # for that case isn't attempted here). Draw both keyframes
            # as-is: closer to the real appearance than nothing at all.
            self._draw_blend_step(start_path, start_style)
            self._draw_blend_step(end_path, end_style)
            return
        for step in range(blend_steps + 1):
            t = step / blend_steps
            path = _interpolate_path(start_path, end_path, t)
            step_style = self._interpolate_blend_style(start_style, end_style, t)
            self._draw_blend_step(path, step_style)

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

    def _draw_blend_step(self, path: tuple, style: dict) -> None:
        """Draws one interpolated (or, on a geometry mismatch, one raw
        keyframe) blend path -- like _emit, but for a synthesised path
        with no Record of its own behind it, so there's no control-word
        visibility bit or per-path "is filled" flag to check (the
        keyframes' own such flags were already folded into start_style/
        end_style by _capture_blend_keyframe when present as a
        FillColourRecord; a genuine per-path filled-flag mismatch
        between the two keyframes isn't specially handled, matching
        this code's general stance of not guessing at AWViewer's own
        undocumented edge-case behaviour)."""
        if not path:
            return
        d = self._path_d(path)
        attrs = self._style_attrs(style)
        self.objects.append(f'<path d="{d}"{attrs}/>')

    def _capture_blend_keyframe(self, records: tuple, ambient_style: dict) -> tuple[Optional[tuple], dict]:
        """Walks one blend keyframe's own record list -- a geometry
        record plus its own attribute siblings/children, folded
        together by denormalise() -- capturing its path and final
        resolved style rather than drawing it, mirroring
        process_record's own attribute-record cascade (duplicated
        rather than shared, since this variant returns instead of
        drawing)."""
        style = dict(ambient_style)
        path: Optional[tuple] = None
        for record in records:
            if isinstance(record, _GEOMETRY_TYPES):
                own_records = tuple(r for child_list in record.child_lists for r in child_list.records)
                _, style = self._capture_blend_keyframe(own_records, style)
                path = record.path
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
            # Anything else (nested groups, text, ...) is out of scope
            # for a blend keyframe -- real ArtWorks blend groups don't
            # appear to nest that kind of content, so it's ignored here
            # rather than guessed at.
        return path, style

    def _interpolate_blend_style(self, start_style: dict, end_style: dict, t: float) -> dict:
        """Builds one blend step's own style: continuous linear
        interpolation for stroke colour/width and (flat-to-flat only)
        fill colour, discrete halfway switchover for everything else --
        see the module docstring and riscos-artworks-js's own
        docs/blend-groups/README.md for why each attribute is treated
        this way."""
        style = dict(end_style if t >= 0.5 else start_style)
        stroke = _interpolate_colour_index(
            self.artwork.resolve_colour(start_style["stroke"]),
            self.artwork.resolve_colour(end_style["stroke"]),
            t,
        )
        if stroke is not None:
            style["stroke"] = stroke
        a, b = start_style["stroke_width"], end_style["stroke_width"]
        style["stroke_width"] = a + (b - a) * t
        if start_style["fill_type"] == FillType.FLAT and end_style["fill_type"] == FillType.FLAT:
            fill = _interpolate_colour_index(
                self.artwork.resolve_colour(start_style["fill_colour"]) if start_style["fill_colour"] else None,
                self.artwork.resolve_colour(end_style["fill_colour"]) if end_style["fill_colour"] else None,
                t,
            )
            if fill is not None:
                style["fill_type"] = FillType.FLAT
                style["fill_colour"] = fill
        return style

    def _emit_character(self, record: Record, style: dict) -> None:
        # See the module docstring for how unknown_values and
        # character_code were reverse-engineered -- riscos_artworks
        # decodes no glyph outlines itself, so this is a plain SVG
        # `<text>` glyph in a browser font, not a trace of ArtWorks'
        # own rendering. Unlike every drawn geometry type, control_word
        # bit 1 is NOT a visibility flag here -- checked against real
        # data (corpus/TestDoc,bc5's own "Shit Creek" picture): every
        # letter has it *clear* and every space has it *set*, the
        # opposite of what "hidden" would mean, so it's ignored here;
        # only actual C0 control codes are skipped, via character_code
        # itself.
        if len(record.unknown_values) < 2:
            return
        char = record.character_code & 0xFF
        if char < 0x20 or char == 0x7F:
            return  # control character (kerning/ligature marker?), nothing to draw
        glyph_paths = [r for cl in record.child_lists for r in cl.records if isinstance(r, _GEOMETRY_TYPES)]
        if glyph_paths:
            # ArtWorks itself "pathified" this character -- confirmed
            # against the SDK manual (MethodsManual.md, on
            # PathifyText_*): the text tool converts individual
            # characters to real vector-traced outline paths (as this
            # character's own child object) when it can't rely on
            # standard text rendering coping with the attributes
            # applied to it -- typically an unusual font (this file's
            # own "Architect"/"Penultimat", confirmed against a real
            # picture, corpus/TestDoc,bc5's own "Shit Creek") no output
            # renderer could be expected to have installed. Render that
            # real outline exactly like any other geometry (same style
            # cascade, same fill/stroke) instead of a generic
            # substitute-font glyph -- a real font glyph in a browser
            # font is never a faithful stand-in for this. Falls through
            # to the generic <text> rendering below only when no such
            # outline exists (not every character is pathified -- only
            # when ArtWorks decided it needed to be).
            for glyph_path in glyph_paths:
                self._emit(glyph_path, style)
            return
        x, y = record.unknown_values[0], record.unknown_values[1]
        font_family = _font_family_css_for_name(style["font_name"])
        font_size = style["font_size"] * FONT_SIZE_TO_NATIVE_UNITS
        angle = style["text_angle"]
        fill = self._fill_css(style)
        stroke_attr = ""
        if style["stroke_width"]:
            stroke = self._stroke_css(style)
            if stroke != "none":
                stroke_attr = f' stroke="{stroke}" stroke-width="{_fmt(style["stroke_width"])}"'
        text = _escape_xml_text(chr(char))
        # The character's own (x, y) is in the same native, Y-up
        # ArtWorks units as everything else, but a plain <text> glyph
        # placed inside the outer <g transform="scale(1,-1)"> (see
        # _wrap) would render upside down -- translate to the native
        # position, then apply a local scale(1,-1) to cancel the
        # ambient flip just for this glyph (the standard idiom for
        # text inside a Y-flipped SVG group), rotating by the text
        # object's own angle first so it turns the right way once the
        # flip is cancelled.
        self.objects.append(
            f'<g transform="translate({_fmt(x)},{_fmt(y)}) scale(1,-1) rotate({_fmt(-angle)})">'
            f'<text x="0" y="0" font-family="{font_family}" font-size="{_fmt(font_size)}" '
            f'fill="{fill}"{stroke_attr}>{text}</text></g>'
        )

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
    return _SvgBuilder(denormalise(artwork)).build()


def artworks_svg_fragment(artwork: ArtWorks) -> tuple[str, str, str, str]:
    """Like artworks_to_svg(), but returns the artwork's own native
    (viewbox, width_pt, height_pt, inner_markup) separately rather than
    one standalone `<svg>...</svg>` document -- for a caller (e.g. a
    picture frame renderer) that wants to embed the artwork's content
    inside its own differently sized/positioned outer `<svg>` element,
    the way an inner `<svg viewBox="...">` acts as its own nested
    viewport. *inner_markup* is `<defs>...</defs><g transform=
    "scale(1,-1)">...</g>` -- everything artworks_to_svg() would put
    inside its own outer `<svg>` tag."""
    return _SvgBuilder(denormalise(artwork)).build_fragment()
