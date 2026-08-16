from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import ChapterNumberMark, MergeMark, Paragraph, Run, Story
from riscos_impression.output.markdown import MarkdownConverter

from tests.test_output_ovprodll import _picture
from tests.test_output_base import _document, _frame, _frame_record, _header, _section, _style


def _document_with_records(records, *, extra_styles=()):
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
    body = _style(0, is_body_text=True, font_size=160)
    document = _document(
        chapters=[chapter], master_pages=[master_page], styles=[body, *extra_styles], header=header
    )
    return document


def test_convert_produces_plain_paragraph_text(tmp_path):
    frame = _frame(dictionary_index=0)
    document = _document_with_records([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Hello world", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = MarkdownConverter(document)
    out = tmp_path / "out.md"
    converter.convert(out)
    text = out.read_text()

    assert text.strip() == "Hello world"
    assert not converter.log.has_errors()


def test_named_style_with_bigger_font_becomes_a_heading(tmp_path):
    heading_style = _style(1, font_size=320)  # body is 160 (10pt); 320 (20pt) is a 2.0 ratio -> H1
    frame = _frame(dictionary_index=0)
    document = _document_with_records([_frame_record(1008, frame)], extra_styles=(heading_style,))
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Title", style_slots=(1,)),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = MarkdownConverter(document)
    out = tmp_path / "out.md"
    converter.convert(out)
    text = out.read_text()

    assert text.strip() == "# Title"


def test_plain_body_text_is_never_a_heading_even_with_a_named_run_style(tmp_path):
    # A named style applied to a run without changing font size (e.g. a
    # colour-only style) shouldn't be promoted to a heading.
    coloured_style = _style(1, font_size=160)
    frame = _frame(dictionary_index=0)
    document = _document_with_records([_frame_record(1008, frame)], extra_styles=(coloured_style,))
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Body copy", style_slots=(1,)),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = MarkdownConverter(document)
    out = tmp_path / "out.md"
    converter.convert(out)
    text = out.read_text()

    assert text.strip() == "Body copy"


def test_picture_frame_renders_bracketed_placeholder(tmp_path):
    picture = _picture(x0=0, y0=0, x1=100000, y1=50000, dictionary_index=1)
    document = _document_with_records([_frame_record(1008, picture)])
    dict_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.append(dict_entry)

    converter = MarkdownConverter(document)
    out = tmp_path / "out.md"
    converter.convert(out)
    text = out.read_text()

    assert "[draw]" in text
    assert any(e.area == "picture" for e in converter.log.entries)


def test_merge_and_chapter_number_marks(tmp_path):
    frame = _frame(dictionary_index=0)
    document = _document_with_records([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(Run(text="Chapter ", style_slots=()), ChapterNumberMark(), MergeMark(field_name="Name"))),),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = MarkdownConverter(document)
    out = tmp_path / "out.md"
    converter.convert(out)
    text = out.read_text()

    assert "Chapter 1<<Name>>" in text


def test_bordered_frame_grid_is_recognised_as_a_table(tmp_path):
    """A clean 2x2 grid of bordered text frames should render as a
    Markdown table, with each frame's story becoming one cell."""
    cells = {}
    records = []
    offset = 1008
    for row, y in enumerate((100000, 50000)):
        for col, x in enumerate((0, 50000)):
            frame = _frame(
                x0=x, y0=y - 50000, x1=x + 50000, y1=y,
                border0=0, dictionary_index=offset,  # any non-negative value; used as a unique key below
            )
            records.append(_frame_record(offset, frame))
            cells[offset] = f"R{row}C{col}"
            offset += 100

    document = _document_with_records(records)
    for idx in cells:
        document.dictionary.append(DictionaryEntry(index=idx, type=DictionaryEntryType.TEXT, id=0, types=0))

    def fake_story(entry):
        text = cells[entry.index]
        return Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text=text, style_slots=()),)),))

    document.story = fake_story

    converter = MarkdownConverter(document)
    out = tmp_path / "out.md"
    converter.convert(out)
    text = out.read_text()

    assert "| R0C0 | R0C1 |" in text
    assert "| R1C0 | R1C1 |" in text
    assert "| --- | --- |" in text
    assert any(e.area == "table" for e in converter.log.entries)


def test_irregular_bordered_frames_are_not_treated_as_a_table(tmp_path):
    """Bordered frames that don't line up into a clean grid -- here, a
    2x2 arrangement whose second row's columns are shifted well past
    the alignment tolerance -- should fall back to being rendered as
    ordinary paragraphs, not a guessed table."""
    records = []
    offset = 1008
    # Row 1 columns at x=0 and x=50000; row 2 columns at x=0 and
    # x=60000 -- the second column doesn't line up between rows.
    for y, xs in ((100000, (0, 50000)), (50000, (0, 60000))):
        for x in xs:
            frame = _frame(x0=x, y0=y - 50000, x1=x + 10000, y1=y, border0=0, dictionary_index=offset)
            records.append(_frame_record(offset, frame))
            offset += 100

    document = _document_with_records(records)
    for record in records:
        document.dictionary.append(DictionaryEntry(index=record.value.dictionary_index, type=DictionaryEntryType.TEXT, id=0, types=0))
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Cell", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = MarkdownConverter(document)
    out = tmp_path / "out.md"
    converter.convert(out)
    text = out.read_text()

    assert "|" not in text
    assert not any(e.area == "table" for e in converter.log.entries)
