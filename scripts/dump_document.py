#!/usr/bin/env python3
"""Diagnostic tool: load an Impression document and print what has been
decoded so far.

Intended to be run manually against files in the local, gitignored
examples/ directory (real sample documents, never committed) -- not part
of CI. See PLAN.md.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from riscos_impression.io.reader import load_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Impression document (file or directory)")
    args = parser.parse_args(argv)

    document = load_document(args.path)

    print(f"path: {args.path}")
    print(f"directory mode: {document.source.directory_mode}")
    print("header:")
    for field in dataclasses.fields(document.header):
        value = getattr(document.header, field.name)
        if isinstance(value, int):
            print(f"  {field.name:16s} = {value} (0x{value:x})")
        else:
            print(f"  {field.name:16s} = {value!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
