from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import Paragraph, Run, Story
from riscos_impression.output.html_paged import PagedHTMLConverter

from tests.test_output_ovprodll import _picture
from tests.test_output_base import _document, _frame, _frame_record, _header, _section, _style
from tests.fixtures.drawfile_builders import build_drawfile, build_path, close_line, end_path, line, move


def _document_with_frames(records):
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
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)
    return document, chapter, master_page


def test_convert_produces_a_paged_page_with_positioned_frame(tmp_path):
    frame = _frame(x0=10000, y0=20000, x1=60000, y1=70000, dictionary_index=0)
    document, _, _ = _document_with_frames([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Hello", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert text.startswith("<!DOCTYPE html>")
    assert 'class="ro-page"' in text
    assert 'class="ro-frame"' in text
    assert "Hello" in text
    # page height is 150pt; frame top-left in doc space (y1=70000=70pt from
    # bottom) becomes CSS top = 150 - 70 = 80pt, left = x0 = 10pt.
    assert "left: 10.00pt" in text
    assert "top: 80.00pt" in text
    assert not converter.log.has_errors()


def test_frame_border_only_emits_the_present_edges(tmp_path):
    """Regression test: a real document's footer frame (PCI_Spec) has
    only its top and bottom borders present, with left/right both
    0xFF -- but the whole frame still came out with a uniform CSS
    `border` on all four edges, since has_border only gated whether to
    emit a border at all, not which edges. The border0..3-to-physical-
    edge mapping (top/left/right/bottom) was confirmed empirically
    against this same real document earlier in this project's
    development."""
    frame = _frame(
        x0=10000, y0=20000, x1=60000, y1=70000, dictionary_index=0,
        border0=1, border1=0xFF, border2=0xFF, border3=1,  # top + bottom only
        border_colour_word=0,
    )
    document, _, _ = _document_with_frames([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Hello", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "border-top:" in text
    assert "border-bottom:" in text
    assert "border-left:" not in text
    assert "border-right:" not in text
    assert "border:" not in text  # the old uniform shorthand


def test_master_linked_frame_uses_the_master_pages_own_origin(tmp_path):
    """Regression test mirroring the PDF converter's own fix: a
    master-linked frame's substituted appearance comes from the master
    page's own, separate absolute coordinate canvas, not the content
    page's -- using the wrong origin would place it far outside the
    page."""
    furniture = _frame(
        x0=1000, y0=2000, x1=51000, y1=52000, dictionary_index=-1, master=False, master_index=7,
    )
    linked = _frame(x0=0, y0=0, x1=1, y1=1, dictionary_index=-1, master=True, master_index=7)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=100,
        records=(_frame_record(108, furniture),),
    )
    content_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, linked),),
        master_page=master_page,
    )
    section = _section(create_number=1, master_page_index=0)
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(content_page,)
    )
    body = _style(0, is_body_text=True, font_size=160)
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    # Master page and content page share the same box here, so the
    # master-relative and content-relative positions coincide: left=1pt,
    # top = 150 - 52 = 98pt.
    assert "left: 1.00pt" in text
    assert "top: 98.00pt" in text
    assert not converter.log.has_errors()


def test_non_drawfile_picture_frame_renders_placeholder_image(tmp_path):
    picture = _picture(x0=0, y0=0, x1=100000, y1=50000, dictionary_index=1)
    document, _, _ = _document_with_frames([_frame_record(1008, picture)])
    dict_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.append(dict_entry)
    document.picture_bytes = lambda entry: b"NOPE" + b"\x00" * 40  # not a DrawFile, not a sprite either

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "<img src=\"data:image/svg+xml;base64," in text
    assert any(e.area == "picture" for e in converter.log.entries)


def test_drawfile_picture_frame_renders_as_real_svg_content(tmp_path):
    picture = _picture(x0=0, y0=0, x1=100000, y1=50000, dictionary_index=1)
    document, _, _ = _document_with_frames([_frame_record(1008, picture)])
    dict_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.append(dict_entry)
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    document.picture_bytes = lambda entry: build_drawfile(path, bounds=(0, 0, 1000, 1000))

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "<svg " in text
    assert "<path " in text
    assert "<img" not in text
    assert not converter.log.has_errors()


def test_multi_frame_chain_logs_best_effort_once(tmp_path):
    frame = _frame(dictionary_index=0)
    document, _, _ = _document_with_frames([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(824,), paragraphs=(Paragraph(items=(Run(text="Body", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "Body" in text
    assert sum(1 for e in converter.log.entries if "multi-frame chain" in e.message) == 1


def test_export_pdf_logs_when_no_tool_is_available(tmp_path, monkeypatch):
    import riscos_impression.output.html_paged as html_paged_module

    monkeypatch.setattr(html_paged_module.shutil, "which", lambda name: None)

    frame = _frame(dictionary_index=0)
    document, _, _ = _document_with_frames([_frame_record(1008, frame)])
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Hi", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PagedHTMLConverter(document, export_pdf=True)
    out = tmp_path / "out.html"
    converter.convert(out)

    assert any("PDF export skipped" in e.message for e in converter.log.entries)
