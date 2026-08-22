from riscos_impression.model.numbering import (
    NumberingStyle,
    format_number,
    parse_numbering_table,
    resolve_number,
    resolve_numbering_style,
)
from tests.fixtures.builders import build_numbering_record


def test_parse_numbering_table():
    data = build_numbering_record(
        start=True, start_value=5, style=0, tag=42, dictionary_index=3
    )
    (record,) = parse_numbering_table(data, numbers=0, numbers_end=len(data))
    assert record.start is True
    assert record.start_value == 5
    assert record.style is NumberingStyle.DECIMAL
    assert record.tag == 42
    assert record.dictionary_index == 3


def test_resolve_numbering_style_unknown():
    assert resolve_numbering_style(0) is NumberingStyle.DECIMAL
    assert resolve_numbering_style(5) is NumberingStyle.BULLET
    assert resolve_numbering_style(99) is None


def test_resolve_number_counts_from_start_record():
    data = b"".join(
        [
            build_numbering_record(start=True, start_value=0, tag=1, dictionary_index=0),
            build_numbering_record(start=False, tag=2, dictionary_index=0),
            build_numbering_record(start=False, tag=3, dictionary_index=0),
        ]
    )
    records = parse_numbering_table(data, numbers=0, numbers_end=len(data))

    assert resolve_number(records, dictionary_index=0, tag=1) == 0
    assert resolve_number(records, dictionary_index=0, tag=2) == 1
    assert resolve_number(records, dictionary_index=0, tag=3) == 2


def test_resolve_number_uses_start_value_as_seed():
    data = b"".join(
        [
            build_numbering_record(start=True, start_value=10, tag=1, dictionary_index=0),
            build_numbering_record(start=False, tag=2, dictionary_index=0),
        ]
    )
    records = parse_numbering_table(data, numbers=0, numbers_end=len(data))
    assert resolve_number(records, dictionary_index=0, tag=2) == 11


def test_resolve_number_scoped_per_story():
    data = b"".join(
        [
            build_numbering_record(start=True, start_value=0, tag=1, dictionary_index=0),
            build_numbering_record(start=True, start_value=100, tag=1, dictionary_index=1),
            build_numbering_record(start=False, tag=2, dictionary_index=1),
        ]
    )
    records = parse_numbering_table(data, numbers=0, numbers_end=len(data))
    # dictionary_index=1's sequence should not be affected by story 0's records.
    assert resolve_number(records, dictionary_index=1, tag=2) == 101


def test_resolve_number_no_match_returns_none():
    data = build_numbering_record(start=True, tag=1, dictionary_index=0)
    records = parse_numbering_table(data, numbers=0, numbers_end=len(data))
    assert resolve_number(records, dictionary_index=0, tag=99) is None


def test_format_number_decimal():
    assert format_number(0, NumberingStyle.DECIMAL) == "0"
    assert format_number(42, NumberingStyle.DECIMAL) == "42"
    assert format_number(-1, NumberingStyle.DECIMAL) == "-1"


def test_format_number_none_style_falls_back_to_decimal():
    assert format_number(7, None) == "7"


def test_format_number_roman():
    cases = {
        1: "I", 4: "IV", 9: "IX", 14: "XIV", 40: "XL", 49: "XLIX",
        90: "XC", 444: "CDXLIV", 1994: "MCMXCIV", 3999: "MMMCMXCIX",
    }
    for value, roman in cases.items():
        assert format_number(value, NumberingStyle.ROMAN_UPPER) == roman
        assert format_number(value, NumberingStyle.ROMAN_LOWER) == roman.lower()


def test_format_number_roman_below_one_falls_back_to_decimal():
    assert format_number(0, NumberingStyle.ROMAN_UPPER) == "0"
    assert format_number(-3, NumberingStyle.ROMAN_LOWER) == "-3"


def test_format_number_alpha_bijective_base26():
    cases = {1: "A", 2: "B", 26: "Z", 27: "AA", 28: "AB", 52: "AZ", 53: "BA", 702: "ZZ", 703: "AAA"}
    for value, alpha in cases.items():
        assert format_number(value, NumberingStyle.ALPHA_UPPER) == alpha
        assert format_number(value, NumberingStyle.ALPHA_LOWER) == alpha.lower()


def test_format_number_alpha_below_one_falls_back_to_decimal():
    assert format_number(0, NumberingStyle.ALPHA_UPPER) == "0"


def test_format_number_bullet_ignores_value():
    assert format_number(1, NumberingStyle.BULLET) == "•"
    assert format_number(999, NumberingStyle.BULLET) == "•"
