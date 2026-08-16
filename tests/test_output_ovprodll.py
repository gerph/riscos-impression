from riscos_impression.model.colours import Colour, ColourModel
from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page, PictureFrame
from riscos_impression.model.story import MergeMark, Paragraph, Run, Story
from riscos_impression.output.ovprodll import (
    DDLColourTable,
    OvProDDLConverter,
    _keystring,
    _picture_transform_bits,
    _tr_multiply,
    _tr_setrotationa,
    _tr_setscale,
    render_style,
)

# Reuse the test helpers already established for output/base.py-style tests.
from tests.test_output_base import _document, _frame, _frame_record, _header, _section, _style


def test_default_colours_numbered_one_to_ten():
    table = DDLColourTable()
    rendered = table.render()
    assert 'COL_01={colour "Black"' in rendered
    assert 'COL_0a={colour "Registration"' in rendered


def test_custom_colour_numbered_from_0x20():
    table = DDLColourTable()
    colour = Colour(
        index=0, name="Corporate Blue", model=ColourModel.RGB, values=(0, 0, 0x8000),
        process=True, overprint=False, palette_word=0,
    )
    entry = table.add(colour)
    assert entry.n == 0x20
    second = Colour(
        index=1, name="Corporate Red", model=ColourModel.RGB, values=(0x8000, 0, 0),
        process=True, overprint=False, palette_word=0,
    )
    assert table.add(second).n == 0x21


def test_colour_with_same_name_reuses_default_slot():
    table = DDLColourTable()
    colour = Colour(
        index=0, name="Black", model=ColourModel.CMYK, values=(0, 0, 0, 0x9000),
        process=True, overprint=False, palette_word=0,
    )
    entry = table.add(colour)
    assert entry.n == 1  # reuses Black's slot, not a new one
    assert entry.values == (0, 0, 0, 0x9000)  # but with the document's own data


def test_reference_transparent():
    table = DDLColourTable()
    assert table.reference(None, transparent=True) == "COL_03 0x10000 0"
    assert table.reference(None) == "COL_03 0x10000 0"  # colour=None also falls back


def test_keystring():
    assert _keystring(0) == ""
    assert _keystring(1) == "C_A"
    assert _keystring(0x181) == "F1"
    assert _keystring(0x189) == "F9"
    assert _keystring(0x1CA) == "F10"
    assert _keystring(0x18B) == "Copy"
    assert _keystring(0x1B0) == "CS_Print"
    assert _keystring(0xFFFF) == ""


def _body_style(**overrides):
    return _style(0, is_body_text=True, **overrides)


def _picture(**overrides):
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
        use_ps_screen=False,
        use_recommended_screen=False,
        xscale=0x10000, yscale=0x10000, xshift=0, yshift=0, angle=0,
        lpi=0, psscreen=0, boundary=None,
    )
    fields.update(overrides)
    return PictureFrame(**fields)


def test_tr_setrotationa_identity_at_zero_degrees():
    assert _tr_setrotationa(0) == (0x10000, 0, 0, 0x10000)


def test_tr_setrotationa_ninety_degrees():
    assert _tr_setrotationa(90 * 0x10000) == (0, 0x10000, -0x10000, 0)


def test_tr_setscale_no_change_is_identity():
    assert _tr_setscale(0x10000, 0x10000, 0x10000, 0x10000) == (0x10000, 0x10000)


def test_tr_multiply_by_identity_is_unchanged():
    identity = (0x10000, 0, 0, 0x10000, 0, 0)
    rotation = (0, 0x10000, -0x10000, 0, 0, 0)
    assert _tr_multiply(rotation, identity) == rotation


def test_picture_transform_bits_pure_rotation_no_scale():
    bits = _picture_transform_bits(90 * 0x10000, 0x10000, 0x10000)
    assert bits.angle == 90 * 0x10000
    assert bits.xscale == 0x10000
    assert bits.yscale == 0x10000
    assert bits.skewxy == 0


def test_picture_transform_bits_uniform_scale_no_rotation():
    # A picture stored at 200% (xscale=yscale=0x20000) unscales to 0x8000
    # (50%) in both axes -- matches the pre-transform-library approximation
    # for the no-rotation case.
    bits = _picture_transform_bits(0, 0x20000, 0x20000)
    assert bits.angle == 0
    assert bits.xscale == 0x8000
    assert bits.yscale == 0x8000
    assert bits.skewxy == 0


def test_picture_transform_bits_rotated_nonuniform_scale_produces_skew():
    # Previously this case (rotated + non-uniformly scaled) could only be
    # approximated with a zero skew; the transform-library port computes a
    # genuine non-zero skew for it.
    bits = _picture_transform_bits(45 * 0x10000, 0x20000, 0x10000)
    assert bits.skewxy != 0


def test_render_picture_data_rotated_nonuniform_scale_has_nonzero_skew():
    document, _ = _document_with_one_text_frame()
    converter = OvProDDLConverter(document)
    picture = _picture(angle=45 * 0x10000, xscale=0x20000, yscale=0x10000)
    text = converter._render_picture_data(picture)
    assert "{skew 0x0}" not in text
    assert not converter.log.has_errors()


def test_render_style_body_text_basics():
    style = _body_style(bold=1, italic=0, font_size=160, alignment=3)
    table = DDLColourTable()
    text = render_style(style, [], table)
    assert text.startswith('STYLE_100={style "BodyText"')
    assert "{align 3}" in text
    assert "{bold  1}" in text
    assert "{italic 0}" in text
    assert "{textsize 10000}" in text  # 160 * 1000 // 16
    assert text.rstrip().endswith("}")


def test_render_style_ordinary_style_omits_unset_fields():
    style = _style(1, name="Emphasis", bold=1)
    table = DDLColourTable()
    text = render_style(style, [], table)
    assert 'STYLE_101={style "Emphasis"' in text
    assert "{bold  1}" in text
    assert "{align" not in text
    assert "{reverse" not in text  # body-text-only trailer


def _document_with_one_text_frame(*, filled=False, text="Hello"):
    body = _body_style()
    frame = _frame(filled=filled, dictionary_index=0)
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

    class _FakeSource:
        directory_mode = False

    # Monkeypatch document.story() so the converter doesn't need real
    # ilinestr-framed bytes for this structural test.
    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(Run(text=text, style_slots=()),)),),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub
    return document, frame


def test_convert_produces_well_formed_looking_document(tmp_path):
    document, _ = _document_with_one_text_frame()
    converter = OvProDDLConverter(document)
    out = tmp_path / "out.ddl"
    converter.convert(out)

    text = out.read_text()
    assert text.startswith("//->DDLFile")
    assert "DOC={document}" in text
    assert "CHAP_" in text
    assert "TEXT_" in text
    assert '"Hello"' in text
    assert "{endoftext}" in text
    assert not converter.log.has_errors()


def test_unfilled_frame_uses_transparent_fill(tmp_path):
    document, _ = _document_with_one_text_frame(filled=False)
    converter = OvProDDLConverter(document)
    out = tmp_path / "out.ddl"
    converter.convert(out)
    assert "{fillcolour COL_03 0x10000 0}" in out.read_text()


def test_merge_reference_creates_a_definition(tmp_path):
    document, _ = _document_with_one_text_frame(text="")
    story = Story(
        frame_chain=(),
        paragraphs=(Paragraph(items=(MergeMark(field_name="CustomerName"),)),),
    )
    document.story = lambda entry: story
    converter = OvProDDLConverter(document)
    out = tmp_path / "out.ddl"
    converter.convert(out)
    text = out.read_text()

    assert "{merge 1}" in text
    assert 'MERGE_1={merge 1 "1" "{macv=impulse(\\"CustomerName\\")}"}' in text
