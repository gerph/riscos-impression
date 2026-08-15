#!/usr/bin/env python3
"""Diagnostic tool: load an Impression document and print a summary of
everything that has been decoded.

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
from riscos_impression.model.dictionary import DictionaryEntryType
from riscos_impression.model.frames import Frame


def _record_type_counts(records) -> dict:
    return dict(
        Counter(
            r.type.name if r.type is not None else f"unrecognised(0x{r.raw_type:x})"
            for r in records
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Impression document (file or directory)")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="also print the file header field by field"
    )
    args = parser.parse_args(argv)

    document = load_document(args.path)
    header = document.header

    print(f"path: {args.path}")
    print(f"directory mode: {document.source.directory_mode}")
    print(f"format version: {header.version}")

    if args.verbose:
        print("header:")
        for field in dataclasses.fields(header):
            value = getattr(header, field.name)
            if isinstance(value, int):
                print(f"  {field.name:16s} = {value} (0x{value:x})")
            else:
                print(f"  {field.name:16s} = {value!r}")

    print(f"colours: {len(document.colours)}")
    print(f"styles: {len(document.styles)}")
    print(f"numbering records: {len(document.numbering)}")

    dict_type_counts = Counter(e.type.name for e in document.dictionary)
    print(f"object dictionary: {len(document.dictionary)} entries {dict(dict_type_counts)}")

    master_page_records = [r for p in document.master_pages for r in p.records]
    print(
        f"master pages: {len(document.master_pages)} pages, "
        f"{_record_type_counts(master_page_records)}"
    )

    print(f"chapters: {len(document.chapters)}")
    for chapter in document.chapters:
        section = chapter.section
        page_records = [r for p in chapter.pages for r in p.records]
        master_linked = sum(
            1
            for p in chapter.pages
            for r in p.records
            if isinstance(r.value, Frame) and r.value.master
        )
        resolved = sum(
            1
            for p in chapter.pages
            for r in p.records
            if isinstance(r.value, Frame)
            and r.value.master
            and document.master_frame(p, r.value) is not None
        )
        print(
            f"  chapter createn={section.create_number} mpindex={section.master_page_index} "
            f"pages={len(chapter.pages)} master_pages="
            f"{1 + (chapter.master_page_2 is not None) if chapter.master_page_1 else 0} "
            f"master-linked frames={master_linked} (resolved={resolved}) "
            f"{_record_type_counts(page_records)}"
        )

    text_entries = [e for e in document.dictionary if e.type is DictionaryEntryType.TEXT]
    total_chars = 0
    story_errors = 0
    for entry in text_entries:
        try:
            story = document.story(entry)
        except Exception as e:  # noqa: BLE001 - diagnostic tool, report and continue
            story_errors += 1
            print(f"  STORY ERROR entry={entry.index}: {e!r}")
            continue
        for paragraph in story.paragraphs:
            for item in paragraph.items:
                text = getattr(item, "text", None)
                if text is not None:
                    total_chars += len(text)
    print(
        f"stories: {len(text_entries)} text entries, {total_chars} characters decoded, "
        f"{story_errors} errors"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
