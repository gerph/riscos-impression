from riscos_impression.io.reader import load_document
from tests.fixtures.builders import build_header


def test_load_document(tmp_path):
    data = build_header(v1=42, contents2=380)
    doc_path = tmp_path / "MyDoc"
    doc_path.write_bytes(data)

    document = load_document(doc_path)

    assert document.source.directory_mode is False
    assert document.header.v1 == 42
