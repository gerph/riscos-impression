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
from collections import Counter

from riscos_impression.io.reader import load_document
from riscos_impression.model.dictionary import parse_dictionary
from riscos_impression.model.frames import parse_object_stream


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Impression document (file or directory)")
    args = parser.parse_args(argv)

    document = load_document(args.path)
    header = document.header
    data = document.source.docdata

    print(f"path: {args.path}")
    print(f"directory mode: {document.source.directory_mode}")
    print("header:")
    for field in dataclasses.fields(header):
        value = getattr(header, field.name)
        if isinstance(value, int):
            print(f"  {field.name:16s} = {value} (0x{value:x})")
        else:
            print(f"  {field.name:16s} = {value!r}")

    dict_entries = parse_dictionary(data, header.dict1, header.mdict1)
    dict_type_counts = Counter(e.type.name for e in dict_entries)
    print(f"object dictionary: {len(dict_entries)} entries {dict(dict_type_counts)}")

    master_records = parse_object_stream(data, header.masterpages1, header.mainpages1)
    main_records = parse_object_stream(data, header.mainpages2, header.contents1)
    for label, records in (("master pages", master_records), ("main pages", main_records)):
        type_counts = Counter(
            r.type.name if r.type is not None else f"unrecognised(0x{r.raw_type:x})"
            for r in records
        )
        print(f"{label}: {len(records)} records {dict(type_counts)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
