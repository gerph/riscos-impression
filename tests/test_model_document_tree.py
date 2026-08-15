from riscos_impression.model.document_tree import (
    assign_master_pages,
    build_chapters,
    find_master_frame,
    find_master_page_pair,
    group_pages,
    split_into_chapters,
)
from riscos_impression.model.frames import ObjectType, TextFrame, parse_object_stream
from tests.fixtures.builders import (
    build_frame_common_body,
    build_object_record,
    build_page_body,
    build_section_body,
)


def _records(*object_records):
    data = b"".join(object_records) + bytes(8)
    return parse_object_stream(data, 0, len(data))


def test_group_pages_basic():
    stream = _records(
        build_object_record(type=0x1, body=build_page_body(x1=100)),
        build_object_record(type=0x2, body=build_frame_common_body(x0=1)),
        build_object_record(type=0x2, body=build_frame_common_body(x0=2)),
        build_object_record(type=0x1, body=build_page_body(x1=200)),
        build_object_record(type=0x2, body=build_frame_common_body(x0=3)),
    )
    groups = group_pages(stream)

    assert len(groups) == 2
    assert groups[0].page.x1 == 100
    assert len(groups[0].records) == 2
    assert groups[1].page.x1 == 200
    assert len(groups[1].records) == 1


def test_group_pages_ignores_records_before_the_first_page():
    stream = _records(
        build_object_record(type=0x2, body=build_frame_common_body()),
        build_object_record(type=0x1, body=build_page_body()),
    )
    groups = group_pages(stream)
    assert len(groups) == 1
    assert groups[0].records == ()


def test_split_into_chapters():
    stream = _records(
        build_object_record(type=0x7, body=build_section_body(create_number=1)),
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x7, body=build_section_body(create_number=2)),
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x1, body=build_page_body()),
    )
    chapters = split_into_chapters(stream)

    assert len(chapters) == 2
    section1, offset1, records1 = chapters[0]
    section2, offset2, records2 = chapters[1]
    assert section1.create_number == 1
    assert len(records1) == 1
    assert section2.create_number == 2
    assert len(records2) == 2


def test_branch_ends_a_chapter_without_starting_one():
    stream = _records(
        build_object_record(type=0x7, body=build_section_body(create_number=1)),
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x6, body=b"\x00" * 40),
        build_object_record(type=0x1, body=build_page_body()),  # orphaned: belongs to no chapter
    )
    chapters = split_into_chapters(stream)
    assert len(chapters) == 1
    assert len(chapters[0][2]) == 1


def test_find_master_page_pair_matching_y1():
    stream = _records(
        build_object_record(type=0x1, body=build_page_body(y1=1000)),
        build_object_record(type=0x1, body=build_page_body(y1=1000)),
        build_object_record(type=0x1, body=build_page_body(y1=2000)),
    )
    groups = group_pages(stream)

    primary, secondary = find_master_page_pair(groups, 0)
    assert primary is groups[0]
    assert secondary is groups[1]

    primary2, secondary2 = find_master_page_pair(groups, 2)
    assert primary2 is groups[2]
    assert secondary2 is None


def test_find_master_page_pair_out_of_range():
    assert find_master_page_pair([], 0) == (None, None)


def test_assign_master_pages_alternates():
    stream = _records(
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x1, body=build_page_body()),
    )
    pages = group_pages(stream)
    mp1, mp2 = group_pages(
        _records(
            build_object_record(type=0x1, body=build_page_body(y1=1)),
            build_object_record(type=0x1, body=build_page_body(y1=1)),
        )
    )[0:2]

    assigned = assign_master_pages(pages, mp1, mp2, start_on_right=False)
    assert [p.master_page for p in assigned] == [mp1, mp2, mp1]

    assigned_right = assign_master_pages(pages, mp1, mp2, start_on_right=True)
    assert [p.master_page for p in assigned_right] == [mp2, mp1, mp2]


def test_assign_master_pages_single_master_page():
    stream = _records(
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x1, body=build_page_body()),
    )
    pages = group_pages(stream)
    (mp1,) = group_pages(_records(build_object_record(type=0x1, body=build_page_body())))

    assigned = assign_master_pages(pages, mp1, None, start_on_right=True)
    assert [p.master_page for p in assigned] == [mp1, mp1]


def test_build_chapters_end_to_end():
    master_stream = _records(
        build_object_record(type=0x1, body=build_page_body(y1=500)),
        build_object_record(
            type=0x2, body=build_frame_common_body(x0=99)
        ),  # master frame, master_index=0 by default
    )
    master_pages = group_pages(master_stream)

    main_stream = _records(
        build_object_record(type=0x7, body=build_section_body(create_number=7, master_page_index=0)),
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(
            type=0x2,
            body=build_frame_common_body(master=True, master_index=0, x0=1),
        ),
    )

    chapters = build_chapters(main_stream, master_pages)

    assert len(chapters) == 1
    chapter = chapters[0]
    assert chapter.section.create_number == 7
    assert chapter.master_page_1 is master_pages[0]
    assert len(chapter.pages) == 1
    page = chapter.pages[0]
    assert page.master_page is master_pages[0]
    assert len(page.frames) == 1
    assert isinstance(page.frames[0], TextFrame)


def test_find_master_frame_matches_by_master_index_value():
    master_stream = _records(
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x2, body=build_frame_common_body(master_index=5, x0=111)),
        build_object_record(type=0x2, body=build_frame_common_body(master_index=9, x0=222)),
    )
    master_pages = group_pages(master_stream)

    main_stream = _records(
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(
            type=0x2, body=build_frame_common_body(master=True, master_index=9)
        ),
    )
    main_pages = group_pages(main_stream)
    main_pages = assign_master_pages(main_pages, master_pages[0], None, start_on_right=False)

    frame = main_pages[0].frames[0]
    record = find_master_frame(main_pages[0], frame)

    assert record is not None
    assert record.value.x0 == 222


def test_find_master_frame_none_when_not_master_linked():
    main_stream = _records(
        build_object_record(type=0x1, body=build_page_body()),
        build_object_record(type=0x2, body=build_frame_common_body(master=False)),
    )
    pages = group_pages(main_stream)
    frame = pages[0].frames[0]
    assert find_master_frame(pages[0], frame) is None
