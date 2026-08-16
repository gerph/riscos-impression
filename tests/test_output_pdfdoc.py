import re

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
from tests.fixtures.drawfile_builders import (
    build_drawfile,
    build_font_table,
    build_group,
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

    # Top edge (y=50) and bottom edge (y=0) as horizontal line segments.
    assert "0 50 m 100 50 l S" in content
    assert "0 0 m 100 0 l S" in content
    # No vertical (left/right) edge segments at all.
    assert "0 0 m 0 50 l S" not in content
    assert "100 0 m 100 50 l S" not in content
    # And no full-rectangle stroke (the old, wrong behaviour).
    assert " re S" not in content


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

    assert "0 50 m 100 50 l S" in content
    assert "0 0 m 100 0 l S" in content
    assert "0 0 m 0 50 l S" in content
    assert "100 0 m 100 50 l S" in content


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


def test_oversized_right_indent_falls_back_to_the_full_container_width(tmp_path):
    """Regression test: a real document (PCI_Spec from the local
    examples/ corpus) has a style whose right_indent (a delta from the
    frame's own right edge) very nearly equals the entire width of the
    frame it's actually used in -- almost certainly authored for a
    wider frame, since styles are shared across frames of any size
    (compare the tab-ruler regression above). Before the fix, this left
    less than _MIN_USABLE_WIDTH on every line, which is treated the
    same as an obstacle leaving no room: skip past it and try the next
    line. Since the paragraph's tokens are never consumed, this burned
    through the whole frame (and every later chain member) without ever
    placing a line, silently dropping this paragraph *and every one
    after it* in the whole story -- not just producing a badly-indented
    line."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # Frame is 100pt wide; a right_indent of 95pt leaves only 5pt, well
    # under _MIN_USABLE_WIDTH (10pt).
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


def test_oversized_right_indent_preserves_a_real_intentional_left_indent(tmp_path):
    """Regression test: a real document's title-block style (PCI_Spec)
    has BOTH a large left_indent (a label column, ~113pt in) and a
    right_indent set to nearly the frame's own full width -- confirmed
    against the user's own reference image (labels like "Distribution:"
    start well right of the frame's own edge) and against Impression's
    own ruler dialog (a "left bound" of about 4cm). The general
    right_indent fallback above (dropping BOTH margins back to the
    container's full width) wiped out this real, intentional indent,
    left-aligning every label flush against the frame's own edge
    instead. Only the right margin should be dropped when the left
    position alone still leaves enough room."""
    from riscos_impression.output.pdfdoc import PDFConverter

    body = _style(0, is_body_text=True, font_size=160)
    # Frame is 300pt wide; left_indent of 113pt leaves 187pt of real
    # room, comfortably above _MIN_USABLE_WIDTH, but right_indent is
    # set to 295pt (near the frame's own width), which alone would
    # leave under 10pt if line_start were also reset to 0.
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


def _picture_document(picture_bytes: bytes, *, x0=0, y0=0, x1=100000, y1=100000):
    document, _unused = _document_with_one_text_frame()
    picture = _picture(x0=x0, y0=y0, x1=x1, y1=y1, dictionary_index=1)
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


def test_drawfile_sprite_sub_object_falls_back_to_a_placeholder_and_logs_best_effort(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    document = _picture_document(build_drawfile(build_sprite(bounds=(0, 0, 1000, 1000)), bounds=(0, 0, 1000, 1000)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"([Sprite])" in data
    assert any("Sprite object embedded within a DrawFile" in e.message for e in converter.log.entries)


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


def test_drawfile_dashed_path_renders_solid_and_logs_best_effort(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    ops = move(0, 0) + line(2560, 0) + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 2560, 100), stroke_colour=0x000000FF, dashed=True)
    document = _picture_document(build_drawfile(path, bounds=(0, 0, 2560, 100)))

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    assert b"\nS\n" in data  # stroked, since there's no fill colour
    assert any("dash patterns are not reproduced" in e.message for e in converter.log.entries)


def test_drawfile_group_and_unknown_object_types_are_handled(tmp_path):
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
    assert any("were not decoded and are omitted" in e.message for e in converter.log.entries)
