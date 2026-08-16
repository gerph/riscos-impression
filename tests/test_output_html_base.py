from riscos_impression.model.colours import Colour, ColourModel
from riscos_impression.output.html_base import (
    HTML5Converter,
    _draw_colour_to_css,
    colour_to_css,
    css_style_attr,
    font_family_css,
    picture_placeholder_data_uri,
    style_css_properties,
)

from tests.test_output_base import _style, _document
from tests.fixtures.drawfile_builders import (
    build_drawfile,
    build_font_table,
    build_path,
    build_sprite,
    build_text,
    build_unknown,
    close_line,
    end_path,
    line,
    move,
)
from riscos_impression.formats.drawfile import DrawFile


def test_colour_to_css_rgb():
    colour = Colour(index=None, name="", model=ColourModel.RGB, values=(0x10000, 0, 0x8000), process=True, overprint=False, palette_word=0)
    assert colour_to_css(colour) == "#ff0080"


def test_colour_to_css_cmyk():
    colour = Colour(index=None, name="", model=ColourModel.CMYK, values=(0, 0, 0, 0x10000), process=True, overprint=False, palette_word=0)
    assert colour_to_css(colour) == "#000000"


def test_colour_to_css_none_is_none():
    assert colour_to_css(None) is None


def test_font_family_css_maps_riscos_families():
    assert "sans-serif" in font_family_css(_style(1, font_style_name="Homerton.Medium"))
    assert "serif" in font_family_css(_style(1, font_style_name="Trinity.Medium"))
    assert "monospace" in font_family_css(_style(1, font_style_name="Corpus.Medium"))


def test_style_css_properties_bold_italic_underline():
    style = _style(1, is_body_text=True, bold=1, italic=1, underline=1)
    props = style_css_properties(style, [])
    assert props["font-weight"] == "bold"
    assert props["font-style"] == "italic"
    assert props["text-decoration"] == "underline"


def test_style_css_properties_omits_absent_fields():
    style = _style(1, is_body_text=True)
    props = style_css_properties(style, [])
    assert "font-weight" not in props
    assert "color" not in props


def test_css_style_attr_joins_with_semicolons():
    assert css_style_attr({"a": "1", "b": "2"}) == "a: 1; b: 2"


def test_picture_placeholder_data_uri_is_self_contained_svg():
    uri = picture_placeholder_data_uri("Draw", 100.0, 50.0)
    assert uri.startswith("data:image/svg+xml;base64,")


def test_draw_colour_to_css_decodes_bbggrr00_word():
    assert _draw_colour_to_css(0x0000FF00) == "#ff0000"
    assert _draw_colour_to_css(None) is None


def _converter() -> HTML5Converter:
    return HTML5Converter(_document())


def test_drawfile_svg_renders_a_filled_path():
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    data = build_drawfile(build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00))
    draw = DrawFile.from_bytes(data)

    svg = _converter()._drawfile_svg(draw, width_pt=100.0, height_pt=100.0)

    assert svg.startswith("<svg ")
    assert '<path d="M' in svg
    assert 'fill="#ff0000"' in svg


def test_drawfile_svg_renders_text_using_the_font_tables_own_name():
    fonts = build_font_table({1: "Trinity.Bold"})
    text = build_text(text="Hi", font_number=1, baseline_x=10, baseline_y=10, size_x=6400, size_y=6400)
    draw = DrawFile.from_bytes(build_drawfile(fonts + text, bounds=(0, 0, 100, 100)))

    svg = _converter()._drawfile_svg(draw, width_pt=100.0, height_pt=100.0)

    assert "<text " in svg
    assert ">Hi</text>" in svg
    assert "font-weight:bold" in svg
    assert "serif" in svg  # Trinity maps to the serif stack


def test_drawfile_svg_text_size_accounts_for_the_points_vs_drawunits_mismatch():
    # Regression test: mirrors pdfdoc.py's own -- text.size_y is
    # already in points, unlike a path's Draw-unit-denominated
    # line_width, so scaling it directly by the picture's own points-
    # per-Draw-unit ratio was a unit mismatch, rendering DrawFile text
    # at roughly 1/100th its intended size. Realistic Draw-unit-scale
    # bounds (tens of thousands of units), not a round number matching
    # the target box in points -- with a 1:1-scale bounds/target box
    # the bug and the fix give the same answer.
    from riscos_impression.output.html_base import _DRAW_UNIT_TO_PT

    fonts = build_font_table({1: "Homerton.Medium"})
    text = build_text(text="Hello", font_number=1, size_x=8960, size_y=8960, baseline_x=0, baseline_y=0)
    bounds = (0, 0, 25600, 25600)
    draw = DrawFile.from_bytes(build_drawfile(fonts + text, bounds=bounds))

    svg = _converter()._drawfile_svg(draw, width_pt=100.0, height_pt=100.0)

    sy = 100.0 / 25600
    expected_size_pt = (8960 / 640.0) * (sy / _DRAW_UNIT_TO_PT)
    assert expected_size_pt > 1.0
    assert f"font-size:{expected_size_pt:.2f}pt" in svg


def test_drawfile_svg_sprite_sub_object_is_a_placeholder_and_logs_best_effort():
    draw = DrawFile.from_bytes(build_drawfile(build_sprite(bounds=(0, 0, 1000, 1000))))
    converter = _converter()

    svg = converter._drawfile_svg(draw, width_pt=100.0, height_pt=100.0)

    assert ">[Sprite]</text>" in svg
    assert any("Sprite object embedded within a DrawFile" in e.message for e in converter.log.entries)


def test_drawfile_svg_unknown_object_type_is_omitted_and_logs_best_effort():
    draw = DrawFile.from_bytes(build_drawfile(build_unknown(11, bounds=(0, 0, 1000, 1000))))
    converter = _converter()

    svg = converter._drawfile_svg(draw, width_pt=100.0, height_pt=100.0)

    assert svg == '<svg xmlns="http://www.w3.org/2000/svg" width="100.0pt" height="100.0pt" viewBox="0 0 100.0 100.0"></svg>'
    assert any("were not decoded and are omitted" in e.message for e in converter.log.entries)
