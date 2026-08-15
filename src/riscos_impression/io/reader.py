"""Top-level orchestration for loading an Impression document.

See PLAN.md: this grows, stage by stage, to wire the file header, colours,
styles, numbering, dictionary, and object-record streams into a complete
``ImpressionDocument``.
"""

from __future__ import annotations

from pathlib import Path

from riscos_impression.io.source import DocumentSource
from riscos_impression.model.document import FileHeader, ImpressionDocument


def load_document(path: str | Path) -> ImpressionDocument:
    """Load an Impression document from *path* (a single file, or a
    directory in directory mode) and decode it as far as this stage of
    the package implements."""
    source = DocumentSource.open(path)
    header = FileHeader.from_bytes(source.docdata)
    return ImpressionDocument(source=source, header=header)
