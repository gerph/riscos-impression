from riscos_impression.io.reader import load_document
from tests.fixtures.builders import build_minimal_document_bytes


def test_load_document(tmp_path):
    data = build_minimal_document_bytes(v1=42)
    doc_path = tmp_path / "MyDoc"
    doc_path.write_bytes(data)

    document = load_document(doc_path)

    assert document.source.directory_mode is False
    assert document.header.v1 == 42
    assert document.colours == []
    assert document.styles == []
    assert document.numbering == []
    assert document.dictionary == []
    assert document.master_pages == []
    assert document.chapters == []
