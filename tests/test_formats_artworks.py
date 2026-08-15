from riscos_impression.formats.artworks import PLACEHOLDER_BOUNDS, ArtWorks


def test_stores_raw_data_and_placeholder_bounds():
    artworks = ArtWorks.from_bytes(b"some artworks bytes")
    assert artworks.data == b"some artworks bytes"
    assert artworks.bounds == PLACEHOLDER_BOUNDS
