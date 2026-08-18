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
is present.
"""

from __future__ import annotations

from typing import Optional

try:
    from riscos_sprites import SpriteFile
    from riscos_sprites.png import (
        COLOUR_TYPE_PALETTE,
        COLOUR_TYPE_RGB,
        COLOUR_TYPE_RGBA,
        PngImage,
        build_png_image,
        encode_png,
        raw_scanline_bytes,
    )
except ImportError:  # pragma: no cover - exercised by CI without the extra
    SpriteFile = None
    PngImage = None
    build_png_image = None
    encode_png = None
    raw_scanline_bytes = None
    COLOUR_TYPE_RGB = 2
    COLOUR_TYPE_PALETTE = 3
    COLOUR_TYPE_RGBA = 6


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
