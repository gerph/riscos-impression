import struct

from riscos_impression.formats.eps import EPSObject


def _build_eps_blob(*, name: str, content: bytes) -> bytes:
    header = bytearray(68)
    struct.pack_into("<I", header, 12, len(content))
    name_bytes = name.encode("latin-1") + b"\x00"
    name_field = name_bytes + b"\x00" * ((4 - len(name_bytes) % 4) % 4)
    return bytes(header) + name_field + content


def test_decodes_name_and_content():
    content = b"%!PS-Adobe-3.0 EPSF-3.0\n...eps content...\n"
    blob = _build_eps_blob(name="MyPicture", content=content)

    eps = EPSObject.from_bytes(blob)

    assert eps.name == "MyPicture"
    assert eps.data == content


def test_name_field_padding_to_four_bytes():
    # A 3-character name: "abc\0" is already 4 bytes, needing no padding.
    content = b"XX"
    blob = _build_eps_blob(name="abc", content=content)
    eps = EPSObject.from_bytes(blob)
    assert eps.name == "abc"
    assert eps.data == content


def test_empty_content():
    blob = _build_eps_blob(name="Empty", content=b"")
    eps = EPSObject.from_bytes(blob)
    assert eps.data == b""
