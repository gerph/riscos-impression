"""Command-line entry point for riscos-impression: the ``convert``
subcommand drives document loading and whichever output converter
matches ``--format``, printing the resulting ConversionLog either as
plain text (default) or JSON (``--json-log``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from riscos_impression import __version__
from riscos_impression.io.reader import load_document
from riscos_impression.log import ConversionLog, LogLevel
from riscos_impression.output.html_paged import PagedHTMLConverter
from riscos_impression.output.html_scrolling import ScrollingHTMLConverter
from riscos_impression.output.markdown import MarkdownConverter
from riscos_impression.output.ovprodll import OvProDDLConverter
from riscos_impression.output.pdfdoc import PDFConverter

#: format name -> (converter class, default output file extension).
_FORMATS = {
    "ddl": (OvProDDLConverter, ".ddl"),
    "pdf": (PDFConverter, ".pdf"),
    "html-scroll": (ScrollingHTMLConverter, ".html"),
    "html-paged": (PagedHTMLConverter, ".html"),
    "markdown": (MarkdownConverter, ".md"),
}

#: LogLevel value -> a severity rank, matching the enum's own
#: (already-increasing) definition order; used to implement --log-level
#: filtering without depending on Python's enum member iteration order.
_LEVEL_RANK = {level.value: rank for rank, level in enumerate(LogLevel)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="riscos-impression", description="Convert RISC OS Impression documents to other formats")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    convert = subparsers.add_parser("convert", help="Convert a document to another format")
    convert.add_argument(
        "input",
        type=Path,
        help="path to the Impression document (a single file, or a directory for a directory-mode document)",
    )
    convert.add_argument("--format", "-f", choices=sorted(_FORMATS), required=True, help="output format")
    convert.add_argument(
        "-o", "--output", type=Path, default=None,
        help="output file path (default: the input's own name with the format's usual extension)",
    )
    convert.add_argument(
        "--strict", action="store_true",
        help="raise on the first conversion problem instead of logging it and continuing",
    )
    convert.add_argument(
        "--to-pdf", action="store_true",
        help="html-paged only: also try to export a PDF via Prince or WeasyPrint, if either is installed",
    )
    convert.add_argument(
        "--log-level", choices=[level.value for level in LogLevel], default=None,
        help="only print log entries at or above this level (default: print every entry)",
    )
    convert.add_argument("--json-log", action="store_true", help="print the conversion log as JSON instead of plain text")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "convert":
        parser.print_help()
        return 1
    return _convert(args)


def _convert(args: argparse.Namespace) -> int:
    converter_cls, default_ext = _FORMATS[args.format]
    output_path = args.output if args.output is not None else args.input.with_suffix(default_ext)

    if args.to_pdf and args.format != "html-paged":
        print("warning: --to-pdf only applies to --format html-paged; ignored", file=sys.stderr)

    try:
        document = load_document(args.input)
    except Exception as e:  # noqa: BLE001 - reported to the user, not a crash
        print(f"error: failed to load '{args.input}': {e}", file=sys.stderr)
        return 1

    kwargs = {"strict": args.strict}
    if args.format == "html-paged":
        kwargs["export_pdf"] = args.to_pdf
    converter = converter_cls(document, **kwargs)

    try:
        converter.convert(output_path)
    except Exception as e:  # noqa: BLE001 - reported to the user, not a crash (only reachable with --strict)
        print(f"error: conversion failed: {e}", file=sys.stderr)
        _print_log(converter.log, args)
        return 1

    print(f"Wrote {output_path}", file=sys.stderr)
    _print_log(converter.log, args)
    return 2 if converter.log.has_errors() else 0


def _print_log(log: ConversionLog, args: argparse.Namespace) -> None:
    threshold = _LEVEL_RANK[args.log_level] if args.log_level is not None else 0
    entries = [e for e in log.entries if _LEVEL_RANK[e.level.value] >= threshold]

    if args.json_log:
        print(json.dumps(
            [{"level": e.level.value, "area": e.area, "message": e.message, "location": e.location} for e in entries],
            indent=2,
        ))
        return

    if not entries:
        return
    print(f"Conversion log: {len(entries)} entries", file=sys.stderr)
    for e in entries:
        location = f" ({e.location})" if e.location else ""
        print(f"  [{e.level.value}] [{e.area}] {e.message}{location}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
