import struct

from riscos_impression.formats.sprite import AREA_HEADER_SIZE, SpriteArea, wrap_single_sprite_as_area


def _build_sprite_area(*, name="MySprite", width_words=9, height=99, mode=28) -> bytes:
    sprite_header = bytearray(44)
    name_bytes = name.encode("latin-1")[:12].ljust(12, b"\x00")
    sprite_header[4:16] = name_bytes
    struct.pack_into("<I", sprite_header, 16, width_words)
    struct.pack_into("<I", sprite_header, 20, height)
    struct.pack_into("<I", sprite_header, 24, 0)  # first_bit_used
    struct.pack_into("<I", sprite_header, 28, 7)  # last_bit_used
    struct.pack_into("<I", sprite_header, 40, mode)

    # On-disk area header (12 bytes, no leading in-memory "size" word --
    # see SpriteArea.from_bytes's own docstring, confirmed against a
    # real file): sprite_count, first_sprite_offset, free_offset, with
    # the offset fields themselves 4 bytes larger than their own real
    # file position.
    area_header = bytearray(AREA_HEADER_SIZE)
    struct.pack_into("<I", area_header, 0, 1)  # sprite_count
    struct.pack_into("<I", area_header, 4, AREA_HEADER_SIZE + 4)  # first_sprite_offset
    struct.pack_into("<I", area_header, 8, AREA_HEADER_SIZE + 4 + len(sprite_header))  # free_offset

    return bytes(area_header) + bytes(sprite_header)


def test_decodes_area_and_first_sprite():
    area = SpriteArea.from_bytes(_build_sprite_area(name="Test", width_words=9, height=99, mode=28))

    assert area.sprite_count == 1
    assert area.first is not None
    assert area.first.name == "Test"
    assert area.first.width_words == 9
    assert area.first.height == 99
    assert area.first.mode == 28
    assert area.first.last_bit_used == 7


def test_zero_sprites_has_no_first():
    area_header = bytearray(AREA_HEADER_SIZE)
    struct.pack_into("<I", area_header, 0, 0)  # sprite_count
    struct.pack_into("<I", area_header, 4, AREA_HEADER_SIZE + 4)
    struct.pack_into("<I", area_header, 8, AREA_HEADER_SIZE + 4)

    area = SpriteArea.from_bytes(bytes(area_header))
    assert area.sprite_count == 0
    assert area.first is None


def test_too_short_returns_none():
    assert SpriteArea.from_bytes(b"\x00" * 10) is None


def test_wrap_single_sprite_as_area_round_trips_through_spritearea():
    sprite_header = bytearray(44)
    sprite_header[4:16] = b"Wrapped\x00\x00\x00\x00\x00"
    struct.pack_into("<I", sprite_header, 16, 4)  # width_words
    struct.pack_into("<I", sprite_header, 20, 10)  # height
    struct.pack_into("<I", sprite_header, 28, 31)  # last_bit_used
    struct.pack_into("<I", sprite_header, 40, 12)  # mode

    wrapped = wrap_single_sprite_as_area(bytes(sprite_header))
    area = SpriteArea.from_bytes(wrapped)

    assert area is not None
    assert area.sprite_count == 1
    assert area.first is not None
    assert area.first.name == "Wrapped"
    assert area.first.width_words == 4
    assert area.first.height == 10
    assert area.first.last_bit_used == 31
    assert area.first.mode == 12
