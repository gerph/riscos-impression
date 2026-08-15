from riscos_impression.model.colours import (
    MAX_TINT,
    MAXCV,
    ColourModel,
    decode_colour_word,
    parse_colour_table,
)
from tests.fixtures.builders import (
    COLOUR_ENTRY_SIZE,
    build_colour_entry,
    build_colour_table,
)


def _table(*entries):
    data = build_colour_table(list(entries))
    return parse_colour_table(data, colour1=0, tints=len(data))


def test_empty_table():
    assert parse_colour_table(b"", colour1=0, tints=0) == []


def test_rgb_base_colour():
    entries = _table(
        build_colour_entry(y=0, c=255, m=0, k=255, name="Red-ish", flags=0x1)
    )
    (colour,) = entries
    assert colour.name == "Red-ish"
    assert colour.model is ColourModel.RGB
    assert colour.values == ((255 * MAXCV) // 255, 0, (255 * MAXCV) // 255)
    assert colour.process is True  # flags & 0x3 == 1


def test_cmyk_base_colour():
    # low 2 bits of y select CMYK (1); upper 6 bits (y & 0xFC) hold yellow
    y = 0b1111_1101  # low2 = 1 (CMYK), y & 0xFC = 0xFC
    entries = _table(
        build_colour_entry(y=y, c=200, m=100, k=50, name="Muddy", flags=0x0)
    )
    (colour,) = entries
    assert colour.model is ColourModel.CMYK
    assert colour.values == (
        (200 * MAXCV) // 255,
        (100 * MAXCV) // 255,
        ((y & 0xFC) * MAXCV) // 255,
        (50 * MAXCV) // 255,
    )
    assert colour.process is False  # flags & 0x3 == 0 -> spot


def test_hsv_base_colour():
    y = 0b0011_0010  # low2 = 2 (HSV)
    c, m, k = 128, 0b1010_0101, 200
    entries = _table(build_colour_entry(y=y, c=c, m=m, k=k, name="Hue"))
    (colour,) = entries
    assert colour.model is ColourModel.HSV
    assert colour.values == (
        ((k << 4) | (m >> 4)) * MAXCV,
        (c * MAXCV) // 255,
        (((y >> 4) | ((m & 0xF) << 4)) * MAXCV) // 255,
    )


def test_overprint_flag():
    entries = _table(build_colour_entry(y=0, c=1, name="Over", flags=0x80))
    (colour,) = entries
    assert colour.overprint is True


def test_unused_slot_is_skipped():
    entries = _table(
        build_colour_entry(name=""),  # unused: empty name
        build_colour_entry(y=0, c=255, name="Real"),
    )
    assert [c.name for c in entries] == ["Real"]
    assert entries[0].index == 1


def test_tint_inherits_model_and_flags_from_base():
    base = build_colour_entry(
        y=0, c=255, m=0, k=0, name="Base", flags=0x81  # process(0x1) + overprint(0x80)
    )
    # tint entry references index 0: base = (y>>2) | ((c & 0x3F)<<6) == 0
    # k holds the tint amount; flags bit 0x2 marks this as a tint
    tint = build_colour_entry(
        y=0, c=0, m=0, k=64, name="Tint50", flags=0x2 | 0x8  # tint bit + spot bit ignored
    )
    entries = _table(base, tint)

    base_colour, tint_colour = entries
    assert tint_colour.name == "Tint50"
    assert tint_colour.model is base_colour.model
    assert tint_colour.process == base_colour.process
    assert tint_colour.overprint == base_colour.overprint
    assert tint_colour.palette_word == base_colour.palette_word

    remainder = MAX_TINT - 64
    r, g, b = base_colour.values
    assert tint_colour.values == (
        (remainder * MAXCV + 64 * r) // MAX_TINT,
        (remainder * MAXCV + 64 * g) // MAX_TINT,
        (remainder * MAXCV + 64 * b) // MAX_TINT,
    )


def test_tint_of_unresolvable_base_is_dropped():
    # References index 5, which doesn't exist in a 1-entry table.
    tint = build_colour_entry(y=0b0001_0100, c=0, k=64, name="Orphan", flags=0x2)
    entries = _table(tint)
    assert entries == []


def test_inline_rgb_word():
    word = (200 << 24) | (100 << 16) | (50 << 8) | 0x00  # b0=200 b1=100 b2=50 b3=0x00
    colour = decode_colour_word(word)
    assert colour.model is ColourModel.RGB
    assert colour.values == (
        (50 * MAXCV) // 255,
        (100 * MAXCV) // 255,
        (200 * MAXCV) // 255,
    )


def test_inline_cmyk_word_uses_0xfc_divisor_for_yellow():
    b3 = 0xFD  # low 2 bits = 1 (CMYK), yellow bits = 0xFC
    word = (10 << 24) | (20 << 16) | (30 << 8) | b3
    colour = decode_colour_word(word)
    assert colour.model is ColourModel.CMYK
    assert colour.values == (
        (30 * MAXCV) // 255,
        (20 * MAXCV) // 255,
        ((b3 & 0xFC) * MAXCV) // 0xFC,
        (10 * MAXCV) // 255,
    )


def test_inline_named_colour_reference():
    entries = _table(build_colour_entry(y=0, c=255, name="Referenced"))
    index = 0
    b3 = (index << 2) & 0xFF | 0x3
    b2 = index >> 6
    word = (0 << 24) | (0 << 16) | (b2 << 8) | b3
    colour = decode_colour_word(word, colours=entries)
    assert colour.name == "Referenced"


def test_inline_named_colour_reference_unresolvable_returns_none():
    word = 0x3  # selector 3, index 0, but no table given
    assert decode_colour_word(word) is None
    assert decode_colour_word(word, colours=[]) is None
