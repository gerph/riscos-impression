"""Uniform byte access to an Impression document, regardless of whether it
is stored as a single file or as a directory (``!DocData`` plus separate
story/picture files).

See docs/impression-documents.xml, "Document storage: single file versus
directory mode".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DOCDATA_NAME = "!DocData"


@dataclass
class DocumentSource:
    """Access to one Impression document's raw data.

    ``docdata`` holds the document-data block: the fixed file header and
    every table addressed by offset from it (colours, styles, numbering,
    dictionary, and the master-page and main-page object-record streams).
    In directory mode, this is the whole of the ``!DocData`` file; in
    single-file mode, it is the whole of the input file, which may also
    have story/picture data appended after the tables, addressed by
    absolute file offset via the master dictionary (see
    ``model.dictionary``).

    Resolving directory-mode story and picture files (``MasterChap``,
    ``ChapterN``, and their ``Text``/``StoryN`` files) is added once the
    object dictionary and document assembly exist; see PLAN.md Stage 6.
    """

    path: Path
    directory_mode: bool
    docdata: bytes

    @classmethod
    def open(cls, path: str | Path) -> "DocumentSource":
        path = Path(path)
        directory_mode = path.is_dir()
        docdata_path = path / DOCDATA_NAME if directory_mode else path
        docdata = docdata_path.read_bytes()
        return cls(path=path, directory_mode=directory_mode, docdata=docdata)
