from riscos_impression.model.colours import Colour, ColourModel
from riscos_impression.output.html_base import (
    colour_to_css,
    css_style_attr,
    font_family_css,
    picture_placeholder_data_uri,
    style_css_properties,
)

from tests.test_output_base import _style


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
