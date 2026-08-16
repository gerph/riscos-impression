from riscos_impression.model.dictionary import DictionaryEntry, DictionaryEntryType
from riscos_impression.model.document_tree import Chapter, PageGroup
from riscos_impression.model.frames import Page
from riscos_impression.model.story import EmbedMark, Paragraph, Run, Story, TabMark
from riscos_impression.model.styles import TabStop
from riscos_impression.output.html_paged import PagedHTMLConverter, _approx_width

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


def test_unresolvable_frame_chain_falls_back_to_single_frame_and_logs_once(tmp_path):
    # frame_chain=(824,) doesn't resolve to any real frame record in
    # this document (matching independently-repeated master content,
    # whose frame_chain is anchored to a master page, not this
    # chapter -- see _flow_chained_story) -- must fall back to
    # rendering fully in this one frame, not silently drop the story,
    # and log the fallback exactly once (not once per occurrence).
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
    assert sum(1 for e in converter.log.entries if "doesn't resolve against this chapter" in e.message) == 1


def test_independently_repeated_master_content_renders_on_every_page(tmp_path):
    # Regression test: the user reported a real document's own running
    # footer (PCI_Spec, "Sheet 1 / Issue F ****LIVE****") appeared on
    # only the first couple of pages, then vanished from every later
    # one. A master-linked frame is literally the same Frame object on
    # every page that uses that master, but the text it carries (not a
    # real, chapter-anchored chain -- its frame_chain doesn't resolve;
    # see _flow_chained_story) must render fresh, independently, in
    # every page's own occurrence -- deduplicating by dictionary_index
    # (the previous behaviour) left only the very first page with the
    # footer at all, matching how pdfdoc.py already treats this same
    # case (see its own test_master_anchored_story_renders_
    # independently_without_erroring).
    body = _style(0, is_body_text=True, font_size=160)
    frame1 = _frame(x0=0, y0=0, x1=100000, y1=20000, dictionary_index=0)
    frame2 = _frame(x0=0, y0=0, x1=100000, y1=20000, dictionary_index=0)
    page1 = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame1),),
    )
    page2 = PageGroup(
        page=Page(x0=0, y0=150000, x1=100000, y1=300000, bleed=0, master_page_name=""),
        offset=2000,
        records=(_frame_record(2008, frame2),),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page1, page2)
    )
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document.dictionary.append(dict_entry)
    # frame_chain=(824,) doesn't resolve to any real frame record here
    # (matching independently-repeated master content).
    story = Story(frame_chain=(824,), paragraphs=(Paragraph(items=(Run(text="Footer", style_slots=()),)),))
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert text.count("Footer") == 2


def test_tab_positions_text_at_the_styles_own_tab_stop_not_a_literal_tab_character():
    # Regression test: the user reported a real document's own footer
    # ("Sheet 1" / "Issue F ****LIVE****", tab-separated) not
    # right-aligning "Issue F" -- a literal tab character collapses to
    # nothing under HTML's default whitespace handling, so the browser
    # never actually jumped to the style's own declared right tab
    # stop. The Nth tab in a paragraph is now positioned at the Nth
    # entry of the style's own tab ruler instead, each its own
    # absolutely-positioned span within the paragraph's own box
    # (centred/right-aligned on its stop via a CSS transform, needing
    # no width measurement of its own).
    document = _document(styles=[_style(0, is_body_text=True, font_size=160)])
    converter = PagedHTMLConverter(document, export_pdf=False)
    style = _style(
        1, font_size=160,
        tab_stops=(TabStop(kind=1, position=255120), TabStop(kind=2, position=507400)),
    )
    items = (
        Run(text="Sheet 1", style_slots=()),
        TabMark(),
        TabMark(),
        Run(text="Issue F", style_slots=()),
    )

    html = converter._render_items(items, 0, None, style, 510.24)

    assert "Sheet 1" in html
    assert (
        '<span style="position:absolute;left:255.12pt;white-space:nowrap;'
        'transform:translateX(-50%)"></span>' in html
    )
    assert 'left:507.40pt;white-space:nowrap;transform:translateX(-100%)' in html
    assert ">Issue F<" in html


def test_tab_jumps_to_the_first_stop_past_the_current_cursor_position():
    # Regression test: the user reported PCI_Spec's title block
    # ("Distribution:", "Title:", "Issue:", ...) landing its own values
    # in two different columns depending on the label's own length, not
    # the single, consistent column the reference document shows. The
    # earlier fix mapped "the Nth tab in a paragraph" to "the Nth
    # declared tab stop" -- which happened to work for a single-stop
    # ruler (this one -- the real style's own tab ruler for these
    # rows), but only by coincidence: "tab 0 -> stop 0" and "first stop
    # past the cursor" agree whenever there's only one stop to choose
    # from at all. The real bug this masked (see the sibling test
    # below, with a genuinely multi-stop ruler) is that a tab must jump
    # to the first declared stop *past the current cursor position*,
    # mirroring pdfdoc.py's own _next_tab_stop, not simply consume
    # stops in declaration order -- this test just confirms the
    # single-stop, real-document case still lands correctly.
    style = _style(1, font_size=192, tab_stops=(TabStop(kind=0, position=212598),))
    document = _document(styles=[_style(0, is_body_text=True, font_size=160), style])
    converter = PagedHTMLConverter(document, export_pdf=False)
    short_row = converter._render_items(
        (Run(text="Issue:", style_slots=(1,)), TabMark(), Run(text="F", style_slots=(1,))), 0, None, style, 510.24,
    )
    long_row = converter._render_items(
        (Run(text="Distribution:", style_slots=(1,)), TabMark(), Run(text="COMPANY", style_slots=(1,))),
        0, None, style, 510.24,
    )

    # Both rows' own label width plus spacer width must land the value
    # at the SAME declared stop (212.598pt), even though "Issue:" and
    # "Distribution:" are very different widths.
    import re

    def spacer_width(html: str) -> float:
        return float(re.search(r"display:inline-block;width:([\d.]+)pt", html).group(1))

    landing_short = _approx_width("Issue:", style) + spacer_width(short_row)
    landing_long = _approx_width("Distribution:", style) + spacer_width(long_row)
    assert round(landing_short, 1) == round(landing_long, 1) == 212.6
    assert ">F<" in short_row
    assert ">COMPANY<" in long_row


def test_tab_skips_a_stop_the_cursor_has_already_passed():
    # A genuinely multi-stop ruler: a short label lands on the first
    # stop past its own (small) cursor position, but a longer label
    # that's already past that first stop by the time its own tab is
    # reached skips it entirely and lands on the next one instead --
    # the property the single-stop, real-document test above can't
    # exercise on its own (see its own docstring): with only one
    # declared stop, "the Nth tab uses the Nth stop" and "the first
    # stop past the cursor" always agree by coincidence.
    style = _style(1, font_size=160, tab_stops=(TabStop(kind=0, position=60000), TabStop(kind=0, position=150000)))
    document = _document(styles=[_style(0, is_body_text=True, font_size=160), style])
    converter = PagedHTMLConverter(document, export_pdf=False)

    short_width = _approx_width("Hi", style)
    long_width = _approx_width("A longer label that runs past 60pt", style)
    assert short_width < 60.0 < long_width  # sanity: the fixture text actually exercises both cases

    short_row = converter._render_items(
        (Run(text="Hi", style_slots=(1,)), TabMark(), Run(text="value", style_slots=(1,))), 0, None, style, 300.0,
    )
    long_row = converter._render_items(
        (
            Run(text="A longer label that runs past 60pt", style_slots=(1,)),
            TabMark(),
            Run(text="value", style_slots=(1,)),
        ),
        0, None, style, 300.0,
    )

    import re

    def spacer_width(html: str) -> float:
        return float(re.search(r"display:inline-block;width:([\d.]+)pt", html).group(1))

    assert round(short_width + spacer_width(short_row), 1) == 60.0
    assert round(long_width + spacer_width(long_row), 1) == 150.0


def test_right_tabbed_segment_does_not_double_count_its_own_width():
    # A right-tab's own segment is rendered position: absolute -- out
    # of normal flow -- so its own text width must NOT additionally
    # advance the cursor a second tab has to search past (cursor_pt is
    # already set to that stop's own position when the governing tab
    # is processed). Confirmed against a real document (PCI_Spec): a
    # numbered-contents row's own two-digit chapter number ("10"-"14",
    # right-aligned) is wider than a single digit's, and before this
    # fix that extra width pushed the cursor for the *next* tab in the
    # same row (a left tab for the chapter name) far enough to skip
    # over its own, nearer stop entirely and land on the page-number
    # column's stop instead -- the "chapter names overlapping/
    # misplaced" symptom. This mirrors that row's own three-stop
    # right/left/right ruler, with only the first segment's width
    # varying between the two rows.
    style = _style(
        1,
        font_size=160,
        tab_stops=(
            TabStop(kind=2, position=113385),
            TabStop(kind=0, position=121889),
            TabStop(kind=2, position=396850),
        ),
        left_indent=121889,
        first_indent_absolute=121889 - 93544,
    )
    document = _document(styles=[_style(0, is_body_text=True, font_size=160), style])
    converter = PagedHTMLConverter(document, export_pdf=False)

    single_digit_row = converter._render_items(
        (
            TabMark(),
            Run(text="1", style_slots=(1,)),
            TabMark(),
            Run(text="History", style_slots=(1,)),
            TabMark(),
            Run(text="2", style_slots=(1,)),
        ),
        0, None, style, 510.24,
    )
    double_digit_row = converter._render_items(
        (
            TabMark(),
            Run(text="10", style_slots=(1,)),
            TabMark(),
            Run(text="External Dependencies", style_slots=(1,)),
            TabMark(),
            Run(text="7", style_slots=(1,)),
        ),
        0, None, style, 510.24,
    )

    import re

    def spans(html: str) -> list[str]:
        return re.findall(r'style="([^"]*)"', html)

    # Both rows' second (chapter-name) tab must resolve to the SAME
    # left-tab spacer stop, and their third (page-number) tab to the
    # SAME right-tab position -- regardless of the first segment's own
    # (1 vs 2 digit) width.
    assert "display:inline-block;width:8.50pt" in single_digit_row
    assert "display:inline-block;width:8.50pt" in double_digit_row
    assert single_digit_row.count("left:274.96pt") == 1
    assert double_digit_row.count("left:274.96pt") == 1
    assert ">External Dependencies<" in double_digit_row
    assert ">7<" in double_digit_row


def test_left_tab_stays_in_normal_flow_and_does_not_overlap_later_rows(tmp_path):
    # Regression test: the user supplied a reference image (PCISpec-
    # HTMLPagedOverwrite.png) showing PCI_Spec's "On Entry:"/"On Exit:"
    # SWI-parameter rows (each starting with a register name, then a
    # LEFT tab, then a description) rendering with heavy overlapping,
    # garbled text on page 5. Every tab kind, including left, had been
    # made position: absolute (fix for the earlier right-alignment
    # bug), which removes a segment from normal document flow
    # entirely -- contributing nothing to its own paragraph's height.
    # A left-tabbed row whose own description is long enough to wrap
    # onto more than one line therefore left its paragraph's own box
    # far too short, and the next row started immediately underneath,
    # visually overlapping the wrapped (but layout-invisible) text
    # above it. A left tab must stay in normal flow -- an inline-block
    # spacer of the right width, not a position: absolute segment --
    # so wrapping and height both work correctly, and two such rows
    # rendered as separate <p> elements must not visually collide.
    document = _document(styles=[_style(0, is_body_text=True, font_size=160)])
    converter = PagedHTMLConverter(document, export_pdf=False)
    style = _style(1, font_size=160, tab_stops=(TabStop(kind=0, position=144000),))
    items = (Run(text="R0", style_slots=()), TabMark(), Run(text="A long wrapping description", style_slots=()))

    html = converter._render_items(items, 0, None, style, 300.0)

    assert "position:absolute" not in html
    # The spacer's own width accounts for "R0"'s own rendered width
    # (not simply the declared stop position itself): a tab jumps to
    # the first stop past the *current cursor position*, not always
    # the same fixed distance regardless of what came before it (see
    # test_tab_jumps_to_the_first_stop_past_the_current_cursor_position
    # for the real-document bug this was confirmed against).
    assert '<span style="display:inline-block;width:131.22pt"></span>' in html
    assert ">A long wrapping description<" in html
    # The description stays in normal flow (able to wrap and
    # contribute height), not removed from it as a position: absolute
    # segment would be.
    assert "position: relative" in html  # still set on the <p> itself, harmless without any absolute children


def test_real_multi_frame_chain_flows_text_across_frames(tmp_path):
    # Regression test: a real document (PCI_Spec from the local
    # examples/ corpus) has many stories chained across 2+ frames;
    # this converter used to render only the first frame's content and
    # leave every later chain member's text area empty (with only its
    # own independently-placed pictures, if any, showing) -- reported
    # directly by the user. A two-frame chain, with text too long for
    # the first frame's own small box, must have its overflow appear in
    # the second frame instead of being silently clipped.
    body = _style(0, is_body_text=True, font_size=160)
    # frame1 is deliberately just tall enough for one line; frame2 is
    # roomy, so the second paragraph must land in frame2.
    frame1 = _frame(x0=0, y0=0, x1=100000, y1=15000, dictionary_index=0)
    frame2 = _frame(x0=0, y0=0, x1=100000, y1=300000, dictionary_index=0)
    page1 = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, frame1),),
    )
    page2 = PageGroup(
        page=Page(x0=0, y0=150000, x1=100000, y1=450000, bleed=0, master_page_name=""),
        offset=2000,
        records=(_frame_record(2008, frame2),),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=100000, y1=150000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page1, page2)
    )
    dict_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)
    document.dictionary.append(dict_entry)
    # frame1 is at offset 1008; frame2's on-disk chain offset (relative
    # to mainpages2) is 2008 - 900 = 1108 (single-file mode).
    story = Story(
        frame_chain=(1108,),
        paragraphs=tuple(Paragraph(items=(Run(text=f"Para{i}", style_slots=()),)) for i in range(8)),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert "Para0" in text
    assert "Para7" in text  # only reachable if flow continued into frame2
    assert not any("doesn't resolve against this chapter" in e.message for e in converter.log.entries)


def test_embed_tagged_picture_frame_is_not_also_drawn_independently(tmp_path):
    """Regression test: the user reported PCI_Spec's DrawFile diagrams
    appearing doubled and overlapping running text in paged HTML. A
    PictureFrame with a non-zero embed_tag is anchored inline within a
    text story at the matching EmbedMark's own position
    (_render_embed), not drawn at its own raw, page-relative box in
    normal front-to-back order -- mirrors pdfdoc.py's own, already-
    fixed _draw_frame check (its docstring: "Drawing it here too, at
    its raw (and often stale/irrelevant) box, was the direct cause of
    inline pictures visually overlaying running text"). html_paged.py's
    own per-page frame walk never had the equivalent check, so an
    embed-tagged picture rendered both inline AND independently -- a
    latent bug only made visible once the multi-frame chain flow fix
    let a story's own text actually reach the matching EmbedMark far
    enough into a chain to render it at all."""
    body = _style(0, is_body_text=True, font_size=160)
    text_frame = _frame(x0=0, y0=0, x1=200000, y1=300000, dictionary_index=0)
    ops = move(0, 0) + line(1000, 0) + line(1000, 1000) + close_line() + end_path()
    path = build_path(ops=ops, bounds=(0, 0, 1000, 1000), fill_colour=0x0000FF00)
    # Deliberately placed at an unrelated raw page position, far from
    # the text frame -- if this leaked into the output at all, the
    # picture would show up a second time, at the wrong place.
    picture_frame = _picture(x0=500000, y0=500000, x1=560000, y1=540000, embed_tag=42, dictionary_index=1)
    page = PageGroup(
        page=Page(x0=0, y0=0, x1=600000, y1=600000, bleed=0, master_page_name=""),
        offset=1000,
        records=(_frame_record(1008, text_frame), _frame_record(1108, picture_frame)),
    )
    section = _section(create_number=1, master_page_index=0)
    master_page = PageGroup(
        page=Page(x0=0, y0=0, x1=600000, y1=600000, bleed=0, master_page_name=""), offset=100, records=(),
    )
    header = _header(mainpages2=900, masterpages1=50, contents2=100000)
    chapter = Chapter(
        section=section, offset=900, master_page_1=master_page, master_page_2=None, pages=(page,)
    )
    document = _document(chapters=[chapter], master_pages=[master_page], styles=[body], header=header)
    text_entry = DictionaryEntry(index=0, type=DictionaryEntryType.TEXT, id=0, types=0)
    picture_entry = DictionaryEntry(index=1, type=DictionaryEntryType.PICTURE, id=0, types=0xAFF)
    document.dictionary.extend([text_entry, picture_entry])
    document.picture_bytes = lambda entry: build_drawfile(path, bounds=(0, 0, 1000, 1000))

    story = Story(
        frame_chain=(),
        paragraphs=(
            Paragraph(items=(Run(text="Before", style_slots=()), EmbedMark(embed_tag=42))),
        ),
    )
    document.story = lambda entry: story  # noqa: ARG005 - test stub

    converter = PagedHTMLConverter(document, export_pdf=False)
    out = tmp_path / "out.html"
    converter.convert(out)
    text = out.read_text()

    assert text.count("<svg ") == 1
    assert not converter.log.has_errors()


def test_estimate_slice_height_positive_right_indent_gives_the_frames_real_width():
    # Regression test: a real document (PCI_Spec from the local
    # examples/ corpus) has a history-table style whose right_indent is
    # a POSITIVE value close to the frame's own width. A POSITIVE
    # right_indent_raw (Style.right_indent_is_delta True) is an offset
    # from the frame's own LEFT edge, not an inset from its right --
    # confirmed against a real DDF export the user supplied, generated
    # by Impression itself (its base style declares leftmargin 19.8pt,
    # rightmargin 510.2pt on a frame that's itself 510.2pt wide). Read
    # (as this project originally, incorrectly assumed universally) as
    # an inset from the right, that squeezed the *measured* width to a
    # ~10pt sliver, wildly inflating each row's estimated height and
    # splitting a nine-row history table (which fits easily in one
    # frame -- confirmed against the PDF converter's own output, all on
    # one page) across three separate frames/pages instead.
    document = _document(styles=[_style(0, is_body_text=True, font_size=160)])
    converter = PagedHTMLConverter(document, export_pdf=False)
    items = (Run(text="0.0.1 25 June 1997 Initial draft released as issue one", style_slots=()),)
    style = _style(1, font_size=160, left_indent=19842, right_indent_raw=510236)

    height = converter._estimate_slice_height_pt(items, style, 510.236, None)

    assert height == 12.0  # fits on a single line (10pt * 1.2) at the frame's own real ~490pt width


def test_estimate_slice_height_negative_right_indent_is_an_inset_from_the_right():
    # A NEGATIVE right_indent_raw (right_indent_is_delta False) is a
    # genuine inset from the frame's own right edge: on a 100pt-wide
    # frame it leaves only 100 - 20 (left_indent) - 90 = -10pt, well
    # under _MIN_USABLE_WIDTH, so every word wraps onto its own line.
    document = _document(styles=[_style(0, is_body_text=True, font_size=160)])
    converter = PagedHTMLConverter(document, export_pdf=False)
    items = (Run(text="one two three", style_slots=()),)
    style = _style(1, font_size=160, left_indent=20000, right_indent_raw=-90000)

    height = converter._estimate_slice_height_pt(items, style, 100.0, None)

    assert height == 3 * 12.0  # each word forced onto its own line


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
