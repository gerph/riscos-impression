from riscos_impression.output.font_metrics import WIDTHS_256PT, char_width_per_mille


def test_all_eight_homerton_trinity_variants_present():
    expected = {
        "Homerton.Medium", "Homerton.Medium.Oblique", "Homerton.Bold", "Homerton.Bold.Oblique",
        "Trinity.Medium", "Trinity.Medium.Italic", "Trinity.Bold", "Trinity.Bold.Italic",
    }
    assert set(WIDTHS_256PT) == expected


def test_every_table_covers_all_224_riscos_latin1_printable_bytes():
    for name, table in WIDTHS_256PT.items():
        assert len(table) == 224, name


def test_char_width_matches_known_adobe_afm_values():
    # Homerton/Trinity are RISC OS's Helvetica/Times-alikes; these are
    # Adobe's own published standard AFM widths (per 1000 units of em).
    assert char_width_per_mille("Homerton.Medium", " ") == 278
    assert char_width_per_mille("Homerton.Medium", "M") == 833
    assert char_width_per_mille("Homerton.Medium", "A") == 667
    assert char_width_per_mille("Trinity.Medium", "M") == 889


def test_char_width_none_for_an_uncovered_font():
    assert char_width_per_mille("Corpus.Medium", "A") is None
    assert char_width_per_mille("NotAFont", "A") is None


def test_char_width_none_for_a_character_outside_riscos_latin1():
    assert char_width_per_mille("Homerton.Medium", "中") is None


def test_char_width_is_real_for_riscos_latin1_smart_quotes():
    # These are genuine RISC OS glyphs (see encoding.py), not gaps --
    # the table should give them a real, non-zero width.
    assert char_width_per_mille("Homerton.Medium", "“") is not None
    assert char_width_per_mille("Homerton.Medium", "“") > 0


def test_char_width_zero_for_riscos_ui_icon_glyphs_with_no_text_form():
    # 0x83/0x84 (resize/close window icons) have no real text glyph;
    # the source table gives them a width of 0, not a made-up guess.
    from riscos_impression import encoding

    resize_icon = encoding.decode_byte(0x83)
    assert char_width_per_mille("Homerton.Medium", resize_icon) == 0
