from riscos_impression.model.story import (
    ChapterNumberMark,
    EmbedMark,
    HeadingNumberMark,
    MergeMark,
    PageBreakMark,
    PageNumberMark,
    Run,
    TabMark,
    parse_story,
)
from tests.fixtures.builders import (
    CTRL_CLOSESQ,
    CTRL_E,
    CTRL_G,
    CTRL_H,
    CTRL_K,
    CTRL_M,
    CTRL_N,
    CTRL_R,
    CTRL_U,
    ContentBuilder,
    build_frame_reference_line,
    build_story_bytes,
    build_text_content_line,
)


def _story_from_content(content: bytes):
    line = build_text_content_line(content)
    return parse_story(build_story_bytes([line]))


def test_empty_story():
    story = parse_story(build_story_bytes([]))
    assert story.frame_chain == ()
    assert len(story.paragraphs) == 1
    assert story.paragraphs[0].items == ()


def test_plain_literal_text():
    content = ContentBuilder().literal('Hello "World"').bytes()
    story = _story_from_content(content)
    (paragraph,) = story.paragraphs
    (run,) = paragraph.items
    assert run == Run(text='Hello "World"', style_slots=())


def test_c1_range_bytes_decode_as_riscos_latin1_curly_quotes():
    # Regression test: bytes 0x94/0x95 are curly double quotes in RISC
    # OS Latin1 (alphabet 101), not the non-printing C1 control codes
    # plain ISO-8859-1 would give -- see docs/impression-documents.xml,
    # "Text and character encoding". Found via a real document
    # (Fletcher) where a curly-quoted name decoded incorrectly.
    content = ContentBuilder().literal("\x94Galadriel\x95").bytes()
    story = _story_from_content(content)
    (paragraph,) = story.paragraphs
    (run,) = paragraph.items
    assert run.text == "“Galadriel”"


def test_paragraph_break_splits_paragraphs_at_the_next_line():
    # A pending break (from CTRL_M) is only actually applied at the start
    # of the next line record, not before ordinary text within the same
    # line -- see test_paragraph_break_not_applied_before_ordinary_content.
    line1 = build_text_content_line(ContentBuilder().literal("First").simple_ctrl(CTRL_M).bytes())
    line2 = build_text_content_line(ContentBuilder().literal("Second").bytes())
    story = parse_story(build_story_bytes([line1, line2]))
    assert len(story.paragraphs) == 2
    assert story.paragraphs[0].items == (Run(text="First", style_slots=()),)
    assert story.paragraphs[1].items == (Run(text="Second", style_slots=()),)


def test_consecutive_paragraph_breaks_do_not_create_empty_paragraphs():
    line1 = build_text_content_line(
        ContentBuilder()
        .literal("First")
        .simple_ctrl(CTRL_M)
        .simple_ctrl(CTRL_M)
        .simple_ctrl(CTRL_M)
        .bytes()
    )
    line2 = build_text_content_line(ContentBuilder().literal("Second").bytes())
    story = parse_story(build_story_bytes([line1, line2]))
    # Only one break ever gets applied, however many CTRL_M bytes preceded it.
    assert len(story.paragraphs) == 2


def test_trailing_paragraph_break_is_not_flushed():
    content = ContentBuilder().literal("Only").simple_ctrl(CTRL_M).bytes()
    story = _story_from_content(content)
    # The pending break at end-of-story is simply dropped, matching the
    # conversion source (never checked again after the last line).
    assert len(story.paragraphs) == 1
    assert story.paragraphs[0].items == (Run(text="Only", style_slots=()),)


def test_ctrl_n_discards_a_pending_paragraph_break():
    content = (
        ContentBuilder()
        .literal("Before")
        .simple_ctrl(CTRL_M)
        .simple_ctrl(CTRL_N)
        .literal("After")
        .bytes()
    )
    story = _story_from_content(content)
    # No paragraph split: the pending break was discarded by CTRL_N, not applied.
    assert len(story.paragraphs) == 1
    items = story.paragraphs[0].items
    assert items[0] == Run(text="Before", style_slots=())
    assert items[1] == PageBreakMark()
    assert items[2] == Run(text="After", style_slots=())


def test_paragraph_break_not_applied_before_ordinary_content():
    # A pending break is only applied at the next line, or a style change --
    # not before literal text or most other control codes.
    content = (
        ContentBuilder()
        .literal("Before")
        .simple_ctrl(CTRL_M)
        .ctrl(CTRL_R, 0)
        .literal("After")
        .bytes()
    )
    story = _story_from_content(content)
    assert len(story.paragraphs) == 1
    items = story.paragraphs[0].items
    assert items == (
        Run(text="Before", style_slots=()),
        TabMark(),
        Run(text="After", style_slots=()),
    )


def test_paragraph_break_applied_at_next_line():
    line1 = build_text_content_line(ContentBuilder().literal("Before").simple_ctrl(CTRL_M).bytes())
    line2 = build_text_content_line(ContentBuilder().literal("After").bytes())
    story = parse_story(build_story_bytes([line1, line2]))
    assert len(story.paragraphs) == 2
    assert story.paragraphs[0].items == (Run(text="Before", style_slots=()),)
    assert story.paragraphs[1].items == (Run(text="After", style_slots=()),)


def test_text_continues_across_lines_without_a_break():
    line1 = build_text_content_line(ContentBuilder().literal("Hello ").bytes())
    line2 = build_text_content_line(ContentBuilder().literal("World").bytes())
    story = parse_story(build_story_bytes([line1, line2]))
    assert len(story.paragraphs) == 1
    assert story.paragraphs[0].items == (Run(text="Hello World", style_slots=()),)


def test_page_chapter_and_heading_number_marks():
    content = (
        ContentBuilder()
        .ctrl(CTRL_K, 0x1)
        .ctrl(CTRL_K, 0x2)
        .ctrl(CTRL_K, 0x4 | (77 << 8))
        .bytes()
    )
    story = _story_from_content(content)
    items = story.paragraphs[0].items
    assert items == (PageNumberMark(), ChapterNumberMark(), HeadingNumberMark(tag=77))


def test_embed_mark():
    content = ContentBuilder().literal("see: ").ctrl_s_embed(embed_tag=1234).bytes()
    story = _story_from_content(content)
    items = story.paragraphs[0].items
    assert items[0] == Run(text="see: ", style_slots=())
    assert items[1] == EmbedMark(embed_tag=1234)


def test_embed_mark_captures_the_active_style_stack():
    # Regression test: a real document had a paragraph consisting of
    # nothing but an embedded picture (no Run at all), styled with a
    # named style carrying a "Centre" alignment effect -- EmbedMark
    # used to be built with no style_slots of its own at all, so a
    # converter had no way to discover that alignment, unlike a Run
    # under the same CTRL_G/CTRL_H style application.
    content = ContentBuilder().ctrl_style(CTRL_G, [5]).ctrl_s_embed(embed_tag=1234).bytes()
    story = _story_from_content(content)
    (item,) = story.paragraphs[0].items
    assert item == EmbedMark(embed_tag=1234, style_slots=(5,))


def test_merge_mark():
    content = ContentBuilder().ctrl_s_merge("CustomerName").bytes()
    story = _story_from_content(content)
    assert story.paragraphs[0].items == (MergeMark(field_name="CustomerName"),)


def test_style_stack_applies_to_subsequent_runs():
    content = (
        ContentBuilder()
        .literal("plain")
        .ctrl_style(CTRL_G, [3, 5])
        .literal("styled")
        .ctrl_style(CTRL_H, [])
        .literal("plain again")
        .bytes()
    )
    story = _story_from_content(content)
    items = story.paragraphs[0].items
    assert items[0] == Run(text="plain", style_slots=())
    assert items[1] == Run(text="styled", style_slots=(3, 5))
    assert items[2] == Run(text="plain again", style_slots=())


def test_style_change_flushes_pending_paragraph_break():
    content = (
        ContentBuilder()
        .literal("Before")
        .simple_ctrl(CTRL_M)
        .ctrl_style(CTRL_G, [1])
        .literal("After")
        .bytes()
    )
    story = _story_from_content(content)
    assert len(story.paragraphs) == 2
    assert story.paragraphs[0].items == (Run(text="Before", style_slots=()),)
    assert story.paragraphs[1].items == (Run(text="After", style_slots=(1,)),)


def test_ctrl_u_and_kerning_and_skip_codes_do_not_affect_text():
    content = (
        ContentBuilder()
        .literal("A")
        .ctrl(CTRL_U, 1, 2)
        .literal("B")
        .ctrl(CTRL_CLOSESQ, 1, 2, 3, 4)
        .literal("C")
        .simple_ctrl(CTRL_E)
        .literal("D")
        .bytes()
    )
    story = _story_from_content(content)
    (run,) = story.paragraphs[0].items
    assert run.text == "ABCD"


def test_frame_reference_lines_build_the_chain_and_do_not_affect_paragraphs():
    ref1 = build_frame_reference_line(frame_offset=1000)
    text = build_text_content_line(ContentBuilder().literal("Hello").bytes())
    ref2 = build_frame_reference_line(frame_offset=2000)
    story = parse_story(build_story_bytes([ref1, text, ref2]))

    assert story.frame_chain == (1000, 2000)
    assert len(story.paragraphs) == 1
    assert story.paragraphs[0].items == (Run(text="Hello", style_slots=()),)
