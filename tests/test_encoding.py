from riscos_impression import encoding


def test_ascii_passes_through_unchanged():
    assert encoding.decode(b"Hello, world!") == "Hello, world!"


def test_upper_latin1_range_matches_iso_8859_1():
    assert encoding.decode(bytes([0xE9])) == "é"  # 0xE9 is e-acute in both
    assert encoding.decode(bytes([0xA3])) == "£"


def test_c1_range_remapped_to_riscos_glyphs_not_control_codes():
    assert encoding.decode(bytes([0x94])) == "“"
    assert encoding.decode(bytes([0x95])) == "”"
    assert encoding.decode(bytes([0x90])) == "‘"
    assert encoding.decode(bytes([0x91])) == "’"
    assert encoding.decode(bytes([0x97])) == "–"
    assert encoding.decode(bytes([0x98])) == "—"
    assert encoding.decode(bytes([0x8C])) == "…"


def test_unassigned_c1_codes_fall_back_to_replacement_character():
    assert encoding.decode_byte(0x87) == "�"


def test_decode_byte_matches_decode_for_a_single_byte():
    for value in (0x41, 0x94, 0xE9):
        assert encoding.decode_byte(value) == encoding.decode(bytes([value]))


def test_encode_is_the_inverse_of_decode_for_every_representable_byte():
    for value in range(256):
        if value in (0x84, 0x87):
            continue  # both decode to U+FFFD; not round-trippable, see encoding.py
        assert encoding.encode_byte(encoding.decode_byte(value)) == value


def test_encode_ascii_passes_through_unchanged():
    assert encoding.encode("Hello, world!") == b"Hello, world!"


def test_encode_smart_quotes_back_to_their_original_c1_bytes():
    assert encoding.encode("“Galadriel”") == b"\x94Galadriel\x95"


def test_encode_unrepresentable_character_falls_back_to_question_mark():
    assert encoding.encode("中") == b"?"
