import re

import pytest

from riscos_impression.model.colours import Colour, ColourModel
from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import EmbedMark, PageBreakMark, Paragraph, Run, Story, TabMark
from riscos_impression.model.styles import TabStop
from riscos_impression.output.pdfdoc import (
    STANDARD_FONTS,
    _AVERAGE_WIDTH_FACTOR,
    _approx_width,
    _fill_colour_op,
    _line_height_pt,
    _narrow_for_obstacles,
    _next_tab_stop,
    _PDFWriter,
    _segment_width,
    _stroke_colour_op,
    _tab_advance,
    _Token,
    _wrap_tokens,
    choose_standard_font,
)

# Reuse the test helpers already established for the OvProDDL converter's tests.
from tests.test_output_ovprodll import _picture
from tests.fixtures.artworks_builders import build_single_path_document
from tests.fixtures.drawfile_builders import (
    build_drawfile,
    build_font_table,
    build_group,
    build_jpeg,
    build_path,
    build_sprite,
    build_text,
    close_line,
    end_path,
    line,
    move,
)
from tests.test_output_base import _document, _frame, _frame_record, _header, _section, _style


# ---------------------------------------------------------------------------
# Low-level PDF writer
# ---------------------------------------------------------------------------


def test_pdf_writer_produces_parseable_structure():
    writer = _PDFWriter()
    pages_obj = writer.reserve()
    kid = writer.add(b"<< /Type /Page /Parent 1 0 R >>")
    writer.set(pages_obj, f"<< /Type /Pages /Kids [{kid} 0 R] /Count 1 >>".encode("latin-1"))
    catalog = writer.add(f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode("latin-1"))

    data = writer.render(catalog)
    assert data.startswith(b"%PDF-1.4")
    assert data.endswith(b"%%EOF")
    assert b"/Type /Catalog" in data
    assert b"xref\n" in data
    assert b"trailer\n" in data
    assert f"/Root {catalog} 0 R".encode("latin-1") in data


def test_pdf_writer_unset_object_raises():
    writer = _PDFWriter()
    writer.reserve()
    try:
        writer.render(1)
    except ValueError:
        pass
    else:
        raise AssertionError("expected a ValueError for an unset object")


# ---------------------------------------------------------------------------
# Font selection
# ---------------------------------------------------------------------------


def test_choose_standard_font_maps_riscos_families():
    assert choose_standard_font(_style(1, font_style_name="Homerton.Medium")) == "Helvetica"
    assert choose_standard_font(_style(1, font_style_name="Trinity.Medium")) == "Times-Roman"
    assert choose_standard_font(_style(1, font_style_name="Corpus.Medium")) == "Courier"


def test_choose_standard_font_honours_bold_italic():
    assert choose_standard_font(_style(1, font_style_name="Trinity.Bold")) == "Times-Bold"
    assert choose_standard_font(_style(1, font_style_name="Trinity.Medium.Italic")) == "Times-Italic"
    assert choose_standard_font(_style(1, font_style_name="Trinity.Bold.Italic")) == "Times-BoldItalic"
    # The bold/italic override flags apply even when the font name itself doesn't say so.
    assert choose_standard_font(_style(1, font_style_name="Homerton.Medium", bold=1)) == "Helvetica-Bold"
    assert choose_standard_font(_style(1, font_style_name="Homerton.Medium", italic=1)) == "Helvetica-Oblique"


def test_all_fourteen_standard_fonts_declared():
    assert len(STANDARD_FONTS) == 14
    assert "Symbol" in STANDARD_FONTS
    assert "ZapfDingbats" in STANDARD_FONTS


def test_approx_width_courier_is_exact_afm_value():
    # Courier is genuinely fixed-pitch: every glyph is exactly 0.6em wide
    # per Adobe's own AFM data, so this is not an approximation.
    style = _style(1, font_style_name="Corpus.Medium", font_size=160)  # 10pt
    assert _approx_width("hello", style) == 5 * 10.0 * 0.6


def test_approx_width_helvetica_uses_real_per_character_metrics():
    # "MI" (a wide glyph next to a narrow one) would be identical under
    # the old flat per-family average; real metrics must tell them apart.
    style = _style(1, font_style_name="Homerton.Medium", font_size=1000)  # 62.5pt, easy arithmetic
    size_pt = 1000 / 16.0
    assert _approx_width("M", style) == 833 / 1000.0 * size_pt
    assert _approx_width("I", style) == 278 / 1000.0 * size_pt
    assert _approx_width("MI", style) == _approx_width("M", style) + _approx_width("I", style)


def test_approx_width_times_uses_real_per_character_metrics():
    style = _style(1, font_style_name="Trinity.Medium", font_size=1000)
    size_pt = 1000 / 16.0
    assert _approx_width("M", style) == 889 / 1000.0 * size_pt


def test_approx_width_bold_italic_selects_the_right_metrics_table():
    style = _style(1, font_style_name="Homerton.Medium", font_size=1000, bold=1, italic=1)
    size_pt = 1000 / 16.0
    # Homerton.Bold.Oblique's own 'A' width (722), not Homerton.Medium's (667).
    assert _approx_width("A", style) == 722 / 1000.0 * size_pt


def test_approx_width_symbol_font_has_no_metrics_table_falls_back_to_average():
    style = _style(1, font_style_name="Symbol", font_size=160)
    size_pt = 160 / 16.0
    assert _approx_width("hello", style) == 5 * size_pt * _AVERAGE_WIDTH_FACTOR["Symbol"]


def test_approx_width_character_outside_riscos_latin1_falls_back_to_average_for_whole_string():
    style = _style(1, font_style_name="Homerton.Medium", font_size=160)
    size_pt = 160 / 16.0
    text = "hi中"  # the CJK character has no RISC OS Latin1 byte at all
    assert _approx_width(text, style) == len(text) * size_pt * _AVERAGE_WIDTH_FACTOR["Helvetica"]


# ---------------------------------------------------------------------------
# Line height
# ---------------------------------------------------------------------------


def test_line_height_proportional_value_is_percent_times_100():
    # Regression test: a real document's own style (shared by a
    # corporate template across at least 14 of the 48 local example
    # documents) stores a proportional line_spacing of 12000 -- taking
    # that as a literal 12000% produced a 1728pt line height for a
    # 12pt style, which overflowed the very first line and silently
    # dropped the rest of the story. 12000 is percent x100, i.e. 120%.
    style = _style(1, is_body_text=True, font_size=192, line_spacing_raw=12000)  # 12pt, 120%
    assert _line_height_pt(style) == 12.0 * 1.2 * 1.2


def test_line_height_fixed_value_is_unaffected():
    # Top bit set = fixed leading; remaining 24 bits minus 0x10000 is the
    # fixed value in millipoints (0x80014e20 -> +20000 millipoints = 20pt).
    style = _style(1, is_body_text=True, font_size=160, line_spacing_raw=0x80014E20)
    assert _line_height_pt(style) == 20.0


def test_line_height_fixed_value_smaller_than_the_font_falls_back_to_120_percent():
    # Regression test: a real document (Telegraph from the local
    # moreexamples/ corpus) has a heading style ("Main Heading", 28pt)
    # whose OWN fixed leading is 19.66pt (raw 0x80014ccc) -- a leftover
    # snapshot from some smaller font size that never got updated when
    # the style's font_size was later increased, confirmed against a
    # real, OvationPro-native DDF export the user supplied (which
    # independently states 130% proportional leading for this style,
    # not this frozen absolute value). Used verbatim, 19.66pt visibly
    # collided the heading's own two wrapped lines. A fixed value must
    # never produce less spacing than the natural 120% default.
    style = _style(1, is_body_text=True, font_size=448, line_spacing_raw=0x80014CCC)  # 28pt
    assert _line_height_pt(style) == 28.0 * 1.2


def test_line_height_no_line_spacing_field_uses_default_120_percent():
    style = _style(1, is_body_text=True, font_size=160, line_spacing_raw=None)
    assert _line_height_pt(style) == 10.0 * 1.2


def test_ascent_pt_is_smaller_than_line_height_pt():
    # Regression test: a real page image the user supplied for
    # PCI_Spec showed every frame's first line sitting visibly too low
    # -- using the full ascent+descent+leading line_height as the drop
    # from a box's top edge to its first baseline pushes it down by
    # roughly the descent+leading amount too much. _ascent_pt is the
    # narrower figure that belongs there instead.
    from riscos_impression.output.pdfdoc import _ascent_pt

    style = _style(1, is_body_text=True, font_style_name="Homerton.Medium", font_size=160)
    assert _ascent_pt(style) == 10.0 * 718 / 1000.0
    assert _ascent_pt(style) < _line_height_pt(style)


# ---------------------------------------------------------------------------
# PDF string encoding
# ---------------------------------------------------------------------------


def test_pdf_str_transcodes_smart_quotes_to_winansi_bytes():
    from riscos_impression.output.pdfdoc import _pdf_str

    # “/” (curly double quotes, as RISC OS Latin1's C1 range
    # now decodes to -- see encoding.py) sit at 0x93/0x94 in
    # WinAnsiEncoding/cp1252, the encoding every text font here
    # declares. Regression test: these used to come out as literal '?'
    # once the content stream's own blanket latin-1 encode step ran,
    # since code points above U+00FF aren't representable in Latin-1
    # at all.
    assert _pdf_str("“Galadriel”") == "(\x93Galadriel\x94)"


def test_pdf_str_escapes_parens_and_backslash():
    from riscos_impression.output.pdfdoc import _pdf_str

    assert _pdf_str(r"a (b) \ c") == r"(a \(b\) \\ c)"


def test_pdf_str_unrepresentable_character_falls_back_to_question_mark():
    from riscos_impression.output.pdfdoc import _pdf_str

    # A character with no WinAnsiEncoding/cp1252 equivalent at all (as
    # opposed to one that just needs transcoding) has no better option
    # in a single-byte PDF text string.
    assert _pdf_str("中") == "(?)"


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def test_fill_colour_op_cmyk_uses_k_operator():
    colour = Colour(
        index=0, name="Test", model=ColourModel.CMYK, values=(0x8000, 0, 0, 0x10000),
        process=True, overprint=False, palette_word=0,
    )
    assert _fill_colour_op(colour) == "0.5 0 0 1 k\n"


def test_fill_colour_op_rgb_uses_rg_operator():
    colour = Colour(
        index=0, name="Test", model=ColourModel.RGB, values=(0x10000, 0, 0x8000),
        process=True, overprint=False, palette_word=0,
    )
    assert _fill_colour_op(colour) == "1 0 0.5 rg\n"


def test_fill_colour_op_none_is_black():
    assert _fill_colour_op(None) == "0 0 0 rg\n"


def test_stroke_colour_op_cmyk_uses_upper_k_operator():
    colour = Colour(
        index=0, name="Test", model=ColourModel.CMYK, values=(0, 0, 0, 0x10000),
        process=True, overprint=False, palette_word=0,
    )
    assert _stroke_colour_op(colour) == "0 0 0 1 K\n"


# ---------------------------------------------------------------------------
# Text wrapping
# ---------------------------------------------------------------------------


def _word(text, size=160, style=None):
    return _Token("word", text, style or _style(1, is_body_text=True, font_size=size))


def _space(size=160, style=None):
    return _Token("space", " ", style or _style(1, is_body_text=True, font_size=size))


def test_wrap_tokens_keeps_short_line_on_one_line():
    tokens = [_word("Hello"), _space(), _word("world")]
    lines = _wrap_tokens(tokens, tab_base_x=0.0, line_start_first=0.0, line_start_normal=0.0, right_edge=200.0)
    assert len(lines) == 1


def test_wrap_tokens_wraps_when_a_word_would_overflow():
    style = _style(1, is_body_text=True, font_size=160)  # Helvetica-family default, 10pt
    tokens = [_word("aaaaaaaaaa", style=style), _space(style=style), _word("bbbbbbbbbb", style=style)]
    # Each word alone is close to the whole available width, so the second
    # must wrap onto its own line.
    lines = _wrap_tokens(tokens, tab_base_x=0.0, line_start_first=0.0, line_start_normal=0.0, right_edge=60.0)
    assert len(lines) == 2
    assert "".join(t.text for t in lines[0]) == "aaaaaaaaaa"
    assert "".join(t.text for t in lines[1]) == "bbbbbbbbbb"


def test_wrap_tokens_tab_past_right_edge_forces_a_wrap():
    # Regression test: a style's tab ruler can be set up for a much wider
    # frame than the one it's actually used in (styles are shared across
    # frames of any size). Real corpus validation (Proj-tech example
    # document) found this previously drove rendered text hundreds of
    # points past the page edge, since the wrap decision treated a tab as
    # zero-width and only the *render* step discovered how far it
    # actually jumped.
    style = _style(1, is_body_text=True, font_size=160, tab_stops=(TabStop(kind=0, position=576000),))
    tokens = [_word("Before", style=style), _Token("tab", "", style), _word("After", style=style)]
    lines = _wrap_tokens(tokens, tab_base_x=0.0, line_start_first=0.0, line_start_normal=0.0, right_edge=300.0)
    assert len(lines) == 2
    assert lines[0][0].text == "Before"
    # The tab moves to the second line with "After" but, since even a
    # fresh line can't reach its target, contributes no positional jump
    # of its own there (see _tab_advance) -- it doesn't produce a third,
    # near-empty line.
    assert [t.text for t in lines[1]] == ["", "After"]


def test_next_tab_stop_uses_style_ruler_when_present():
    style = _style(1, is_body_text=True, tab_stops=(TabStop(kind=0, position=50000),))
    assert _next_tab_stop(10.0, tab_base_x=0.0, style=style) == (50.0, 0)


def test_next_tab_stop_default_pitch_without_a_ruler():
    style = _style(1, is_body_text=True, tab_stops=())
    assert _next_tab_stop(10.0, tab_base_x=0.0, style=style) == (36.0, 0)
    assert _next_tab_stop(40.0, tab_base_x=0.0, style=style) == (72.0, 0)


def test_next_tab_stop_reports_the_stops_own_kind():
    style = _style(1, is_body_text=True, tab_stops=(TabStop(kind=2, position=50000),))
    assert _next_tab_stop(10.0, tab_base_x=0.0, style=style) == (50.0, 2)


def test_next_tab_stop_skips_rule_line_markers():
    # A stop whose kind isn't 0-3 is a rule-line marker, not a real tab
    # stop, and must be skipped in favour of the next genuine one.
    style = _style(
        1, is_body_text=True,
        tab_stops=(TabStop(kind=9, position=30000), TabStop(kind=0, position=50000)),
    )
    assert _next_tab_stop(10.0, tab_base_x=0.0, style=style) == (50.0, 0)


def test_tab_advance_moves_to_the_stop_when_it_fits():
    style = _style(1, is_body_text=True, tab_stops=(TabStop(kind=0, position=50000),))
    assert _tab_advance(10.0, tab_base_x=0.0, style=style, right_edge=100.0) == 50.0


def test_tab_advance_is_a_no_op_when_the_stop_would_overflow():
    style = _style(1, is_body_text=True, tab_stops=(TabStop(kind=0, position=576000),))
    assert _tab_advance(10.0, tab_base_x=0.0, style=style, right_edge=300.0) == 10.0


def test_tab_advance_right_kind_ends_the_segment_at_the_stop():
    style = _style(1, is_body_text=True, font_size=160, tab_stops=(TabStop(kind=2, position=100000),))
    segment = [_word("hello", style=style)]  # ~5 chars
    width = _segment_width(segment)
    x = _tab_advance(10.0, tab_base_x=0.0, style=style, right_edge=200.0, segment_width=width)
    assert abs(x - (100.0 - width)) < 1e-9


def test_tab_advance_centre_kind_centres_the_segment_on_the_stop():
    style = _style(1, is_body_text=True, font_size=160, tab_stops=(TabStop(kind=1, position=100000),))
    segment = [_word("hello", style=style)]
    width = _segment_width(segment)
    x = _tab_advance(10.0, tab_base_x=0.0, style=style, right_edge=200.0, segment_width=width)
    assert abs(x - (100.0 - width / 2.0)) < 1e-9


def test_tab_advance_right_kind_never_moves_before_the_tabs_own_position():
    # A segment too wide to fit even at its own natural stop starts
    # immediately after the tab instead of overlapping earlier content.
    style = _style(1, is_body_text=True, tab_stops=(TabStop(kind=2, position=50000),))
    x = _tab_advance(40.0, tab_base_x=0.0, style=style, right_edge=200.0, segment_width=100.0)
    assert x == 40.0


def test_segment_width_stops_at_the_next_tab_or_break():
    style = _style(1, is_body_text=True, font_size=160)
    tokens = [_word("aaaaa", style=style), _Token("tab", "", style), _word("bbbbb", style=style)]
    assert _segment_width(tokens) == _approx_width("aaaaa", style)


# ---------------------------------------------------------------------------
# Full-document / converter-level tests
# ---------------------------------------------------------------------------


def _document_with_one_text_frame(*, text="Hello"):
    from riscos_impression.output.pdfdoc import PDFConverter  # local import to avoid an unused warning above

    body = _style(0, is_body_text=True, font_size=160)
    frame = _frame(filled=False, dictionary_index=0, x0=0, y0=0, x1=100000, y1=100000)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=100,
        records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)

    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text=text, style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub
    return document, PDFConverter


def test_draw_box_only_draws_the_present_edges():
    """Regression test: a real document's footer frame (PCI_Spec) has
    only its top and bottom borders present (border0/border3), with
    border1/border2 (left/right) both 0xFF -- but the whole frame
    still came out with all four edges drawn, since _draw_box always
    stroked a full rectangle whenever *any* edge was present. The
    border0..3-to-physical-edge mapping (top/left/right/bottom) was
    confirmed empirically against this same real document earlier in
    this project's development."""
    from riscos_impression.output.pdfdoc import PDFConverter

    document, PDFConverter_ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(
        x0=0, y0=0, x1=100000, y1=50000,
        border0=1, border1=0xFF, border2=0xFF, border3=1,  # top + bottom only
        border_colour_word=0,
    )
    converter._draw_box(frame)
    content = "".join(converter._content)

    # Top edge (y=50.5, offset half the 1pt line width outside the
    # frame's own box -- see _draw_line_with_caps) and bottom edge
    # (y=-0.5) as horizontal line segments.
    assert "0 50.5 m 100 50.5 l S" in content
    assert "0 -0.5 m 100 -0.5 l S" in content
    # No vertical (left/right) edge segments at all.
    assert "-0.5 0 m -0.5 50 l S" not in content
    assert "100.5 0 m 100.5 50 l S" not in content
    # And no full-rectangle stroke (the old, wrong behaviour).
    assert " re S" not in content
    # Both ends of both lines are open (left and right are both
    # absent), so each gets a round cap.
    assert content.count("h f\n") == 4


def test_draw_box_draws_all_four_edges_when_all_present():
    from riscos_impression.output.pdfdoc import PDFConverter

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=1, border1=1, border2=1, border3=1, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    # Style 1 ("Border 2") is a 1pt line, offset outward by half its own
    # width (0.5) so it doesn't encroach into the frame's own box.
    assert "0 50.5 m 100 50.5 l S" in content
    assert "0 -0.5 m 100 -0.5 l S" in content
    assert "-0.5 0 m -0.5 50 l S" in content
    assert "100.5 0 m 100.5 50 l S" in content
    # Every one of the four edges' own two ends is round-capped, even
    # where a neighbouring edge is present -- confirmed against
    # TestDoc-Real2Border2+3.png: "Border 2"/"Border 3" are rounded at
    # all four of their own ends, not only the one end that reference
    # image happens to leave open.
    assert content.count("h f\n") == 8


def test_draw_box_border_style_1_draws_only_a_plain_line_no_band():
    # Regression test for a since-corrected assumption: this project's
    # PDF converter used to draw a fixed grey shadow band around *every*
    # bordered frame regardless of which of Impression's own ten border
    # styles the stored border0..3 byte actually selected -- inherited
    # from the original C DDL emitter's own hardcoded behaviour (see
    # _SHADOW_WIDTH_PT's own docstring), which never distinguished them
    # either. Measuring a controlled test document's own labelled
    # reference frames (corpus/TestDoc,bc5 page 2) showed the band is
    # specific to styles 4-7 ("Border 4" onward); style 1 ("Border 1",
    # stored as byte 0) is a plain line only.
    from riscos_impression.output.pdfdoc import PDFConverter

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=0, border1=0, border2=0, border3=0, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    # 0.5pt line, offset outward by half its own width (0.25).
    assert "0 50.25 m 100 50.25 l S" in content
    assert " re f" not in content  # no filled band


def test_draw_box_border_style_4_draws_a_filled_mitred_band():
    # Style byte 3 ("Border 4" in Impression's own 1-based UI numbering,
    # see model.frames.Frame.has_border) is a solid mid-grey band
    # outside the frame's own box, mitred 45 degrees at each end (a
    # picture-frame moulding, not a plain axis-aligned rectangle) --
    # confirmed against TestDoc-Real2Border4+5.png, which also showed a
    # markedly thicker keyline on the band's own outer edge only.
    from riscos_impression.output.pdfdoc import PDFConverter, _SHADOW_WIDTH_PT, _fmt

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=3, border1=3, border2=3, border3=3, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    sw = _SHADOW_WIDTH_PT
    # A grey fill colour set before the band.
    assert "0.471 0.471 0.471 rg" in content
    # Top band's quad: outer corners mitred 45 degrees out from the
    # frame's own top-left/top-right corners, inner edge exactly on the
    # frame's own top edge.
    assert f"{_fmt(-sw)} {_fmt(50 + sw)} m" in content
    assert f"{_fmt(100 + sw)} {_fmt(50 + sw)} l" in content
    assert f"{_fmt(100)} {_fmt(50)} l" in content
    assert f"{_fmt(0)} {_fmt(50)} l" in content
    # A markedly thicker keyline along the band's own outer edge only.
    assert f"{_fmt(-sw)} {_fmt(50 + sw)} m {_fmt(100 + sw)} {_fmt(50 + sw)} l S" in content
    assert "2 w" in content  # border_width_pt(0.5) * 4


def test_draw_box_border_style_5_draws_a_light_grey_fill_band():
    # Style byte 4 ("Border 5") is the same mitred band shape as style 3
    # ("Border 4"), but light grey rather than mid-grey -- confirmed
    # directly by the user after TestDoc-Real2Border4+5.png's own
    # "Border 5" reference frame (rendered as a transparency checkerboard
    # in that particular screenshot -- Impression's own editor showing
    # an unfilled selection state, not the style's actual printed
    # appearance) was briefly, incorrectly taken to mean the style has
    # no fill at all.
    from riscos_impression.output.pdfdoc import PDFConverter

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=4, border1=4, border2=4, border3=4, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    assert "0.886 0.886 0.886 rg" in content  # _BORDER5_COLOUR_RGB
    assert "h\nf\n" in content  # the band is actually filled


def test_draw_box_border_style_6_offset_shadow_with_thin_outline():
    # Style byte 5 ("Border 6") is a thin outline at the frame's own
    # true edge, plus a solid band outside it that's full-length at one
    # end (extending sw past the frame's own corner) and stops sw short
    # of the other -- rotating consistently around the box. Derived by
    # tracing the exact pixel extent of TestDoc-Real2Border6+7.png's own
    # single "Border 6" reference frame; supersedes an earlier version
    # (derived from a different, ambiguous multi-frame reference image)
    # that retracted the short end by the wrong amount and omitted the
    # thin outline entirely.
    from riscos_impression.output.pdfdoc import PDFConverter, _SHADOW_WIDTH_PT, _fmt

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=5, border1=5, border2=5, border3=5, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    sw = _SHADOW_WIDTH_PT
    # Thin outline at the frame's own true top edge.
    assert "0 50 m 100 50 l S" in content
    # Top band: full-left (extends to x0-sw), short-right (retracts to x1-sw).
    assert f"{_fmt(-sw)} {_fmt(50)} {_fmt(100)} {_fmt(sw)} re f" in content
    # Right band: short-bottom (retracts to y0+sw), full-top (extends to y1+sw).
    assert f"{_fmt(100)} {_fmt(sw)} {_fmt(sw)} {_fmt(50)} re f" in content
    # Bottom band: short-left (retracts to x0+sw), full-right (extends to x1+sw).
    assert f"{_fmt(sw)} {_fmt(-sw)} {_fmt(100)} {_fmt(sw)} re f" in content


def test_draw_box_border_style_10_on_every_edge_draws_one_rounded_stroke():
    # Style byte 9 ("Border 10") is a thick line with rounded corners --
    # confirmed against TestDoc-Real2NoDots.png's own "Border 10"
    # reference frame. Rounding needs to know about two edges at once,
    # so it's only attempted (as a single whole-frame stroke, not four
    # independent edges) when every edge shares the style.
    from riscos_impression.output.pdfdoc import PDFConverter

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=9, border1=9, border2=9, border3=9, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    assert content.count(" c\n") == 4  # four Bezier corner arcs
    assert "0 50 m 100 50 l S" not in content  # not drawn as four square-cornered edges


def test_draw_box_border_style_10_with_one_edge_absent_still_rounds_both_corners():
    # Style 9 ("Border 10") applied to only three of a frame's four
    # edges still uses the single rounded-rectangle path, not four
    # independent straight edges -- confirmed against
    # TestDoc-Real2Border10.png's own "Border 10, no left" reference
    # frame: both corner arcs adjoining the missing left edge are still
    # drawn as complete quarter-circles (all four corner arcs present),
    # simply with no straight run between them where the left edge
    # would have been.
    from riscos_impression.output.pdfdoc import PDFConverter

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(
        x0=0, y0=0, x1=100000, y1=50000,
        border0=9, border1=0xFF, border2=9, border3=9,  # no left
        border_colour_word=0,
    )
    converter._draw_box(frame)
    content = "".join(converter._content)

    assert content.count(" c\n") == 4  # all four corner arcs still drawn
    assert "h S\n" not in content  # path isn't closed -- the left edge has a real gap
    assert content.count(" m\n") == 2  # two disconnected subpaths either side of the gap


def test_draw_box_border_style_10_sits_a_gap_outside_the_frame():
    # Unlike every other line-family style, Border 10 doesn't touch the
    # frame's own boundary directly -- the user confirmed it still
    # looked too close to the frame after the earlier non-encroaching
    # fix, and TestDoc-Real2Border10.png's own dotted frame-bounds
    # marker (confirmed not to be part of the border itself) sits well
    # clear of the rounded line. Checked here via the single-edge
    # style-9 fallback (mixed with another style, so the whole-frame
    # rounded path in _draw_rounded_border doesn't apply), which is
    # the simpler of the two code paths to assert an exact offset
    # against.
    from riscos_impression.output.pdfdoc import PDFConverter, _fmt

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(
        x0=0, y0=0, x1=100000, y1=50000,
        border0=9, border1=0xFF, border2=0xFF, border3=1,  # top is Border 10, bottom is Border 2
        border_colour_word=0,
    )
    converter._draw_box(frame)
    content = "".join(converter._content)

    thick = 0.5 * 7.0  # border_width_pt(0.5) * 7
    gap = thick * 3.0
    y = 50 + gap + thick / 2.0
    assert f"{_fmt(0)} {_fmt(y)} m {_fmt(100)} {_fmt(y)} l S" in content


def test_draw_box_border_style_7_lines_meet_cleanly_at_every_corner():
    # Style byte 7 ("Border 8") is a thin line at the frame's own
    # boundary plus a thicker one further out -- both extended by their
    # own offset at each end so they reach exactly the point a
    # same-offset perpendicular neighbour's own line would cross, and
    # so meet with a clean mitred corner instead of a small diagonal
    # gap. An earlier version drew each line only the frame's own edge
    # length (x0 to x1 / y0 to y1), leaving that gap -- the user
    # reported this directly ("8 and 9 don't seem to meet at the
    # corners"), and TestDoc-Real2Border8+9NoDotted.png's own "Border
    # 8" reference frame confirmed every corner should be fully closed.
    from riscos_impression.output.pdfdoc import PDFConverter, _fmt

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=7, border1=7, border2=7, border3=7, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    thin, med = 0.5, 1.0
    gap = thin * 3.0
    outer_offset = thin + gap + med / 2.0
    y = 50 + outer_offset
    # The outer line's top run extends past both x0 and x1 by its own
    # offset, rather than stopping exactly at the frame's own corners.
    assert f"{_fmt(-outer_offset)} {_fmt(y)} m {_fmt(100 + outer_offset)} {_fmt(y)} l S" in content


def test_draw_box_border_style_8_lines_meet_cleanly_at_every_corner():
    # Style byte 8 ("Border 9"): same corner-meeting fix as style 7
    # (_draw_border_ring_line), for its own two thicker, further-apart
    # lines -- an earlier version instead deliberately inset both lines
    # from every corner, based on a misreading of an earlier, less
    # clear reference image; TestDoc-Real2Border8+9NoDotted.png's own
    # "Border 9" reference frame shows fully closed corners, matching
    # the user's own direct report.
    from riscos_impression.output.pdfdoc import PDFConverter, _fmt

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000, border0=8, border1=8, border2=8, border3=8, border_colour_word=0)
    converter._draw_box(frame)
    content = "".join(converter._content)

    med = 1.0
    gap = 0.5 * 4.5
    outer_offset = med + gap + med / 2.0
    y = 50 + outer_offset
    assert f"{_fmt(-outer_offset)} {_fmt(y)} m {_fmt(100 + outer_offset)} {_fmt(y)} l S" in content


def test_draw_box_without_a_border_draws_no_shadow():
    from riscos_impression.output.pdfdoc import PDFConverter

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._origin = (0, 0)
    converter._content = []

    frame = _frame(x0=0, y0=0, x1=100000, y1=50000)  # default border0..3 = 0xFF (absent)
    converter._draw_box(frame)
    content = "".join(converter._content)

    assert content == ""  # no fill (unfilled), no border, no shadow -- nothing to draw at all


def test_first_line_baseline_uses_ascent_not_full_line_height(tmp_path):
    from riscos_impression.output.pdfdoc import _ascent_pt, _fmt

    document, PDFConverter = _document_with_one_text_frame()
    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    body = _style(0, is_body_text=True, font_size=160)
    expected_y = 100.0 - _ascent_pt(body)  # 100pt-tall frame's own top edge
    assert f"1 0 0 1 0 {_fmt(expected_y)} Tm".encode("latin-1") in data


def test_render_line_applies_font_aspect_ratio_via_tz(tmp_path):
    # Regression test: the user reported a real document's own table
    # header, styled with a 140%-ish font_aspect_ratio, rendering with
    # regular (unstretched) text -- _render_line never emitted PDF's
    # own Tz (horizontal scaling) operator at all, unlike
    # _draw_drawfile_text (used only for text *within* a DrawFile
    # picture), which already did. font_aspect_ratio maps directly
    # onto a PDF Tz percentage (0x10000 raw = unity, no inversion --
    # confirmed against ovprodll.py's own DDL emission, which maps
    # this field straight onto a style's "scale" property). Tz is
    # text *state*, not reset by ET/BT like Tm, so a later token with
    # no aspect ratio at all must still explicitly reset to 100%, or
    # it would silently inherit an earlier token's own stretched value.
    from riscos_impression.output.pdfdoc import PDFConverter, _Token

    document, _ = _document_with_one_text_frame()
    converter = PDFConverter(document)
    converter.begin_document()
    converter._content = []

    stretched = _style(1, font_size=160, font_aspect_ratio=0x8000)  # 50%
    normal = _style(2, font_size=160)
    tokens = [_Token("word", "Half", stretched), _Token("word", "Normal", normal)]
    converter._render_line(tokens, start_x=0.0, tab_base_x=0.0, right_edge=1000.0, y=0.0, justify=False, alignment=None)
    content = "".join(converter._content)

    assert "50 Tz" in content
    assert "100 Tz" in content
    assert content.index("50 Tz") < content.index("(Half)") < content.index("100 Tz") < content.index("(Normal)")


def test_approx_width_scales_with_font_aspect_ratio():
    # _approx_width folds in font_aspect_ratio (rather than each of
    # its own several call sites separately) so line wrapping,
    # tab-stop/segment-width measurement, and justification all stay
    # consistent with what Tz actually draws -- confirmed against the
    # same real document: applying Tz alone (without this) stretched a
    # table header's own text wide enough to visibly overlap the next
    # column, since the column's own tab stop was still positioned
    # assuming 100%-width text.
    from riscos_impression.output.pdfdoc import _approx_width

    normal = _style(0, is_body_text=True, font_size=160)
    doubled = _style(1, font_size=160, font_aspect_ratio=0x20000)  # 200%
    assert round(_approx_width("Hello", doubled), 3) == round(2 * _approx_width("Hello", normal), 3)


def test_convert_produces_a_well_formed_pdf(tmp_path):
    document, PDFConverter = _document_with_one_text_frame()
    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)

    data = out.read_bytes()
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in data
    assert b"/Type /Pages" in data
    assert b"/Type /Page " in data or b"/Type /Page\n" in data
    assert b"(Hello) Tj" in data
    assert not converter.log.has_errors()


def test_later_chain_frame_does_not_paint_over_already_rendered_text(tmp_path):
    """Regression test: a real document (PBServer2 from the local
    examples/ corpus) has two TextFrame records on one page sharing the
    same dictionary_index, where the second's box fully encloses the
    first's. Since a story is only rendered (clipped) in the first chain
    frame encountered (see the module docstring), the second frame's own
    box was still being drawn on top of it -- an opaque white fill
    painted directly over already-placed, still-selectable text. Once a
    story's text has been placed, no later frame sharing that story
    should draw its own box either.
    """
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    small_frame = _frame(
        x0=30000, y0=60000, x1=90000, y1=90000, filled=True,
        fill_colour_word=0x0000FF00, dictionary_index=0,
    )
    large_frame = _frame(
        # Fully encloses small_frame's box, and comes after it on the page.
        x0=0, y0=0, x1=100000, y1=100000, filled=True,
        fill_colour_word=0x0000FF00, dictionary_index=0,
    )
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, small_frame), _frame_record(1108, large_frame)),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Visible", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Visible) Tj" in data
    # The large frame's own fill rectangle (spanning the whole 0..100pt
    # box) must not appear -- only the small frame's fill should be drawn.
    assert b"0 0 100 100 re f" not in data


def test_positive_right_indent_is_an_offset_from_the_frames_own_left_edge(tmp_path):
    """Regression test: a real document (PCI_Spec from the local
    examples/ corpus) has a style whose right_indent very nearly equals
    the entire width of the frame it's actually used in -- this project
    originally assumed right_indent is always a delta/inset from the
    frame's own right edge, which left less than _MIN_USABLE_WIDTH on
    every line, dropping every paragraph using that style (and every
    paragraph after it in the whole story). The user pointed out that
    the document's own styles show no such oversized margin, tracing it
    to Style.right_indent_is_delta: a POSITIVE right_indent_raw (DDL
    kind 2, INDENTOFFSET, per c/styles in the sibling riscos-source
    repo) is an offset from the frame's own LEFT edge, not an inset
    from its right -- confirmed against a real DDF export the user
    supplied, generated by Impression itself: its base style declares
    leftmargin 19.8pt, rightmargin 510.2pt on a frame that's itself
    510.2pt wide, which only makes sense read as an offset from the
    left (landing almost exactly at the frame's own right edge, a
    normal narrow margin), not as an inset from the right (which would
    leave nothing at all). A right_indent_raw of 95pt on a 100pt-wide
    frame should therefore leave 95pt of real usable width, not 5pt."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # Frame is 100pt wide; a POSITIVE right_indent_raw of 95pt is an
    # offset from the frame's own left edge, leaving 95pt of usable
    # width -- not an inset from the right edge (which would leave 5pt).
    indented = _style(1, font_size=160, right_indent_raw=95000, paragraph_apply=True)
    frame = _frame(x0=0, y0=0, x1=100000, y1=100000, dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body, indented], header=header
    )
    document.dictionary.append(dict_entry)
    # Long enough that it would need to wrap onto several single-word
    # lines within a genuinely squeezed ~5pt column, but fits on one
    # line within the correctly-resolved ~95pt one.
    story = Story(
        frame_chain=(),
        paragraphs=(
            Paragraph(items=(Run(text="Indented Text Here", style_slots=(1,)),)),
            Paragraph(items=(Run(text="AfterIt", style_slots=()),)),
        ),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    content = out.read_bytes().decode("latin-1")

    # All three words must share the same line (the same Tm y
    # coordinate) -- a genuinely squeezed ~5pt column would force each
    # onto its own line instead, each with a different y.
    ys = [
        float(re.search(rf"1 0 0 1 [\d.]+ ([\d.]+) Tm\n.*?\({word}\) Tj", content, re.S).group(1))
        for word in ("Indented", "Text", "Here")
    ]
    assert len(set(ys)) == 1
    assert "(AfterIt) Tj" in content
    assert not converter.log.has_errors()


def test_positive_right_indent_combines_correctly_with_a_real_left_indent(tmp_path):
    """Regression test: a real document's title-block style (PCI_Spec)
    has BOTH a large left_indent (a label column, ~113pt in) and a
    POSITIVE right_indent (295pt, close to the frame's own 300pt width)
    -- confirmed against the user's own reference image (labels like
    "Distribution:" start well right of the frame's own edge) and
    against Impression's own ruler dialog (a "left bound" of about
    4cm). Before understanding right_indent_is_delta correctly (see the
    test above), this project's old right_indent-as-inset-from-the-
    right-edge assumption combined with this real left_indent to leave
    under _MIN_USABLE_WIDTH, triggering a fallback that reset the label
    flush against the frame's own edge -- wiping out the real,
    intentional indent. Resolving a positive right_indent as an offset
    from the frame's own LEFT edge instead (independent of left_indent)
    needs no such fallback at all: the label lands at its own real
    113pt position, with 182pt of genuine room after it."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # Frame is 300pt wide; left_indent of 113pt, right_indent_raw of
    # 295pt (positive -- an offset from the frame's own left edge, so
    # the column runs from 113pt to 295pt: 182pt of real room).
    labelled = _style(1, font_size=160, left_indent=113000, right_indent_raw=295000, paragraph_apply=True)
    frame = _frame(x0=0, y0=0, x1=300000, y1=100000, dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=300000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=300000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body, labelled], header=header
    )
    document.dictionary.append(dict_entry)
    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(Run(text="Distribution:", style_slots=(1,)),)),),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()
    content = data.decode("latin-1")

    assert "(Distribution:) Tj" in content
    assert not converter.log.has_errors()

    # The label's own Tm x-coordinate must reflect the real 113pt
    # indent, not 0 (which is what unconditionally resetting line_start
    # back to the container's own left edge would produce).
    match = re.search(r"1 0 0 1 ([\d.]+) [\d.]+ Tm\n.*?\(Distribution:\) Tj", content, re.S)
    assert match is not None
    assert float(match.group(1)) > 100.0


def test_negative_right_indent_is_an_inset_from_the_frames_own_right_edge(tmp_path):
    """A NEGATIVE right_indent_raw (DDL kind 0 -- the conversion
    source's own default/else-branch formula, per c/styles) is a
    genuine inset from the frame's own right edge, unlike the positive
    (kind 2, offset-from-the-left) case covered above. An oversized
    one -- deliberately contrived here, since no real document in the
    local corpus has been found using this sign -- still needs the
    defensive full-width fallback: without it, tokens are never
    consumed and the paragraph (and everything after it in the story)
    would be silently dropped."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # Frame is 100pt wide; right_indent_raw=-95000 (negative -> kind 0,
    # an inset from the right edge) leaves only 5pt, well under
    # _MIN_USABLE_WIDTH (10pt).
    indented = _style(1, font_size=160, right_indent_raw=-95000, paragraph_apply=True)
    frame = _frame(x0=0, y0=0, x1=100000, y1=100000, dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body, indented], header=header
    )
    document.dictionary.append(dict_entry)
    story = Story(
        frame_chain=(),
        paragraphs=(
            Paragraph(items=(Run(text="Indented", style_slots=(1,)),)),
            Paragraph(items=(Run(text="AfterIt", style_slots=()),)),
        ),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Indented) Tj" in data
    assert b"(AfterIt) Tj" in data
    assert not converter.log.has_errors()


def test_paragraph_tokens_leading_mark_uses_the_paragraphs_own_style():
    # Regression test: a real document's own numbered contents list
    # (PCI_Spec) starts each paragraph with a TabMark, before any Run,
    # to right-align the chapter number against a dedicated style's own
    # tab ruler. That leading mark had no Run of its own to inherit a
    # style from yet, so it fell back to the *document's* body style
    # instead of the paragraph's own applied one -- using the wrong tab
    # ruler for exactly the tab that was supposed to right-align the
    # number, while every later tab in the same paragraph correctly
    # used the right one (style is set per-Run as they're encountered).
    # This produced inconsistent alignment from row to row, since each
    # row's own text interacted differently with the wrong ruler.
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    numbered = _style(1, font_size=160, tab_stops=(TabStop(kind=2, position=50000),))
    document = _document(styles=[body, numbered])
    converter = PDFConverter(document)
    converter.begin_document()
    chapter = Chapter(section=_section(), offset=0, master_page_1=None, master_page_2=None, pages=())

    paragraph = Paragraph(items=(TabMark(), Run(text="1", style_slots=(1,))))
    tokens, para_style = converter._paragraph_tokens(paragraph, dictionary_index=0, body_style=body, chapter=chapter)

    assert tokens[0].kind == "tab"
    assert tokens[0].style.tab_stops == numbered.tab_stops
    assert para_style.tab_stops == numbered.tab_stops


def test_centre_and_right_tabs_keep_a_short_line_together(tmp_path):
    """Regression test: a real document (PCI_Spec from the local
    examples/ corpus) has a footer paragraph "Sheet <n><tab><tab>Issue
    F ****LIVE****" whose style has a centre tab then a right tab, no
    left tabs at all. Treating both as plain left tabs (jump to the
    stop, text starts there) landed the second tab's target so close
    to the frame's own right edge that the whole "Issue F ****LIVE****"
    segment overflowed past it and wrapped to a second line -- which
    the frame (exactly one line tall) had no room for, silently
    dropping the text entirely rather than just misplacing it. A right
    tab must position its segment so it *ends* at the stop, not starts
    there.
    """
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160, tab_stops=(TabStop(kind=1, position=100000), TabStop(kind=2, position=190000)))
    # Frame is 200pt wide, exactly one line tall.
    frame = _frame(x0=0, y0=0, x1=200000, y1=14000, dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=200000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=200000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)
    story = Story(
        frame_chain=(),
        paragraphs=(
            Paragraph(
                items=(
                    Run(text="Sheet 1", style_slots=()),
                    TabMark(),
                    TabMark(),
                    Run(text="Issue F LIVE", style_slots=()),
                )
            ),
        ),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Sheet) Tj" in data
    assert b"(LIVE) Tj" in data  # the last word; only present if the whole segment made it onto the line
    assert not any("overflowed" in e.message for e in converter.log.entries)


def test_embed_frame_map_finds_a_frame_by_its_embed_tag():
    from riscos_impression.output.pdfdoc import PDFConverter

    picture = _picture(embed_tag=42, dictionary_index=1)
    other = _picture(embed_tag=0, dictionary_index=2)  # not embedded; must be ignored
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=100000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, picture), _frame_record(1108, other)),
    )
    chapter = Chapter(section=_section(), offset=900, master_page_1=None, master_page_2=None, pages=(page,))
    converter = PDFConverter(_document())
    converter.begin_document()

    mapping = converter._embed_frame_map(chapter)

    assert mapping == {42: picture}


def test_inline_drawfile_picture_pushes_following_text_below_it(tmp_path):
    """Regression test: the user reported that PCI_Spec's inline
    DrawFile diagrams (referenced from the story via an EmbedMark, and
    carried by a PictureFrame with a matching non-zero embed_tag) were
    overlaying the running text instead of pushing it down -- because
    the referenced frame was drawn independently at its own raw,
    page-relative box (see docs/impression-documents.xml, "Frame
    object common layout": an embed-tagged frame is "anchored inline
    within a text story... rather than being placed directly on the
    page in normal front-to-back order"), while the story's own text
    flow just skipped the EmbedMark entirely and carried on as if the
    picture didn't exist -- so the two independently-positioned things
    visually collided wherever the picture's raw box happened to
    intersect the running text.

    This drives a real conversion end-to-end and checks the actual
    computed Y coordinates: "Before" sits at its own ascent-based
    first-line position, the picture block starts right below it, at
    the *frame's own real box size* (60pt x 40pt here -- confirmed
    against a real document that this, not the paragraph's own much
    wider column, is a picture's true intended on-page size), and
    "After" sits below *that*, using the normal line-height drop --
    not overlapping either the picture or "Before".
    """
    from riscos_impression.output.pdfdoc import PDFConverter, _ascent_pt, _fmt, _line_height_pt

    body = _style(0, is_body_text=True, font_size=160)  # 10pt
    text_frame = _frame(x0=0, y0=0, x1=200000, y1=300000, dictionary_index=0)  # 200pt-wide column
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    # A 60pt x 40pt frame box -- its own real on-page size, well
    # within the 200pt-wide text column -- deliberately placed at an
    # unrelated raw page position far from the text frame; if this raw
    # position leaked into the output at all, the picture would be
    # drawn in the wrong place (or drawn twice); it must not appear.
    picture_frame = _picture(
        x0=500000, y0=500000, x1=560000, y1=540000, embed_tag=42, dictionary_index=1,
    )
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=600000, y1=600000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, text_frame), _frame_record(1108, picture_frame)),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=600000, y1=600000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)
    text_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    picture_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.extend([text_entry, picture_entry])
    document.picture_bytes = lambda entry: build_drawfile(path, bounds=(0, 0, 1000, 1000))

    story = Story(
        frame_chain=(),
        paragraphs=(
            Paragraph(
                items=(
                    Run(text="Before", style_slots=()),
                    EmbedMark(embed_tag=42),
                    Run(text="After", style_slots=()),
                )
            ),
        ),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()
    content = data.decode("latin-1")

    assert not converter.log.has_errors()
    assert content.count("(Before) Tj") == 1
    assert content.count("(After) Tj") == 1
    # The fill colour operator is unique to this one path; it must
    # appear exactly once -- twice would mean the picture was ALSO
    # drawn independently at its own raw page position (the exact bug
    # being fixed), not just inline.
    assert content.count("1 0 0 rg") == 1  # 0x0000FF00 -> pure red, see colour_rgb

    resolved = converter.resolve_style(())
    before_y = 300.0 - _ascent_pt(resolved)
    embed_height = 40.0  # the frame's own real height (60pt x 40pt), unrelated to the 200pt column
    embed_y1 = before_y
    embed_y0 = embed_y1 - embed_height
    after_y = embed_y0 - _line_height_pt(resolved)

    assert f"1 0 0 1 0 {_fmt(before_y)} Tm" in content
    assert f"1 0 0 1 0 {_fmt(after_y)} Tm" in content


def test_inline_drawfile_picture_honours_a_centre_alignment_effect(tmp_path):
    """Regression test: a real document's inline picture sat inside a
    paragraph carrying a "Centre" alignment effect (confirmed via the
    document's own EmbedMark.style_slots), and the picture -- narrower
    than its own text column -- was left flush against the column's
    left edge instead of centred within it, unlike ordinary text lines
    (which already honour alignment via _render_line)."""
    from riscos_impression.output.pdfdoc import PDFConverter, _fmt

    body = _style(0, is_body_text=True, font_size=160)
    centred = _style(1, alignment=1, paragraph_apply=True)  # centre
    text_frame = _frame(x0=0, y0=0, x1=200000, y1=300000, dictionary_index=0)  # 200pt-wide column
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    picture_frame = _picture(
        x0=500000, y0=500000, x1=560000, y1=540000, embed_tag=42, dictionary_index=1,
    )  # 60pt x 40pt, narrower than the 200pt column
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=600000, y1=600000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, text_frame), _frame_record(1108, picture_frame)),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=600000, y1=600000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body, centred], header=header)
    text_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    picture_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.extend([text_entry, picture_entry])
    document.picture_bytes = lambda entry: build_drawfile(path, bounds=(0, 0, 1000, 1000))

    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(EmbedMark(embed_tag=42, style_slots=(1,)),)),),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()
    content = data.decode("latin-1")

    assert not converter.log.has_errors()
    # 60pt-wide picture centred in a 200pt column -> 70pt margin either side.
    expected_x0 = 70.0
    assert f"{_fmt(expected_x0)} " in content
    # The clip rect for the embed block should start at the centred x0.
    assert f"{_fmt(expected_x0)} 260 60 40 re W n" in content


def test_inline_drawfile_picture_shrinks_to_fit_a_narrower_column(tmp_path):
    """A frame whose own real box is wider than the paragraph's
    current column can't be placed at full size; it should shrink
    (preserving aspect) to the column's own width instead of
    overflowing it, the one case _inline_drawfile_picture_pushes...
    above doesn't cover (there, the frame already fit)."""
    from riscos_impression.output.pdfdoc import PDFConverter, _fmt, _line_height_pt

    body = _style(0, is_body_text=True, font_size=160)
    text_frame = _frame(x0=0, y0=0, x1=100000, y1=300000, dictionary_index=0)  # 100pt-wide column
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    # 200pt x 100pt frame (2:1 aspect) -- wider than the 100pt column.
    picture_frame = _picture(
        x0=500000, y0=500000, x1=700000, y1=600000, embed_tag=42, dictionary_index=1,
    )
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=800000, y1=800000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, text_frame), _frame_record(1108, picture_frame)),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=800000, y1=800000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)
    text_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    picture_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.extend([text_entry, picture_entry])
    document.picture_bytes = lambda entry: build_drawfile(path, bounds=(0, 0, 1000, 1000))

    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(EmbedMark(embed_tag=42), Run(text="After", style_slots=()))),),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()
    content = data.decode("latin-1")

    assert not converter.log.has_errors()
    resolved = converter.resolve_style(())
    # Shrunk to the 100pt column width; aspect (2:1) preserved -> 50pt tall.
    embed_y1 = 300.0
    embed_height = 100.0 * (100.0 / 200.0)
    embed_y0 = embed_y1 - embed_height
    # first_line_pending is cleared once the embed itself is placed, so
    # "After" (the paragraph's next item) uses the normal line-height
    # drop, not an ascent-only one -- that only applies to the very
    # first thing placed into a fresh container.
    after_y = embed_y0 - _line_height_pt(resolved)

    assert f"1 0 0 1 0 {_fmt(after_y)} Tm" in content


def test_multi_page_chain_flows_text_across_frames(tmp_path):
    """A story whose frame_chain fully resolves against the chapter's own
    content pages is a real, flowing chain (like PBServer2's actual
    letter body): text that doesn't fit the first frame should continue
    into the next chain member instead of being clipped, with no
    best_effort overflow note logged once it all fits somewhere."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # frame1 is deliberately just tall enough for one line; frame2 is
    # roomy, so the content (five short, one-word paragraphs) must spill
    # from frame1 into frame2 to all fit.
    frame1 = _frame(x0=0, y0=0, x1=100000, y1=15000, dictionary_index=0)
    frame2 = _frame(x0=0, y0=0, x1=100000, y1=300000, dictionary_index=0)
    page1 = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame1),),
    )
    page2 = PageGroup(
        page=Page(x0=0, y0=150000, x1=100000, y1=450000, bleed=0, master_page_name=""),
        offset=2000,
        records=(_frame_record(2008, frame2),),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page1, page2)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)
    # frame1 is at offset 1008; frame2's on-disk chain offset (relative to
    # mainpages2) is 2008 - 900 = 1108 (single-file mode; see
    # Converter.resolve_frame_chain).
    story = Story(
        frame_chain=(1108,),
        paragraphs=tuple(Paragraph(items=(Run(text=f"Para{i}", style_slots=()),)) for i in range(5)),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Para0) Tj" in data
    assert b"(Para4) Tj" in data  # only reachable if flow continued into frame2
    assert not any("overflow" in e.message for e in converter.log.entries)


def test_forced_page_break_advances_to_the_next_container_leaving_the_skipped_one_empty():
    """Regression test: a real document (ForSimon3 from the local
    moreexamples/ corpus) has its body paragraph followed by TWO
    consecutive PageBreakMarks (CTRL_N -- "force to next" in the
    conversion source, c/styles' txwritedata, which emits a DDL
    {newpage} for it, not a plain newline) before its final heading
    paragraph, across a three-frame chain -- meant to leave the middle
    frame blank and land the heading on the third. Treating
    PageBreakMark as merely an in-line blank line (the previous
    behaviour, via _wrap_one_line consuming it like an ordinary line
    terminator) left the heading on the SECOND frame instead, with the
    real third frame sitting entirely empty."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    document = _document(styles=[body])
    converter = PDFConverter(document)

    containers = [
        (1, 1, 0.0, 0.0, 200.0, 100.0),
        (2, 2, 0.0, 0.0, 200.0, 100.0),
        (3, 3, 0.0, 0.0, 200.0, 100.0),
    ]
    paragraphs = (
        Paragraph(items=(Run(text="First", style_slots=()), PageBreakMark(), PageBreakMark())),
        Paragraph(items=(Run(text="Third", style_slots=()),)),
    )
    assignments = converter._flow_paragraphs_into_containers(paragraphs, 0, containers, None)

    def text_in(key):
        return "".join(
            tok.text for entry in assignments[key] for tok in entry[0] if tok.kind in ("word", "space")
        )

    assert "First" in text_in(1)
    assert text_in(2) == ""
    assert "Third" in text_in(3)


def test_space_before_adds_a_gap_before_a_paragraph_but_not_at_a_containers_top():
    """Regression test: a real document (Telegraph from the local
    moreexamples/ corpus) has a heading style with spaceabove 20pt, but
    space_before was never consumed anywhere in this converter -- the
    gap between the preceding paragraph and the heading was simply
    missing from the PDF. Applied between paragraphs (added on top of
    the normal line-to-line gap, same as space_after already is), but
    suppressed for a paragraph that starts fresh at a container's own
    top (no preceding paragraph in that container to space away from)."""
    from riscos_impression.output.pdfdoc import PDFConverter, _ascent_pt, _line_height_pt

    body = _style(0, is_body_text=True, font_size=160)  # 10pt, no space_before
    heading = _style(1, font_size=280, space_before=20000)  # 28pt style, 20pt space_before
    document = _document(styles=[body, heading])
    converter = PDFConverter(document)

    containers = [(1, 1, 0.0, 0.0, 300.0, 300.0)]
    paragraphs = (
        Paragraph(items=(Run(text="First", style_slots=()),)),
        Paragraph(items=(Run(text="Second", style_slots=(1,)),)),
    )
    assignments = converter._flow_paragraphs_into_containers(paragraphs, 0, containers, None)
    lines = assignments[1]
    first_y = lines[0][4]
    second_y = lines[1][4]

    resolved_body = converter.resolve_style(())
    resolved_heading = converter.resolve_style((1,))
    expected_first_y = 300.0 - _ascent_pt(resolved_body)
    expected_second_y = first_y - 20.0 - _line_height_pt(resolved_heading)

    assert first_y == expected_first_y  # unaffected: fresh container, no space_before applied
    assert second_y == expected_second_y


def test_side_by_side_containers_do_not_share_a_page_floor():
    """Regression test: a real document (ForDad from the local
    moreexamples/ corpus) chains four caption frames laid out two-by-
    two on one page (top-left, top-right, bottom-left, bottom-right),
    each following the previous with a PageBreakMark. advance_container
    used to clamp a freshly-entered container's starting Y down to the
    lowest point *any* earlier container on the same page had reached
    (page_floor, keyed by page_key) -- correct for the documented case
    of a narrow frame chaining into a full-width one below it (whose
    box genuinely, horizontally overlaps), but wrong for side-by-side
    cells that never overlap at all: the top-right frame inherited the
    top-left's own leftover Y position, leaving it almost no room, and
    its own content (which fits easily in its full height) overflowed
    into a third container that should have stayed empty -- landing
    "Through rain," visually below "Through sunshine," instead of
    beside it. A later container must only inherit an earlier one's
    floor when their X-ranges actually overlap (not just touch at a
    shared edge)."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    document = _document(styles=[body])
    converter = PDFConverter(document)

    containers = [
        (1, 1, 0.0, 0.0, 100.0, 100.0),  # left
        (2, 1, 100.0, 0.0, 200.0, 100.0),  # right, same page, touching (not overlapping) edge
        (3, 1, 200.0, 0.0, 300.0, 100.0),  # only reachable if the bug regresses
    ]
    paragraphs = (
        # Six lines in the left container push its own Y most of the
        # way down, well past where the right container's own content
        # would land if it wrongly inherited that position.
        Paragraph(items=(Run(text="X", style_slots=()),)),
        *[Paragraph(items=()) for _ in range(5)],
        Paragraph(items=(Run(text="X", style_slots=()), PageBreakMark())),
        # Three lines' worth of text: fits comfortably in the right
        # container's own full height, but not in the sliver left by
        # the (buggy) inherited floor.
        Paragraph(items=(Run(text="One Two Three Four Five Six Seven Eight Nine Ten", style_slots=()),)),
    )
    assignments = converter._flow_paragraphs_into_containers(paragraphs, 0, containers, None)

    def text_in(key):
        return "".join(
            tok.text for entry in assignments[key] for tok in entry[0] if tok.kind in ("word", "space")
        )

    right_text = text_in(2)
    assert "One" in right_text and "Ten" in right_text
    assert text_in(3) == ""


def test_master_anchored_story_renders_independently_without_erroring(tmp_path):
    """Regression test: a real document (funcspec from the local
    examples/ corpus) has stories repeated, unlinked, across several
    chapters via a shared master page. Their frame_chain data (when they
    have any) is anchored to the master page they're defined on, not to
    any particular chapter -- resolving it against the chapter's own
    content pages (as a real chain would need) fails for every offset.
    That must not surface as a "did not resolve" error; it should just
    mean this content isn't a flow at all, and gets laid out fresh,
    independently, wherever it's referenced.
    """
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    frame = _frame(x0=0, y0=0, x1=100000, y1=30000, dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)
    # An offset that resolves to nothing at all within this chapter's own
    # content pages -- simulating a master-page-anchored chain entry.
    story = Story(frame_chain=(999999,), paragraphs=(Paragraph(items=(Run(text="Footer", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Footer) Tj" in data
    assert not converter.log.has_errors()


def test_master_anchored_story_repeated_across_chapters_renders_on_every_chapter(tmp_path):
    """Regression test: a real document (FieldWork) has a running
    footer repeated, unlinked, on every page of every chapter via a
    shared master page -- but it only ever appeared on the very first
    chapter's own page. _resolve_content_chain_quietly resolves a
    story's frame_chain relative to *one particular* chapter (it takes
    chapter as a parameter): for this real footer, resolution happened
    to succeed by coincidence on the very first chapter it was drawn
    in (matching a length-1 "chain" that's really just that one
    chapter's own frame), and every later chapter's own attempt failed
    (its offset only makes sense relative to the chapter it was
    defined against). The resulting layout was cached under
    dictionary_index alone, with no chapter scoping, so every later
    chapter's own (entirely different) frame silently looked up an
    empty result in the first chapter's cached layout instead of ever
    computing its own fresh flow -- the footer only ever appeared on
    its first occurrence, identically to the real bug."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )

    frame1 = _frame(x0=0, y0=0, x1=100000, y1=30000, dictionary_index=0)
    page1 = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame1),),
    )
    chapter1 = Chapter(
        section=_section(create_number=1, master_page_index=0),
        offset=900, master_page_1=master_page, master_page_2=None, pages=(page1,),
    )

    frame2 = _frame(x0=0, y0=0, x1=100000, y1=30000, dictionary_index=0)
    page2 = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=2000,
        records=(_frame_record(2008, frame2),),
    )
    chapter2 = Chapter(
        section=_section(create_number=2, master_page_index=0),
        offset=1900, master_page_1=master_page, master_page_2=None, pages=(page2,),
    )

    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter1, chapter2], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)
    # 1008 - mainpages2(900) = 108: resolves to frame1's own record
    # within chapter1's own pages -- a coincidental length-1 "chain"
    # match, exactly like the real footer's own frame_chain=(320,).
    story = Story(frame_chain=(108,), paragraphs=(Paragraph(items=(Run(text="Footer", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert data.count(b"(Footer) Tj") == 2
    assert not converter.log.has_errors()


def test_narrow_for_obstacles_pushes_in_from_the_nearer_side():
    # Obstacle on the left (closer to `left` than `right`): left edge moves in.
    left, right = _narrow_for_obstacles(0.0, 100.0, y_top=50.0, y_bottom=40.0, obstacles=[(0.0, 30.0, 20.0, 60.0)])
    assert (left, right) == (20.0, 100.0)
    # Obstacle on the right: right edge moves in.
    left, right = _narrow_for_obstacles(0.0, 100.0, y_top=50.0, y_bottom=40.0, obstacles=[(80.0, 30.0, 100.0, 60.0)])
    assert (left, right) == (0.0, 80.0)


def test_narrow_for_obstacles_ignores_obstacles_outside_the_line_band():
    # Obstacle's Y-range doesn't reach this line's [y_bottom, y_top].
    left, right = _narrow_for_obstacles(0.0, 100.0, y_top=50.0, y_bottom=40.0, obstacles=[(0.0, 0.0, 20.0, 10.0)])
    assert (left, right) == (0.0, 100.0)


def test_narrow_for_obstacles_handles_obstacles_on_both_sides():
    obstacles = [(0.0, 30.0, 20.0, 60.0), (80.0, 30.0, 100.0, 60.0)]
    left, right = _narrow_for_obstacles(0.0, 100.0, y_top=50.0, y_bottom=40.0, obstacles=obstacles)
    assert (left, right) == (20.0, 80.0)


def test_text_repels_around_an_obstacle_picture(tmp_path):
    """Regression test for PBServer (from the local examples/ corpus): a
    picture with repel=True should push the body text's lines away from
    it (dynamic text repel), rather than the text simply filling its
    frame's whole box top to bottom while ignoring the obstacle.
    """
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # A picture occupying the left third of the page, tall enough to
    # cover the first several lines of body text.
    picture = _picture(
        x0=0, y0=50000, x1=30000, y1=100000,
        exx0=0, exy0=50000, exx1=30000, exy1=100000,
        repel=True, dictionary_index=1,
    )
    text_frame = _frame(x0=0, y0=0, x1=100000, y1=100000, dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, picture), _frame_record(1108, text_frame)),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Hello", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    idx = data.find(b"(Hello) Tj")
    assert idx != -1
    tm_idx = data.rfind(b"Tm", 0, idx)
    line_start = data.rfind(b"\n", 0, tm_idx) + 1
    tm_line = data[line_start:tm_idx]
    x = float(tm_line.split()[4])
    assert x >= 30.0  # pushed right past the obstacle's own right edge (30pt)


def test_repel_flagged_frame_does_not_obstruct_its_own_text(tmp_path):
    """Regression test: a frame that's itself repel-flagged (e.g. an
    address block meant to push *other* frames' text away from it, like
    PBServer's letterhead) must not treat its own repel box as an
    obstacle when its own text is being laid out -- that previously
    left it with zero usable width anywhere in its own frame, silently
    dropping all of its text.
    """
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # repel box (exx0..exy1) far larger than the frame's own outer box,
    # covering virtually the whole page -- if this obstructed itself,
    # no line anywhere in the frame would have room.
    frame = _frame(
        x0=40000, y0=40000, x1=60000, y1=60000,
        exx0=0, exy0=0, exx1=100000, exy1=100000,
        repel=True, dictionary_index=0,
    )
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Visible", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Visible) Tj" in data


def test_repel_flagged_frame_does_not_obstruct_a_frame_it_fully_encloses(tmp_path):
    # Regression test: a real document (FieldWork) has 3 picture
    # captions (and 2 diagram labels) that never appeared at all.
    # Traced to their own small frames sitting entirely inside a much
    # larger, also repel-flagged frame -- the chapter's own main
    # body-text container, which needs to repel *its own* text around
    # the smaller frames layered within it, but was *also* being
    # treated as an obstacle to those smaller frames' own text in the
    # other direction. Since a small frame's own box sits entirely
    # inside the big one, narrowing left no usable width anywhere,
    # silently dropping all of its text -- unlike the genuine
    # picture-repel case, which only partially overlaps.
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # The chapter's own main body-text frame: repel-flagged, covering
    # virtually the whole page.
    container = _frame(
        x0=0, y0=0, x1=100000, y1=150000,
        exx0=0, exy0=0, exx1=100000, exy1=150000,
        repel=True, dictionary_index=0,
    )
    # A small caption frame entirely inside the container's own box.
    caption = _frame(
        x0=40000, y0=40000, x1=60000, y1=60000,
        exx0=40000, exy0=40000, exx1=60000, exy1=60000,
        repel=True, dictionary_index=1,
    )
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, container), _frame_record(1108, caption)),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body], header=header
    )
    document.dictionary.extend([
        DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0),
        DictionaryEntry(index=1, type=DictionaryEntryType.TEXT, id=1, types=0),
    ])
    stories = {
        0: Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Body", style_slots=()),)),)),
        1: Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Caption", style_slots=()),)),)),
    }
    document.story = lambda entry: stories[entry.index]

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Body) Tj" in data
    assert b"(Caption) Tj" in data


def test_master_furniture_is_rebased_onto_the_content_page(tmp_path):
    """Regression test: a master page keeps its own, entirely separate
    absolute coordinate canvas (confirmed empirically -- real documents
    place successive content pages in one shared vertical canvas, but
    master pages are decoded from a different object-record stream
    with their own origin). Drawing a piece of master furniture (an
    unlinked master-page frame) using the content page's own origin
    put it far outside the page in real documents; it must instead be
    rebased using the master page's own origin.
    """
    from riscos_impression.output.pdfdoc import PDFConverter

    furniture = _frame(
        x0=10000, y0=80000, x1=90000, y1=95000, filled=True,
        fill_colour_word=0x0000FF00,  # selector 0 -> RGB, red=0xFF
        dictionary_index=-1, master=False, master_index=0,
    )
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=100000, bleed=0, master_page_name=""),
        offset=100,
        records=(_frame_record(108, furniture),),
    )
    content_page = PageGroup(
        page=Page(x0=500000, y0=500000, x1=600000, y1=600000, bleed=0, master_page_name=""),
        offset=1000,
        records=(),
        master_page=master_page,
    )
    section = _section(create_number=1, master_page_index=0)
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(content_page,)
    )
    body = _style(0, is_body_text=True)
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    # Rebased onto the content page: (10000-0)/1000=10 .. (90000-0)/1000=90,
    # well within the page's own 0..100pt box. The pre-fix behaviour used
    # the content page's own origin (500000,500000) directly, which would
    # have put this rectangle far into negative coordinates instead.
    assert b"10 80 80 15 re f" in data
    assert b"-490" not in data
    assert not converter.log.has_errors()


def _picture_document(picture_bytes: bytes, *, x0=0, y0=0, x1=100000, y1=100000, **picture_overrides):
    document, _unused = _document_with_one_text_frame()
    picture = _picture(x0=x0, y0=y0, x1=x1, y1=y1, dictionary_index=1, **picture_overrides)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, picture),),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    body = _style(0, is_body_text=True)
    document.chapters = [chapter]
    document.master_pages = [master_page]
    document.header = header
    dict_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.append(dict_entry)
    document.picture_bytes = lambda entry: picture_bytes
    return document


def test_picture_frame_with_an_empty_drawfile_renders_cleanly(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    # A valid header with no object stream at all -- a legitimately empty
    # drawing, not a decoding failure, so nothing should be logged either.
    document = _picture_document(b"Draw" + b"\x00" * 40)

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)

    assert not converter.log.has_errors()


def test_drawfile_effective_bounds_unions_object_bounds_beyond_the_file_header(tmp_path):
    # Regression test: the user reported a real document (FieldWork)
    # rendering a picture's own caption text ("Groyne") cropped at the
    # top of its frame. Root cause: the DrawFile's own file-header-
    # declared bounding box did not include one of its own objects'
    # full extent, even though that object's own individually-decoded
    # bounds (in its own object header) correctly did -- sizing the
    # picture's content from the header bounds alone under-measured how
    # much room the content actually needs. _drawfile_effective_bounds
    # unions every object's own bounds with the file header's own bounds
    # to fix this. Reproduced here with a DrawPath (not DrawText) purely
    # so the test can assert on exact geometry via the established
    # move-to-point helper, without depending on font-size arithmetic:
    # the path's own declared bounds (0,0,1000,5000) are 5x taller than
    # the file header's own declared bounds (0,0,1000,1000) -- the same
    # shape of discrepancy as the real document's (header too short; an
    # object's own bounds correctly taller). A huge xshift forces the
    # centring fallback (see the sibling "falls back to centring" test
    # above), whose own origin position is directly sensitive to the
    # bounds height used, isolating the effect cleanly.
    from riscos_impression.output.pdfdoc import PDFConverter, _DRAW_UNIT_TO_PT

    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + line(0, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 5000), fill_colour=0x0000FF00)
    picture_bytes = build_drawfile(path, bounds=(0, 0, 1000, 1000))

    document = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=2000000, yshift=1000000)
    out = tmp_path / "out.pdf"
    PDFConverter(document).convert(out)
    x, y = _first_moveto_point(out.read_bytes())

    frame_w = frame_h = 40000 / 1000.0
    displayed_w = 1000 * _DRAW_UNIT_TO_PT
    displayed_h = 5000 * _DRAW_UNIT_TO_PT  # only correct if the object's own taller bounds were used
    expected_x = (frame_w - displayed_w) / 2.0
    expected_y = (frame_h - displayed_h) / 2.0
    assert round(x, 3) == round(expected_x, 3)
    assert round(y, 3) == round(expected_y, 3)


def test_drawfile_path_renders_as_real_vector_fill_content(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(2560, 0) + line(2560, 2560) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 2560, 2560), fill_colour=0x0000FF00)  # red fill (&BBGGRR00: R=0xFF)
    document = _picture_document(build_drawfile(path, bounds=(0, 0, 2560, 2560)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"\nf\n" in data  # a real fill paint operator, not the placeholder's stroked box
    assert b"(\\[Draw\\])" not in data  # the old placeholder's label text
    assert not converter.log.has_errors()


def _first_moveto_point(data: bytes) -> tuple[float, float]:
    match = re.search(rb"([\d.-]+) ([\d.-]+) m\n", data)
    assert match is not None, data
    return float(match.group(1)), float(match.group(2))


def test_page_positioned_picture_xshift_yshift_anchors_content_to_the_frames_own_box(tmp_path):
    # Regression test: the user reported a real document (FieldWork)
    # rendering a location-marker map with its label cut off and the
    # marker landing nowhere near its real-world location -- confirmed
    # to be xshift/yshift being ignored entirely (the picture's content
    # was simply centred in its frame instead). xshift/yshift anchor
    # the drawfile's own native (0, 0) origin point -- not either
    # corner of its own declared bounding box -- at (frame's own left
    # edge - xshift, frame's own bottom edge - yshift); the content's
    # own bottom-left corner then follows from wherever it actually
    # sits relative to that origin. Derived and pixel-verified against
    # two purpose-built calibration documents -- see
    # _draw_drawfile_picture's own docstring for the full history,
    # including two superseded earlier formulas that each appeared to
    # fit some real data but turned out to be coincidences. A 40x40pt
    # square drawfile, itself declared starting exactly at (0, 0) (so
    # its own bounding-box corner and its own native origin coincide,
    # keeping this test focused purely on the xshift/yshift part of the
    # formula -- the origin/bounds distinction has its own dedicated
    # test below), in a 40x40pt frame confirms this: changing
    # xshift/yshift by a known amount must shift the drawn content by
    # exactly that amount, in the expected direction.
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(25600, 0) + line(25600, 25600) + line(0, 25600) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 25600, 25600), fill_colour=0x0000FF00)
    picture_bytes = build_drawfile(path, bounds=(0, 0, 25600, 25600))

    baseline = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=0, yshift=0)
    out0 = tmp_path / "baseline.pdf"
    PDFConverter(baseline).convert(out0)
    x0, y0 = _first_moveto_point(out0.read_bytes())
    assert round(x0, 3) == 0.0  # content's own left edge lands exactly at the frame's own x0
    assert round(y0, 3) == 0.0  # content's own bottom edge lands exactly at the frame's own y0

    shifted = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=1000, yshift=500)
    out1 = tmp_path / "shifted.pdf"
    PDFConverter(shifted).convert(out1)
    x1, y1 = _first_moveto_point(out1.read_bytes())

    # xshift=1000 (1pt) -> content moves LEFT by 1pt; yshift=500 (0.5pt)
    # -> content moves DOWN by 0.5pt (both are subtracted in the
    # formula).
    assert round(x1 - x0, 3) == -1.0
    assert round(y1 - y0, 3) == -0.5


def test_picture_xshift_anchors_the_drawfiles_own_origin_not_its_bounding_box(tmp_path):
    # Regression test: a first calibration document (a grid of
    # identically-sized, distinctly-marked pictures) suggested xshift
    # anchored the content's own bounding-box corner, offset by half
    # its own displayed size -- matching that document's own data
    # closely. A second, cleaner calibration document (a plain shape
    # drawn starting exactly at the drawfile's own (0, 0), at
    # round-number millimetre offsets) revealed this was a coincidence:
    # the first document's own content happened to have its own
    # bounds.x0 sitting almost exactly half its own width away from
    # (0, 0), which is what made a half-size correction appear to fit.
    # The real anchor point is the drawfile's own native (0, 0) origin,
    # regardless of where the content's own declared bounds happen to
    # start -- confirmed here with two drawfiles sharing the same
    # xshift/yshift but declaring their own bounds starting at
    # different offsets from (0, 0): the one whose own bounds start
    # further from the origin must be shifted by exactly that same
    # extra amount (scaled), not stay in the same place.
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(6400, 0) + line(6400, 6400) + line(0, 6400) + close_line() + end_path()

    at_origin = build_path(ops=ops, bounds=(0, 0, 6400, 6400), fill_colour=0x0000FF00)
    picture_at_origin = build_drawfile(at_origin, bounds=(0, 0, 6400, 6400))

    offset_ops = move(12800, 12800) + line(19200, 12800) + line(19200, 19200) + line(12800, 19200) + close_line() + end_path()
    offset_from_origin = build_path(ops=offset_ops, bounds=(12800, 12800, 19200, 19200), fill_colour=0x0000FF00)
    picture_offset_from_origin = build_drawfile(offset_from_origin, bounds=(12800, 12800, 19200, 19200))

    document_a = _picture_document(picture_at_origin, x1=40000, y1=40000, xshift=0, yshift=0)
    out_a = tmp_path / "a.pdf"
    PDFConverter(document_a).convert(out_a)
    xa, ya = _first_moveto_point(out_a.read_bytes())

    document_b = _picture_document(picture_offset_from_origin, x1=40000, y1=40000, xshift=0, yshift=0)
    out_b = tmp_path / "b.pdf"
    PDFConverter(document_b).convert(out_b)
    xb, yb = _first_moveto_point(out_b.read_bytes())

    # 12800 Draw units at 100% display scale is 20pt (12800 *
    # _DRAW_UNIT_TO_PT); the second picture's own bounds start that far
    # from (0, 0) on both axes, so its own drawn corner must land 20pt
    # further right and up than the first picture's, even though both
    # share the same xshift/yshift and the same frame.
    assert round(xb - xa, 3) == 20.0
    assert round(yb - ya, 3) == 20.0


def test_picture_xshift_anchor_moves_inward_by_the_frames_own_hinset(tmp_path):
    # Regression test: the user supplied a third real document's own
    # dialog reading, for a second (ungrouped) picture on the same
    # page as the one confirming the sign/anchor formula above:
    # x=-17.87mm, y=-4.09mm, scale=70%, against raw xshift=44995,
    # yshift=11595, xscale=93623, hinset=vinset=5669. y and scale
    # matched the existing formula exactly, but x was off by exactly
    # hinset/UNIT in mm (5669 raw = 2.00mm -- and the discrepancy
    # between the naive x and the dialog reading was exactly 2.00mm
    # too, to the thousandth of a millimetre): x's own anchor moves
    # inward by the frame's own hinset -- x0+hinset for an ungrouped
    # picture, x1-hinset for a grouped one. A third real picture (the
    # document's own wind-direction diagram, hinset=0) matched the
    # unmodified formula exactly on its own, confirming this is a
    # correction for hinset specifically, not a change to the base
    # formula. (hinset's own place in the newer, half-size-term formula
    # above is unconfirmed against the newer calibration document,
    # which had no hinset variation in it, but is kept as the closest
    # prior evidence -- this test only pins down that it still shifts
    # content by exactly its own amount, not that its role in the
    # formula is still correct.)
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(25600, 0) + line(25600, 25600) + line(0, 25600) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 25600, 25600), fill_colour=0x0000FF00)
    picture_bytes = build_drawfile(path, bounds=(0, 0, 25600, 25600))

    # xshift=20000 (half the content's own 40000-unit size) puts the
    # unshifted content's own left edge exactly at the frame's own x0
    # (see the sibling test above), keeping it comfortably overlapping
    # regardless of a small extra hinset.
    no_inset = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=20000, yshift=20000, hinset=0)
    out_a = tmp_path / "a.pdf"
    PDFConverter(no_inset).convert(out_a)
    xa, _ = _first_moveto_point(out_a.read_bytes())

    with_inset = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=20000, yshift=20000, hinset=2000)
    out_b = tmp_path / "b.pdf"
    PDFConverter(with_inset).convert(out_b)
    xb, _ = _first_moveto_point(out_b.read_bytes())

    # hinset=2000 (2pt) -> content moves 2pt to the RIGHT (inward from
    # the frame's own left edge, still anchored there since this
    # picture is ungrouped).
    assert round(xb - xa, 3) == 2.0


def test_grouped_picture_uses_the_same_left_edge_anchor_as_ungrouped(tmp_path):
    # Regression test: an earlier formula (superseded -- see
    # _draw_drawfile_picture's own docstring) anchored a grouped
    # picture's own x from the frame's own *right* edge instead of its
    # left, based on a real document's own dialog-reported x/y/scale
    # values appearing to match that anchor exactly. That agreement
    # turned out to be a false positive specific to that picture being
    # much bigger than its own frame (an oversized picture's own
    # "content fully covers the frame" self-check passes regardless of
    # which anchor is used). Once the anchor/bounds formula itself was
    # corrected (see the sibling test above), re-rendering that same
    # real document's own *two* grouped pictures against their own
    # real reference screenshots showed both need the ordinary
    # left-edge anchor, no grouped-specific handling at all: one had
    # been showing the wrong region of its own content entirely
    # (missing its own compass marker and every other landmark), the
    # other cropping its own caption text -- both matched their own
    # reference exactly once rendered with a plain left-edge anchor.
    # This test confirms a grouped and an ungrouped picture, given the
    # identical frame/xshift/yshift, now render identically.
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(25600, 0) + line(25600, 25600) + line(0, 25600) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 25600, 25600), fill_colour=0x0000FF00)
    picture_bytes = build_drawfile(path, bounds=(0, 0, 25600, 25600))

    ungrouped = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=0, yshift=0, grouped=False)
    out_a = tmp_path / "a.pdf"
    PDFConverter(ungrouped).convert(out_a)
    xa, ya = _first_moveto_point(out_a.read_bytes())
    assert round(xa, 3) == 0.0  # left edge exactly at the frame's own x0

    grouped = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=0, yshift=0, grouped=True)
    out_b = tmp_path / "b.pdf"
    PDFConverter(grouped).convert(out_b)
    xb, yb = _first_moveto_point(out_b.read_bytes())

    assert (xb, yb) == (xa, ya)


def test_page_positioned_picture_falls_back_to_centring_when_the_shift_would_leave_the_frame_mostly_empty(tmp_path):
    # Regression test: the same real document (FieldWork) also has 2
    # page-positioned pictures, both nested in the same GroupFrame,
    # whose own xshift/yshift, applied via the formula confirmed by
    # the sibling test above, land the content with little or no
    # overlap with the frame at all -- as if anchored against
    # something other than this frame's own box, for reasons not yet
    # understood (see _draw_drawfile_picture's own docstring). Since a
    # real picture frame is never deliberately left almost entirely
    # empty, this is treated as a sign the shift isn't trustworthy for
    # that picture and centring is used instead. A drawfile much
    # bigger than its own frame, shifted so far (in both x and y, well
    # beyond anything a real document would use, so this doesn't
    # depend on either axis's own sign convention) the two barely
    # overlap, must render identically regardless of the exact
    # (wildly different) shift values -- both fall back to the same
    # centred position.
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(256000, 0) + line(256000, 256000) + line(0, 256000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 256000, 256000), fill_colour=0x0000FF00)
    picture_bytes = build_drawfile(path, bounds=(0, 0, 256000, 256000))

    document_a = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=2000000, yshift=1000000)
    out_a = tmp_path / "a.pdf"
    PDFConverter(document_a).convert(out_a)
    xa, ya = _first_moveto_point(out_a.read_bytes())

    document_b = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=-3000000, yshift=5000000)
    out_b = tmp_path / "b.pdf"
    PDFConverter(document_b).convert(out_b)
    xb, yb = _first_moveto_point(out_b.read_bytes())

    assert (xa, ya) == (xb, yb)


def test_untrustworthy_shift_shrinks_oversized_content_to_fit_instead_of_cropping(tmp_path):
    # Regression test: the user reported the sibling test's own real
    # document (FieldWork) still showing the *wrong region* on one of
    # its 2 grouped/nested pictures after the centring fallback above
    # -- a UK-relief inset map whose own native-scale content is much
    # bigger than its frame landed, once centred, on the South-West of
    # Great Britain instead of Norfolk (the region the map exists to
    # show), since centring alone still only shows an arbitrary crop.
    # When the fallback's own content doesn't fit the frame at native
    # scale, it's now shrunk (preserving aspect ratio) until it does,
    # guaranteeing the *whole* picture is visible somewhere in the
    # frame -- never matching Impression's own real, presumably-cropped
    # rendering exactly, but never hiding the one region a crop might
    # have been aimed at either.
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(256000, 0) + line(256000, 256000) + line(0, 256000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 256000, 256000), fill_colour=0x0000FF00)
    picture_bytes = build_drawfile(path, bounds=(0, 0, 256000, 256000))

    document = _picture_document(picture_bytes, x1=40000, y1=40000, xshift=2000000, yshift=1000000)
    out = tmp_path / "out.pdf"
    PDFConverter(document).convert(out)
    data = out.read_bytes()

    # Every coordinate the path's own corners are drawn at (both "m"
    # and "l" operators) must fall within the frame's own [0,40] box
    # on both axes -- nothing cropped away, unlike native-scale
    # centring, which would have put at least one corner well outside.
    coords = [float(v) for v in re.findall(r"([\d.]+) [\d.]+ [ml]\n", data.decode("latin-1"))]
    coords += [float(v) for v in re.findall(r"[\d.]+ ([\d.]+) [ml]\n", data.decode("latin-1"))]
    assert coords  # sanity: the path was actually found
    assert all(-0.01 <= c <= 40.01 for c in coords)


def test_drawfile_sprite_sub_object_falls_back_to_a_placeholder_and_logs_best_effort(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    document = _picture_document(build_drawfile(
        build_sprite(bounds=(0, 0, 1000, 1000), body=b"x" * 44), bounds=(0, 0, 1000, 1000),
    ))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"([Sprite])" in data
    assert any("Sprite object embedded within a DrawFile" in e.message for e in converter.log.entries)


#: A minimal, structurally valid (but not visually meaningful) JPEG:
#: SOI, one SOF0 (baseline DCT) marker declaring a 3x2 pixel, 3-
#: component image, then straight to EOI -- enough for _jpeg_info to
#: read real width/height/component values, without needing a real
#: image library dependency just for this test.
_MINIMAL_JPEG = (
    b"\xff\xd8"  # SOI
    b"\xff\xc0\x00\x11\x08\x00\x02\x00\x03\x03"  # SOF0, len=17, 8-bit, h=2, w=3, 3 components
    b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"  # component 1/2/3 (id, sampling, quant table)
    b"\xff\xd9"  # EOI
)


def test_drawfile_jpeg_object_embeds_as_a_dctdecode_image_xobject(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    document = _picture_document(build_drawfile(
        build_jpeg(_MINIMAL_JPEG, bounds=(0, 0, 1000, 1000)), bounds=(0, 0, 1000, 1000),
    ))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"/Filter /DCTDecode" in data
    assert b"/Subtype /Image" in data
    assert b"/Width 3 /Height 2" in data
    assert b"/ColorSpace /DeviceRGB" in data
    assert _MINIMAL_JPEG in data
    assert b"/XObject <<" in data
    assert b"Do Q" in data
    assert not converter.log.has_errors()


def test_drawfile_unparseable_jpeg_falls_back_to_a_placeholder_and_logs_best_effort(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    document = _picture_document(build_drawfile(
        build_jpeg(b"not actually a jpeg", bounds=(0, 0, 1000, 1000)), bounds=(0, 0, 1000, 1000),
    ))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"([JPEG])" in data
    assert b"/DCTDecode" not in data
    assert any("could not be parsed" in e.message for e in converter.log.entries)


class _FakePngImage:
    """A minimal stand-in for riscos_sprites.png.PngImage -- real
    sprite decoding is riscos_sprites' own, separately-tested concern
    (see riscos-dumpsprites/tests/test_png.py); these tests exercise
    only this project's own PDF-object-building logic in
    _draw_sprite_image, given a decoded image already in hand."""

    def __init__(self, *, width, height, colour_type, bit_depth=8, palette=None,
                 rows=(), trns_palette=None, trns_colour=None):
        self.width = width
        self.height = height
        self.colour_type = colour_type
        self.bit_depth = bit_depth
        self.palette = palette
        self.rows = rows
        self.trns_palette = trns_palette
        self.trns_colour = trns_colour


def test_drawfile_sprite_object_embeds_as_an_indexed_image_with_colour_key_mask(tmp_path):
    from unittest.mock import patch

    from riscos_impression.formats.sprite_png import COLOUR_TYPE_PALETTE
    from riscos_impression.output.pdfdoc import PDFConverter

    image = _FakePngImage(
        width=2, height=1, colour_type=COLOUR_TYPE_PALETTE, bit_depth=8,
        palette=[(0, 0, 0), (255, 0, 0)], rows=[[0, 1]], trns_palette=[0, 255],
    )
    document = _picture_document(build_drawfile(
        build_sprite(bounds=(0, 0, 1000, 1000), body=b"x" * 44), bounds=(0, 0, 1000, 1000),
    ))

    with patch("riscos_impression.output.pdfdoc.sprite_area_to_png_image", return_value=image):
        converter = PDFConverter(document)
        out = tmp_path / "out.pdf"
        converter.convert(out)
        data = out.read_bytes()

    assert b"/Subtype /Image" in data
    assert b"/Indexed /DeviceRGB 1 <000000ff0000>" in data
    assert b"/Mask [0 0]" in data
    assert b"/Filter /FlateDecode" in data
    assert b"([Sprite])" not in data
    assert not converter.log.has_errors()


def test_drawfile_sprite_object_embeds_as_plain_rgb_with_colour_key_mask(tmp_path):
    from unittest.mock import patch

    from riscos_impression.formats.sprite_png import COLOUR_TYPE_RGB
    from riscos_impression.output.pdfdoc import PDFConverter

    image = _FakePngImage(
        width=1, height=1, colour_type=COLOUR_TYPE_RGB,
        rows=[[(10, 20, 30)]], trns_colour=(255, 255, 255),
    )
    document = _picture_document(build_drawfile(
        build_sprite(bounds=(0, 0, 1000, 1000), body=b"x" * 44), bounds=(0, 0, 1000, 1000),
    ))

    with patch("riscos_impression.output.pdfdoc.sprite_area_to_png_image", return_value=image):
        converter = PDFConverter(document)
        out = tmp_path / "out.pdf"
        converter.convert(out)
        data = out.read_bytes()

    assert b"/ColorSpace /DeviceRGB" in data
    assert b"/Mask [255 255 255 255 255 255]" in data
    assert not converter.log.has_errors()


def test_drawfile_sprite_object_with_real_alpha_gets_a_real_smask(tmp_path):
    from unittest.mock import patch

    from riscos_impression.formats.sprite_png import COLOUR_TYPE_RGBA
    from riscos_impression.output.pdfdoc import PDFConverter

    image = _FakePngImage(
        width=2, height=1, colour_type=COLOUR_TYPE_RGBA,
        rows=[[(1, 2, 3, 128), (4, 5, 6, 255)]],
    )
    document = _picture_document(build_drawfile(
        build_sprite(bounds=(0, 0, 1000, 1000), body=b"x" * 44), bounds=(0, 0, 1000, 1000),
    ))

    with patch("riscos_impression.output.pdfdoc.sprite_area_to_png_image", return_value=image):
        converter = PDFConverter(document)
        out = tmp_path / "out.pdf"
        converter.convert(out)
        data = out.read_bytes()

    assert b"/SMask" in data
    assert data.count(b"/Subtype /Image") == 2  # the RGB image plus its own SMask
    assert b"/ColorSpace /DeviceGray" in data
    assert not converter.log.has_errors()


def test_drawfile_sprite_missing_extra_falls_back_to_a_placeholder_and_logs_best_effort(tmp_path):
    from unittest.mock import patch

    from riscos_impression.output.pdfdoc import PDFConverter

    document = _picture_document(build_drawfile(
        build_sprite(bounds=(0, 0, 1000, 1000), body=b"x" * 44), bounds=(0, 0, 1000, 1000),
    ))

    with patch("riscos_impression.output.pdfdoc.sprite_area_to_png_image", return_value=None):
        converter = PDFConverter(document)
        out = tmp_path / "out.pdf"
        converter.convert(out)
        data = out.read_bytes()

    assert b"([Sprite])" in data
    assert any("riscos_sprites" in e.message for e in converter.log.entries)


def test_drawfile_text_object_renders_using_the_font_tables_own_name(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    fonts = build_font_table({1: "Trinity.Bold"})
    text = build_text(text="Hello", font_number=1, baseline_x=100, baseline_y=100)
    document = _picture_document(build_drawfile(fonts + text, bounds=(0, 0, 1000, 1000)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"(Hello) Tj" in data
    # "Trinity" maps to the Times family, and the font table marks this one bold.
    assert b"/BaseFont /Times-Bold" in data


def test_drawfile_text_size_accounts_for_the_points_vs_drawunits_mismatch(tmp_path):
    # Regression test: a real document (PCI_Spec) had DrawFile text
    # completely invisible -- not missing from the PDF, but rendered
    # at roughly 1/100th its intended size. text.size_y is already in
    # points (1/640 point per the format), unlike a path's Draw-unit-
    # denominated line_width, so scaling it directly by the picture's
    # own points-per-Draw-unit ratio was a unit mismatch. At the
    # picture frame's own default 100% display scale (see
    # _draw_drawfile_picture), the correct font size is simply
    # text.size_y/640 points, independent of the frame's own box size
    # entirely (a picture is no longer stretched to fill its frame;
    # see that method's own docstring) -- the old, buggy formula gave
    # a font size roughly 100x too small instead.
    from riscos_impression.output.pdfdoc import PDFConverter, _fmt

    fonts = build_font_table({1: "Homerton.Medium"})
    text = build_text(text="Hello", font_number=1, size_x=8960, size_y=8960, baseline_x=0, baseline_y=0)
    document = _picture_document(build_drawfile(fonts + text, bounds=(0, 0, 25600, 25600)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    expected_size_pt = 8960 / 640.0
    assert expected_size_pt > 1.0  # sanity: this is nowhere near the old ~0.01pt bug
    assert f"{_fmt(expected_size_pt)} Tf".encode("latin-1") in data


def test_unit_vector_normalises_and_handles_zero_length():
    from riscos_impression.output.pdfdoc import _unit_vector

    ux, uy = _unit_vector(3.0, 4.0)
    assert abs(ux - 0.6) < 1e-9
    assert abs(uy - 0.8) < 1e-9
    assert _unit_vector(0.0, 0.0) == (0.0, 0.0)


def test_triangular_cap_polygon_geometry():
    from riscos_impression.output.pdfdoc import _triangular_cap_polygon

    base_left, apex, base_right = _triangular_cap_polygon((10.0, 10.0), (1.0, 0.0), width_pt=4.0, length_pt=6.0)
    # Pointing along +x: base perpendicular (along y), apex further along +x.
    assert base_left == (10.0, 12.0)
    assert base_right == (10.0, 8.0)
    assert apex == (16.0, 10.0)


def test_subpath_cap_directions_skips_closed_subpaths():
    from riscos_impression.output.pdfdoc import _subpath_cap_directions

    ops = [move(0, 0), line(100, 0), line(100, 100), close_line(), end_path()]
    # Parse the raw bytes back into DrawPathOp objects via the real
    # parser, rather than hand-building op objects, so this exercises
    # the same decode path real documents go through.
    from riscos_impression.formats.drawfile import DrawFile

    data = build_drawfile(build_path(ops=b"".join(ops), stroke_colour=0))
    path = DrawFile.from_bytes(data).objects[0]
    results = _subpath_cap_directions(path.ops, lambda x, y: (float(x), float(y)))
    assert results == []  # closed subpath: no cap points at all


def test_subpath_cap_directions_open_subpath():
    from riscos_impression.output.pdfdoc import _subpath_cap_directions
    from riscos_impression.formats.drawfile import DrawFile

    ops = b"".join([move(0, 0), line(100, 0), end_path()])
    data = build_drawfile(build_path(ops=ops, stroke_colour=0))
    path = DrawFile.from_bytes(data).objects[0]
    ((start_pt, start_dir, end_pt, end_dir),) = _subpath_cap_directions(
        path.ops, lambda x, y: (float(x), float(y))
    )
    assert start_pt == (0.0, 0.0)
    assert start_dir == (-1.0, 0.0)  # points back past the path's own start
    assert end_pt == (100.0, 0.0)
    assert end_dir == (1.0, 0.0)  # points on past the path's own end


def test_drawfile_path_with_triangular_end_cap_draws_an_arrowhead(tmp_path):
    """Regression test: a real document (PCI_Spec) used a triangular
    trailing cap to draw pointer/arrow lines in its own DrawFile
    diagrams -- confirmed against the real RISC OS DrawFile module's
    own rendering implementation, and previously rendered as a plain,
    uncapped stroke (looking like a stray filled bar for a short, wide
    line) since caps/joins weren't honoured at all."""
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(2560, 0) + end_path()  # a 10pt-long horizontal line
    path = build_path(
        ops=ops, bounds=(0, 0, 2560, 100), stroke_colour=0xFF000000, line_width=256,  # blue, 1pt line
        end_cap=3, triangle_cap_width=32, triangle_cap_length=64,  # 2x/4x line width
    )
    document = _picture_document(build_drawfile(path, bounds=(0, 0, 2560, 100)), x1=100000, y1=100000)

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()
    content = data.decode("latin-1")

    assert not converter.log.has_errors()
    # A filled ("f", not stroked) triangle: 3 points via m/l/l, closed, filled.
    assert " m " in content and " l " in content and content.count(" l ") >= 2
    assert "h f\n" in content


def test_drawfile_dashed_path_emits_a_real_dash_array(tmp_path):
    # build_path's own dashed=True fixture writes a real (offset=0,
    # elements=[10, 5]) dash pattern -- see drawfile_builders.py.
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(2560, 0) + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 2560, 100), stroke_colour=0x000000FF, dashed=True)
    document = _picture_document(build_drawfile(path, bounds=(0, 0, 2560, 100)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"\nS\n" in data  # stroked, since there's no fill colour
    assert re.search(rb"\[[\d. ]+\] [\d.]+ d\n", data)
    assert not converter.log.has_errors()


def test_drawfile_non_dashed_path_after_a_dashed_one_resets_to_solid(tmp_path):
    # PDF's own dash array is graphics *state*, unlike SVG's per-element
    # stroke-dasharray attribute -- a later solid path drawn within the
    # same DrawFile (sharing one q/Q pair) must not inherit an earlier
    # path's own dash pattern.
    from riscos_impression.output.pdfdoc import PDFConverter

    dashed_ops = move(0, 0) + line(2560, 0) + end_path()
    dashed_path = build_path(ops=dashed_ops, bounds=(0, 0, 2560, 100), stroke_colour=0x000000FF, dashed=True)
    solid_ops = move(0, 200) + line(2560, 200) + end_path()
    solid_path = build_path(ops=solid_ops, bounds=(0, 200, 2560, 300), stroke_colour=0x000000FF, dashed=False)
    document = _picture_document(
        build_drawfile(dashed_path + solid_path, bounds=(0, 0, 2560, 300))
    )

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"[] 0 d\n" in data
    assert not converter.log.has_errors()


def test_drawfile_group_and_unknown_object_types_are_handled(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    from tests.fixtures.drawfile_builders import build_unknown

    ops = move(0, 0) + line(500, 0) + line(500, 500) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 500, 500), fill_colour=0x00FF0000)
    group = build_group("G", path + build_unknown(99, bounds=(0, 0, 500, 500)), bounds=(0, 0, 500, 500))
    document = _picture_document(build_drawfile(group, bounds=(0, 0, 500, 500)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"\nf\n" in data
    assert any("were not decoded and are omitted" in e.message for e in converter.log.entries)


def test_drawfile_options_object_is_omitted_without_logging(tmp_path):
    # Options objects (DrawFile type 11) carry no rendering component of
    # their own and are present in nearly every real DrawFile -- the
    # user confirmed this against several real documents -- so, unlike a
    # genuinely undecoded object type, they must not be logged as
    # best-effort.
    from riscos_impression.output.pdfdoc import PDFConverter

    from tests.fixtures.drawfile_builders import build_unknown

    ops = move(0, 0) + line(500, 0) + line(500, 500) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 500, 500), fill_colour=0x00FF0000)
    group = build_group("G", path + build_unknown(11, bounds=(0, 0, 500, 500)), bounds=(0, 0, 500, 500))
    document = _picture_document(build_drawfile(group, bounds=(0, 0, 500, 500)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"\nf\n" in data
    assert not converter.log.has_errors()
    assert not any("were not decoded" in e.message for e in converter.log.entries)


def test_drawfile_picture_angle_rotates_about_the_drawfiles_own_origin(tmp_path):
    # Regression test: the user built a purpose-built calibration
    # document -- three otherwise-identical, unshifted pictures at 15,
    # 30, and 45 degrees -- and confirmed (via the point where two
    # adjacent shapes meet, pixel-for-pixel against Impression's own
    # rendering) that pict.angle is a standard mathematical
    # (counter-clockwise) rotation about the drawfile's own native
    # (0, 0) origin -- the same point xshift/yshift anchor -- applied
    # before that anchor's own scale/translate. 90 degrees gives exact,
    # not merely approximate, expected coordinates: a point at
    # (1000, 0) rotates to (0, 1000).
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(1000, 0) + line(1000, 0) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    picture_bytes = build_drawfile(path, bounds=(0, 0, 1000, 1000))

    unrotated = _picture_document(picture_bytes, x1=100000, y1=100000, xshift=0, yshift=0, angle=0)
    out_a = tmp_path / "a.pdf"
    PDFConverter(unrotated).convert(out_a)
    xa, ya = _first_moveto_point(out_a.read_bytes())

    ninety_degrees = 90 * 65536
    rotated = _picture_document(picture_bytes, x1=100000, y1=100000, xshift=0, yshift=0, angle=ninety_degrees)
    out_b = tmp_path / "b.pdf"
    PDFConverter(rotated).convert(out_b)
    xb, yb = _first_moveto_point(out_b.read_bytes())

    # (1000, 0) at 0 degrees draws at (origin_x + 1000*sx, origin_y);
    # at 90 degrees, the same source point (1000, 0) has rotated to
    # (0, 1000), so it now draws at (origin_x, origin_y + 1000*sy).
    # origin_x == origin_y == 0 here (frame's own x0/y0, unshifted), so
    # this reduces to: the x and y displacements from the origin swap
    # (sx == sy, both at the picture's own default 100% scale).
    assert xa > 0.0  # sanity: the unrotated point isn't trivially at the origin too
    assert round(xb, 3) == 0.0
    assert round(ya, 3) == 0.0
    assert round(yb, 3) == round(xa, 3)


def _artworks_picture_document(picture_bytes: bytes, **kwargs):
    document = _picture_document(picture_bytes, **kwargs)
    entry = document.dictionary[-1]
    document.dictionary[-1] = DictionaryEntry(index=entry.index, type=entry.type, id=entry.id, types=0xD94)
    return document


def test_artworks_picture_frame_renders_as_real_pdf_path_content(tmp_path):
    # No FillColourRecord is present in this minimal fixture, so the
    # ambient _DEFAULT_STYLE fill colour (ArtWorks' own "no colour"
    # sentinel, ColourIndex(0xFFFFFFFF)) resolves to None -- matching
    # the SVG converter's own "solid black hairline stroke, no fill"
    # default -- so only the stroke operator is expected here, not a
    # fill/both operator; see the module docstring on _DEFAULT_STYLE.
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_impression.output.pdfdoc import PDFConverter

    document = _artworks_picture_document(build_single_path_document())

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b" m\n" in data
    assert b" l\n" in data
    assert b"\nS\n" in data
    assert b"([ArtWorks])" not in data
    assert not converter.log.has_errors()


def test_artworks_picture_frame_with_unparseable_data_falls_back_to_placeholder(tmp_path):
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_impression.output.pdfdoc import PDFConverter

    document = _artworks_picture_document(b"not a real ArtWorks file at all")

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"([ArtWorks])" in data
    assert any("ArtWorks" in e.message for e in converter.log.entries)


def test_artworks_pdf_character_font_size_is_converted_via_font_size_to_native_units():
    # Regression test: FontSizeRecord.y_size was previously used
    # directly as if it were already in native ArtWorks coordinate
    # units -- see formats/artworks_svg.py's own
    # FONT_SIZE_TO_NATIVE_UNITS docstring for the empirical derivation
    # and why the bug went unnoticed for a while (only glaringly
    # visible in a picture whose own frame was small).
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import CharacterRecord, FontSizeRecord, TextRecord

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE, FONT_SIZE_TO_NATIVE_UNITS
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _list, _record

    size = _record(FontSizeRecord, x_size=512, y_size=512)
    char_a = _record(CharacterRecord, character_code=ord("A"), unknown_values=(0, 0, 0, 0))
    text = _record(TextRecord, unknown_values=(0, 0, 0, 1, 1, 0), rectangle=(), child_lists=(_list(size), _list(char_a)))
    artwork = _artwork((_list(text),))

    converter = PDFConverter(None)
    converter._content = []
    converter._font_resource_name = {"Helvetica": "F1"}
    style = dict(_DEFAULT_STYLE)
    converter._artworks_pdf_process_lists(artwork.record_lists, style, artwork, lambda x, y: (x, y), 1.0, [])
    content = "".join(converter._content)

    assert FONT_SIZE_TO_NATIVE_UNITS == 30.0
    assert " 15360 Tf " in content  # 512 * 30 * scale(1.0)


def test_artworks_pdf_blend_group_interpolates_geometry_and_stroke_colour():
    # Unit-tests PDFConverter._artworks_pdf_process_blend_group directly
    # against hand-built riscos_artworks dataclasses (reusing the same
    # helpers as formats/artworks_svg.py's own blend tests), bypassing
    # ArtWorks.from_buffer()'s byte-level decoding entirely -- this
    # class needs no document/page state at all for that method, only
    # self._content, so a full PDF document isn't built here.
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import BlendGroupRecord, BlendOptionsRecord, PathRecord, StrokeColourRecord

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _direct, _list, _record, _square

    start_stroke = _record(StrokeColourRecord, colour=_direct(255, 0, 0))
    start_path = _record(PathRecord, path=_square(0, 0, 1000), child_lists=(_list(start_stroke),))
    options = _record(BlendOptionsRecord, unknown_24=0, blend_steps=4, values=(0,) * 8)
    end_stroke = _record(StrokeColourRecord, colour=_direct(0, 0, 255))
    end_path = _record(PathRecord, path=_square(2000, 2000, 200), child_lists=(_list(end_stroke),))
    group = _record(
        BlendGroupRecord, values=(0,) * 11,
        child_lists=(_list(start_path), _list(options), _list(end_path)),
    )
    artwork = _artwork((_list(group),))

    converter = PDFConverter(None)
    converter._content = []
    converter._artworks_pdf_process_blend_group(
        group, dict(_DEFAULT_STYLE), artwork, lambda x, y: (x, y), 1.0, [],
    )
    content = "".join(converter._content)

    assert content.count(" m\n") == 5  # blend_steps + 1
    assert "0 0 m\n" in content  # t=0: exactly the start keyframe
    assert "2000 2000 m\n" in content  # t=1: exactly the end keyframe
    assert "1 0 0 RG" in content  # t=0 stroke colour
    assert "0 0 1 RG" in content  # t=1 stroke colour


def test_artworks_pdf_blend_group_with_mismatched_point_counts_draws_both_keyframes():
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import BlendGroupRecord, BlendOptionsRecord, CloseElement, EndElement, LineElement, MoveElement, PathRecord, Point

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _list, _record, _square

    start_path = _record(PathRecord, path=_square(0, 0, 1000))
    options = _record(BlendOptionsRecord, unknown_24=0, blend_steps=4, values=(0,) * 8)
    triangle_path = (
        MoveElement(2, Point(2000, 2000)),
        LineElement(8, Point(2200, 2000)),
        LineElement(8, Point(2100, 2200)),
        CloseElement(5),
        EndElement(0),
    )
    end_path = _record(PathRecord, path=triangle_path)
    group = _record(
        BlendGroupRecord, values=(0,) * 11,
        child_lists=(_list(start_path), _list(options), _list(end_path)),
    )
    artwork = _artwork((_list(group),))

    converter = PDFConverter(None)
    converter._content = []
    converter._artworks_pdf_process_blend_group(
        group, dict(_DEFAULT_STYLE), artwork, lambda x, y: (x, y), 1.0, [],
    )
    content = "".join(converter._content)

    assert content.count(" m\n") == 2
    assert "0 0 m\n" in content
    assert "2000 2000 m\n" in content


def test_artworks_pdf_linear_gradient_fill_emits_a_real_shading():
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import FillColourRecord, PathRecord, Point

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _direct, _list, _record, _square_path

    fill = _record(
        FillColourRecord,
        fill_type=1, unknown_28=0, colour=None,
        gradient_line=(Point(0, 500), Point(1000, 500)),
        start_colour=_direct(255, 255, 255), end_colour=_direct(0, 0, 0),
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(path)))

    converter = PDFConverter(None)
    converter._content = []
    converter._page_shadings = {}
    converter._writer = _PDFWriter()
    style = dict(_DEFAULT_STYLE)
    converter._artworks_pdf_process_lists(artwork.record_lists, style, artwork, lambda x, y: (x, y), 1.0, [])
    content = "".join(converter._content)

    assert "W*\nn\n" in content  # _DEFAULT_STYLE's own winding is even-odd
    assert re.search(r"/Sh\d+ sh", content)
    assert len(converter._page_shadings) == 1
    # The clip must be scoped inside its own q/Q -- see the dedicated
    # leaked-clip regression test below for why.
    assert re.search(r"q\n[^q]*W\*\nn\n/Sh1 sh\nQ\n", content)
    shading_obj = converter._writer._objects[converter._page_shadings["Sh1"]]
    assert b"/ShadingType 2" in shading_obj
    assert b"/Coords [0 500 1000 500]" in shading_obj


def test_artworks_pdf_radial_gradient_fill_emits_a_real_shading():
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import FillColourRecord, PathRecord, Point

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _direct, _list, _record, _square_path

    fill = _record(
        FillColourRecord, fill_type=2, unknown_28=0, colour=None,
        gradient_line=(Point(500, 500), Point(500, 1000)),
        start_colour=_direct(255, 255, 255), end_colour=_direct(0, 0, 0),
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(path)))

    converter = PDFConverter(None)
    converter._content = []
    converter._page_shadings = {}
    converter._writer = _PDFWriter()
    style = dict(_DEFAULT_STYLE)
    converter._artworks_pdf_process_lists(artwork.record_lists, style, artwork, lambda x, y: (x, y), 1.0, [])
    content = "".join(converter._content)

    assert re.search(r"/Sh\d+ sh", content)
    shading_obj = converter._writer._objects[converter._page_shadings["Sh1"]]
    assert b"/ShadingType 3" in shading_obj
    assert b"/Coords [500 500 0 500 500 500]" in shading_obj


def test_artworks_pdf_gradient_with_unresolvable_colour_falls_back_to_flat():
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import ColourIndex, FillColourRecord, PathRecord, Point

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _direct, _list, _record, _square_path

    # start_colour is a palette *index*, and this artwork has no
    # palette -- resolve_colour() returns None, so no real shading can
    # be built; the flat-colour fallback (using fill_start) applies.
    fill = _record(
        FillColourRecord, fill_type=1, unknown_28=0, colour=None,
        gradient_line=(Point(0, 500), Point(1000, 500)),
        start_colour=ColourIndex(3), end_colour=_direct(0, 0, 0),
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(path)))

    converter = PDFConverter(None)
    converter._content = []
    converter._page_shadings = {}
    converter._writer = _PDFWriter()
    notes: list[str] = []
    style = dict(_DEFAULT_STYLE)
    converter._artworks_pdf_process_lists(artwork.record_lists, style, artwork, lambda x, y: (x, y), 1.0, notes)
    content = "".join(converter._content)

    assert "sh\n" not in content
    assert len(converter._page_shadings) == 0
    assert any("approximated as a flat colour" in n for n in notes)


def test_artworks_pdf_gradient_fill_clip_does_not_leak_to_later_objects():
    # Regression test: a gradient fill's own clip (W/W* n) was
    # previously emitted outside any q/Q pair, so it never got
    # restored -- it leaked onto every draw call for the rest of the
    # picture (all sharing one outer q/Q; see _draw_artworks_picture),
    # silently clipping away anything drawn afterwards outside that one
    # gradient shape's own boundary. Found via a real document
    # (corpus/TestDoc,bc5's own CD-cover picture): its disc's own
    # gradient clip was leaking onto every text glyph drawn after it.
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import FillColourRecord, PathRecord, Point

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _direct, _list, _record, _square_path

    gradient_fill = _record(
        FillColourRecord, fill_type=1, unknown_28=0, colour=None,
        gradient_line=(Point(0, 500), Point(1000, 500)),
        start_colour=_direct(255, 255, 255), end_colour=_direct(0, 0, 0),
    )
    gradient_path = _record(PathRecord, path=_square_path(filled=True))

    flat_fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0, colour=_direct(0, 255, 0),
        gradient_line=None, start_colour=None, end_colour=None,
    )
    later_path = _record(PathRecord, path=_square_path(filled=True))

    artwork = _artwork((_list(gradient_fill), _list(gradient_path), _list(flat_fill), _list(later_path)))

    converter = PDFConverter(None)
    converter._content = []
    converter._page_shadings = {}
    converter._writer = _PDFWriter()
    style = dict(_DEFAULT_STYLE)
    converter._artworks_pdf_process_lists(artwork.record_lists, style, artwork, lambda x, y: (x, y), 1.0, [])
    content = "".join(converter._content)

    assert content.count("q\n") == content.count("Q\n")
    # The later object's own fill draws after the gradient's own q/Q
    # has already closed, not nested inside it.
    gradient_end = content.index("Q\n") + len("Q\n")
    assert "rg\n" in content[gradient_end:]


def test_artworks_pdf_pathified_character_renders_its_own_glyph_outline_not_tf_tj():
    # PDF counterpart of formats/artworks_svg.py's own equivalent test
    # -- see that test and _emit_character's own docstring for the
    # full explanation (ArtWorks "pathifies" individual characters when
    # it can't rely on standard text rendering, confirmed against a
    # real picture and the SDK manual's own PathifyText_* description).
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_artworks import CharacterRecord, PathRecord, TextRecord

    from riscos_impression.formats.artworks_svg import _DEFAULT_STYLE
    from riscos_impression.output.pdfdoc import PDFConverter
    from tests.test_formats_artworks_svg import _artwork, _list, _record, _square_path

    glyph_path = _record(PathRecord, path=_square_path(filled=True))
    char_a = _record(
        CharacterRecord, character_code=ord("A"), unknown_values=(1000, 2000, 0, 0),
        child_lists=(_list(glyph_path),),
    )
    text = _record(TextRecord, unknown_values=(0, 0, 0, 1, 1, 0), rectangle=(), child_lists=(_list(char_a),))
    artwork = _artwork((_list(text),))

    converter = PDFConverter(None)
    converter._content = []
    style = dict(_DEFAULT_STYLE)
    converter._artworks_pdf_process_lists(artwork.record_lists, style, artwork, lambda x, y: (x, y), 1.0, [])
    content = "".join(converter._content)

    assert " m\n" in content  # the glyph outline's own path ops
    assert "Tf " not in content
    assert "Tj" not in content


def test_artworks_pdf_picture_applies_xshift_yshift_and_xscale_yscale(tmp_path):
    # Regression test: the user reported two placements of similar
    # ArtWorks content in a real document rendering identically, when
    # their own declared xshift/yshift/xscale/yscale should have shown
    # each at its own distinct size and position -- _draw_artworks_
    # picture previously always scaled-to-fit-and-centred regardless
    # of these fields, matching neither placement's own real appearance.
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_impression.output.pdfdoc import PDFConverter

    picture_bytes = build_single_path_document()

    default = _artworks_picture_document(picture_bytes, x0=0, y0=0, x1=100000, y1=100000)
    out_a = tmp_path / "a.pdf"
    PDFConverter(default).convert(out_a)
    xa, ya = _first_moveto_point(out_a.read_bytes())

    shifted = _artworks_picture_document(
        picture_bytes, x0=0, y0=0, x1=100000, y1=100000, xshift=20000, yshift=10000,
    )
    out_b = tmp_path / "b.pdf"
    PDFConverter(shifted).convert(out_b)
    xb, yb = _first_moveto_point(out_b.read_bytes())

    assert (xb, yb) != (xa, ya)

    half_scale = _artworks_picture_document(
        picture_bytes, x0=0, y0=0, x1=100000, y1=100000, xscale=0x20000, yscale=0x20000,
    )
    out_c = tmp_path / "c.pdf"
    PDFConverter(half_scale).convert(out_c)
    # The first moveto point sits exactly at the shape's own native
    # origin, which xscale/yscale alone doesn't move (only points away
    # from it) -- the first lineto point does move, since it isn't at
    # the origin.
    match_a = re.search(rb"([\d.-]+) ([\d.-]+) l\n", out_a.read_bytes())
    match_c = re.search(rb"([\d.-]+) ([\d.-]+) l\n", out_c.read_bytes())
    assert match_a is not None and match_c is not None
    assert match_a.groups() != match_c.groups()


def test_artworks_pdf_picture_content_is_clipped_to_its_own_frame(tmp_path):
    # Regression test: _draw_artworks_picture had no clip rectangle at
    # all (unlike _draw_drawfile_picture's own "re W n"), so a picture
    # placed/scaled bigger than its own frame (a real, legitimate case
    # once xshift/yshift/xscale are honoured -- see the sibling test
    # above) would bleed into whatever else shares the same page.
    pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")
    from riscos_impression.output.pdfdoc import PDFConverter

    document = _artworks_picture_document(
        build_single_path_document(), x0=10000, y0=20000, x1=60000, y1=70000,
    )
    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"10 20 50 50 re W n\n" in data
    assert not converter.log.has_errors()
