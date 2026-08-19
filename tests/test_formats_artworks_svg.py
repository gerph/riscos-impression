"""Unit tests for formats/artworks_svg.py, built directly against
riscos_artworks' own model dataclasses rather than real (or hand-built
binary) ArtWorks files -- the decoder itself is a separate, already
independently-tested third-party package; these tests only exercise
this project's own rendering logic on top of it."""

import pytest

pytest.importorskip("riscos_artworks", reason="optional 'artworks' extra not installed")

from riscos_artworks import (
    ArtWorks,
    ArtWorksHeader,
    BezierElement,
    BoundingBox,
    CloseElement,
    ColourIndex,
    DecodedString,
    EndElement,
    FillColourRecord,
    GroupRecord,
    JoinStyle,
    LineElement,
    MoveElement,
    PathRecord,
    Point,
    RecordList,
    RelativePointer,
    SourceSpan,
    StrokeColourRecord,
    StrokeWidthRecord,
    WindingRuleRecord,
)

from riscos_artworks import BlendPathRecord, SpriteRecord, TextRecord, CharacterRecord, FontNameRecord, FontSizeRecord
from riscos_artworks import BlendGroupRecord, BlendOptionsRecord

from riscos_impression.formats.artworks_svg import artworks_svg_fragment, artworks_to_svg

FILLED = 0x80000000  # bit 31 set on a path's first element's tag


def _pointer():
    return RelativePointer(offset=0, previous=0, next=0)


def _span():
    return SourceSpan(offset=0, length=0)


def _bbox(x0=0, y0=0, x1=1000, y1=1000):
    return BoundingBox(x0, y0, x1, y1)


def _record(cls, *, control_word=2, bbox=None, child_lists=(), **fields):
    """control_word=2 sets bit 1 (visible); pass 0 to build a hidden object."""
    return cls(
        type_word=0, type_code=0, control_word=control_word,
        bounding_box=bbox if bbox is not None else _bbox(),
        pointer=_pointer(), span=_span(), body_span=_span(),
        child_lists=child_lists, extra_bytes=None, raw_body=None,
        **fields,
    )


def _list(*records):
    return RecordList(pointer=_pointer(), records=tuple(records), span=_span())


def _direct(r, g, b):
    """A direct (non-palette-indexed) ColourIndex -- values below
    0x01000000 are palette *indices*, not colours (see
    riscos_artworks.model.ColourIndex.is_indexed/is_direct)."""
    return ColourIndex(0x01000000 | (b << 16) | (g << 8) | r)


def _square_path(*, filled=True):
    tag_move = 2 | (FILLED if filled else 0)
    return (
        MoveElement(tag_move, Point(0, 0)),
        LineElement(8, Point(1000, 0)),
        LineElement(8, Point(1000, 1000)),
        LineElement(8, Point(0, 1000)),
        CloseElement(5),
        EndElement(0),
    )


def _artwork(record_lists, palette=None):
    header = ArtWorksHeader(
        identifier=DecodedString("Top!", b"Top!", b""), version=9,
        program=DecodedString("", b"", b""), unknown_16=0, body_offset=128,
        european_paper_width=0, european_paper_height=0, unknown_32=0, unknown_36=0,
        undo_buffer_offset=0, sprite_area_offset=0, unknown_48=0,
        american_paper_width=0, american_paper_height=0, palette_offset=0,
        unknown_64=0, unknown_68=0, unknown_72=0, unknown_76=0, unknown_80=0,
        unknown_84=0, unknown_88=0, reserved=b"",
    )
    return ArtWorks(header=header, record_lists=record_lists, palette=palette, work_areas=(), source_length=0)


def test_flat_filled_path_renders_as_svg_path_with_resolved_colour():
    fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 255), gradient_line=None, start_colour=None, end_colour=None,
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(path)))

    svg = artworks_to_svg(artwork)

    assert "<path d=\"M0,0L1000,0L1000,1000L0,1000Z\"" in svg
    assert 'fill="rgb(0,0,255)"' in svg


def test_path_without_the_filled_flag_ignores_the_propagated_fill():
    fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 255), gradient_line=None, start_colour=None, end_colour=None,
    )
    path = _record(PathRecord, path=_square_path(filled=False))
    artwork = _artwork((_list(fill), _list(path)))

    svg = artworks_to_svg(artwork)

    assert "<path" in svg
    assert 'fill="none"' in svg
    assert 'fill="rgb(0,0,255)"' not in svg


def test_hidden_object_is_not_drawn():
    path = _record(PathRecord, control_word=0, path=_square_path(filled=True))
    artwork = _artwork((_list(path),))

    svg = artworks_to_svg(artwork)

    assert "<path" not in svg


def test_second_fill_before_a_path_overrides_the_first():
    fill_a = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(255, 0, 0), gradient_line=None, start_colour=None, end_colour=None,
    )
    fill_b = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 255, 0), gradient_line=None, start_colour=None, end_colour=None,
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill_a), _list(fill_b), _list(path)))

    svg = artworks_to_svg(artwork)

    assert "<path" in svg
    assert 'fill="rgb(0,255,0)"' in svg
    assert 'fill="rgb(255,0,0)"' not in svg


def test_a_fill_inside_a_group_does_not_leak_out_to_a_later_sibling():
    # Style changes inside a group's own child_lists are scoped to that
    # group; a path drawn *after* the group, back in the outer list,
    # must not see the group-local fill.
    inner_fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(255, 0, 0), gradient_line=None, start_colour=None, end_colour=None,
    )
    inner_path = _record(PathRecord, path=_square_path(filled=True))
    group = _record(GroupRecord, unknown_values=(0, 0, 0),
                    child_lists=(_list(inner_fill), _list(inner_path)))
    outer_path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(group), _list(outer_path)))

    svg = artworks_to_svg(artwork)

    paths = svg.split("<path")[1:]
    assert len(paths) == 2
    assert 'fill="rgb(255,0,0)"' in paths[0]
    assert 'fill="none"' in paths[1]  # the outer path sees no fill at all (default)


def test_stroke_colour_and_width_are_applied():
    stroke = _record(StrokeColourRecord, colour=_direct(0, 255, 0))
    width = _record(StrokeWidthRecord, width=320)
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(stroke), _list(width), _list(path)))

    svg = artworks_to_svg(artwork)

    assert "<path" in svg
    assert 'stroke="rgb(0,255,0)"' in svg
    assert 'stroke-width="320"' in svg


def test_winding_rule_non_zero_matches_svgs_own_default_and_is_omitted():
    # SVG's own native default is nonzero, not ArtWorks' own default of
    # even-odd -- so the *default* ArtWorks render state (even-odd)
    # still needs an explicit fill-rule, and only an explicit non-zero
    # winding-rule record omits it, matching riscos-artworks-js's own
    # mapAttributeWithDefault (compared against WINDING_RULE_NON_ZERO,
    # not against ArtWorks' own default).
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(path),))
    svg_default = artworks_to_svg(artwork)
    assert 'fill-rule="evenodd"' in svg_default

    non_zero = _record(WindingRuleRecord, winding_rule=0)
    path2 = _record(PathRecord, path=_square_path(filled=True))
    artwork2 = _artwork((_list(non_zero), _list(path2)))
    svg_nonzero = artworks_to_svg(artwork2)
    assert "<path" in svg_nonzero
    assert "fill-rule" not in svg_nonzero


def test_linear_gradient_fill_adds_a_definition_and_references_it():
    fill = _record(
        FillColourRecord, fill_type=1, unknown_28=0, colour=None,
        gradient_line=(Point(0, 0), Point(1000, 0)),
        start_colour=_direct(255, 0, 0), end_colour=_direct(0, 0, 255),
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(path)))

    svg = artworks_to_svg(artwork)

    assert "<path" in svg
    assert "<linearGradient" in svg
    assert 'stop-color="rgb(255,0,0)"' in svg
    assert 'stop-color="rgb(0,0,255)"' in svg
    assert "fill=\"url(#linear-gradient-1)\"" in svg


def test_bezier_element_is_emitted_as_svg_cubic_curve():
    path_elements = (
        MoveElement(2 | FILLED, Point(0, 0)),
        BezierElement(6, Point(100, 0), Point(100, 100), Point(0, 100)),
        EndElement(0),
    )
    path = _record(PathRecord, path=path_elements)
    artwork = _artwork((_list(path),))

    svg = artworks_to_svg(artwork)

    assert "C100,0 100,100 0,100" in svg


def test_bounding_box_drives_the_viewbox_and_unit_scaled_size():
    path = _record(PathRecord, bbox=_bbox(0, 0, 640, 640), path=_square_path(filled=True))
    artwork = _artwork((_list(path),))

    svg = artworks_to_svg(artwork)

    assert 'viewBox="0 -640 640 640"' in svg
    # 640 artworks units * (1/640) = 1pt.
    assert 'width="1pt"' in svg


def test_artworks_svg_fragment_matches_artworks_to_svgs_own_viewbox_and_content():
    fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 255), gradient_line=None, start_colour=None, end_colour=None,
    )
    path = _record(PathRecord, bbox=_bbox(0, 0, 640, 640), path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(path)))

    full = artworks_to_svg(artwork)
    viewbox, width_pt, height_pt, inner = artworks_svg_fragment(artwork)

    assert viewbox == "0 -640 640 640"
    assert width_pt == "1"
    assert height_pt == "1"
    assert "<defs>" in inner
    assert '<g transform="scale(1,-1)">' in inner
    assert 'fill="rgb(0,0,255)"' in inner
    # the fragment's own inner markup is exactly what artworks_to_svg()
    # wraps in its own outer <svg ...> tag -- same content, not a
    # separately-derived rendering.
    assert inner in full
    assert f'viewBox="{viewbox}"' in full


def test_a_fill_set_in_one_top_level_list_is_seen_by_a_later_sibling_list():
    # Regression test: a real ArtWorks file typically opens with a run
    # of single-record top-level lists -- one default attribute per
    # list (winding rule, dash, caps, join, fill, stroke, ...) -- ahead
    # of the list holding the actual content. An earlier version of
    # process_lists took a fresh copy of the style dict for *every*
    # list it processed, rather than sharing and mutating one dict
    # across every list a single call covers (matching
    # riscos-artworks-js's own processLists(), which shares one
    # class-level RenderState stack across every list it walks) --
    # so each of those single-record lists discarded the one before
    # it, and the content list that followed saw none of them: every
    # object in every real ArtWorks file tried (corpus/TestDoc,bc5's
    # own embedded pictures) rendered with no fill at all. Two
    # separate top-level record_lists reproduce the same shape at unit
    # scale: a fill in the first list, a path in the second.
    fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 255), gradient_line=None, start_colour=None, end_colour=None,
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(path)))

    svg = artworks_to_svg(artwork)

    assert 'fill="rgb(0,0,255)"' in svg


def test_a_trailing_local_fill_override_is_applied_to_the_object_before_it():
    # Regression test for the actual bug behind the "final black
    # rectangle" investigation: a shape immediately followed, as a
    # flat sibling in the *same* list, by its own local attribute
    # override -- exactly how AWDocs/TestDocs/TestDocs/BlueRect,d94
    # (a single rectangle plus one local FillColourRecord after it)
    # stores a shape's own local override. Before artworks_to_svg
    # called riscos_artworks.denormalise() first, this flat trailing
    # sibling was read as an inert record affecting nothing (nothing
    # follows it), and BlueRect rendered solid black -- the ambient
    # default -- instead of blue.
    ambient_default = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 0), gradient_line=None, start_colour=None, end_colour=None,
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    local_fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 255), gradient_line=None, start_colour=None, end_colour=None,
    )
    # One flat list -- [path, local_fill] -- exactly as BlueRect,d94
    # decodes: the object, then its own trailing local override,
    # never a fresh sibling list of its own.
    artwork = _artwork((_list(ambient_default), _list(path, local_fill)))

    svg = artworks_to_svg(artwork)

    assert "<path" in svg
    assert 'fill="rgb(0,0,255)"' in svg
    assert 'fill="rgb(0,0,0)"' not in svg


def test_a_trailing_local_override_does_not_leak_to_a_later_sibling_list():
    # The other half of the same regression: a local override nested
    # under its own object via denormalise() must stay scoped there,
    # not leak forward to a later, unrelated object in a separate list
    # -- otherwise "denormalise fixed BlueRect" could just as easily
    # have introduced the opposite bug (over-eager propagation).
    first_path = _record(PathRecord, path=_square_path(filled=True))
    local_fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 255), gradient_line=None, start_colour=None, end_colour=None,
    )
    second_path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(first_path, local_fill), _list(second_path)))

    svg = artworks_to_svg(artwork)

    paths = svg.split("<path")[1:]
    assert len(paths) == 2
    assert 'fill="rgb(0,0,255)"' in paths[0]
    assert 'fill="none"' in paths[1]  # no ambient default was ever set


def test_sprite_record_draws_nothing():
    # Sprites are deliberately out of scope: a separate project is
    # expected to provide sprite handling, so a SpriteRecord just
    # recurses into its own (typically empty) child_lists like any
    # other not-yet-handled record type, drawing nothing of its own.
    sprite = _record(
        SpriteRecord, bbox=_bbox(0, 0, 1000, 1000),
        unknown_24=0, name=DecodedString("photo", b"photo", b""), unknown_values=(), palette=(),
    )
    artwork = _artwork((_list(sprite),))

    svg = artworks_to_svg(artwork)

    assert "<rect" not in svg
    assert "<path" not in svg


def test_visible_blend_path_keyframe_is_drawn_like_a_plain_path():
    fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(255, 128, 0), gradient_line=None, start_colour=None, end_colour=None,
    )
    blend_path = _record(BlendPathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill), _list(blend_path)))

    svg = artworks_to_svg(artwork)

    assert "<path" in svg
    assert 'fill="rgb(255,128,0)"' in svg


def _text(*, unknown_values=(0, 0, 0, 1, 1, 0), rectangle=None, child_lists=()):
    return _record(
        TextRecord, unknown_values=unknown_values,
        rectangle=rectangle if rectangle is not None else (Point(0, 0),) * 4,
        child_lists=child_lists,
    )


def _character(code, x, y, *, x_offset=0, y_offset=0):
    return _record(CharacterRecord, character_code=code, unknown_values=(x, y, x_offset, y_offset))


def test_text_renders_one_svg_text_glyph_per_character_at_its_own_position():
    # Reverse-engineered against a real file (see the module docstring):
    # CharacterRecord.character_code's low byte is the actual character,
    # everything above it something else entirely -- 0x100 | ord('A')
    # is exactly the pattern seen there.
    font = _record(FontNameRecord, font_name=DecodedString("Trinity", b"Trinity\0", b""))
    size = _record(FontSizeRecord, x_size=320, y_size=320)
    char_a = _character(0x100 | ord("A"), 1000, 2000)
    char_b = _character(0x100 | ord("B"), 1500, 2000, x_offset=500)
    text = _text(
        unknown_values=(0, 1000, 2000, 2, 2, 0),
        child_lists=(_list(font), _list(size), _list(char_a), _list(char_b)),
    )
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert svg.count("<text") == 2
    assert ">A<" in svg
    assert ">B<" in svg
    assert 'translate(1000,2000)' in svg
    assert 'translate(1500,2000)' in svg
    # y_size(320) * FONT_SIZE_TO_NATIVE_UNITS(40) -- see that constant's
    # own docstring for the empirical derivation.
    assert 'font-size="12800"' in svg


def test_text_uses_the_current_fill_colour():
    fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 200, 0), gradient_line=None, start_colour=None, end_colour=None,
    )
    char_a = _character(ord("Z"), 0, 0)
    text = _text(child_lists=(_list(fill), _list(char_a)))
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert 'fill="rgb(0,200,0)"' in svg


def test_character_font_size_is_converted_via_font_size_to_native_units():
    # Regression test: FontSizeRecord.y_size was previously used
    # directly as if it were already in native ArtWorks coordinate
    # units -- see FONT_SIZE_TO_NATIVE_UNITS's own docstring for the
    # empirical derivation (RISC OS's own "1/16th of a point"
    # convention) and why the bug went unnoticed for a while (it only
    # became glaringly visible in a picture with a small frame).
    from riscos_impression.formats.artworks_svg import FONT_SIZE_TO_NATIVE_UNITS

    size = _record(FontSizeRecord, x_size=512, y_size=512)
    char_a = _character(ord("A"), 0, 0)
    text = _text(child_lists=(_list(size), _list(char_a)))
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert FONT_SIZE_TO_NATIVE_UNITS == 40.0
    assert 'font-size="20480"' in svg  # 512 * 40


def test_text_object_angle_rotates_every_one_of_its_own_characters():
    char_a = _character(ord("Q"), 0, 0)
    # unknown_values[5] / 65536.0 == degrees -- confirmed against a
    # real rotated text object (see the module docstring): 983270
    # there matched a visibly ~15-degree-slanted selection rectangle.
    # 90 * 65536 here for a clean, easy-to-check angle.
    text = _text(unknown_values=(0, 0, 0, 1, 1, 90 * 65536), child_lists=(_list(char_a),))
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert "rotate(-90)" in svg


def test_text_control_characters_are_skipped_but_printable_ones_still_render():
    control = _character(0x0A, 0, 0)  # newline -- not drawable
    letter = _character(ord("X"), 100, 0)
    text = _text(child_lists=(_list(control), _list(letter)))
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert svg.count("<text") == 1
    assert ">X<" in svg


def test_text_object_not_visible_draws_no_characters():
    char_a = _character(ord("N"), 0, 0)
    text = _record(
        TextRecord, control_word=0, unknown_values=(0, 0, 0, 1, 1, 0),
        rectangle=(Point(0, 0),) * 4, child_lists=(_list(char_a),),
    )
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert "<text" not in svg


def _square(x0, y0, size, *, filled=True):
    tag_move = 2 | (FILLED if filled else 0)
    return (
        MoveElement(tag_move, Point(x0, y0)),
        LineElement(8, Point(x0 + size, y0)),
        LineElement(8, Point(x0 + size, y0 + size)),
        LineElement(8, Point(x0, y0 + size)),
        CloseElement(5),
        EndElement(0),
    )


def test_blend_group_interpolates_geometry_and_stroke_colour_between_keyframes():
    start_stroke = _record(StrokeColourRecord, colour=_direct(255, 0, 0))
    start_path = _record(PathRecord, path=_square(0, 0, 1000), child_lists=(_list(start_stroke),))
    options = _record(BlendOptionsRecord, unknown_24=0, blend_steps=4, values=(0,) * 8)
    end_stroke = _record(StrokeColourRecord, colour=_direct(0, 0, 255))
    end_path = _record(PathRecord, path=_square(2000, 2000, 200), child_lists=(_list(end_stroke),))
    group = _record(
        BlendGroupRecord, values=(0,) * 11,
        child_lists=(_list(start_path), _list(options), _list(end_path)),
    )
    artwork = _artwork((_list(group),))

    svg = artworks_to_svg(artwork)

    assert svg.count("<path") == 5  # blend_steps + 1
    assert 'd="M0,0L1000,0L1000,1000L0,1000Z"' in svg  # t=0: exactly the start keyframe
    assert 'd="M2000,2000L2200,2000L2200,2200L2000,2200Z"' in svg  # t=1: exactly the end keyframe
    assert 'stroke="rgb(255,0,0)"' in svg  # t=0 stroke colour
    assert 'stroke="rgb(0,0,255)"' in svg  # t=1 stroke colour
    assert 'stroke="rgb(128,0,128)"' in svg  # t=0.5 midpoint stroke colour


def test_blend_group_with_mismatched_point_counts_draws_both_keyframes_as_is():
    start_path = _record(PathRecord, path=_square(0, 0, 1000))
    options = _record(BlendOptionsRecord, unknown_24=0, blend_steps=4, values=(0,) * 8)
    # A triangle (5 elements) vs a square (6 elements) -- can't be
    # linearly paired up element-by-element, so this code doesn't
    # attempt AWViewer's own point-insertion algorithm for unequal
    # path shapes (see process_blend_group's own docstring).
    triangle_path = (
        MoveElement(2, Point(2000, 2000)),
        LineElement(8, Point(2200, 2000)),
        LineElement(8, Point(2100, 2200)),
        CloseElement(5),
        EndElement(0),
    )
    end_path = _record(PathRecord, path=triangle_path)
    group = _record(
        BlendGroupRecord, values=(0,) * 11,
        child_lists=(_list(start_path), _list(options), _list(end_path)),
    )
    artwork = _artwork((_list(group),))

    svg = artworks_to_svg(artwork)

    assert svg.count("<path") == 2
    assert 'd="M0,0L1000,0L1000,1000L0,1000Z"' in svg
    assert 'd="M2000,2000L2200,2000L2100,2200Z"' in svg


def test_hidden_blend_group_draws_nothing():
    start_path = _record(PathRecord, path=_square(0, 0, 1000))
    options = _record(BlendOptionsRecord, unknown_24=0, blend_steps=4, values=(0,) * 8)
    end_path = _record(PathRecord, path=_square(2000, 2000, 200))
    group = _record(
        BlendGroupRecord, control_word=0, values=(0,) * 11,
        child_lists=(_list(start_path), _list(options), _list(end_path)),
    )
    artwork = _artwork((_list(group),))

    svg = artworks_to_svg(artwork)

    assert "<path" not in svg


def test_pathified_character_renders_its_own_glyph_outline_not_a_text_element():
    # ArtWorks "pathifies" individual characters (converts them to a
    # real vector-traced outline, stored as the CharacterRecord's own
    # child object) when it can't rely on standard text rendering --
    # see the module docstring (AWDocs/MethodsManual.md's own
    # PathifyText_* description) and _emit_character's own docstring.
    # Confirmed against a real picture (corpus/TestDoc,bc5's own "Shit
    # Creek"): every visible character there has exactly this shape.
    glyph_path = _record(PathRecord, path=_square_path(filled=True))
    char_a = _record(
        CharacterRecord, character_code=ord("A"), unknown_values=(1000, 2000, 0, 0),
        child_lists=(_list(glyph_path),),
    )
    text = _text(child_lists=(_list(char_a),))
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert "<path" in svg
    assert "<text" not in svg
    assert ">A<" not in svg


def test_character_without_a_pathified_glyph_falls_back_to_a_text_element():
    char_a = _character(ord("A"), 1000, 2000)
    text = _text(child_lists=(_list(char_a),))
    artwork = _artwork((_list(text),))

    svg = artworks_to_svg(artwork)

    assert "<text" in svg
    assert ">A<" in svg
