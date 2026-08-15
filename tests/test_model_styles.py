from riscos_impression.model.colours import ColourModel
from riscos_impression.model.styles import parse_style_table
from tests.fixtures.builders import build_style_record, build_style_table


def _one_style(**kwargs):
    record = build_style_record(**kwargs)
    table = build_style_table({0: record})
    (style,) = parse_style_table(table, stylebase=0)
    return style


def test_body_text_style_has_everything_present_except_underline_colour():
    style = _one_style(is_body_text=True, name="Ignored")

    assert style.name == "BodyText"
    assert style.is_body_text is True
    assert style.underline is not None
    assert style.alignment is not None
    assert style.left_indent is not None
    assert style.font_size is not None
    assert style.foreground_colour_word is not None
    assert style.background_colour_word is not None
    assert len(style.tab_stops) == 32
    # The one confirmed asymmetry: underline colour is NOT forced present
    # for body text the way foreground/background are.
    assert style.underline_colour_word is None


def test_body_text_style_with_underline_colour_flag_set():
    style = _one_style(
        is_body_text=True,
        flags1=1 << 20,
        underline_colour_word=0x00,
    )
    assert style.underline_colour_word == 0x00


def test_ordinary_style_only_carries_flagged_fields():
    flags1 = (1 << 26) | (1 << 27)  # italic, bold
    flags2 = 1 << 13  # font size
    record = build_style_record(
        flags1=flags1,
        flags2=flags2,
        name="Emphasis",
        values={"italic": 1, "bold": 1, "font_size": 240},
    )
    table = build_style_table({1: record})
    (style,) = parse_style_table(table, stylebase=0)

    assert style.index == 1
    assert style.name == "Emphasis"
    assert style.is_body_text is False
    assert style.italic == 1
    assert style.bold == 1
    assert style.font_size == 240
    # Nothing else was flagged, so it should all be absent.
    assert style.underline is None
    assert style.alignment is None
    assert style.left_indent is None
    assert style.tab_stops == ()
    assert style.font_style_name is None
    # b1/b2/xxx624-628 come from the fixed header, always captured
    # regardless of presence flags; nothing else was flagged here.
    assert set(style.unknown) == {"b1", "b2", "xxx624", "xxx625", "xxx626", "xxx627", "xxx628"}


def test_unknown_fields_are_captured_by_name():
    flags1 = (1 << 0) | (1 << 2)  # xxx100, xxx102
    record = build_style_record(
        flags1=flags1,
        values={"xxx100": 7, "xxx102": 9},
    )
    table = build_style_table({3: record})
    (style,) = parse_style_table(table, stylebase=0)
    assert style.unknown["xxx100"] == 7
    assert style.unknown["xxx102"] == 9


def test_multiple_slots_including_unused():
    body_record = build_style_record(is_body_text=True)
    ordinary = build_style_record(flags1=1 << 26, name="Italic", values={"italic": 1})
    tabbed = build_style_record(
        flags2=0, tabs=(1 << 0) | (1 << 5), name="Tabbed", tab_words=[0x00000000, 0x0000C801]
    )
    table = build_style_table({0: body_record, 1: ordinary, 3: tabbed})

    styles = parse_style_table(table, stylebase=0)

    assert [s.index for s in styles] == [0, 1, 3]
    assert styles[0].is_body_text is True
    assert styles[1].name == "Italic"
    assert styles[2].name == "Tabbed"
    assert len(styles[2].tab_stops) == 2


def test_tab_stop_decoding():
    # tab word: low byte = kind, remaining 24 bits = position
    word = (12345 << 8) | 2  # kind=right(2), position=12345
    record = build_style_record(tabs=1 << 0, tab_words=[word])
    table = build_style_table({1: record})
    (style,) = parse_style_table(table, stylebase=0)
    (stop,) = style.tab_stops
    assert stop.kind == 2
    assert stop.position == 12345


def test_line_spacing_fixed_vs_proportional():
    fixed_word = 0x80000000 | (0x10000 + 500)
    record_fixed = build_style_record(flags2=1 << 5, values={"line_spacing_raw": fixed_word})
    table_fixed = build_style_table({1: record_fixed})
    (style_fixed,) = parse_style_table(table_fixed, stylebase=0)
    assert style_fixed.line_spacing_is_fixed is True
    assert style_fixed.line_spacing == 500

    record_prop = build_style_record(flags2=1 << 5, values={"line_spacing_raw": 1200})
    table_prop = build_style_table({1: record_prop})
    (style_prop,) = parse_style_table(table_prop, stylebase=0)
    assert style_prop.line_spacing_is_fixed is False
    assert style_prop.line_spacing == 1200


def test_right_indent_sign_convention():
    delta = build_style_record(flags2=1 << 1, values={"right_indent_raw": 300})
    (style_delta,) = parse_style_table(build_style_table({1: delta}), stylebase=0)
    assert style_delta.right_indent_is_delta is True
    assert style_delta.right_indent == 300

    absolute = build_style_record(flags2=1 << 1, values={"right_indent_raw": -300})
    (style_abs,) = parse_style_table(build_style_table({1: absolute}), stylebase=0)
    assert style_abs.right_indent_is_delta is False
    assert style_abs.right_indent == 300


def test_first_indent_is_relative_to_left_indent():
    record = build_style_record(
        flags2=(1 << 0) | (1 << 2),
        values={"left_indent": 100, "first_indent_absolute": 150},
    )
    (style,) = parse_style_table(build_style_table({1: record}), stylebase=0)
    assert style.first_indent == 50


def test_colour_resolution_helpers():
    flags1 = (1 << 23) | (1 << 20)  # textbackcolour, underlinecolour
    flags2 = (1 << 24) | (1 << 25)  # textcolour1, textcolour2
    record = build_style_record(
        flags1=flags1,
        flags2=flags2,
        foreground_colour_word=0x00000000,
        background_colour_word=0x00000000,
        underline_colour_word=0x00000000,
    )
    (style,) = parse_style_table(build_style_table({1: record}), stylebase=0)

    fg = style.foreground_colour([])
    bg = style.background_colour([])
    ul = style.underline_colour([])
    assert fg is not None and fg.model is ColourModel.RGB
    assert bg is not None
    assert ul is not None


def test_effect_style_never_resolves_a_background_colour():
    flags1 = (1 << 28) | (1 << 23)  # iseffect, textbackcolour
    flags2 = 1 << 25  # textcolour2
    record = build_style_record(
        flags1=flags1, flags2=flags2, background_colour_word=0x00000000
    )
    (style,) = parse_style_table(build_style_table({1: record}), stylebase=0)
    assert style.is_effect is True
    assert style.background_colour([]) is None
