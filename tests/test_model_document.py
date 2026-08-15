import pytest

from riscos_impression.model.document import (
    MIN_VERSION,
    FileHeader,
    ImpressionFormatError,
)
from tests.fixtures.builders import build_header


def test_parses_fields():
    header = FileHeader.from_bytes(
        build_header(v1=0x1234, contents2=1000, oldname="MyDoc")
    )
    assert header.v1 == 0x1234
    assert header.magic == 0x12345678
    assert header.version == MIN_VERSION
    assert header.contents2 == 1000
    assert header.oldname == "MyDoc"


def test_offsets_round_trip():
    header = FileHeader.from_bytes(
        build_header(
            colour1=380,
            tints=500,
            stylebase=500,
            dict1=900,
            mdict1=1200,
        )
    )
    assert header.colour1 == 380
    assert header.tints == 500
    assert header.stylebase == 500
    assert header.dict1 == 900
    assert header.mdict1 == 1200


def test_rejects_too_old_version():
    with pytest.raises(ImpressionFormatError):
        FileHeader.from_bytes(build_header(version=MIN_VERSION - 1))


def test_rejects_short_data():
    with pytest.raises(ImpressionFormatError):
        FileHeader.from_bytes(b"\x00" * 10)
