"""Smoke tests against the real, committed documents in corpus/ -- a
small, personal-information-audited subset of the much larger local
examples/ and moreexamples/ corpora used for manual testing throughout
development (see PLAN.md Stage 13). Every fixture here is a real
Impression document, hand-picked and added directly by the user rather
than by this project, precisely so this doesn't depend on this project's
own judgement about what's safe to publish.

Unlike the rest of the test suite (hand-built byte fixtures exercising
one specific code path each), these tests are deliberately broad and
shallow: each real document, run through every output converter, must
not crash and must not log a hard error -- catching regressions the
synthetic, single-feature fixtures elsewhere in this suite could easily
miss simply because a real document combines many features at once.
"""

from pathlib import Path

import pytest

from riscos_impression.cli import main
from riscos_impression.log import LogLevel

_CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"
_CORPUS_DOCUMENTS = sorted(p for p in _CORPUS_DIR.iterdir() if p.is_file()) if _CORPUS_DIR.is_dir() else []

_FORMATS = [
    ("ddl", ".ddl"),
    ("pdf", ".pdf"),
    ("html-scroll", ".html"),
    ("html-paged", ".html"),
    ("markdown", ".md"),
]


def _ids(path: Path) -> str:
    return path.name


@pytest.mark.skipif(not _CORPUS_DOCUMENTS, reason="no documents committed under corpus/ yet")
@pytest.mark.parametrize("document", _CORPUS_DOCUMENTS, ids=_ids)
@pytest.mark.parametrize("fmt,ext", _FORMATS)
def test_corpus_document_converts_without_crashing_or_erroring(document, fmt, ext, tmp_path, capsys):
    out = tmp_path / f"out{ext}"
    # --to-pdf is left off html-paged here: it shells out to an
    # optional, environment-dependent external tool (Prince/
    # WeasyPrint) that CI can't be expected to have installed, and
    # PagedHTMLConverter already logs (rather than errors) when
    # neither is found -- see test_export_pdf_logs_when_no_tool_is_
    # available in test_output_html_paged.py for that path's own
    # coverage.
    exit_code = main(["convert", str(document), "--format", fmt, "-o", str(out), "--json-log"])
    captured = capsys.readouterr()

    if exit_code != 0:
        import json

        entries = json.loads(captured.out) if captured.out else []
        errors = [e for e in entries if e["level"] == LogLevel.ERROR.value]
        pytest.fail(f"{document.name} ({fmt}) exited {exit_code}: {errors or captured.err}")

    assert out.exists()
    assert out.stat().st_size > 0
