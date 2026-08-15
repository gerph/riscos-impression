from riscos_impression.io.source import DocumentSource
from tests.fixtures.builders import build_header


def test_single_file_mode(tmp_path):
    data = build_header(contents2=380)
    doc_path = tmp_path / "MyDoc"
    doc_path.write_bytes(data)

    source = DocumentSource.open(doc_path)

    assert source.directory_mode is False
    assert source.docdata == data


def test_directory_mode(tmp_path):
    data = build_header(contents2=380)
    doc_dir = tmp_path / "MyDoc"
    doc_dir.mkdir()
    (doc_dir / "!DocData").write_bytes(data)

    source = DocumentSource.open(doc_dir)

    assert source.directory_mode is True
    assert source.docdata == data
