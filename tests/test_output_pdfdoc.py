from riscos_impression.model.colours import Colour, ColourModel
from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import Paragraph, Run, Story
from riscos_impression.model.styles import TabStop
from riscos_impression.output.pdfdoc import (
    STANDARD_FONTS,
    _approx_width,
    _fill_colour_op,
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


def test_picture_frame_renders_a_placeholder_and_logs_best_effort(tmp_path):
    from riscos_impression.output.pdfdoc import PDFConverter

    document, _unused = _document_with_one_text_frame()
    picture = _picture(x0=0, y0=0, x1=100000, y1=100000, dictionary_index=1)
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
    document.picture_bytes = lambda entry: b"Draw" + b"\x00" * 40  # a minimal, valid-enough DrawFile header

    converter = PDFConverter(document)
    out = tmp_path / "out.pdf"
    converter.convert(out)

    assert any(e.area == "picture" for e in converter.log.entries)
