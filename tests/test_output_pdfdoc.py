from riscos_impression.model.colours import Colour, ColourModel
from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import Paragraph, Run, Story
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
    assert _next_tab_stop(10.0, tab_base_x=0.0, style=style) == 50.0


def test_next_tab_stop_default_pitch_without_a_ruler():
    style = _style(1, is_body_text=True, tab_stops=())
    assert _next_tab_stop(10.0, tab_base_x=0.0, style=style) == 36.0
    assert _next_tab_stop(40.0, tab_base_x=0.0, style=style) == 72.0


def test_tab_advance_moves_to_the_stop_when_it_fits():
    style = _style(1, is_body_text=True, tab_stops=(TabStop(kind=0, position=50000),))
    assert _tab_advance(10.0, tab_base_x=0.0, style=style, right_edge=100.0) == 50.0


def test_tab_advance_is_a_no_op_when_the_stop_would_overflow():
    style = _style(1, is_body_text=True, tab_stops=(TabStop(kind=0, position=576000),))
    assert _tab_advance(10.0, tab_base_x=0.0, style=style, right_edge=300.0) == 10.0


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
    # own points-per-Draw-unit ratio was a unit mismatch. Uses
    # realistic Draw-unit-scale bounds (tens of thousands of units, not
    # a round number matching the target box in points) -- with a 1:1-
    # scale bounds/target box, the bug and the fix give the same
    # answer, which is exactly why this needed its own test.
    from riscos_impression.output.pdfdoc import PDFConverter, _DRAW_UNIT_TO_PT, _fmt

    fonts = build_font_table({1: "Homerton.Medium"})
    text = build_text(text="Hello", font_number=1, size_x=8960, size_y=8960, baseline_x=0, baseline_y=0)
    bounds = (0, 0, 25600, 25600)  # a 100 OS-unit-square native canvas
    document = _picture_document(build_drawfile(fonts + text, bounds=bounds), x1=100000, y1=100000)  # 100pt target

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)
    data = out.read_bytes()

    sy = 100.0 / 25600  # target points per source Draw unit
    expected_size_pt = (8960 / 640.0) * (sy / _DRAW_UNIT_TO_PT)
    assert expected_size_pt > 1.0  # sanity: this is nowhere near the old ~0.01pt bug
    assert f"{_fmt(expected_size_pt)} Tf".encode("latin-1") in data


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
