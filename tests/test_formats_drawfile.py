import struct

from riscos_impression.formats.drawfile import (
    DrawGroup,
    DrawPath,
    DrawPathOpCode,
    DrawSprite,
    DrawTagged,
    DrawText,
    DrawUnknown,
    DrawFile,
    colour_rgb,
)
from tests.fixtures.drawfile_builders import (
    build_drawfile,
    build_font_table,
    build_group,
    build_path,
    build_sprite,
    build_tagged,
    build_text,
    build_unknown,
    close_line,
    curve,
    end_path,
    line,
    move,
)


def _build_drawfile(*, x0=0, y0=0, x1=1000, y1=2000) -> bytes:
    data = bytearray(40)
    data[0:4] = b"Draw"
    struct.pack_into("<i", data, 24, x0)
    struct.pack_into("<i", data, 28, y0)
    struct.pack_into("<i", data, 32, x1)
    struct.pack_into("<i", data, 36, y1)
    return bytes(data)


def test_decodes_bounding_box():
    drawfile = DrawFile.from_bytes(_build_drawfile(x0=10, y0=20, x1=1010, y1=2020))
    assert drawfile is not None
    assert drawfile.bounds.x0 == 10
    assert drawfile.bounds.y0 == 20
    assert drawfile.bounds.x1 == 1010
    assert drawfile.bounds.y1 == 2020


def test_negative_coordinates():
    drawfile = DrawFile.from_bytes(_build_drawfile(x0=-500, y0=-500))
    assert drawfile.bounds.x0 == -500
    assert drawfile.bounds.y0 == -500


def test_wrong_signature_returns_none():
    data = _build_drawfile()
    bad = b"NOPE" + data[4:]
    assert DrawFile.from_bytes(bad) is None


def test_too_short_returns_none():
    assert DrawFile.from_bytes(b"Draw") is None


def test_colour_rgb_decodes_bbggrr00_word():
    # &BBGGRR00: byte 1 (bits 8-15) is R, byte 2 (16-23) is G, byte 3 (24-31) is B.
    assert colour_rgb(0x0000FF00) == (0xFF, 0, 0)  # pure red
    assert colour_rgb(0x00FF0000) == (0, 0xFF, 0)  # pure green
    assert colour_rgb(0xFF000000) == (0, 0, 0xFF)  # pure blue


def test_font_table_maps_numbers_to_names():
    data = build_drawfile(build_font_table({1: "Trinity.Medium", 2: "Homerton.Bold"}))
    drawfile = DrawFile.from_bytes(data)
    assert drawfile.fonts == {1: "Trinity.Medium", 2: "Homerton.Bold"}
    assert drawfile.objects == []  # a font table object contributes no renderable object


def test_path_object_decodes_colours_width_and_ops():
    ops = move(0, 0) + line(1000, 0) + curve(100, 100, 200, 200, 300, 300) + close_line() + end_path()
    data = build_drawfile(
        build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00, stroke_colour=0x00FF0000, line_width=40)
    )
    drawfile = DrawFile.from_bytes(data)
    assert len(drawfile.objects) == 1
    path = drawfile.objects[0]
    assert isinstance(path, DrawPath)
    assert path.fill_colour == 0x0000FF00
    assert path.stroke_colour == 0x00FF0000
    assert path.line_width == 40
    assert not path.even_odd
    assert not path.dashed
    codes = [op.code for op in path.ops]
    assert codes == [
        DrawPathOpCode.MOVE,
        DrawPathOpCode.LINE,
        DrawPathOpCode.CURVE,
        DrawPathOpCode.CLOSE_LINE,
    ]
    curve_op = path.ops[2]
    assert (curve_op.cx1, curve_op.cy1, curve_op.cx2, curve_op.cy2, curve_op.x, curve_op.y) == (
        100, 100, 200, 200, 300, 300,
    )


def test_path_object_no_colour_sentinel_decodes_to_none():
    ops = move(0, 0) + end_path()
    data = build_drawfile(build_path(ops=ops, fill_colour=0xFFFFFFFF, stroke_colour=0xFFFFFFFF))
    path = DrawFile.from_bytes(data).objects[0]
    assert path.fill_colour is None
    assert path.stroke_colour is None


def test_path_object_dashed_flag_skips_past_the_dash_pattern_to_the_path_data():
    ops = move(5, 5) + end_path()
    data = build_drawfile(build_path(ops=ops, dashed=True, stroke_colour=0))
    path = DrawFile.from_bytes(data).objects[0]
    assert path.dashed is True
    assert len(path.ops) == 1
    assert (path.ops[0].x, path.ops[0].y) == (5, 5)


def test_path_object_even_odd_winding_rule():
    ops = move(0, 0) + end_path()
    data = build_drawfile(build_path(ops=ops, fill_colour=0, even_odd=True))
    path = DrawFile.from_bytes(data).objects[0]
    assert path.even_odd is True


def test_text_object_decodes_font_number_size_and_baseline():
    data = build_drawfile(build_text(text="Hi", font_number=3, size_x=320, size_y=640, baseline_x=-10, baseline_y=20))
    text = DrawFile.from_bytes(data).objects[0]
    assert isinstance(text, DrawText)
    assert text.text == "Hi"
    assert text.font_number == 3
    assert text.size_x == 320
    assert text.size_y == 640
    assert text.baseline_x == -10
    assert text.baseline_y == 20


def test_sprite_object_keeps_only_its_bounding_box():
    data = build_drawfile(build_sprite(bounds=(1, 2, 3, 4)))
    sprite = DrawFile.from_bytes(data).objects[0]
    assert isinstance(sprite, DrawSprite)
    assert (sprite.bounds.x0, sprite.bounds.y0, sprite.bounds.x1, sprite.bounds.y1) == (1, 2, 3, 4)


def test_group_object_recurses_into_children():
    ops = move(0, 0) + end_path()
    child = build_path(ops=ops, fill_colour=0)
    data = build_drawfile(build_group("MyGroup", child))
    group = DrawFile.from_bytes(data).objects[0]
    assert isinstance(group, DrawGroup)
    assert group.name.strip() == "MyGroup"
    assert len(group.objects) == 1
    assert isinstance(group.objects[0], DrawPath)


def test_group_fonts_are_merged_into_the_top_level_font_map():
    child = build_font_table({5: "Corpus.Medium"})
    data = build_drawfile(build_group("G", child))
    drawfile = DrawFile.from_bytes(data)
    assert drawfile.fonts == {5: "Corpus.Medium"}


def test_tagged_object_wraps_its_inner_object():
    ops = move(0, 0) + end_path()
    inner = build_path(ops=ops, fill_colour=0)
    data = build_drawfile(build_tagged(42, inner))
    tagged = DrawFile.from_bytes(data).objects[0]
    assert isinstance(tagged, DrawTagged)
    assert tagged.tag == 42
    assert isinstance(tagged.inner, DrawPath)


def test_unrecognised_object_type_becomes_draw_unknown():
    data = build_drawfile(build_unknown(11, bounds=(0, 0, 10, 10)))  # Options object
    obj = DrawFile.from_bytes(data).objects[0]
    assert isinstance(obj, DrawUnknown)
    assert obj.type == 11


def test_truncated_object_stream_stops_without_raising():
    data = build_drawfile(build_font_table({1: "A"}))[:-3]  # cut mid-object
    drawfile = DrawFile.from_bytes(data)
    assert drawfile is not None
    assert drawfile.objects == []


def test_object_claiming_a_size_past_the_end_of_the_file_is_dropped():
    data = bytearray(build_drawfile(build_unknown(11)))
    struct.pack_into("<i", data, 44, 10_000)  # object's own "size" field, made absurd
    drawfile = DrawFile.from_bytes(bytes(data))
    assert drawfile.objects == []
