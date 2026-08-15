from riscos_impression.model.frames import (
    Branch,
    GroupFrame,
    ObjectType,
    Page,
    PathOpCode,
    PictureFrame,
    Section,
    TextFrame,
    parse_object_stream,
    resolve_object_type,
)
from tests.fixtures.builders import (
    build_boundary_subrecord,
    build_frame_common_body,
    build_object_record,
    build_page_body,
    build_picture_extension,
    build_section_body,
    build_tagged_subrecord,
)


def test_resolve_object_type():
    assert resolve_object_type(0x2) is ObjectType.TEXT
    assert resolve_object_type(0xFF) is ObjectType.BLANK  # legacy XXXXX byte
    assert resolve_object_type(0x5) is None  # undefined type code


def test_empty_stream():
    # A zero-length record terminates the stream immediately.
    terminator = bytes(8)
    assert parse_object_stream(terminator, 0, len(terminator)) == []


def test_stream_of_two_records_and_terminator():
    page = build_object_record(type=0x1, body=build_page_body(x1=1000, y1=2000))
    text = build_object_record(
        type=0x2, body=build_frame_common_body(x0=10, y0=20, x1=110, y1=220)
    )
    terminator = bytes(8)
    data = page + text + terminator

    records = parse_object_stream(data, 0, len(data))

    assert len(records) == 2
    assert records[0].type is ObjectType.PAGE
    assert isinstance(records[0].value, Page)
    assert records[0].value.x1 == 1000
    assert records[1].type is ObjectType.TEXT
    assert isinstance(records[1].value, TextFrame)
    assert records[1].value.x0 == 10
    assert records[1].offset == len(page)


def test_stream_stops_at_end_boundary():
    text = build_object_record(type=0x2, body=build_frame_common_body())
    data = text + text  # two identical records back to back, no terminator
    records = parse_object_stream(data, 0, len(text))
    assert len(records) == 1


def test_unrecognised_type_code_yields_none_value_and_continues():
    unknown = build_object_record(type=0x5, body=b"\x00" * 4)
    page = build_object_record(type=0x1, body=build_page_body())
    terminator = bytes(8)
    data = unknown + page + terminator

    records = parse_object_stream(data, 0, len(data))

    assert records[0].type is None
    assert records[0].value is None
    assert records[1].type is ObjectType.PAGE


def test_frame_common_fields_round_trip():
    body = build_frame_common_body(
        x0=1,
        y0=2,
        x1=3,
        y1=4,
        selected=True,
        repel=True,
        filled=True,
        master=True,
        locked=True,
        grouped=True,
        repeating=True,
        level=7,
        dictionary_index=42,
        exx0=5,
        exy0=6,
        exx1=7,
        exy1=8,
        master_index=9,
        fill_colour_word=0x11223300,
        hinset=10,
        vinset=11,
        border0=1,
        border1=2,
        border2=3,
        border3=4,
        border_colour_word=0x44556600,
        embed_tag=99,
        group_number=3,
        overprint=True,
    )
    record = build_object_record(type=0x2, body=body)
    (rec,) = parse_object_stream(record, 0, len(record))
    frame = rec.value

    assert (frame.x0, frame.y0, frame.x1, frame.y1) == (1, 2, 3, 4)
    assert frame.selected and frame.repel and frame.filled
    assert frame.master and frame.locked and frame.grouped and frame.repeating
    assert frame.level == 7
    assert frame.dictionary_index == 42
    assert frame.has_story is True
    assert (frame.exx0, frame.exy0, frame.exx1, frame.exy1) == (5, 6, 7, 8)
    assert frame.master_index == 9
    assert frame.fill_colour_word == 0x11223300
    assert (frame.hinset, frame.vinset) == (10, 11)
    assert (frame.border0, frame.border1, frame.border2, frame.border3) == (1, 2, 3, 4)
    assert frame.has_border is True
    assert frame.border_colour_word == 0x44556600
    assert frame.embed_tag == 99
    assert frame.group_number == 3
    assert frame.overprint is True


def test_no_border_and_no_story():
    body = build_frame_common_body(dictionary_index=-1)
    record = build_object_record(type=0x4, body=body)  # XBLANK
    (rec,) = parse_object_stream(record, 0, len(record))
    assert rec.value.has_border is False
    assert rec.value.has_story is False


def test_fill_and_border_colour_helpers():
    from riscos_impression.model.colours import ColourModel

    filled_body = build_frame_common_body(filled=True, fill_colour_word=0x00000000)
    unfilled_body = build_frame_common_body(filled=False, fill_colour_word=0x00000000)
    bordered_body = build_frame_common_body(border_colour_word=0x00000000)
    unbordered_body = build_frame_common_body()  # default NO_COLOUR sentinel

    for body, expect_colour in ((filled_body, True), (unfilled_body, False)):
        record = build_object_record(type=0x2, body=body)
        (rec,) = parse_object_stream(record, 0, len(record))
        colour = rec.value.fill_colour([])
        assert (colour is not None) is expect_colour
        if colour is not None:
            assert colour.model is ColourModel.RGB

    for body, expect_colour in ((bordered_body, True), (unbordered_body, False)):
        record = build_object_record(type=0x2, body=body)
        (rec,) = parse_object_stream(record, 0, len(record))
        colour = rec.value.border_colour([])
        assert (colour is not None) is expect_colour


def test_group_frame_type():
    body = build_frame_common_body(group_number=5, grouped=True)
    record = build_object_record(type=0xB, body=body)
    (rec,) = parse_object_stream(record, 0, len(record))
    assert isinstance(rec.value, GroupFrame)
    assert rec.value.group_number == 5


def test_picture_frame_extension_fields():
    body = build_frame_common_body(flags_bit16=True, group_flags_bit15=True) + build_picture_extension(
        xscale=0x20000, yscale=0x8000, xshift=100, yshift=-50, angle=90 * 0x10000, lpi=133, psscreen=45
    )
    record = build_object_record(type=0x3, body=body)
    (rec,) = parse_object_stream(record, 0, len(record))
    pict = rec.value

    assert isinstance(pict, PictureFrame)
    assert pict.use_ps_screen is True
    assert pict.use_recommended_screen is True
    assert pict.xscale == 0x20000
    assert pict.yscale == 0x8000
    assert pict.xshift == 100
    assert pict.yshift == -50
    assert pict.angle == 90 * 0x10000
    assert pict.lpi == 133
    assert pict.psscreen == 45
    assert pict.boundary is None


def test_picture_frame_irregular_boundary():
    boundary = build_boundary_subrecord(
        [
            (PathOpCode.MOVE.value, 10, 20),
            (PathOpCode.DRAW.value, 30, 40),
            (PathOpCode.CLOSE.value, None, None),
            (PathOpCode.END.value, None, None),
        ]
    )
    body = (
        build_frame_common_body()
        + build_picture_extension()
        + build_tagged_subrecord(tag=7, payload_length=12)  # should be skipped
        + boundary
    )
    record = build_object_record(type=0x3, body=body)
    (rec,) = parse_object_stream(record, 0, len(record))
    pict = rec.value

    assert pict.boundary is not None
    codes = [op.code for op in pict.boundary]
    assert codes == [
        PathOpCode.MOVE,
        PathOpCode.DRAW,
        PathOpCode.CLOSE,
        PathOpCode.END,
    ]
    assert (pict.boundary[0].x, pict.boundary[0].y) == (10, 20)
    assert (pict.boundary[1].x, pict.boundary[1].y) == (30, 40)
    assert pict.boundary[2].x is None


def test_page_decode():
    body = build_page_body(x0=0, y0=0, x1=1000, y1=1500, bleed=10, master_page_name="Main")
    record = build_object_record(type=0x1, body=body)
    (rec,) = parse_object_stream(record, 0, len(record))
    page = rec.value

    assert isinstance(page, Page)
    assert page.master_page_name == "Main"
    assert page.print_width == 980
    assert page.print_height == 1480


def test_section_decode():
    body = build_section_body(
        create_number=3,
        master_page_index=1,
        start_page_number=5,
        override_start_page=True,
        start_on_right=True,
        copy_previous=False,
        start_chapter_number=2,
        override_start_chapter=True,
    )
    record = build_object_record(type=0x7, body=body)
    (rec,) = parse_object_stream(record, 0, len(record))
    section = rec.value

    assert isinstance(section, Section)
    assert section.create_number == 3
    assert section.master_page_index == 1
    assert section.start_page_number == 5
    assert section.override_start_page is True
    assert section.start_on_right is True
    assert section.copy_previous is False
    assert section.start_chapter_number == 2
    assert section.override_start_chapter is True


def test_branch_decode():
    record = build_object_record(type=0x6, body=b"\x00" * 40)
    (rec,) = parse_object_stream(record, 0, len(record))
    assert isinstance(rec.value, Branch)
