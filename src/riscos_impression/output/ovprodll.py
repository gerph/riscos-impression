"""OvationPro DDL (Document Description Language) output: the reference
converter, ported from the original TransIMP C source (c/main, c/colours,
c/frames, c/styles, c/pxexp in the sibling riscos-source repository).

This aims to be structurally and semantically faithful to the original
tool's output -- the same DDL object types, the same field values and
hardcoded constants where the original uses them, the same identifier
scheme -- rather than byte-for-byte identical. One thing stops full
fidelity from being practical:

* Anything already noted in docs/impression-documents.xml as
  unconfirmed or a known converter gap (non-decimal numbering styles,
  PostScript screening, irregular boundaries beyond synthetic fixtures,
  ...) is carried through here as a logged best-effort/unsupported
  case rather than guessed at.

Output is written as RISC OS Latin1 (alphabet 101; see encoding.py),
not the platform's own default text encoding (UTF-8 on most systems
this runs on) -- DDL is a RISC OS-native format, read by a RISC OS-
native importer, so its bytes should match what that importer expects
rather than what's convenient for this Python process's own platform.

The picture rotation/scale/skew decomposition is a direct port of the
original's tr_setrotationa()/tr_setscale()/tr_multiply()/tr_getbits()
from the OvationPro XL transform library, whose source is present in
the sibling riscos-source repository at XL/Task/h/transform and
XL/Task/c/transform (see the _tr_*/_ttmul/_scale/_pythag helpers
below).
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Optional, Union

from riscos_impression import encoding
from riscos_impression.model.colours import MAXCV, Colour, ColourModel
from riscos_impression.model.dictionary import DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import (
    BlankFrame,
    Frame,
    GroupFrame,
    GuideFrame,
    PictureFrame,
    TextFrame,
)
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
from riscos_impression.output.base import Converter, page_origin, to_page_coordinates

# ---------------------------------------------------------------------------
# Picture transform (ported from the OvationPro XL transform library --
# XL/Task/h/transform and XL/Task/c/transform in the sibling riscos-source
# repository -- as used by the original tool's ixpictdata() in c/frames to
# decompose a picture's rotation+scale into DDL's scale/aspect/angle/skew
# fields).
# ---------------------------------------------------------------------------


def _cdiv(n: int, d: int) -> int:
    """C-style integer division: truncates toward zero (unlike Python's
    floor-dividing //), matching the original's scale(a,b,c) = a*b/c."""
    q = abs(n) // abs(d)
    return -q if (n < 0) != (d < 0) else q


def _scale(a: int, b: int, c: int) -> int:
    return _cdiv(a * b, c)


def _ttmul(a: int, b: int) -> int:
    """16.16 fixed-point multiply: (a*b)>>16, as in the original's ttmul()."""
    return (a * b) >> 16


def _pythag(x: int, y: int) -> int:
    return int(math.sqrt(x * x + y * y))


def _tr_cos(a: int, r: int) -> int:
    angle = (a / 0x10000) / 180.0 * math.pi
    return int(math.cos(angle) * r)


def _tr_sin(a: int, r: int) -> int:
    angle = (a / 0x10000) / 180.0 * math.pi
    return int(math.sin(angle) * r)


def _tr_setrotationa(angle_1616: int) -> tuple[int, int, int, int]:
    """Port of tr_setrotationa(): returns the (a,b,c,d) of a rotation-only
    transformstr for an angle in 16.16 fixed-point degrees."""
    angle = (angle_1616 / 0x10000) / 180.0 * math.pi
    a = d = int(0x10000 * math.cos(angle))
    b = int(0x10000 * math.sin(angle))
    c = -b
    return a, b, c, d


def _tr_setscale(oldx: int, newx: int, oldy: int, newy: int) -> tuple[int, int]:
    """Port of tr_setscale(): returns the (a,d) of a scale-only
    transformstr (b=c=0 always)."""
    if oldx == newx:
        a = 0x10000
    elif oldx:
        a = _scale(newx, 0x10000, oldx)
    else:
        a = 0x10000
    if oldy == newy:
        d = 0x10000
    elif oldy:
        d = _scale(0x10000, newy, oldy)
    else:
        d = 0x10000
    return a, d


def _tr_multiply(
    t1: tuple[int, int, int, int, int, int], t2: tuple[int, int, int, int, int, int]
) -> tuple[int, int, int, int, int, int]:
    """Port of tr_multiply(): r = t1 * t2."""
    a1, b1, c1, d1, e1, f1 = t1
    a2, b2, c2, d2, e2, f2 = t2
    a = _ttmul(a1, a2) + _ttmul(b1, c2)
    b = _ttmul(a1, b2) + _ttmul(b1, d2)
    c = _ttmul(c1, a2) + _ttmul(d1, c2)
    d = _ttmul(c1, b2) + _ttmul(d1, d2)
    e = _ttmul(a2, e1) + _ttmul(c2, f1) + e2
    f = _ttmul(b2, e1) + _ttmul(d2, f1) + f2
    return a, b, c, d, e, f


@dataclasses.dataclass(frozen=True)
class _TransformBits:
    """Port of transformbitstr: a transform decomposed into its shift,
    scale, rotation and skew components."""

    xshift: int
    yshift: int
    xscale: int
    yscale: int
    angle: int
    skewxy: int
    skewyx: int


def _tr_getbits(t: tuple[int, int, int, int, int, int]) -> _TransformBits:
    """Port of tr_getbits(): decomposes a composed transformstr into its
    shift/scale/rotate/skew components."""
    ta, tb, tc, td, te, tf = t
    xshift, yshift, skewyx = te, tf, 0
    if ta == 0:
        if tb < 0:
            angle, s, xscale = -90 * 0x10000, -0x10000, -tb
        else:
            angle, s, xscale = 90 * 0x10000, 0x10000, tb
        c = 0
    elif tb == 0:
        if ta < 0:
            angle, c, xscale = 180 * 0x10000, -0x10000, -ta
        else:
            angle, c, xscale = 0, 0x10000, ta
        s = 0
    else:
        angle = int((180 * math.atan2(tb, ta) / math.pi) * 0x10000)
        xscale = _pythag(ta, tb)
        c = _tr_cos(angle, 0x10000)
        s = _tr_sin(angle, 0x10000)

    top = _ttmul(tc, c) + _ttmul(td, s)
    bot = _ttmul(td, c) - _ttmul(tc, s)
    skewxy = 0 if bot == 0 else _scale(0x10000, top, bot)

    bot = _ttmul(skewxy, c) - s
    top = _ttmul(skewxy, s) + c
    if abs(bot) > abs(top):
        yscale = _scale(0x10000, tc, bot)
    else:
        yscale = _scale(0x10000, td, top)

    return _TransformBits(
        xshift=xshift,
        yshift=yshift,
        xscale=xscale,
        yscale=yscale,
        angle=angle,
        skewxy=skewxy,
        skewyx=skewyx,
    )


def _picture_transform_bits(angle: int, xscale: int, yscale: int) -> _TransformBits:
    """Port of ixpictdata()'s tr_setrotationa()/tr_setscale()/tr_multiply()/
    tr_getbits() composition (c/frames in the sibling riscos-source
    repository): decomposes a picture's rotation+scale into the DDL
    scale/aspect/angle/skew fields."""
    ra, rb, rc, rd = _tr_setrotationa(angle)
    rotation = (ra, rb, rc, rd, 0, 0)
    sa, sd = _tr_setscale(xscale, 0x10000, yscale, 0x10000)
    scaling = (sa, 0, 0, sd, 0, 0)
    return _tr_getbits(_tr_multiply(rotation, scaling))


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

_OVERPRINT_DEFAULT = (0x10000 * 90) // 100
TRANSPARENT_COLOUR_N = 3


@dataclasses.dataclass(frozen=True)
class _DDLColourEntry:
    n: int
    name: str
    model: ColourModel
    values: tuple
    locked: bool = False
    transparent: bool = False
    spot: bool = False
    overprint: bool = False
    all_plates: bool = False
    no_print: bool = False
    overprint_limit: int = _OVERPRINT_DEFAULT


def _default_colour_entries() -> list[_DDLColourEntry]:
    # Matches defcolours[] in c/colours.c exactly.
    return [
        _DDLColourEntry(1, "Black", ColourModel.CMYK, (0, 0, 0, MAXCV), locked=True),
        _DDLColourEntry(2, "White", ColourModel.RGB, (MAXCV, MAXCV, MAXCV), locked=True),
        _DDLColourEntry(
            3, "Transparent", ColourModel.RGB, (MAXCV, MAXCV, MAXCV), locked=True, transparent=True
        ),
        _DDLColourEntry(4, "Red", ColourModel.RGB, (MAXCV, 0, 0)),
        _DDLColourEntry(5, "Green", ColourModel.RGB, (0, MAXCV, 0)),
        _DDLColourEntry(6, "Blue", ColourModel.RGB, (0, 0, MAXCV)),
        _DDLColourEntry(7, "Cyan", ColourModel.CMYK, (MAXCV, 0, 0, 0), locked=True),
        _DDLColourEntry(8, "Magenta", ColourModel.CMYK, (0, MAXCV, 0, 0), locked=True),
        _DDLColourEntry(9, "Yellow", ColourModel.CMYK, (0, 0, MAXCV, 0), locked=True),
        _DDLColourEntry(
            10,
            "Registration",
            ColourModel.CMYK,
            (MAXCV, MAXCV, MAXCV, MAXCV),
            locked=True,
            all_plates=True,
        ),
    ]


def _synthetic_colour_key(colour: Colour) -> str:
    """A key matching how the conversion source names an inline (unnamed)
    colour value for its own colour table, so repeated references to the
    same computed colour reuse one DDL entry; see definecolours() in
    c/colours.c."""
    if colour.name:
        return colour.name
    if colour.model is ColourModel.RGB:
        r, g, b = (v * 255 // MAXCV for v in colour.values)
        return f"RGB{r:02X}{g:02X}{b:02X}"
    if colour.model is ColourModel.CMYK:
        c, m, y, k = (v * 255 // MAXCV for v in colour.values)
        return f"CMYK{c:02X}{m:02X}{y:02X}{k:02X}"
    # HSV: never observed in real documents (see docs/impression-documents.xml).
    # The original names these from the raw, unscaled source bytes; this is a
    # best-effort approximation re-derived from the already-scaled values.
    h, s, v = colour.values
    return f"HSV{(h // 0x10000) & 0xFFF:03X}{(s * 255 // MAXCV) & 0xFF:02X}{(v * 255 // MAXCV) & 0xFF:02X}"


class DDLColourTable:
    """Assigns each colour a DDL COL_xx identifier, replicating the
    conversion source's numbering: the ten built-in Impression colours
    always occupy COL_01-COL_0A (a document colour of the same name
    updates that entry's data in place, keeping the low number, rather
    than creating a new one), and every other colour -- named, or an
    inline value with no name of its own -- is numbered from COL_20 up,
    in first-reference order."""

    def __init__(self) -> None:
        self._entries: dict[str, _DDLColourEntry] = {
            e.name: e for e in _default_colour_entries()
        }
        self._order: list[str] = [e.name for e in _default_colour_entries()]
        self._next_custom = 0x20

    def add(self, colour: Colour) -> _DDLColourEntry:
        key = _synthetic_colour_key(colour)
        existing = self._entries.get(key)
        if existing is not None:
            updated = dataclasses.replace(
                existing,
                model=colour.model,
                values=colour.values,
                spot=not colour.process,
                overprint=colour.overprint,
            )
            self._entries[key] = updated
            return updated

        entry = _DDLColourEntry(
            n=self._next_custom,
            name=key,
            model=colour.model,
            values=colour.values,
            spot=not colour.process,
            overprint=colour.overprint,
        )
        self._next_custom += 1
        self._entries[key] = entry
        self._order.append(key)
        return entry

    def reference(
        self, colour: Optional[Colour], overprint: int = 0, transparent: bool = False
    ) -> str:
        if transparent or colour is None:
            return f"COL_{TRANSPARENT_COLOUR_N:02x} 0x10000 {overprint}"
        entry = self.add(colour)
        return f"COL_{entry.n:02x} 0x10000 {overprint}"

    def render(self) -> str:
        lines = ["// Colours", ""]
        for key in self._order:
            lines.append(_render_colour_entry(self._entries[key]))
        lines.append("")
        return "\n".join(lines)


def _render_colour_entry(entry: _DDLColourEntry) -> str:
    parts = [f'COL_{entry.n:02x}={{colour "{_quote(entry.name)}" ']
    if entry.model is ColourModel.RGB:
        r, g, b = entry.values
        parts.append(f"{{rgb 0x{r:x} 0x{g:x} 0x{b:x}}}")
    elif entry.model is ColourModel.HSV:
        h, s, v = entry.values
        parts.append(f"{{hsv 0x{h:x} 0x{s:x} 0x{v:x}}}")
    elif entry.model is ColourModel.CMYK:
        c, m, y, k = entry.values
        parts.append(f"{{cmyk 0x{c:x} 0x{m:x} 0x{y:x} 0x{k:x}}}")
    if entry.transparent:
        parts.append("{transparent 1}")
    if entry.no_print:
        parts.append("{noprint  1}")
    if entry.locked:
        parts.append("{lock 1}")
    if entry.spot:
        parts.append("{spot 1}")
    if entry.overprint:
        parts.append("{overprint 1}")
    parts.append(f"{{overprintlimit 0x{entry.overprint_limit:x}}}")
    parts.append("}")
    return "".join(parts)


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

#: Matches keystring() in c/styles.c; see docs/impression-documents.xml,
#: "Keyboard Shortcut Encoding".
_NAMED_KEYS = {
    0x180: "Print", 0x190: "S_Print", 0x1A0: "C_Print", 0x1B0: "CS_Print",
    0x18C: "CLeft", 0x18D: "CRight", 0x18E: "CDown", 0x18F: "CUp",
    0x19C: "S_CLeft", 0x19D: "S_CRight", 0x19E: "S_CDown", 0x19F: "S_CUp",
    0x1AC: "C_CLeft", 0x1AD: "C_CRight", 0x1AE: "C_CDown", 0x1AF: "C_CUp",
    0x1BC: "CS_CLeft", 0x1BD: "CS_CRight", 0x1BE: "CS_CDown", 0x1BF: "CS_CUp",
    0x18B: "Copy", 0x19B: "S_Copy", 0x1AB: "C_Copy", 0x1BB: "CS_Copy",
    0x18A: "Tab", 0x19A: "S_Tab", 0x1AA: "C_Tab", 0x1BA: "CS_Tab",
    0x1CD: "Insert", 0x1DD: "S_Insert", 0x1ED: "C_Insert", 0x1FD: "CS_Insert",
}
_FKEY_RANGES = [
    (0x181, 0x189, "", 1), (0x191, 0x199, "S_", 1),
    (0x1A1, 0x1A9, "C_", 1), (0x1B1, 0x1B9, "CS_", 1),
    (0x1CA, 0x1CC, "", 10), (0x1DA, 0x1DC, "S_", 10),
    (0x1EA, 0x1EC, "C_", 10), (0x1FA, 0x1FC, "CS_", 10),
]


def _keystring(key: int) -> str:
    if key == 0:
        return ""
    if 1 <= key <= 0x1A:
        return f"C_{chr(0x40 + key)}"
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    for lo, hi, prefix, base in _FKEY_RANGES:
        if lo <= key <= hi:
            return f"{prefix}F{key - lo + base}"
    return ""


def _tab_field(tab) -> str:
    if tab.kind == 0:
        return f"{{lefttab {tab.position}}}\n"
    if tab.kind == 1:
        return f"{{centretab {tab.position}}}\n"
    if tab.kind == 2:
        return f"{{righttab {tab.position}}}\n"
    if tab.kind == 3:
        return f"{{decimaltab {tab.position}}}\n"
    return f"{{ruleposn {tab.position}}}\n"


def style_ddl_id(style: Style) -> str:
    """Matches BODYSEQ (0x100) + slot number in c/styles.c."""
    return f"STYLE_{0x100 + style.index:02x}"


def _underline_colour_reference(
    style: Style, document_colours: list[Colour], ddl_colours: DDLColourTable
) -> str:
    if style.underline_colour_word is not None:
        colour = style.underline_colour(document_colours)
        if colour is not None:
            return ddl_colours.reference(colour)
    # The conversion source reads this from a variable that is only
    # assigned when underline_colour_word is present; when it isn't, the
    # value used is whatever was already on the stack, not a defined
    # default. Black is used here instead, since that's what a
    # zero-valued colour word decodes to, and is a far more sensible
    # rendering than the alternative of leaving it undefined.
    return "COL_01 0x10000 0"


def render_style(
    style: Style, document_colours: list[Colour], ddl_colours: DDLColourTable
) -> str:
    """Matches expandstyle() in c/styles.c."""
    parts = []
    name = "BodyText" if style.is_body_text else _quote(style.name)
    parts.append(f'{style_ddl_id(style)}={{style "{name}"\n')
    parts.append(f'{{keypress "{_quote(_keystring(style.key))}"}}\n')

    if style.alignment is not None:
        parts.append(f"{{align {style.alignment}}}\n")
    if style.hyphenation is not None:
        parts.append(
            f"{{hyphenation {style.hyphenation} {{hpminsize 5}} {{hpminbefore 2}} "
            f"{{hpminafter 2}} {{hpmaxconsecutive 3}} {{hpzone 0}} {{hpbreakpara 0}} "
            f"{{hpbreakcaps 0}}}}\n"
        )
    if style.lock_to_grid is not None:
        parts.append(f"{{baselinelock {style.lock_to_grid}}}\n")
    if style.left_indent is not None:
        parts.append(f"{{leftindent 1 {style.left_indent}}}\n")
    if style.right_indent_raw is not None:
        kind = 2 if style.right_indent_is_delta else 0
        parts.append(f"{{rightindent {kind} {style.right_indent}}}\n")
    if style.first_indent is not None:
        parts.append(f"{{firstindent {style.first_indent}}}\n")
    if style.line_spacing_raw is not None:
        if style.line_spacing_is_fixed:
            parts.append(f"{{leading 2 0x{style.line_spacing & 0xFFFFFF:x}}}\n")
        else:
            parts.append(f"{{leading 1 {style.line_spacing}}}\n")
    if style.space_after is not None:
        parts.append(f"{{spaceafter {style.space_after}}}\n")
    if style.space_before is not None:
        parts.append(f"{{spacebefore {style.space_before}}}\n")
    if style.font_size is not None:
        parts.append(f"{{textsize {(1000 * style.font_size) // 16}}}\n")
    if style.font_aspect_ratio is not None:
        parts.append(f"{{scale 0x{style.font_aspect_ratio & 0xFFFFFFFF:x}}}\n")
    if style.tracking is not None:
        parts.append(f"{{track {style.tracking}}}\n{{baseshift 0}}\n")

    if style.tab_stops:
        leader = style.leader or ""
        parts.append(f'{{tabruler {{tableader "{_quote(leader)}"}}\n')
        if style.decimal_tab is not None:
            char = chr(style.decimal_tab) if style.decimal_tab else ""
            parts.append(f'{{tabdec "{_quote(char)}"}}\n')
        for tab in style.tab_stops:
            parts.append(_tab_field(tab))
        parts.append("}\n")

    if style.font_style_name is not None:
        parts.append(f'{{fontstyle "{_quote(style.font_style_name)}"}}\n')

    if style.bold is not None:
        parts.append(f"{{bold  {style.bold}}}\n")
    if style.italic is not None:
        parts.append(f"{{italic {style.italic}}}\n")

    parts.append(f"{{scope {1 if style.paragraph_apply else 0}}}\n")

    if style.foreground_colour_word is not None:
        fg = style.foreground_colour(document_colours)
        parts.append(f"{{foreground {ddl_colours.reference(fg)}}}")
    if style.background_colour_word is not None:
        bg = style.background_colour(document_colours)
        if bg is not None:
            parts.append(f"{{background {ddl_colours.reference(bg)}}}")

    if style.underline is not None:
        ref = _underline_colour_reference(style, document_colours, ddl_colours)
        parts.append(
            f"{{underline {style.underline} {{type 1}} {{thickness 1310}} "
            f"{{colourvalue {ref}}} {{wordunderline 0}} {{doubleunderline 0}}}}\n"
        )
    if style.strikeout is not None:
        ref = _underline_colour_reference(style, document_colours, ddl_colours)
        parts.append(
            f"{{strikeout {style.strikeout} {{type 1}} {{thickness 1310}} "
            f"{{colourvalue {ref}}}}}\n"
        )

    if style.is_body_text:
        parts.append("{reverse 0}\n")
        parts.append(
            "{verticalrule {leftrule 0 0} {rightrule 0 0} {thickness 0} "
            "{colourvalue COL_01 0x10000 0}}\n"
        )
        parts.append(
            "{horizontalrule {aboverule 0 0} {belowrule 0 0} {thickness 0} "
            "{colourvalue COL_01 0x10000 0}}\n"
        )
        parts.append("{case 0}\n")
        parts.append("{drop 0 3}\n")
        parts.append(
            '{bullet 0 {bulletbefore ""} {bulletafter ""} {bulletstyle ""} '
            "{bullettab 1} {bulletstart 1}{bulletformat 0}}\n"
        )
        parts.append("{autokern 0}\n")
        parts.append("{wordwrap 1}\n")
        parts.append("{level 0}\n")
        parts.append("{smallcaps 0}\n")
        parts.append("{language 0}\n")
        parts.append("{angle 0x0}\n")
        parts.append("{skew 0x0}\n")
        parts.append(
            "{justification 0 {justminletter 0x0} {justmaxletter 0x4000} "
            "{justminword 0xc000} {justmaxword 0x14000} {justflushzone 0} "
            "{justsinglewords 0}}\n"
        )

    parts.append("}\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Frame geometry
# ---------------------------------------------------------------------------


def _outsets(frame: Frame) -> tuple[int, int, int, int]:
    left = frame.x0 - frame.exx0
    right = frame.exx1 - frame.x1
    top = frame.exy1 - frame.y1
    bottom = frame.y0 - frame.exy0
    return left, right, top, bottom


def _border_mask(frame: Frame) -> int:
    # Matches the (border0,border2,border3,border1) -> bit0..3 reordering in
    # ixtext()/ixpict()/ixblank(); see "Frame object common layout".
    return (
        (frame.border0 != 0xFF)
        | ((frame.border2 != 0xFF) << 1)
        | ((frame.border3 != 0xFF) << 2)
        | ((frame.border1 != 0xFF) << 3)
    )


def _border_field(
    frame: Frame, document_colours: list[Colour], ddl_colours: DDLColourTable, border_width: int
) -> str:
    if not frame.has_border:
        return ""
    mask = _border_mask(frame)
    border_colour = frame.border_colour(document_colours)
    ref = ddl_colours.reference(border_colour) if border_colour is not None else "COL_01 0x10000 0"
    return (
        f'{{frameborder "1_Plain" {{border 1 0x{mask:x} {{colourvalue {ref}}} '
        f"{{w {border_width}}}}}{{shadow 0 0x3 {{colourvalue COL_01 0x10000 0}} {{w 5669}}}}}}\n"
    )


def _box_field(tag: str, origin, x0: int, y0: int, x1: int, y1: int) -> str:
    px, py = to_page_coordinates(origin, x0, y1)
    return f"{{{tag} {{box {{x {px}}}\n{{y {py}}}\n{{w {x1 - x0}}}\n{{h {y1 - y0}}}\n}}}}\n"


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


class OvProDDLConverter(Converter):
    """Ports the DDL emission in c/main, c/colours, c/frames, and
    c/styles onto the decoded document model. See the module docstring
    for the two things that stop this from being byte-for-byte identical
    to the original tool's output."""

    def __init__(
        self,
        document,
        log=None,
        strict: bool = False,
        border_width: int = 0,
    ):
        super().__init__(document, log=log, strict=strict)
        # The original reads this from an external config file
        # (<TransIMP$Dir>.transimp), not from the document itself.
        self.border_width = border_width
        self.colours = DDLColourTable()
        self._style_by_slot = {style.index: style for style in document.styles}
        self._embedded_definitions: dict[int, str] = {}
        self._picture_definitions: dict[int, str] = {}
        self._merge_definitions: list[tuple[int, str]] = []
        self._merge_seq = 1
        self._rendered_stories: set[int] = set()
        self._dictionary_by_index = {entry.index: entry for entry in document.dictionary}

    # -- Top level ---------------------------------------------------------

    def convert(self, output_path: Path) -> None:
        style_text = self._render_styles()

        chapter_text = []
        for chapter_number, chapter in enumerate(self.document.chapters, start=1):
            with self.catch("chapter", location=f"chapter {chapter.section.create_number}"):
                chapter_text.append(
                    self._render_chapter(chapter, chapter_number, epoch_index=chapter_number - 1)
                )

        out = ["//->DDLFile\n// Produced by riscos-impression\n//\n\n", "DOC={document}\n\n"]
        out.append(self.colours.render())
        out.append("\n")
        out.append(style_text)
        out.append("\n")
        for seq, field_name in self._merge_definitions:
            out.append(
                f'MERGE_{seq}={{merge {seq} "{seq}" "{{macv=impulse(\\"{_quote(field_name)}\\")}}"}}\n'
            )
        if self._merge_definitions:
            out.append("\n")
        out.extend(chapter_text)

        # OvationPro DDL is a RISC OS-native format, read by a RISC OS-
        # native importer -- write it as RISC OS Latin1 (alphabet 101;
        # see encoding.py), not the platform's own default text encoding
        # (which would be UTF-8 on most systems this runs on, but isn't
        # what a real OvationPro/TransIMP expects).
        Path(output_path).write_bytes(encoding.encode("".join(out)))

    def _render_styles(self) -> str:
        parts = ["// Styles\n\n"]
        for style in self.document.styles:
            parts.append(render_style(style, self.document.colours, self.colours))
            parts.append("\n")
        return "".join(parts)

    # -- Frame identifiers ---------------------------------------------------

    def _frame_number(self, offset: int, master: bool, epoch_index: int) -> int:
        """Matches epoch() in c/frames.c."""
        if master:
            return offset + epoch_index * self.document.header.contents2
        return offset

    # -- Chapters and pages --------------------------------------------------

    def _render_chapter(self, chapter: Chapter, chapter_number: int, epoch_index: int) -> str:
        section = chapter.section
        seq = self._frame_number(chapter.offset, True, epoch_index)
        mpage1 = chapter.master_page_1
        mpage2 = chapter.master_page_2

        parts = []
        if mpage1 is None:
            self.log.error(
                "chapter", "no master page found", location=f"chapter {section.create_number}"
            )
            return ""

        page = mpage1.page
        parts.append(
            f'CHAP_{seq:x}={{chapter {{papersize "A4" 1 {{w {page.x1 - page.x0 - 2 * page.bleed}}} '
            f"{{h {page.y1 - page.y0 - 2 * page.bleed}}}{{sideways 0}}}}\n"
        )
        parts.append("{omitheader 0} {omitfooter 0}\n")
        parts.append(f"{{chapternumber {chapter_number}}}\n")
        parts.append("{pagenumberformat 2}\n")
        parts.append(f"{{startonleft {0 if section.start_on_right else 1}}}\n")
        if section.override_start_page:
            parts.append(f"{{pagestartnumber {section.start_page_number}}}\n")
        parts.append("}\n\n")

        parts.append(f"\nPAGE_{seq + 1:x}={{masterpage {{colourvalue COL_02 0x10000 0}}}}\n\n")
        parts.append(self._render_page(mpage1, chapter, master=True, epoch_index=epoch_index))
        if mpage2 is not None:
            parts.append(f"\nPAGE_{seq + 2:x}={{masterpage {{colourvalue COL_02 0x10000 0}}}}\n\n")
            parts.append(self._render_page(mpage2, chapter, master=True, epoch_index=epoch_index))

        for page_group in chapter.pages:
            parts.append(
                self._render_page(page_group, chapter, master=False, epoch_index=epoch_index)
            )

        return "".join(parts)

    def _render_page(
        self, page: PageGroup, chapter: Chapter, master: bool, epoch_index: int
    ) -> str:
        parts = []
        if not master:
            number = self._frame_number(page.offset, False, epoch_index)
            parts.append(f"\nPAGE_{number:x}={{page}}\n\n")

        origin = page_origin(page.page)
        for record in page.records:
            frame = record.value
            if not isinstance(frame, Frame):
                continue
            if frame.embed_tag:
                continue  # placed inline in its story instead; see _render_embed_reference
            with self.catch(
                "frame", location=f"chapter {chapter.section.create_number} @0x{record.offset:x}"
            ):
                parts.append(
                    self._render_frame(record, chapter, page, master, epoch_index, generation=0)
                )
        return "".join(parts)

    # -- Frame dispatch -------------------------------------------------------

    def _ddl_id(self, prefix: str, offset: int, master: bool, epoch_index: int, generation: int = 0) -> str:
        number = self._frame_number(offset, master, epoch_index)
        if generation:
            return f"{prefix}_{generation:x}_{number:x}"
        return f"{prefix}_{number:x}"

    def _render_frame(
        self,
        record,
        chapter: Chapter,
        page: PageGroup,
        master: bool,
        epoch_index: int,
        generation: int,
    ) -> str:
        frame = record.value
        if isinstance(frame, (TextFrame, BlankFrame)):
            return self._render_text_frame(
                frame, record.offset, chapter, page, master, epoch_index, generation
            )
        if isinstance(frame, PictureFrame):
            return self._render_picture_frame(
                frame, record.offset, chapter, page, master, epoch_index, generation
            )
        if isinstance(frame, GuideFrame):
            return self._render_guide_frame(frame, record.offset, chapter, page, master, epoch_index)
        if isinstance(frame, GroupFrame):
            self.log.best_effort(
                "frame", "group frame membership not re-derived; group emitted with no members"
            )
            number = self._frame_number(record.offset, master, epoch_index)
            return f"\nGROUP_{number:x}={{group}}\n\n"
        return ""

    # -- Text and blank frames ------------------------------------------------

    def _render_text_frame(
        self,
        frame: Union[TextFrame, BlankFrame],
        offset: int,
        chapter: Chapter,
        page: PageGroup,
        master: bool,
        epoch_index: int,
        generation: int,
    ) -> str:
        ddl_id = self._ddl_id("TEXT", offset, master, epoch_index, generation)

        if not master and frame.master:
            master_record = self.master_frame(page, frame)
            if master_record is None:
                self.log.error("frame", f"master frame not found for {ddl_id}")
                return ""
            master_id = self._ddl_id("TEXT", master_record.offset, True, epoch_index)
            text = f"{ddl_id}={{textframe\n{{master {master_id}}}{{local 0}}\n}}\n\n"
            return text + self._render_story_for_frame(
                frame, offset, chapter, page, master, epoch_index, generation
            )

        left, right, top, bottom = _outsets(frame)
        origin = page_origin(page.page)
        fill = frame.fill_colour(self.document.colours) if frame.filled else None

        parts = [f"{ddl_id}={{textframe"]
        parts.append(f"{{lock {1 if frame.locked else 0}}} {{spread 0}}\n")
        parts.append(
            f"{{fillcolour {self.colours.reference(fill, overprint=int(frame.overprint), transparent=not frame.filled)}}}\n"
        )
        parts.append("{radius 0}\n")
        parts.append("{columns 1}{gutterwidth 14173}")
        parts.append("{columnguides 0 {thickness 2834} {colourvalue COL_01 0x10000 0}}\n")
        parts.append("{vertalign 0}\n")
        parts.append("{firstline 1}\n")
        parts.append(_border_field(frame, self.document.colours, self.colours, self.border_width))
        parts.append(f"{{inset {max(0, min(frame.hinset, frame.vinset))}}}\n")
        parts.append(
            f"{{textflow 3 {1 if frame.repel else 0} "
            f"{max(0, min(min(top, bottom), min(left, right)))}}}\n"
        )
        parts.append(_box_field("fcurve", origin, frame.x0, frame.y0, frame.x1, frame.y1))
        if frame.hinset != frame.vinset or frame.hinset < 0:
            parts.append(
                _box_field(
                    "icurve",
                    origin,
                    frame.x0 + frame.hinset,
                    frame.y0 + frame.vinset,
                    frame.x1 - frame.hinset,
                    frame.y1 - frame.vinset,
                )
            )
        if left != right or left != top or left != bottom or left < 0:
            parts.append(_box_field("ocurve", origin, frame.exx0, frame.exy0, frame.exx1, frame.exy1))
        parts.append("{angle 0x0}\n{skew 0x0}\n}\n\n")

        text = "".join(parts)
        return text + self._render_story_for_frame(
            frame, offset, chapter, page, master, epoch_index, generation
        )

    # -- Picture frames ---------------------------------------------------------

    def _render_picture_data(self, pict: PictureFrame) -> str:
        bits = _picture_transform_bits(pict.angle, pict.xscale, pict.yscale)
        aspect = _scale(0x10000, bits.xscale, bits.yscale) if bits.yscale else 0x10000
        skew = int((180 * math.atan(bits.skewxy / 0x10000) / math.pi) * 0x10000)
        return (
            "{picturedata\n{lockmode 0}\n{aspectlock 1}\n{autoscale 0}\n{hide 0}\n"
            "{bottomleft 1}\n"
            f"{{x {-pict.xshift}}}\n{{y {pict.yshift}}}\n"
            f"{{scale 0x{bits.yscale & 0xFFFFFFFF:x}}}\n"
            f"{{aspect 0x{aspect & 0xFFFFFFFF:x}}}\n"
            f"{{angle 0x{bits.angle & 0xFFFFFFFF:x}}}\n"
            f"{{skew 0x{skew & 0xFFFFFFFF:x}}}\n"
            "{tile 0 0 0}}\n"
        )

    def _render_irregular_boundary(self, pict: PictureFrame, origin) -> str:
        cx = (pict.x0 - origin.x + pict.x1 - origin.x) // 2
        cy = (origin.y - pict.y1 + origin.y - pict.y0) // 2
        parts = ["{fcurve {path \n"]
        for op in pict.boundary:
            if op.code.name == "END":
                parts.append("{end}\n")
            elif op.code.name == "MOVE":
                parts.append(f"{{move {cx + op.x} {cy - op.y}}}\n")
            elif op.code.name == "CLOSE":
                parts.append("{close}\n")
            elif op.code.name == "DRAW":
                parts.append(f"{{draw {cx + op.x} {cy - op.y}}}\n")
            # CURVE is recognised but not decoded; see model.frames.PathOpCode.
        parts.append("}}\n")
        return "".join(parts)

    def _render_picture_frame(
        self,
        pict: PictureFrame,
        offset: int,
        chapter: Chapter,
        page: PageGroup,
        master: bool,
        epoch_index: int,
        generation: int,
    ) -> str:
        ddl_id = self._ddl_id("PICT", offset, master, epoch_index, generation)

        if not master and pict.master:
            master_record = self.master_frame(page, pict)
            if master_record is None:
                self.log.error("frame", f"master frame not found for {ddl_id}")
                return ""
            master_id = self._ddl_id("PICT", master_record.offset, True, epoch_index)
            master_is_pict = isinstance(master_record.value, PictureFrame)
            parts = [f"{ddl_id}={{pictframe\n{{master {master_id}}}{{local 0}}\n"]
            if not master_is_pict:
                parts.append(self._render_picture_data(pict))
            parts.append("}\n\n")
            if master_is_pict:
                parts.append(
                    self._render_picture_reference(pict, offset, chapter, page, master, epoch_index)
                )
            return "".join(parts)

        left, right, top, bottom = _outsets(pict)
        origin = page_origin(page.page)
        fill = pict.fill_colour(self.document.colours) if pict.filled else None

        parts = [f"{ddl_id}={{pictframe"]
        parts.append(f"{{lock {1 if pict.locked else 0}}} {{spread 0}}\n")
        parts.append(
            f"{{fillcolour {self.colours.reference(fill, overprint=int(pict.overprint), transparent=not pict.filled)}}}\n"
        )
        parts.append("{radius 0}\n")
        parts.append(_border_field(pict, self.document.colours, self.colours, self.border_width))
        parts.append(f"{{inset {min(pict.hinset, pict.vinset)}}}\n")
        parts.append(
            f"{{textflow 3 {1 if pict.repel else 0} "
            f"{max(0, min(min(top, bottom), min(left, right)))}}}\n"
        )
        if pict.boundary:
            parts.append(self._render_irregular_boundary(pict, origin))
        else:
            parts.append(_box_field("fcurve", origin, pict.x0, pict.y0, pict.x1, pict.y1))
        if pict.hinset != pict.vinset:
            parts.append(
                _box_field(
                    "icurve",
                    origin,
                    pict.x0 + pict.hinset,
                    pict.y0 + pict.vinset,
                    pict.x1 - pict.hinset,
                    pict.y1 - pict.vinset,
                )
            )
        if not pict.boundary and (left != right or left != top or left != bottom or left < 0):
            parts.append(_box_field("ocurve", origin, pict.exx0, pict.exy0, pict.exx1, pict.exy1))
        parts.append("{angle 0x0}\n{skew 0x0}\n")
        parts.append(self._render_picture_data(pict))
        parts.append("}\n\n")

        text = "".join(parts)
        return text + self._render_picture_reference(pict, offset, chapter, page, master, epoch_index)

    def _render_picture_reference(
        self, pict: PictureFrame, offset: int, chapter: Chapter, page: PageGroup, master: bool, epoch_index: int
    ) -> str:
        """Matches ixpictpicture(): the first frame referencing a given
        dictionary entry emits the picture's actual data; later frames
        referencing the same entry just point at the first one."""
        if pict.dictionary_index < 0:
            return ""
        ddl_id = self._ddl_id("PICT", offset, master, epoch_index)
        existing = self._picture_definitions.get(pict.dictionary_index)
        if existing is not None:
            return f"PICTURE_{ddl_id[5:]}=PICTURE_{existing[5:]}\n"

        picture_tag = f"PICTURE_{ddl_id[5:]}"
        self._picture_definitions[pict.dictionary_index] = picture_tag
        entry = self._dictionary_by_index.get(pict.dictionary_index)
        if entry is None:
            self.log.error("picture", f"no dictionary entry for index {pict.dictionary_index}")
            return ""

        with self.catch("picture", location=f"dictionary entry {entry.index}"):
            data = self.document.picture_bytes(entry)
        embedded_type = entry.embedded_object_type
        kind = embedded_type.value if embedded_type is not None else "data"
        self.log.best_effort(
            "picture",
            f"{kind} picture referenced but not decoded/embedded by this converter "
            f"(stub decoders exist in formats/, but DDL {{data ...}} embedding is not "
            f"implemented yet)",
        )
        return ""

    # -- Guide lines --------------------------------------------------------

    def _render_guide_frame(
        self, guide: GuideFrame, offset: int, chapter: Chapter, page: PageGroup, master: bool, epoch_index: int
    ) -> str:
        base = self._frame_number(offset, master, epoch_index)

        if not master and guide.master:
            master_record = self.master_frame(page, guide)
            if master_record is None:
                self.log.error("frame", f"master frame not found for GLINE_{base:x}")
                return ""
            master_base = self._frame_number(master_record.offset, True, epoch_index)
            parts = []
            for i in range(4):
                parts.append(
                    f"GLINE_{base + i:x}={{pageguideline\n"
                    f"{{master GLINE_{master_base + i:x}}}{{local 0}}\n}}\n\n"
                )
            return "".join(parts)

        origin = page_origin(page.page)
        edges = [
            ("x", guide.x0 - origin.x),
            ("x", guide.x1 - origin.x),
            ("y", origin.y - guide.y0),
            ("y", origin.y - guide.y1),
        ]
        parts = []
        for i, (axis, value) in enumerate(edges):
            parts.append(
                f"GLINE_{base + i:x}={{pageguideline{{lock {1 if guide.locked else 0}}} "
                f"{{spread 0}}\n{{{axis} {value}}}}}\n\n"
            )
        return "".join(parts)

    # -- Text stories ----------------------------------------------------------

    def _render_story_for_frame(
        self,
        frame: Union[TextFrame, BlankFrame],
        offset: int,
        chapter: Chapter,
        page: PageGroup,
        master: bool,
        epoch_index: int,
        generation: int,
    ) -> str:
        if frame.dictionary_index < 0:
            return ""
        entry = self._dictionary_by_index.get(frame.dictionary_index)
        if entry is None:
            return ""
        if entry.type is not DictionaryEntryType.TEXT:
            # A blank frame's linked dictionary entry may turn out to be a
            # picture instead of a text story, once instantiated via its
            # master; see "Text, blank, guide, and group frames". Nothing
            # to attach here in that case -- the frame's own DDL was
            # already emitted as a plain textframe, matching ixblank(),
            # which doesn't render picture content for a blank frame either.
            return ""
        if entry.index in self._rendered_stories:
            return ""
        self._rendered_stories.add(entry.index)

        story = None
        with self.catch("story", location=f"dictionary entry {entry.index}"):
            story = self.document.story(entry)
        if story is None:
            return ""

        number = self._frame_number(offset, master, epoch_index)
        story_id = f"STORY_{generation:x}_{number:x}" if generation else f"STORY_{number:x}"
        parts = [self._render_story_text(story, story_id, entry.index, chapter, page, master, epoch_index)]

        chain = self.resolve_frame_chain(
            story, chapter=(None if master else chapter), master=master
        )
        if len(chain) > 1:
            chain_id = f"CHAIN_{generation:x}_{number:x}" if generation else f"CHAIN_{number:x}"
            refs = " ".join(
                self._ddl_id("TEXT", record.offset, master, epoch_index) for record in chain
            )
            parts.append(f"{chain_id}={{textchain {refs}}}\n\n")

        return "".join(parts)

    def _render_story_text(
        self,
        story: Story,
        story_id: str,
        dictionary_index: int,
        chapter: Chapter,
        page: PageGroup,
        master: bool,
        epoch_index: int,
    ) -> str:
        body_style = self._style_by_slot.get(0)
        parts = [f"{story_id}={{story\n"]
        if body_style is not None:
            parts.append(f"{{adduserstyle {style_ddl_id(body_style)}}}\n")

        active_slots: tuple = ()
        buffer: list = []

        def flush_text() -> None:
            if buffer:
                escaped = "".join(buffer).replace("\\", "\\\\").replace('"', '\\"')
                parts.append(f'"{escaped}"\n')
                buffer.clear()

        for index, paragraph in enumerate(story.paragraphs):
            if index > 0:
                flush_text()
                parts.append("{newpara}\n")
            for item in paragraph.items:
                if isinstance(item, Run):
                    if item.style_slots != active_slots:
                        flush_text()
                        for slot in reversed(active_slots):
                            style = self._style_by_slot.get(slot)
                            if style is not None:
                                parts.append(f"{{remuserstyle {style_ddl_id(style)}}}\n")
                        for slot in item.style_slots:
                            style = self._style_by_slot.get(slot)
                            if style is not None:
                                parts.append(f"{{adduserstyle {style_ddl_id(style)}}}\n")
                        active_slots = item.style_slots
                    buffer.append(item.text)
                elif isinstance(item, PageNumberMark):
                    flush_text()
                    parts.append("{pageno}\n")
                elif isinstance(item, ChapterNumberMark):
                    flush_text()
                    parts.append("{chapterno}\n")
                elif isinstance(item, HeadingNumberMark):
                    flush_text()
                    parts.append(self._render_number(item.tag, dictionary_index))
                elif isinstance(item, TabMark):
                    flush_text()
                    parts.append("{tab}\n")
                elif isinstance(item, PageBreakMark):
                    flush_text()
                    parts.append("{newpage}\n")
                elif isinstance(item, EmbedMark):
                    flush_text()
                    parts.append(
                        self._render_embed_reference(item.embed_tag, chapter, page, master, epoch_index)
                    )
                elif isinstance(item, MergeMark):
                    flush_text()
                    parts.append(self._render_merge_reference(item.field_name))
            flush_text()

        parts.append("{endoftext}\n")
        parts.append("}\n\n")
        return "".join(parts)

    def _render_number(self, tag: int, dictionary_index: int) -> str:
        record = next(
            (
                r
                for r in self.document.numbering
                if r.dictionary_index == dictionary_index and r.tag == tag
            ),
            None,
        )
        if record is None:
            self.log.error("numbering", f"no numbering record for tag {tag}")
            return ""
        from riscos_impression.model.numbering import NumberingStyle, resolve_number

        if record.style is not NumberingStyle.DECIMAL:
            self.log.unsupported(
                "numbering",
                f"{record.style.name if record.style else record.raw_style} numbering "
                f"style not implemented; only decimal is (matches the conversion source's "
                f"own gap, not just this converter's)",
            )
            return ""
        value = resolve_number(self.document.numbering, dictionary_index, tag)
        return f'"{value}"\n'

    def _render_embed_reference(
        self, embed_tag: int, chapter: Chapter, page: PageGroup, master: bool, epoch_index: int
    ) -> str:
        if embed_tag in self._embedded_definitions:
            return f"{{embed {self._embedded_definitions[embed_tag]}}}\n"

        # embedframe() in c/frames.c searches the whole of the current
        # object-record stream (every page of the current chapter, or
        # every master page, not just the one page currently being
        # emitted), on the basis that embed tags are unique across that
        # scope; see "Frame object common layout".
        search_pages = self.document.master_pages if master else chapter.pages
        match = None
        match_page = None
        for candidate_page in search_pages:
            for record in candidate_page.records:
                if isinstance(record.value, Frame) and record.value.embed_tag == embed_tag:
                    match = record
                    match_page = candidate_page
                    break
            if match is not None:
                break

        if match is None:
            self.log.error("story", f"embedded frame tag {embed_tag} not found")
            return ""

        ddl_id = self._ddl_id(
            "PICT" if isinstance(match.value, PictureFrame) else "TEXT",
            match.offset,
            master,
            epoch_index,
        )
        self._embedded_definitions[embed_tag] = ddl_id
        # Render the embedded frame's own definition inline, at the point
        # it's first referenced, matching embedframe()'s behaviour.
        definition = self._render_frame(match, chapter, match_page, master, epoch_index, generation=0)
        return definition + f"{{embed {ddl_id}}}\n"

    def _render_merge_reference(self, field_name: str) -> str:
        seq = self._merge_seq
        self._merge_seq += 1
        self._merge_definitions.append((seq, field_name))
        return f"{{merge {seq}}}\n"
