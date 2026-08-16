from pathlib import Path

import pytest

from riscos_impression.cli import main
from tests.fixtures.builders import build_minimal_document_bytes


@pytest.fixture
def empty_doc(tmp_path: Path) -> Path:
    """A minimal, valid, empty document (no styles, no chapters) --
    good enough to exercise the CLI's own plumbing without needing a
    full byte-level document with real content."""
    doc_path = tmp_path / "MyDoc"
    doc_path.write_bytes(build_minimal_document_bytes())
    return doc_path


@pytest.mark.parametrize("fmt,ext", [
    ("ddl", ".ddl"),
    ("pdf", ".pdf"),
    ("html-scroll", ".html"),
    ("html-paged", ".html"),
    ("markdown", ".md"),
])
def test_convert_each_format_writes_output(empty_doc, tmp_path, capsys, fmt, ext):
    out = tmp_path / f"out{ext}"
    assert main(["convert", str(empty_doc), "--format", fmt, "-o", str(out)]) == 0
    assert out.exists()
    assert out.stat().st_size > 0
    assert f"Wrote {out}" in capsys.readouterr().err


def test_convert_default_output_path_uses_format_extension(empty_doc, capsys):
    assert main(["convert", str(empty_doc), "--format", "pdf"]) == 0
    expected = empty_doc.with_suffix(".pdf")
    assert expected.exists()
    assert f"Wrote {expected}" in capsys.readouterr().err


def test_convert_missing_input_reports_error(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    assert main(["convert", str(missing), "--format", "pdf"]) == 1
    assert "error" in capsys.readouterr().err.lower()


def test_convert_json_log_is_valid_json(empty_doc, tmp_path, capsys):
    import json

    out = tmp_path / "out.pdf"
    main(["convert", str(empty_doc), "--format", "pdf", "-o", str(out), "--json-log"])
    entries = json.loads(capsys.readouterr().out)
    assert isinstance(entries, list)


def test_convert_log_level_flag_is_accepted(empty_doc, tmp_path):
    # The empty document fixture produces no log entries at all, so this
    # only confirms --log-level is accepted and doesn't disturb a clean
    # conversion; filtering itself is covered by _print_log directly not
    # having its own unit test surface (it's a thin argparse-driven
    # wrapper around ConversionLog, already covered by log.py's tests).
    out = tmp_path / "out.pdf"
    assert main(["convert", str(empty_doc), "--format", "pdf", "-o", str(out), "--log-level", "error"]) == 0


def test_print_log_filters_by_level(capsys):
    import argparse

    from riscos_impression.cli import _print_log
    from riscos_impression.log import ConversionLog

    log = ConversionLog()
    log.info("area", "info message")
    log.best_effort("area", "best-effort message")
    log.error("area", "error message")

    args = argparse.Namespace(log_level="best_effort", json_log=False)
    _print_log(log, args)
    err = capsys.readouterr().err
    assert "info message" not in err
    assert "best-effort message" in err
    assert "error message" in err


def test_print_log_json_respects_level_filter(capsys):
    import argparse
    import json

    from riscos_impression.cli import _print_log
    from riscos_impression.log import ConversionLog

    log = ConversionLog()
    log.info("area", "info message")
    log.error("area", "error message")

    args = argparse.Namespace(log_level="error", json_log=True)
    _print_log(log, args)
    entries = json.loads(capsys.readouterr().out)
    assert [e["message"] for e in entries] == ["error message"]


def test_convert_to_pdf_on_non_paged_format_warns(empty_doc, tmp_path, capsys):
    out = tmp_path / "out.pdf"
    main(["convert", str(empty_doc), "--format", "pdf", "-o", str(out), "--to-pdf"])
    assert "only applies to --format html-paged" in capsys.readouterr().err


def test_convert_html_paged_export_pdf_flag_reaches_the_converter(empty_doc, tmp_path, capsys, monkeypatch):
    import riscos_impression.output.html_paged as html_paged_module

    monkeypatch.setattr(html_paged_module.shutil, "which", lambda name: None)
    out = tmp_path / "out.html"
    assert main(["convert", str(empty_doc), "--format", "html-paged", "-o", str(out), "--to-pdf"]) == 0
    assert "PDF export skipped" in capsys.readouterr().err


def test_no_subcommand_prints_help_and_returns_nonzero(capsys):
    assert main([]) == 1
    assert "usage" in capsys.readouterr().out.lower()
