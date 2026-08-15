from riscos_impression.model.numbering import (
    NumberingStyle,
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
