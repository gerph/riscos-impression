"""Real Sprite pixel decoding, via the optional third-party
riscos_sprites decoder (see the "sprites" extra in pyproject.toml).

Deliberately thin: this module's only job is turning raw sprite-area
bytes into PNG bytes (or, for the PDF converter, riscos_sprites' own
PngImage description of the same decoded pixels), with the
riscos_sprites import guarded so every caller (html_base.py,
pdfdoc.py) can fall back to the existing labelled-placeholder
behaviour when it isn't installed, exactly like formats/artworks_svg.py's
own optional riscos_artworks dependency. formats/sprite.py stays a
dependency-free stub for the cases that only need "is this
recognisably a sprite" (no pixels), independent of whether this extra
is present. raw_scanline_bytes/_pack_scanline are the one exception:
they only repack an already-decoded PngImage-shaped object (real or a
test double) into raw pixel bytes, so they're vendored here rather
than imported from riscos_sprites, and stay available unconditionally.
"""

from __future__ import annotations

from typing import Optional

COLOUR_TYPE_RGB = 2
COLOUR_TYPE_PALETTE = 3
COLOUR_TYPE_RGBA = 6

try:
    from riscos_sprites import SpriteFile
    from riscos_sprites.png import PngImage, build_png_image, encode_png
except ImportError:  # pragma: no cover - exercised by CI without the extra
    SpriteFile = None
    PngImage = None
    build_png_image = None
    encode_png = None


def _pack_scanline(row, colour_type: int, bit_depth: int) -> bytes:
    if colour_type == COLOUR_TYPE_PALETTE and bit_depth < 8:
        per_byte = 8 // bit_depth
        out = bytearray()
        for start in range(0, len(row), per_byte):
            chunk = row[start : start + per_byte]
            byte = 0
            for position, value in enumerate(chunk):
                shift = 8 - bit_depth * (position + 1)
                byte |= (value & ((1 << bit_depth) - 1)) << shift
            out.append(byte)
        return bytes(out)
    if colour_type == COLOUR_TYPE_PALETTE:
        return bytes(row)
    if colour_type == COLOUR_TYPE_RGB:
        out = bytearray()
        for red, green, blue in row:
            out.extend((red, green, blue))
        return bytes(out)
    if colour_type == COLOUR_TYPE_RGBA:
        out = bytearray()
        for red, green, blue, alpha in row:
            out.extend((red, green, blue, alpha))
        return bytes(out)
    raise ValueError(f"unsupported PNG colour type: {colour_type}")


def raw_scanline_bytes(image: "PngImage") -> bytes:
    """*image*'s own pixel/index data as concatenated raw scanlines --
    no PNG per-row filter-type byte, no PNG chunk framing, not
    compressed. For a consumer that wants the decoded raster data
    itself rather than a PNG file (e.g. a PDF Image XObject, built
    directly from the same width/height/bit_depth/colour_type/palette
    this describes). A pure repacking of *image*'s own rows -- vendored
    from riscos_sprites.png rather than imported from it, since this is
    the PDF converter's own object-building logic, not sprite decoding,
    and must keep working on an already-decoded image (e.g. from a
    test double) even when the optional 'sprites' extra isn't
    installed."""
    raw = bytearray()
    for row in image.rows:
        raw.extend(_pack_scanline(row, image.colour_type, image.bit_depth))
    return bytes(raw)


def sprite_area_to_png_image(data: bytes) -> Optional["PngImage"]:
    """riscos_sprites' own PngImage description (width/height/
    bit_depth/colour_type/palette/rows/trns_*) of the first sprite in
    a sprite-area byte blob (a ,ff9 file's own bytes, or a DrawFile
    Sprite object's body already wrapped via formats/sprite.py's
    wrap_single_sprite_as_area) -- or None if riscos_sprites isn't
    installed, the blob has no sprites, or it fails to decode. A
    caller (the PDF converter) that wants to build its own image
    object directly from the same decoded pixel/mask/colour-key data
    the PNG encoder itself uses can use this instead of
    sprite_area_to_png(), rather than re-parsing a full PNG file."""
    if SpriteFile is None:
        return None
    try:
        sprite_file = SpriteFile.from_bytes(data)
        if not sprite_file.sprites:
            return None
        return build_png_image(sprite_file.sprites[0])
    except Exception:
        return None


def sprite_area_to_png(data: bytes) -> Optional[bytes]:
    """The first sprite in *data* (see sprite_area_to_png_image), as
    standalone PNG bytes -- or None on the same conditions as that
    function, or if PNG encoding itself fails."""
    image = sprite_area_to_png_image(data)
    if image is None:
        return None
    try:
        return encode_png(image)
    except Exception:
        return None
