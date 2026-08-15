from riscos_impression.log import ConversionLog, LogLevel


def test_records_entries_in_order():
    log = ConversionLog()
    log.info("colours", "using default palette")
    log.best_effort("picture", "rendered as placeholder box")
    log.unsupported("numbering", "roman numerals not implemented")
    log.error("frame", "could not resolve master frame")

    assert len(log) == 4
    assert [e.level for e in log.entries] == [
        LogLevel.INFO,
        LogLevel.BEST_EFFORT,
        LogLevel.UNSUPPORTED,
        LogLevel.ERROR,
    ]


def test_has_errors():
    log = ConversionLog()
    assert log.has_errors() is False
    log.best_effort("x", "y")
    assert log.has_errors() is False
    log.error("x", "y")
    assert log.has_errors() is True


def test_counts():
    log = ConversionLog()
    log.info("a", "1")
    log.info("a", "2")
    log.best_effort("b", "3")
    counts = log.counts()
    assert counts[LogLevel.INFO] == 2
    assert counts[LogLevel.BEST_EFFORT] == 1
    assert counts[LogLevel.UNSUPPORTED] == 0
    assert counts[LogLevel.ERROR] == 0


def test_summary_groups_identical_messages_with_a_count():
    log = ConversionLog()
    for i in range(3):
        log.best_effort("picture", "DrawFile rendered as placeholder", location=f"@0x{i:x}")

    summary = log.summary()
    assert "BEST_EFFORT:" in summary
    assert "[picture] DrawFile rendered as placeholder (x3)" in summary
    assert "@0x0" in summary


def test_summary_omits_levels_with_no_entries():
    log = ConversionLog()
    log.info("a", "b")
    summary = log.summary()
    assert "INFO:" in summary
    assert "ERROR:" not in summary
