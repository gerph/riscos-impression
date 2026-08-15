"""Top-level orchestration for loading an Impression document: wires the
file header, colour and style tables, numbering, object dictionary, and
master/main object-record streams into one ImpressionDocument.
"""

from __future__ import annotations

from pathlib import Path

from riscos_impression.io.source import DocumentSource
from riscos_impression.model.colours import parse_colour_table
from riscos_impression.model.dictionary import parse_dictionary, parse_master_dictionary
from riscos_impression.model.document import FileHeader, ImpressionDocument
from riscos_impression.model.document_tree import build_chapters, group_pages
from riscos_impression.model.frames import parse_object_stream
from riscos_impression.model.numbering import parse_numbering_table
from riscos_impression.model.styles import parse_style_table


def load_document(path: str | Path) -> ImpressionDocument:
    """Load and fully decode an Impression document from *path* (a single
    file, or a directory in directory mode)."""
    source = DocumentSource.open(path)
    data = source.docdata
    header = FileHeader.from_bytes(data)

    colours = parse_colour_table(data, header.colour1, header.tints)
    styles = parse_style_table(data, header.stylebase)
    numbering = parse_numbering_table(data, header.numbers, header.numbers_end)
    dictionary = parse_dictionary(data, header.dict1, header.mdict1)
    master_dictionary = parse_master_dictionary(data, header.mdict1, len(dictionary))

    master_records = parse_object_stream(data, header.masterpages1, header.mainpages1)
    master_pages = group_pages(master_records)

    main_records = parse_object_stream(data, header.mainpages2, header.contents1)
    chapters = build_chapters(main_records, master_pages)

    return ImpressionDocument(
        source=source,
        header=header,
        colours=colours,
        styles=styles,
        numbering=numbering,
        dictionary=dictionary,
        master_dictionary=master_dictionary,
        master_pages=master_pages,
        chapters=chapters,
    )
