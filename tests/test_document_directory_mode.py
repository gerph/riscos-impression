import struct

from riscos_impression.io.reader import load_document
from tests.fixtures.builders import (
    build_dict_entry,
    build_header,
    build_object_record,
    build_page_body,
    build_section_body,
)


def _build_directory_mode_docdata() -> bytes:
    """A minimal but complete !DocData: header, an empty style table, a
    4-entry dictionary, an empty master-page stream, and a main-page
    stream holding one chapter (createn=42) with one page.

    The dictionary always carries a leading DCSECT entry for the master
    pages themselves, before any entry belonging to a real chapter (see
    model.dictionary.chapter_index_for) -- so a document with one real
    chapter has *two* DCSECT dictionary entries before that chapter's
    own story/picture entries, not one.
    """
    style_offset = 380
    style_size = 255 * 4
    dict_offset = style_offset + style_size

    dict_entries = (
        build_dict_entry(type=3, id=0)  # DCSECT: master pages themselves
        + build_dict_entry(type=3, id=1)  # DCSECT: the one real chapter
        + build_dict_entry(type=2, id=5)  # DCTEXT
        + build_dict_entry(type=1, id=7)  # DCPICT
    )
    mdict_offset = dict_offset + len(dict_entries)
    mdict_bytes = struct.pack("<5I", 0, 100, 200, 300, 400)  # 4 entries + sentinel; unused in directory mode

    main_offset = mdict_offset + len(mdict_bytes)
    section_record = build_object_record(
        type=0x7, body=build_section_body(create_number=42, master_page_index=0)
    )
    page_record = build_object_record(type=0x1, body=build_page_body())
    terminator = bytes(8)
    main_stream = section_record + page_record + terminator

    total_size = main_offset + len(main_stream)

    header = build_header(
        stylebase=style_offset,
        dict1=dict_offset,
        dict2=dict_offset,
        mdict1=mdict_offset,
        mdict2=mdict_offset,
        masterpages1=main_offset,
        masterpages2=main_offset,
        mainpages1=main_offset,
        mainpages2=main_offset,
        contents1=total_size,
        contents2=total_size,
    )

    return header + bytes(style_size) + dict_entries + mdict_bytes + main_stream


def test_directory_mode_resolves_story_and_picture_bytes(tmp_path):
    doc_dir = tmp_path / "MyDoc"
    doc_dir.mkdir()
    (doc_dir / "!DocData").write_bytes(_build_directory_mode_docdata())

    chapter_dir = doc_dir / "Chapter42"
    chapter_dir.mkdir()

    chunk_content = b"Hello, this is the story text."
    chunk = struct.pack("<II", 8 + len(chunk_content), 5) + chunk_content
    (chapter_dir / "Text").write_bytes(chunk)
    (chapter_dir / "Story7").write_bytes(b"picture bytes go here")

    document = load_document(doc_dir)

    assert document.source.directory_mode is True
    assert len(document.dictionary) == 4
    assert len(document.chapters) == 1
    assert document.chapters[0].section.create_number == 42

    text_entry = document.dictionary[2]
    picture_entry = document.dictionary[3]

    assert document.story_bytes(text_entry) == chunk_content
    assert document.picture_bytes(picture_entry) == b"picture bytes go here"


def test_directory_mode_text_chunk_scanning_skips_non_matching_chunks(tmp_path):
    doc_dir = tmp_path / "MyDoc"
    doc_dir.mkdir()
    (doc_dir / "!DocData").write_bytes(_build_directory_mode_docdata())

    chapter_dir = doc_dir / "Chapter42"
    chapter_dir.mkdir()

    other_content = b"not the one you want"
    wanted_content = b"this is the wanted chunk"
    chunks = (
        struct.pack("<II", 8 + len(other_content), 99) + other_content
        + struct.pack("<II", 8 + len(wanted_content), 5) + wanted_content
    )
    (chapter_dir / "Text").write_bytes(chunks)
    (chapter_dir / "Story7").write_bytes(b"unused")

    document = load_document(doc_dir)
    text_entry = document.dictionary[2]
    assert document.story_bytes(text_entry) == wanted_content
