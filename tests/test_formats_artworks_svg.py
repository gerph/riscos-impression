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

from riscos_artworks import BlendPathRecord, SpriteRecord

from riscos_impression.formats.artworks_svg import artworks_to_svg

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
    artwork = _artwork((_list(fill, path),))

    svg = artworks_to_svg(artwork)

    assert "<path d=\"M0,0L1000,0L1000,1000L0,1000Z\"" in svg
    assert 'fill="rgb(0,0,255)"' in svg


def test_path_without_the_filled_flag_ignores_the_propagated_fill():
    fill = _record(
        FillColourRecord, fill_type=0, unknown_28=0,
        colour=_direct(0, 0, 255), gradient_line=None, start_colour=None, end_colour=None,
    )
    path = _record(PathRecord, path=_square_path(filled=False))
    artwork = _artwork((_list(fill, path),))

    svg = artworks_to_svg(artwork)

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
    artwork = _artwork((_list(fill_a, fill_b, path),))

    svg = artworks_to_svg(artwork)

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
    group = _record(GroupRecord, unknown_values=(0, 0, 0), child_lists=(_list(inner_fill, inner_path),))
    outer_path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(group, outer_path),))

    svg = artworks_to_svg(artwork)

    paths = svg.split("<path")[1:]
    assert len(paths) == 2
    assert 'fill="rgb(255,0,0)"' in paths[0]
    assert 'fill="none"' in paths[1]  # the outer path sees no fill at all (default)


def test_stroke_colour_and_width_are_applied():
    stroke = _record(StrokeColourRecord, colour=_direct(0, 255, 0))
    width = _record(StrokeWidthRecord, width=320)
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(stroke, width, path),))

    svg = artworks_to_svg(artwork)

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
    artwork2 = _artwork((_list(non_zero, path2),))
    svg_nonzero = artworks_to_svg(artwork2)
    assert "fill-rule" not in svg_nonzero


def test_linear_gradient_fill_adds_a_definition_and_references_it():
    fill = _record(
        FillColourRecord, fill_type=1, unknown_28=0, colour=None,
        gradient_line=(Point(0, 0), Point(1000, 0)),
        start_colour=_direct(255, 0, 0), end_colour=_direct(0, 0, 255),
    )
    path = _record(PathRecord, path=_square_path(filled=True))
    artwork = _artwork((_list(fill, path),))

    svg = artworks_to_svg(artwork)

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
    # 640 artworks units * (1/640) * (4/3) = 4/3 pt.
    assert 'width="1.3333pt"' in svg


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
    artwork = _artwork((_list(fill, blend_path),))

    svg = artworks_to_svg(artwork)

    assert 'fill="rgb(255,128,0)"' in svg
