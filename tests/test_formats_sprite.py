import struct

from riscos_impression.formats.sprite import SpriteArea


def _build_sprite_area(*, name="MySprite", width_words=9, height=99, mode=28) -> bytes:
    sprite_header = bytearray(44)
    name_bytes = name.encode("latin-1")[:12].ljust(12, b"\x00")
    sprite_header[4:16] = name_bytes
    struct.pack_into("<I", sprite_header, 16, width_words)
    struct.pack_into("<I", sprite_header, 20, height)
    struct.pack_into("<I", sprite_header, 24, 0)  # first_bit_used
    struct.pack_into("<I", sprite_header, 28, 7)  # last_bit_used
    struct.pack_into("<I", sprite_header, 40, mode)

    area_header = bytearray(16)
    struct.pack_into("<I", area_header, 0, 16 + len(sprite_header))
    struct.pack_into("<I", area_header, 4, 1)  # sprite_count
    struct.pack_into("<I", area_header, 8, 16)  # first_offset
    struct.pack_into("<I", area_header, 12, 16 + len(sprite_header))  # free_offset

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
    area_header = bytearray(16)
    struct.pack_into("<I", area_header, 0, 16)
    struct.pack_into("<I", area_header, 4, 0)
    struct.pack_into("<I", area_header, 8, 16)
    struct.pack_into("<I", area_header, 12, 16)

    area = SpriteArea.from_bytes(bytes(area_header))
    assert area.sprite_count == 0
    assert area.first is None


def test_too_short_returns_none():
    assert SpriteArea.from_bytes(b"\x00" * 10) is None
