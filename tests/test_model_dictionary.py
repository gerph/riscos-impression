import struct

from riscos_impression.model.dictionary import (
    DictionaryEntryType,
    EmbeddedObjectType,
    classify_embedded_object,
    parse_dictionary,
    parse_master_dictionary,
    story_length,
)
from tests.fixtures.builders import DICT_ENTRY_SIZE, build_dict_entry


def test_parse_dictionary():
    data = build_dict_entry(type=2, id=5) + build_dict_entry(type=1, id=9)
    entries = parse_dictionary(data, dict1=0, mdict1=len(data))

    assert [e.type for e in entries] == [
        DictionaryEntryType.TEXT,
        DictionaryEntryType.PICTURE,
    ]
    assert [e.id for e in entries] == [5, 9]
    assert [e.index for e in entries] == [0, 1]


def test_parse_dictionary_with_leading_offset():
    prefix = b"\x00" * 100
    data = prefix + build_dict_entry(type=3, id=1)
    entries = parse_dictionary(data, dict1=100, mdict1=100 + DICT_ENTRY_SIZE)
    assert len(entries) == 1
    assert entries[0].type is DictionaryEntryType.SECTION


def test_embedded_object_type_only_meaningful_for_pictures():
    data = build_dict_entry(type=2, id=0, types=0xFF5)  # TEXT, not PICTURE
    (entry,) = parse_dictionary(data, dict1=0, mdict1=len(data))
    assert entry.embedded_object_type is None


def test_classify_eps():
    assert classify_embedded_object(0xFF5) is EmbeddedObjectType.EPS


def test_classify_draw_family_outer_code_alone():
    # outer 0xAFF with a zero inner sub-code falls back to the outer code
    # itself, which is also the Draw case.
    assert classify_embedded_object(0xAFF) is EmbeddedObjectType.DRAW


def test_classify_draw_family_inner_subcodes():
    for sub in (0xFF9, 0xC85, 0xAFF):
        types = 0xAFF | (sub << 12)
        assert classify_embedded_object(types) is EmbeddedObjectType.DRAW


def test_classify_draw_family_inner_subcode():
    # outer code 0xAFF, inner sub-code (bits 12-23) selecting Tablemate
    types = 0xAFF | (0xBCF << 12)
    assert classify_embedded_object(types) is EmbeddedObjectType.TABLEMATE


def test_classify_draw_family_unknown_subcode_is_data():
    types = 0xAFF | (0x123 << 12)
    assert classify_embedded_object(types) is EmbeddedObjectType.DATA


def test_classify_artworks():
    assert classify_embedded_object(0xD94) is EmbeddedObjectType.ARTWORKS


def test_classify_unknown_is_data():
    assert classify_embedded_object(0x123) is EmbeddedObjectType.DATA


def test_master_dictionary_and_story_length():
    offsets = [1000, 1200, 1500]  # 2 entries + 1 sentinel
    data = struct.pack("<3I", *offsets)
    master = parse_master_dictionary(data, mdict1=0, entry_count=2)
    assert master == offsets
    assert story_length(master, 0) == 200
    assert story_length(master, 1) == 300
