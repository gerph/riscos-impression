import struct

from riscos_impression.formats.eps import EPSObject
from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.story import Paragraph, Run, Story
from riscos_impression.output.extract import ExtractConverter

from tests.test_output_base import _document, _style
from tests.fixtures.drawfile_builders import build_drawfile, build_path, close_line, end_path, line, move


def _build_eps_blob(*, name: str, content: bytes) -> bytes:
    header = bytearray(68)
    struct.pack_into("<I", header, 12, len(content))
    name_bytes = name.encode("latin-1") + b"\x00"
    name_field = name_bytes + b"\x00" * ((4 - len(name_bytes) % 4) % 4)
    return bytes(header) + name_field + content


def _document_with_dictionary(entries, styles=None):
    body = styles[0] if styles else _style(0, is_body_text=True, font_size=160)
    document = _document(styles=styles or [body])
    document.dictionary = list(entries)
    return document


def test_extract_writes_plain_text_and_html_for_a_text_entry(tmp_path):
    entry = DictionaryEntry(index=3, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document_with_dictionary([entry])
    story = Story(
        frame_chain=(),
        paragraphs=(
            Paragraph(items=(Run(text="Hello world", style_slots=()),)),
            Paragraph(items=(Run(text="Second paragraph", style_slots=()),)),
        ),
    )
    document.story = lambda e: story  # noqa: ARG005 - test stub

    converter = ExtractConverter(document)
    converter.extract(tmp_path)

    text = (tmp_path / "text" / "0003.txt").read_text()
    assert text == "Hello world\n\nSecond paragraph\n"

    html = (tmp_path / "html" / "0003.html").read_text()
    assert "<!DOCTYPE html>" in html
    assert "Hello world" in html
    assert "Second paragraph" in html
    assert not converter.log.has_errors()


def test_extract_text_uses_the_style_tables_own_css():
    entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    heading = _style(1, is_body_text=False, font_size=320, bold=True)
    document = _document_with_dictionary([entry], styles=[_style(0, is_body_text=True, font_size=160), heading])
    story = Story(frame_chain=(), paragraphs=(Paragraph(items=(Run(text="Heading", style_slots=(1,)),)),))
    document.story = lambda e: story  # noqa: ARG005 - test stub

    converter = ExtractConverter(document)
    html = converter._story_html(story)

    assert "font-size: 20.00pt" in html
    assert "font-weight: bold" in html


def test_extract_writes_drawfile_picture_as_raw_and_svg(tmp_path):
    entry = DictionaryEntry(index=5, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document = _document_with_dictionary([entry])
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    raw = build_drawfile(path, bounds=(0, 0, 1000, 1000))
    document.picture_bytes = lambda e: raw  # noqa: ARG005 - test stub

    converter = ExtractConverter(document)
    converter.extract(tmp_path)

    assert (tmp_path / "images" / "0005,aff").read_bytes() == raw
    svg = (tmp_path / "svg" / "0005.svg").read_text()
    assert svg.startswith("<svg ")
    assert "<path " in svg
    assert not converter.log.has_errors()


def test_extract_writes_eps_content_stripped_of_its_wrapper(tmp_path):
    entry = DictionaryEntry(index=7, type=DictionaryEntryType.PICTURE, id=0, types=0xFF5)
    document = _document_with_dictionary([entry])
    content = b"%!PS-Adobe-3.0 EPSF-3.0\n...eps content...\n"
    raw = _build_eps_blob(name="MyPicture", content=content)
    document.picture_bytes = lambda e: raw  # noqa: ARG005 - test stub

    converter = ExtractConverter(document)
    converter.extract(tmp_path)

    written = (tmp_path / "images" / "0007,ff5").read_bytes()
    assert written == content
    assert written == EPSObject.from_bytes(raw).data


def test_extract_undecodable_drawable_picture_falls_back_to_bin_and_logs_error(tmp_path):
    entry = DictionaryEntry(index=9, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document = _document_with_dictionary([entry])
    document.picture_bytes = lambda e: b"NOPE"  # noqa: ARG005 - too short to be a DrawFile or even a sprite area header

    converter = ExtractConverter(document)
    converter.extract(tmp_path)

    assert (tmp_path / "images" / "0009.bin").exists()
    assert converter.log.has_errors()


def test_extract_only_creates_directories_it_actually_uses(tmp_path):
    entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document_with_dictionary([entry])
    document.story = lambda e: Story(frame_chain=(), paragraphs=())  # noqa: ARG005 - test stub

    converter = ExtractConverter(document)
    converter.extract(tmp_path)

    assert (tmp_path / "text").is_dir()
    assert (tmp_path / "html").is_dir()
    assert not (tmp_path / "images").exists()
    assert not (tmp_path / "svg").exists()
