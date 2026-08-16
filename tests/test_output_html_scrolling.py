from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import ChapterNumberMark, MergeMark, Paragraph, Run, Story
from riscos_impression.output.html_scrolling import ScrollingHTMLConverter

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
