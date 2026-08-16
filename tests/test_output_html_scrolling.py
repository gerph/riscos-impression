import re

from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import ChapterNumberMark, EmbedMark, MergeMark, Paragraph, Run, Story, TabMark
from riscos_impression.model.styles import TabStop
from riscos_impression.output.html_scrolling import ScrollingHTMLConverter, _approx_width

from tests.test_output_ovprodll import _picture
from tests.test_output_base import _document, _frame, _frame_record, _header, _section, _style
from tests.fixtures.drawfile_builders import build_drawfile, build_path, close_line, end_path, line, move


def _document_with_frames(records, *, styles=None):
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=tuple(records),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    body = styles[0] if styles else _style(0, is_body_text=True, font_size=160)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=styles or [body], header=header
    )
    return document, chapter


def test_convert_produces_well_formed_html_with_text(tmp_path):
    frame = _frame(dictionary_index=0)
    document, _ = _document_with_frames([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Hello world", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = ScrollingHTMLConverter(document)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert text.startswith("<!DOCTYPE html>")
    assert "<section class=\"chapter\"" in text
    assert "Hello world" in text
    assert "<p " in text  # always carries an inline style (line-height at least)
    assert not converter.log.has_errors()


def test_repeated_dictionary_index_renders_once(tmp_path):
    frame_a = _frame(x0=0, y0=0, x1=50000, y1=50000, dictionary_index=0)
    frame_b = _frame(x0=0, y0=60000, x1=50000, y1=100000, dictionary_index=0)
    document, _ = _document_with_frames([_frame_record(1008, frame_a), _frame_record(1108, frame_b)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Once", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = ScrollingHTMLConverter(document)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert text.count("Once") == 1


def test_non_drawfile_picture_frame_renders_placeholder_image(tmp_path):
    picture = _picture(x0=0, y0=0, x1=100000, y1=50000, dictionary_index=1)
    document, _ = _document_with_frames([_frame_record(1008, picture)])
    dict_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.append(dict_entry)
    document.picture_bytes = lambda entry: b"NOPE" + b"\x00" * 40  # not a DrawFile, not a sprite either

    converter = ScrollingHTMLConverter(document)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "<img src=\"data:image/svg+xml;base64," in text
    assert any(e.area == "picture" for e in converter.log.entries)


def test_drawfile_picture_frame_renders_as_real_svg_content(tmp_path):
    picture = _picture(x0=0, y0=0, x1=100000, y1=50000, dictionary_index=1)
    document, _ = _document_with_frames([_frame_record(1008, picture)])
    dict_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.append(dict_entry)
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    document.picture_bytes = lambda entry: build_drawfile(path, bounds=(0, 0, 1000, 1000))

    converter = ScrollingHTMLConverter(document)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "<svg " in text
    assert "<path " in text
    assert "<img" not in text
    assert not converter.log.has_errors()


def test_embed_tagged_picture_frame_is_not_also_drawn_independently(tmp_path):
    """Regression test: the user reported PCI_Spec's 3 DrawFile diagrams
    appearing repeated (once inline, once again independently, near
    the end of the document) in scrolling HTML. A PictureFrame with a
    non-zero embed_tag is anchored inline within a text story at the
    matching EmbedMark's own position (_render_embed), not drawn again
    at its own top-level position in the page's frame list -- mirrors
    html_paged.py's own, already-fixed _render_frame check (and
    pdfdoc.py's _draw_frame) exactly. This converter has no page
    geometry of its own, so a leaked independent draw doesn't show up
    at a visibly wrong position the way it did for html_paged.py --
    it just renders twice, wherever the raw frame happens to sit in
    the page's flat frame list (in PCI_Spec, at the very end)."""
    text_frame = _frame(dictionary_index=0)
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    picture_frame = _picture(x0=500000, y0=500000, x1=560000, y1=540000, embed_tag=42, dictionary_index=1)
    document, _ = _document_with_frames(
        [_frame_record(1008, text_frame), _frame_record(1108, picture_frame)]
    )
    text_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    picture_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.extend([text_entry, picture_entry])
    document.picture_bytes = lambda entry: build_drawfile(path, bounds=(0, 0, 1000, 1000))

    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(Run(text="Before", style_slots=()), EmbedMark(embed_tag=42))),),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = ScrollingHTMLConverter(document)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert text.count("<svg ") == 1
    assert not converter.log.has_errors()


def test_merge_and_chapter_number_marks(tmp_path):
    frame = _frame(dictionary_index=0)
    document, _ = _document_with_frames([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(Run(text="Chapter ", style_slots=()), ChapterNumberMark(), MergeMark(field_name="Name"))),),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = ScrollingHTMLConverter(document)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "Chapter 1" in text
    assert "&lt;&lt;Name&gt;&gt;" in text  # merge placeholder text is HTML-escaped like any other text


def test_tab_lands_on_declared_stop_via_an_in_flow_spacer():
    # Regression test: the user reported PCI_Spec's numbered Contents
    # list not right-aligning its chapter numbers in scrolling HTML
    # either (a plain, unaligned literal tab -- deliberate, given this
    # format tracks no frame width; see the module docstring). That
    # reasoning conflated two different things: a tab's own declared
    # stop is an absolute point offset from the paragraph's own left
    # margin, needing no frame width at all to resolve -- only
    # right_indent (an inset from the frame's own *right* edge) does.
    # Tabs now use the same in-flow, measure-ahead spacer mechanism as
    # html_paged.py (see its own test_right_tabbed_segment_lands_via_
    # an_in_flow_spacer_not_position_absolute), mirroring this row's
    # real three-stop right/left/right ruler, with only the first
    # segment's width (1 vs 2 digit chapter number) varying between
    # the two rows.
    document, chapter = _document_with_frames([])
    style = _style(
        1,
        font_size=160,
        tab_stops=(
            TabStop(kind=2, position=113385),
            TabStop(kind=0, position=121889),
            TabStop(kind=2, position=396850),
        ),
        left_indent=121889,
        first_indent_absolute=121889 - 93544,
    )
    document.styles.append(style)
    converter = ScrollingHTMLConverter(document)
    converter._chapter_number = 1
    converter._dictionary_by_index = {}

    def render(number_text, name_text, page_text):
        paragraph = Paragraph(
            items=(
                TabMark(),
                Run(text=number_text, style_slots=(1,)),
                TabMark(),
                Run(text=name_text, style_slots=(1,)),
                TabMark(),
                Run(text=page_text, style_slots=(1,)),
            )
        )
        return converter._render_paragraph(paragraph, 0, chapter)

    single_digit_row = render("1", "History", "2")
    double_digit_row = render("10", "External Dependencies", "7")

    assert "position:absolute" not in single_digit_row
    assert "position:absolute" not in double_digit_row

    def checkpoints(html: str, *texts: str) -> list[float]:
        spacers = [float(w) for w in re.findall(r"display:inline-block;width:([\d.]+)pt", html)]
        assert len(spacers) == len(texts) == 3
        total = (121889 - 93544) / 1000.0  # left_indent_pt + first_indent_pt for this style
        total += spacers[0] + _approx_width(texts[0], style)
        checkpoint1 = total
        total += spacers[1]
        checkpoint2 = total
        total += _approx_width(texts[1], style) + spacers[2] + _approx_width(texts[2], style)
        checkpoint3 = total
        return [round(checkpoint1, 1), round(checkpoint2, 1), round(checkpoint3, 1)]

    assert checkpoints(single_digit_row, "1", "History", "2") == [113.4, 121.9, 396.8]
    assert checkpoints(double_digit_row, "10", "External Dependencies", "7") == [113.4, 121.9, 396.8]
    assert ">External Dependencies<" in double_digit_row
    assert ">7<" in double_digit_row
