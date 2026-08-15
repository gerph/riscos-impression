from riscos_impression import binary


def test_unsigned_ints():
    data = bytes([0x01, 0x02, 0x03, 0x04])
    assert binary.u8(data, 0) == 0x01
    assert binary.u16(data, 0) == 0x0201
    assert binary.u32(data, 0) == 0x04030201


def test_signed_ints():
    data = (-1).to_bytes(4, "little", signed=True)
    assert binary.s32(data, 0) == -1
    assert binary.s16(data, 0) == -1
    assert binary.s8(data, 0) == -1


def test_u24():
    data = bytes([0x01, 0x02, 0x03])
    assert binary.u24(data, 0) == 0x030201


def test_bit_and_bits():
    word = 0b1011_0010
    assert binary.bit(word, 1) is True
    assert binary.bit(word, 0) is False
    assert binary.bits(word, 4, 4) == 0b1011


def test_cstring_nul_terminated():
    data = b"Hello\x00\x00\x00\x00\x00\x00\x00"
    assert binary.cstring(data, 0, 12) == "Hello"


def test_cstring_cr_terminated():
    data = b"Hello\x0dpadding"
    assert binary.cstring(data, 0, 12) == "Hello"


def test_cstring_full_length_no_terminator():
    data = b"HelloWorld12"
    assert binary.cstring(data, 0, 12) == "HelloWorld12"


def test_nul_string():
    data = b"prefix\x00Hello\x00suffix"
    text, offset_after = binary.nul_string(data, 7)
    assert text == "Hello"
    assert offset_after == 13
