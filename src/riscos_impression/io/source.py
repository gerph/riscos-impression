"""Uniform byte access to an Impression document, regardless of whether it
is stored as a single file or as a directory (``!DocData`` plus separate
story/picture files).

See docs/impression-documents.xml, "Document storage: single file versus
directory mode".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from riscos_impression import binary

DOCDATA_NAME = "!DocData"
TEXT_CHUNK_HEADER_SIZE = 8


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

    Directory-mode story and picture files (``MasterChap``, ``ChapterN``,
    and their ``Text``/``StoryN`` files) are read with
    ``read_picture_file``/``read_text_chunk`` below, given a directory
    name; mapping a dictionary entry to that directory name needs the
    decoded dictionary and chapter structure, so it lives on
    ``model.document.ImpressionDocument`` instead (see
    ``model.dictionary.chapter_index_for``).
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

    def read_picture_file(self, chapter_directory: str, story_id: int) -> bytes:
        """Read a directory-mode DCPICT entry's whole StoryN file."""
        return (self.path / chapter_directory / f"Story{story_id}").read_bytes()

    def read_text_chunk(self, chapter_directory: str, chunk_id: int) -> bytes:
        """Read a directory-mode DCTEXT entry's chunk from a chapter's
        Text file, scanning textchunkstr-framed chunks by id; see
        "Directory layout and story files"."""
        data = (self.path / chapter_directory / "Text").read_bytes()
        pos = 0
        while pos + TEXT_CHUNK_HEADER_SIZE <= len(data):
            length = binary.u32(data, pos)
            found_id = binary.u32(data, pos + 4)
            if length == 0:
                break
            if found_id == chunk_id:
                return data[pos + TEXT_CHUNK_HEADER_SIZE : pos + length]
            pos += length
        raise LookupError(
            f"text chunk {chunk_id} not found in {chapter_directory}/Text"
        )
