"""The character/paragraph style table.

See docs/impression-documents.xml, "Character and Paragraph Styles". This
is the most intricate part of the on-disk format: a style record's
variable-length body is a sequence of optional fields whose presence is
controlled by two 32-bit flag words, almost all of them also forced
"present" when decoding the reserved body-text style (slot 0). Getting
the presence rules and field order exactly right matters more here than
almost anywhere else in this package: a single misjudged field silently
misaligns every field read after it for the rest of the record.

The decoder below walks fields in precisely the order and with precisely
the presence rules used by the conversion source's own stylecolours() and
expandstyle() (c/styles.c), including its one genuine asymmetry: the
foreground/background trailing colour words are also read for the
body-text style even when their own flag bit is clear, but the
underline/strikeout trailing colour word is not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Optional

from riscos_impression import binary
from riscos_impression.model.colours import Colour, decode_colour_word

STYLESTR_HEADER_SIZE = 56
STYLE_TABLE_SLOTS = 255


@dataclass(frozen=True)
class TabStop:
    kind: int  #: 0=left, 1=centre, 2=right, 3=decimal, other=rule-line marker
    position: int


class _Cursor:
    """A simple read position into a style record's variable-length body,
    used to keep the long, strictly-ordered walk below readable."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos

    def byte(self) -> int:
        value = binary.u8(self.data, self.pos)
        self.pos += 1
        return value

    def word(self) -> int:
        value = binary.u32(self.data, self.pos)
        self.pos += 4
        return value

    def sword(self) -> int:
        value = binary.s32(self.data, self.pos)
        self.pos += 4
        return value

    def take(self, n: int) -> bytes:
        value = self.data[self.pos : self.pos + n]
        self.pos += n
        return value

    def align4(self) -> None:
        self.pos = (self.pos + 3) & ~3


@dataclass(frozen=True)
class Style:
    """A single decoded character/paragraph style.

    Every field below that comes from the variable-length body is
    ``None`` when this style doesn't carry it (its governing presence-flag
    bit was clear, and this isn't the body-text style). The body-text
    style (index 0) has almost every field forced present; see the module
    docstring for its one exception (underline_colour_word).
    """

    index: int
    is_body_text: bool
    name: str
    key: int
    paragraph_apply: bool
    is_contents_entry_style: bool
    is_index_entry_style: bool
    is_effect: bool
    shows_on_style_menu: bool
    tabs: int

    # One-byte fields, in on-disk order.
    auto_indent: Optional[int] = None
    font_name_selector0: Optional[int] = None
    underline: Optional[int] = None
    script: Optional[int] = None
    strikeout: Optional[int] = None
    alignment: Optional[int] = None
    keep_single: Optional[int] = None
    keep_multiple: Optional[int] = None
    hyphenation: Optional[int] = None
    decimal_tab: Optional[int] = None
    keep_next: Optional[int] = None
    lock_to_grid: Optional[int] = None
    rule_off_0: Optional[int] = None
    italic: Optional[int] = None
    bold: Optional[int] = None

    # Four-byte fields, in on-disk order.
    left_indent: Optional[int] = None
    right_indent_raw: Optional[int] = None
    first_indent_absolute: Optional[int] = None
    script_offset: Optional[int] = None
    script_size: Optional[int] = None
    line_spacing_raw: Optional[int] = None
    space_after: Optional[int] = None
    space_before: Optional[int] = None
    underline_offset_0: Optional[int] = None
    underline_offset_1: Optional[int] = None
    rule_vertical_width: Optional[int] = None
    font_size: Optional[int] = None
    font_aspect_ratio: Optional[int] = None
    keep_together: Optional[int] = None
    leader: Optional[str] = None
    tracking: Optional[int] = None

    # Tab ruler, font name, and trailing colour words.
    tab_stops: tuple[TabStop, ...] = ()
    font_style_name: Optional[str] = None
    foreground_colour_word: Optional[int] = None
    background_colour_word: Optional[int] = None
    underline_colour_word: Optional[int] = None

    #: Fields with no confirmed name or meaning, keyed by their
    #: docs/impression-documents.xml placeholder name, present in this
    #: dict only when this style's data actually carries them.
    unknown: dict = field(default_factory=dict)

    @property
    def first_indent(self) -> Optional[int]:
        """First-line indent, relative to left_indent (matching what the
        conversion source emits), or None if either underlying field is
        absent."""
        if self.first_indent_absolute is None or self.left_indent is None:
            return None
        return self.first_indent_absolute - self.left_indent

    @property
    def right_indent_is_delta(self) -> Optional[bool]:
        if self.right_indent_raw is None:
            return None
        return self.right_indent_raw > 0

    @property
    def right_indent(self) -> Optional[int]:
        if self.right_indent_raw is None:
            return None
        return self.right_indent_raw if self.right_indent_raw > 0 else -self.right_indent_raw

    @property
    def line_spacing_is_fixed(self) -> Optional[bool]:
        if self.line_spacing_raw is None:
            return None
        return bool(self.line_spacing_raw & 0x80000000)

    @property
    def line_spacing(self) -> Optional[int]:
        if self.line_spacing_raw is None:
            return None
        masked = self.line_spacing_raw & 0xFFFFFF
        if self.line_spacing_raw & 0x80000000:
            return masked - 0x10000
        return masked

    def foreground_colour(self, colours: Sequence[Colour]) -> Optional[Colour]:
        if self.foreground_colour_word is None:
            return None
        return decode_colour_word(self.foreground_colour_word, colours)

    def background_colour(self, colours: Sequence[Colour]) -> Optional[Colour]:
        if self.background_colour_word is None:
            return None
        if self.is_effect:
            return None  # effect styles can't set a background colour
        return decode_colour_word(self.background_colour_word, colours)

    def underline_colour(self, colours: Sequence[Colour]) -> Optional[Colour]:
        if self.underline_colour_word is None:
            return None
        return decode_colour_word(self.underline_colour_word, colours)


def _parse_style_header(data: bytes, offset: int) -> dict:
    key_word = binary.u32(data, offset + 20)
    return dict(
        flags1=binary.u32(data, offset + 0),
        flags2=binary.u32(data, offset + 4),
        tabs=binary.u32(data, offset + 8),
        key=binary.bits(key_word, 0, 9),
        b1=binary.bits(key_word, 9, 7),
        b2=binary.bits(key_word, 16, 8),
        xxx624=binary.bit(key_word, 24),
        xxx625=binary.bit(key_word, 25),
        xxx626=binary.bit(key_word, 26),
        xxx627=binary.bit(key_word, 27),
        xxx628=binary.bit(key_word, 28),
        paragraph_apply=binary.bit(key_word, 29),
        is_contents_entry_style=binary.bit(key_word, 30),
        is_index_entry_style=binary.bit(key_word, 31),
        name=binary.cstring(data, offset + 24, 32),
    )


def _decode_style_body(data: bytes, offset: int, index: int, is_body_text: bool) -> Style:
    header = _parse_style_header(data, offset)
    flags1 = header["flags1"]
    flags2 = header["flags2"]
    tabs = header["tabs"]
    is_effect = binary.bit(flags1, 28)
    shows_on_style_menu = binary.bit(flags1, 30)

    def present1(bit: int) -> bool:
        return binary.bit(flags1, bit) or is_body_text

    def present2(bit: int) -> bool:
        return binary.bit(flags2, bit) or is_body_text

    unknown: dict = {
        "b1": header["b1"],
        "b2": header["b2"],
        "xxx624": header["xxx624"],
        "xxx625": header["xxx625"],
        "xxx626": header["xxx626"],
        "xxx627": header["xxx627"],
        "xxx628": header["xxx628"],
    }
    cursor = _Cursor(data, offset + STYLESTR_HEADER_SIZE)

    # --- One-byte fields, in on-disk order --------------------------------
    if present1(0):
        unknown["xxx100"] = cursor.byte()
    auto_indent = cursor.byte() if present1(1) else None
    for bit, name in ((2, "xxx102"), (3, "xxx103"), (4, "xxx104"), (5, "xxx105"), (6, "xxx106")):
        if present1(bit):
            unknown[name] = cursor.byte()
    font_name_selector0 = cursor.byte() if present1(7) else None
    underline = cursor.byte() if present1(8) else None
    script = cursor.byte() if present1(9) else None
    strikeout = cursor.byte() if present1(10) else None
    alignment = cursor.byte() if present1(11) else None
    keep_single = cursor.byte() if present1(12) else None
    keep_multiple = cursor.byte() if present1(13) else None
    hyphenation = cursor.byte() if present1(14) else None
    if present1(15):
        unknown["xxx115"] = cursor.byte()
    decimal_tab = cursor.byte() if present1(16) else None
    keep_next = cursor.byte() if present1(17) else None
    if present1(18):
        cursor.byte()  # textcolour0: placeholder, superseded by the trailing foreground word
    if present1(19):
        unknown["xxx119"] = cursor.byte()
    if present1(20):
        cursor.byte()  # underlinecolour: placeholder, superseded by the trailing word
    for bit, name in ((21, "xxx121"), (22, "xxx122")):
        if present1(bit):
            unknown[name] = cursor.byte()
    if present1(23):
        cursor.byte()  # textbackcolour: placeholder, superseded by the trailing background word
    lock_to_grid = cursor.byte() if present1(24) else None
    rule_off_0 = cursor.byte() if present1(25) else None
    italic = cursor.byte() if present1(26) else None
    bold = cursor.byte() if present1(27) else None
    # iseffect / xxx129 / showonstylemenu / xxx131: their one-byte body
    # position is only ever consumed for the body-text style, and even
    # then the value is discarded outright by the conversion source, not
    # read into anything -- see the module docstring.
    if is_body_text:
        cursor.take(4)

    cursor.align4()

    # --- Four-byte fields, in on-disk order --------------------------------
    left_indent = cursor.sword() if present2(0) else None
    right_indent_raw = cursor.sword() if present2(1) else None
    first_indent_absolute = cursor.sword() if present2(2) else None
    script_offset = cursor.sword() if present2(3) else None
    script_size = cursor.sword() if present2(4) else None
    line_spacing_raw = cursor.word() if present2(5) else None
    space_after = cursor.sword() if present2(6) else None
    space_before = (cursor.word() & 0xFFFFFF) if present2(7) else None
    underline_offset_0 = cursor.sword() if present2(8) else None
    underline_offset_1 = cursor.sword() if present2(9) else None
    rule_vertical_width = cursor.sword() if present2(10) else None
    for bit, name in ((11, "xxx211"), (12, "xxx212")):
        if present2(bit):
            unknown[name] = cursor.word()
    font_size = cursor.sword() if present2(13) else None
    font_aspect_ratio = cursor.sword() if present2(14) else None
    if present2(15):
        unknown["xxx215"] = cursor.word()
    keep_together = cursor.sword() if present2(16) else None
    leader = None
    if present2(17):
        leader = binary.cstring(cursor.data, cursor.pos, 4)
        cursor.pos += 4
    for bit, name in ((18, "xxx218"), (19, "xxx219"), (20, "xxx220")):
        if present2(bit):
            unknown[name] = cursor.word()
    if present2(21):
        cursor.word()  # fontname1: placeholder, superseded by the 40-byte name string
    tracking = cursor.sword() if present2(22) else None
    if present2(23):
        unknown["xxx223"] = cursor.word()
    if present2(24):
        cursor.word()  # textcolour1: placeholder, superseded by the trailing foreground word
    if present2(25):
        cursor.word()  # textcolour2: placeholder, superseded by the trailing background word
    for bit, name in (
        (26, "xxx226"),
        (27, "xxx227"),
        (28, "xxx228"),
        (29, "xxx229"),
        (30, "xxx230"),
        (31, "xxx231"),
    ):
        if present2(bit):
            unknown[name] = cursor.word()

    # --- Tab ruler -----------------------------------------------------
    tab_stops = []
    for bit in range(32):
        if binary.bit(tabs, bit) or is_body_text:
            word = cursor.word()
            tab_stops.append(TabStop(kind=word & 0xFF, position=(word >> 8) & 0xFFFFFF))

    # --- Font style name -------------------------------------------------
    font_style_name = None
    if present2(21):
        font_style_name = binary.cstring(cursor.data, cursor.pos, 40)
        cursor.pos += 40

    # --- Trailing colour words ------------------------------------------
    # Foreground/background are also read for the body-text style even if
    # their own flag is clear; underline/strikeout colour is not.
    foreground_colour_word = cursor.word() if present2(24) else None
    background_colour_word = cursor.word() if present2(25) else None
    underline_colour_word = cursor.word() if binary.bit(flags1, 20) else None

    return Style(
        index=index,
        is_body_text=is_body_text,
        name="BodyText" if is_body_text else header["name"],
        key=header["key"],
        paragraph_apply=header["paragraph_apply"],
        is_contents_entry_style=header["is_contents_entry_style"],
        is_index_entry_style=header["is_index_entry_style"],
        is_effect=is_effect,
        shows_on_style_menu=shows_on_style_menu,
        tabs=tabs,
        auto_indent=auto_indent,
        font_name_selector0=font_name_selector0,
        underline=underline,
        script=script,
        strikeout=strikeout,
        alignment=alignment,
        keep_single=keep_single,
        keep_multiple=keep_multiple,
        hyphenation=hyphenation,
        decimal_tab=decimal_tab,
        keep_next=keep_next,
        lock_to_grid=lock_to_grid,
        rule_off_0=rule_off_0,
        italic=italic,
        bold=bold,
        left_indent=left_indent,
        right_indent_raw=right_indent_raw,
        first_indent_absolute=first_indent_absolute,
        script_offset=script_offset,
        script_size=script_size,
        line_spacing_raw=line_spacing_raw,
        space_after=space_after,
        space_before=space_before,
        underline_offset_0=underline_offset_0,
        underline_offset_1=underline_offset_1,
        rule_vertical_width=rule_vertical_width,
        font_size=font_size,
        font_aspect_ratio=font_aspect_ratio,
        keep_together=keep_together,
        leader=leader,
        tracking=tracking,
        tab_stops=tuple(tab_stops),
        font_style_name=font_style_name,
        foreground_colour_word=foreground_colour_word,
        background_colour_word=background_colour_word,
        underline_colour_word=underline_colour_word,
        unknown=unknown,
    )


def parse_style_table(data: bytes, stylebase: int) -> list[Style]:
    """Decode the style table: a 255-entry array of 4-byte offsets
    (relative to stylebase) at stylebase, each either zero (slot unused)
    or pointing to a stylestr record. Slot 0 is the body-text style."""
    styles = []
    for slot in range(STYLE_TABLE_SLOTS):
        entry_offset = binary.u32(data, stylebase + slot * 4)
        if entry_offset == 0:
            continue
        styles.append(
            _decode_style_body(
                data, stylebase + entry_offset, index=slot, is_body_text=(slot == 0)
            )
        )
    return styles
