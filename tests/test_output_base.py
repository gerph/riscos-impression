from pathlib import Path

import pytest

from riscos_impression.io.source import DocumentSource
from riscos_impression.model.document import FileHeader, ImpressionDocument
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import ObjectRecord, ObjectType, Page, Section, TextFrame
from riscos_impression.model.styles import Style
from riscos_impression.model.story import Story
from riscos_impression.output.base import Converter, page_origin, to_page_coordinates
from tests.fixtures.builders import build_header


def _style(index, is_body_text=False, **overrides):
    fields = dict(
        index=index,
        is_body_text=is_body_text,
        name=f"Style{index}",
        key=0,
        paragraph_apply=False,
        is_contents_entry_style=False,
        is_index_entry_style=False,
        is_effect=False,
        shows_on_style_menu=False,
        text_back_colour=False,
        tabs=0,
    )
    fields.update(overrides)
    return Style(**fields)


def _frame(**overrides):
    fields = dict(
        x0=0, y0=0, x1=100, y1=100,
        selected=False, repel=False, filled=False, master=False, locked=False,
        grouped=False, repeating=False, level=0,
        dictionary_index=-1,
        exx0=0, exy0=0, exx1=0, exy1=0,
        master_index=0,
        fill_colour_word=0,
        hinset=0, vinset=0,
        border0=0xFF, border1=0xFF, border2=0xFF, border3=0xFF,
        border_colour_word=0xFFFFFFFF,
        embed_tag=0,
        group_number=0,
        overprint=False,
    )
    fields.update(overrides)
    return TextFrame(**fields)


def _page_record(offset, x0=0, y0=0, x1=1000, y1=1000, bleed=0):
    return ObjectRecord(
        offset=offset,
        raw_type=0x1,
        type=ObjectType.PAGE,
        length=68 + 8,
        value=Page(x0=x0, y0=y0, x1=x1, y1=y1, bleed=bleed, master_page_name=""),
    )


def _frame_record(offset, frame):
    return ObjectRecord(offset=offset, raw_type=0x2, type=ObjectType.TEXT, length=104 + 8, value=frame)


def _section(create_number=1, master_page_index=0, **overrides):
    fields = dict(
        create_number=create_number,
        master_page_index=master_page_index,
        start_page_number=0,
        override_start_page=False,
        start_on_right=False,
        copy_previous=False,
        start_chapter_number=0,
        override_start_chapter=False,
    )
    fields.update(overrides)
    return Section(**fields)


def _header(**overrides):
    return FileHeader.from_bytes(build_header(**overrides))


def _document(*, directory_mode=False, styles=(), chapters=(), master_pages=(), header=None):
    source = DocumentSource(path=Path("dummy"), directory_mode=directory_mode, docdata=b"")
    return ImpressionDocument(
        source=source,
        header=header if header is not None else _header(),
        colours=[],
        styles=list(styles),
        numbering=[],
        dictionary=[],
        master_dictionary=[],
        master_pages=list(master_pages),
        chapters=list(chapters),
    )


def test_catch_suppresses_and_logs():
    converter = Converter(_document())
    with converter.catch("test-area", location="here"):
        raise ValueError("boom")
    assert converter.log.has_errors()
    assert converter.log.entries[0].area == "test-area"
    assert "boom" in converter.log.entries[0].message
    assert converter.log.entries[0].location == "here"


def test_catch_reraises_when_strict():
    converter = Converter(_document(), strict=True)
    with pytest.raises(ValueError):
        with converter.catch("test-area"):
            raise ValueError("boom")


def test_page_origin_and_coordinates():
    page = Page(x0=100, y0=200, x1=900, y1=1200, bleed=10, master_page_name="")
    origin = page_origin(page)
    assert origin.x == 110
    assert origin.y == 1190
    assert to_page_coordinates(origin, 110, 1190) == (0, 0)
    assert to_page_coordinates(origin, 210, 1090) == (100, 100)


def test_resolve_style_cascades_over_body():
    body = _style(0, is_body_text=True, bold=0, italic=0, font_size=160)
    bold_style = _style(1, bold=1)  # only overrides bold; italic/font_size fall through
    converter = Converter(_document(styles=[body, bold_style]))

    resolved = converter.resolve_style([1])
    assert resolved.bold == 1
    assert resolved.italic == 0
    assert resolved.font_size == 160


def test_resolve_style_unknown_slot_is_ignored():
    body = _style(0, is_body_text=True, bold=0)
    converter = Converter(_document(styles=[body]))
    resolved = converter.resolve_style([99])
    assert resolved.bold == 0


def test_resolve_style_raises_without_body_style():
    converter = Converter(_document(styles=[]))
    with pytest.raises(ValueError):
        converter.resolve_style([])


def test_resolve_frame_chain_single_file_mode():
    # Single-file mode: the conversion source resolves a chain offset as
    # mainpages2 + offset (see txstorychain()'s base pointer), not as an
    # absolute docdata offset directly.
    frame = _frame(dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100, y1=100, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    chapter = Chapter(
        section=_section(), offset=900, master_page_1=None, master_page_2=None, pages=(page,)
    )
    header = _header(mainpages2=900)
    converter = Converter(_document(directory_mode=False, chapters=[chapter], header=header))

    story = Story(frame_chain=(1008 - 900,), paragraphs=())
    frames = converter.resolve_frame_chain(story, chapter=chapter)
    assert [r.value for r in frames] == [frame]


def test_resolve_frame_chain_directory_mode_adjusts_by_chapter_offset():
    frame = _frame(dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100, y1=100, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    chapter = Chapter(
        section=_section(), offset=900, master_page_1=None, master_page_2=None, pages=(page,)
    )
    converter = Converter(_document(directory_mode=True, chapters=[chapter]))

    # Directory mode: raw offset is relative to the chapter's own Section
    # record (offset=900), so 1008 - 900 = 108 is what's stored on disk.
    story = Story(frame_chain=(108,), paragraphs=())
    frames = converter.resolve_frame_chain(story, chapter=chapter)
    assert [r.value for r in frames] == [frame]


def test_resolve_frame_chain_unresolved_offset_is_logged_not_raised():
    chapter = Chapter(
        section=_section(), offset=0, master_page_1=None, master_page_2=None, pages=()
    )
    converter = Converter(_document(chapters=[chapter]))
    story = Story(frame_chain=(99999,), paragraphs=())

    frames = converter.resolve_frame_chain(story, chapter=chapter)
    assert frames == []
    assert converter.log.has_errors()


def test_resolve_frame_chain_requires_chapter_unless_master():
    converter = Converter(_document())
    story = Story(frame_chain=(), paragraphs=())
    with pytest.raises(ValueError):
        converter.resolve_frame_chain(story)


def test_resolve_frame_chain_master_pages():
    # A master-page story's chain offset resolves as masterpages1 + offset.
    frame = _frame(dictionary_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100, y1=100, bleed=0, master_page_name=""),
        offset=100,
        records=(_frame_record(108, frame),),
    )
    header = _header(masterpages1=50)
    converter = Converter(_document(master_pages=[master_page], header=header))
    story = Story(frame_chain=(108 - 50,), paragraphs=())
    frames = converter.resolve_frame_chain(story, master=True)
    assert [r.value for r in frames] == [frame]


def test_default_convert_walk_calls_hooks_in_order(tmp_path):
    frame = _frame(dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100, y1=100, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    chapter = Chapter(
        section=_section(), offset=900, master_page_1=None, master_page_2=None, pages=(page,)
    )
    document = _document(chapters=[chapter])

    events = []

    class RecordingConverter(Converter):
        def begin_document(self):
            events.append("begin_document")

        def begin_chapter(self, chapter):
            events.append("begin_chapter")

        def begin_page(self, chapter, page):
            events.append("begin_page")

        def emit_frame(self, chapter, page, frame):
            events.append(("emit_frame", frame))

        def end_page(self, chapter, page):
            events.append("end_page")

        def end_chapter(self, chapter):
            events.append("end_chapter")

        def end_document(self):
            events.append("end_document")

        def write(self, output_path):
            events.append("write")

    RecordingConverter(document).convert(tmp_path / "out")

    assert events == [
        "begin_document",
        "begin_chapter",
        "begin_page",
        ("emit_frame", frame),
        "end_page",
        "end_chapter",
        "end_document",
        "write",
    ]


def test_default_convert_walk_catches_emit_frame_exceptions(tmp_path):
    frame = _frame(dictionary_index=0)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=100, y1=100, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame),),
    )
    chapter = Chapter(
        section=_section(), offset=900, master_page_1=None, master_page_2=None, pages=(page,)
    )
    document = _document(chapters=[chapter])

    class FailingConverter(Converter):
        def emit_frame(self, chapter, page, frame):
            raise RuntimeError("emit failed")

        def write(self, output_path):
            pass

    converter = FailingConverter(document)
    converter.convert(tmp_path / "out")  # must not raise
    assert converter.log.has_errors()
