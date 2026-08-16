from riscos_impression.model.colours import Colour, ColourModel
from riscos_impression.output.html_base import (
    HTML5Converter,
    _draw_colour_to_css,
    colour_to_css,
    css_style_attr,
    font_family_css,
    paragraph_css_properties,
    picture_placeholder_data_uri,
    style_css_properties,
)

from tests.test_output_base import _style, _document
from tests.test_output_ovprodll import _picture
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


# ---------------------------------------------------------------------------
# Paragraph-level CSS
# ---------------------------------------------------------------------------


def test_paragraph_css_properties_maps_margins_indent_and_alignment():
    # Regression test: a real document (PCI_Spec from the local
    # examples/ corpus) had no indentation at all in either HTML
    # converter's output -- style_css_properties (applied per-span)
    # never touched left/right margin, first-line indent, or alignment
    # at all; nothing else applied them either.
    style = _style(
        1, is_body_text=True, left_indent=20000, first_indent_absolute=30000, alignment=1,
    )
    props = paragraph_css_properties(style)
    assert props["margin-left"] == "20.00pt"
    assert props["text-indent"] == "10.00pt"  # first_indent is relative to left_indent
    assert props["text-align"] == "center"
    assert "margin-right" not in props  # no max_width_pt given -- see its own test


def test_paragraph_css_properties_alignment_left_needs_no_property():
    style = _style(1, is_body_text=True, alignment=0)
    props = paragraph_css_properties(style)
    assert "text-align" not in props


def test_paragraph_css_properties_space_before_and_after_map_to_margins():
    style = _style(1, is_body_text=True, space_before=20000, space_after=5000)
    props = paragraph_css_properties(style)
    assert props["margin-top"] == "20.00pt"
    assert props["margin-bottom"] == "5.00pt"


def test_paragraph_css_properties_always_sets_a_line_height():
    style = _style(1, is_body_text=True, font_size=160, line_spacing_raw=None)
    props = paragraph_css_properties(style)
    assert props["line-height"] == "12.00pt"  # 10pt * 1.2 default


def test_paragraph_css_properties_fixed_line_height_smaller_than_the_font_falls_back_to_120_percent():
    # Regression test: mirrors pdfdoc.py's own fix for the same real
    # document (Telegraph from the local moreexamples/ corpus) -- a
    # heading style's OWN fixed leading (19.66pt, raw 0x80014ccc) is a
    # stale snapshot from some earlier, smaller font size and is barely
    # 70% of its current 28pt font, which would visibly collide a
    # wrapped heading's own lines if trusted verbatim.
    style = _style(1, is_body_text=True, font_size=448, line_spacing_raw=0x80014CCC)  # 28pt
    props = paragraph_css_properties(style)
    assert props["line-height"] == "33.60pt"  # 28pt * 1.2, not the stale 19.66pt


def test_paragraph_css_properties_right_indent_needs_a_max_width_to_apply_at_all():
    # Regression test: scrolling HTML has no frame width of its own to
    # sanity-check right_indent against (it deliberately drops absolute
    # geometry -- see html_scrolling.py's own module docstring), so
    # right_indent must never be applied there at all, regardless of
    # its own value -- unlike margin-left, which is safe unconditionally.
    style = _style(1, is_body_text=True, right_indent_raw=5000)
    assert "margin-right" not in paragraph_css_properties(style)
    assert "margin-right" not in paragraph_css_properties(style, max_width_pt=None)


def test_paragraph_css_properties_right_indent_applies_within_a_known_width():
    style = _style(1, is_body_text=True, right_indent_raw=5000)
    props = paragraph_css_properties(style, max_width_pt=100.0)
    assert props["margin-right"] == "5.00pt"


def test_paragraph_css_properties_oversized_right_indent_is_dropped():
    # Regression test: a real document (PCI_Spec from the local
    # examples/ corpus) has a title-block style whose right_indent was
    # authored for a much wider frame than it's actually used in --
    # already worked around in pdfdoc.py's own text-flow logic.
    # Applying it verbatim as a CSS margin-right on a frame whose real
    # width doesn't leave room for it would squeeze that frame's own
    # text into an unreadable (here, negative) column.
    style = _style(1, is_body_text=True, left_indent=20000, right_indent_raw=510000)
    props = paragraph_css_properties(style, max_width_pt=100.0)
    assert "margin-right" not in props
    assert props["margin-left"] == "20.00pt"  # left_indent is unaffected


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

    svg = _converter()._drawfile_svg(draw, _picture(), width_pt=100.0, height_pt=100.0)

    assert svg.startswith("<svg ")
    assert '<path d="M' in svg
    assert 'fill="#ff0000"' in svg


def test_drawfile_svg_renders_text_using_the_font_tables_own_name():
    fonts = build_font_table({1: "Trinity.Bold"})
    text = build_text(text="Hi", font_number=1, baseline_x=10, baseline_y=10, size_x=6400, size_y=6400)
    draw = DrawFile.from_bytes(build_drawfile(fonts + text, bounds=(0, 0, 100, 100)))

    svg = _converter()._drawfile_svg(draw, _picture(), width_pt=100.0, height_pt=100.0)

    assert "<text " in svg
    assert ">Hi</text>" in svg
    assert "font-weight:bold" in svg
    assert "serif" in svg  # Trinity maps to the serif stack


def test_drawfile_svg_text_size_accounts_for_the_points_vs_drawunits_mismatch():
    # Regression test: mirrors pdfdoc.py's own -- text.size_y is
    # already in points, unlike a path's Draw-unit-denominated
    # line_width, so scaling it directly by the picture's own points-
    # per-Draw-unit ratio was a unit mismatch, rendering DrawFile text
    # at roughly 1/100th its intended size. A non-default display scale
    # (0x20000 raw = a genuine 50% display scale, per the same
    # xscale/yscale convention pdfdoc.py's own _draw_drawfile_picture
    # and ovprodll.py's _tr_setscale already use) makes the expected
    # size clearly distinguishable from the picture's own 100% case.
    fonts = build_font_table({1: "Homerton.Medium"})
    text = build_text(text="Hello", font_number=1, size_x=8960, size_y=8960, baseline_x=0, baseline_y=0)
    bounds = (0, 0, 25600, 25600)
    draw = DrawFile.from_bytes(build_drawfile(fonts + text, bounds=bounds))

    svg = _converter()._drawfile_svg(draw, _picture(xscale=0x20000, yscale=0x20000), width_pt=100.0, height_pt=100.0)

    display_scale = 0x10000 / 0x20000  # 50%
    expected_size_pt = (8960 / 640.0) * display_scale
    assert expected_size_pt > 1.0
    assert f"font-size:{expected_size_pt:.2f}pt" in svg


def test_drawfile_svg_triangular_end_cap_draws_an_arrowhead():
    # Mirrors pdfdoc.py's own regression test: a real document
    # (PCI_Spec) used a triangular trailing cap for pointer/arrow
    # lines, previously rendered as a plain, uncapped stroke.
    ops = move(0, 0) + line(2560, 0) + end_path()
    path = build_path(
        ops=ops, bounds=(0, 0, 2560, 100), stroke_colour=0xFF000000, line_width=256,  # blue
        end_cap=3, triangle_cap_width=32, triangle_cap_length=64,
    )
    draw = DrawFile.from_bytes(build_drawfile(path, bounds=(0, 0, 2560, 100)))

    svg = _converter()._drawfile_svg(draw, _picture(), width_pt=100.0, height_pt=100.0)

    # The line's own stroked path, plus a second, separately-filled
    # triangle path for the arrowhead (fill="none" for the line itself,
    # a real fill for the cap).
    assert svg.count("<path ") == 2
    assert 'fill="none" stroke="#0000ff"' in svg  # the line itself
    assert 'fill="#0000ff" stroke="none"' in svg  # the arrowhead


def test_drawfile_svg_uses_pict_scale_and_centres_rather_than_stretching():
    # Regression test: a real document (PCI_Spec from the local
    # examples/ corpus) showed DrawFile pictures at visibly, sometimes
    # drastically, wrong size with their own text badly misplaced --
    # the SVG renderer was stretching the DrawFile's own bounding box
    # to exactly fill the picture frame's box (two independent x/y
    # scale factors derived from width_pt/height_pt), ignoring the
    # frame's own declared display scale (pict.xscale/yscale) entirely.
    # Mirrors pdfdoc.py's own _draw_drawfile_picture fix: at the
    # picture's native (100%) scale, a 25600x25600 Draw-unit bounds
    # (40x40pt) must render at 40x40pt, centred within a much larger
    # 100x100pt picture box -- not stretched to fill all 100x100pt.
    draw = DrawFile.from_bytes(build_drawfile(build_sprite(bounds=(0, 0, 25600, 25600)), bounds=(0, 0, 25600, 25600)))

    svg = _converter()._drawfile_svg(draw, _picture(), width_pt=100.0, height_pt=100.0)

    assert '<rect x="30.0" y="30.0" width="40.0" height="40.0"' in svg


def test_drawfile_svg_sprite_sub_object_is_a_placeholder_and_logs_best_effort():
    draw = DrawFile.from_bytes(build_drawfile(build_sprite(bounds=(0, 0, 1000, 1000))))
    converter = _converter()

    svg = converter._drawfile_svg(draw, _picture(), width_pt=100.0, height_pt=100.0)

    assert ">[Sprite]</text>" in svg
    assert any("Sprite object embedded within a DrawFile" in e.message for e in converter.log.entries)


def test_drawfile_svg_unknown_object_type_is_omitted_and_logs_best_effort():
    draw = DrawFile.from_bytes(build_drawfile(build_unknown(11, bounds=(0, 0, 1000, 1000))))
    converter = _converter()

    svg = converter._drawfile_svg(draw, _picture(), width_pt=100.0, height_pt=100.0)

    assert svg == (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100.0pt" height="100.0pt" '
        'viewBox="0 0 100.0 100.0" style="overflow: hidden;"></svg>'
    )
    assert any("were not decoded and are omitted" in e.message for e in converter.log.entries)
