import struct

from riscos_impression.formats.drawfile import DrawFile


def _build_drawfile(*, x0=0, y0=0, x1=1000, y1=2000) -> bytes:
    data = bytearray(40)
    data[0:4] = b"Draw"
    struct.pack_into("<i", data, 24, x0)
    struct.pack_into("<i", data, 28, y0)
    struct.pack_into("<i", data, 32, x1)
    struct.pack_into("<i", data, 36, y1)
    return bytes(data)


def test_decodes_bounding_box():
    drawfile = DrawFile.from_bytes(_build_drawfile(x0=10, y0=20, x1=1010, y1=2020))
    assert drawfile is not None
    assert drawfile.bounds.x0 == 10
    assert drawfile.bounds.y0 == 20
    assert drawfile.bounds.x1 == 1010
    assert drawfile.bounds.y1 == 2020


def test_negative_coordinates():
    drawfile = DrawFile.from_bytes(_build_drawfile(x0=-500, y0=-500))
    assert drawfile.bounds.x0 == -500
    assert drawfile.bounds.y0 == -500


def test_wrong_signature_returns_none():
    data = _build_drawfile()
    bad = b"NOPE" + data[4:]
    assert DrawFile.from_bytes(bad) is None


def test_too_short_returns_none():
    assert DrawFile.from_bytes(b"Draw") is None
