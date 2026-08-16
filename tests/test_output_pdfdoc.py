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
