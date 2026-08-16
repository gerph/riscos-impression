"""RISC OS "Latin1" (alphabet 101) text decoding.

See docs/impression-documents.xml, "Text and character encoding": every
text field in an Impression document -- story content, colour/style/
font names, merge field names, and text within embedded DrawFile
pictures -- uses this alphabet, not plain ISO-8859-1/Latin-1 (bytes
0x80-0x9F decode very differently) and not Windows-1252 either (a
related but distinct remapping of the same byte range).

Bytes 0x00-0x7F match ASCII and bytes 0xA0-0xFF match ISO-8859-1
exactly (byte value == Unicode code point); only the C1 control range,
0x80-0x9F, needs a lookup. This table is reproduced from the
independent python-codecs-riscos project's own definition of RISC OS
alphabet 101 (https://github.com/gerph/python-codecs-riscos), not
derived from the Impression conversion source (which, being RISC OS
software itself, never needed to translate out of its own native
alphabet).
"""

from __future__ import annotations

from typing import Optional

#: RISC OS's own remapping of the C1 control-code range to visible
#: characters: accented capitals, typographic quotes and dashes, the
#: ellipsis, ligatures, and a handful of RISC OS UI glyphs that have no
#: exact Unicode equivalent (those fall back to U+FFFD).
_C1_CHANGES: dict[int, str] = {
    0x80: "€",  # euro
    0x81: "Ŵ",  # W circumflex
    0x82: "ŵ",  # w circumflex
    0x83: "◰",  # resize icon (approximated; no exact Unicode equivalent)
    0x84: "�",  # close icon; no Unicode equivalent
    0x85: "Ŷ",  # Y circumflex
    0x86: "ŷ",  # y circumflex
    0x87: "�",  # unassigned; no Unicode equivalent
    0x88: "⇦",  # left arrow
    0x89: "⇨",  # right arrow
    0x8a: "⇩",  # down arrow
    0x8b: "⇧",  # up arrow
    0x8c: "…",  # ellipsis
    0x8d: "™",  # trademark
    0x8e: "‰",  # per mille
    0x8f: "•",  # bullet
    0x90: "‘",  # left single quotation mark
    0x91: "’",  # right single quotation mark
    0x92: "‹",  # left single guillemet
    0x93: "›",  # right single guillemet
    0x94: "“",  # left double quotation mark
    0x95: "”",  # right double quotation mark
    0x96: "„",  # double low quotation mark
    0x97: "–",  # en dash
    0x98: "—",  # em dash
    0x99: "−",  # minus sign
    0x9a: "Œ",  # OE ligature
    0x9b: "œ",  # oe ligature
    0x9c: "†",  # dagger
    0x9d: "‡",  # double dagger
    0x9e: "ﬁ",  # fi ligature
    0x9f: "ﬂ",  # fl ligature
}


def decode_byte(value: int) -> str:
    """The RISC OS Latin1 (alphabet 101) character for a single byte
    value 0-255."""
    return _C1_CHANGES.get(value, chr(value))


def decode(data: bytes) -> str:
    """Decode a byte string as RISC OS Latin1 (alphabet 101)."""
    return "".join(decode_byte(b) for b in data)


#: Character -> byte, the inverse of _C1_CHANGES. Both 0x84 and 0x87
#: decode to U+FFFD (see _C1_CHANGES); this reverse mapping only keeps
#: one of them (0x87, "unassigned", being the plainer choice), which is
#: fine -- round-tripping U+FFFD back to the *other* original byte was
#: never going to be possible anyway, since the two are indistinguishable
#: once decoded.
_C1_REVERSE: dict[str, int] = {ch: byte for byte, ch in _C1_CHANGES.items()}

#: Used for a character with no RISC OS Latin1 representation at all
#: (this alphabet has no ASCII-substitution convention of its own to
#: fall back to).
_NO_REPRESENTATION = ord("?")


def encode_byte_or_none(ch: str) -> Optional[int]:
    """The RISC OS Latin1 (alphabet 101) byte value for a single
    Unicode character, or None if it has no representation at all
    (unlike encode_byte, this doesn't paper over that with '?' --
    needed by callers, such as font_metrics.py's width lookup, that
    must tell "no such character" apart from "this character happens
    to be byte 0x3F, '?'")."""
    byte = _C1_REVERSE.get(ch)
    if byte is not None:
        return byte
    code = ord(ch)
    if code <= 0xFF and not (0x80 <= code <= 0x9F):
        return code
    return None


def encode_byte(ch: str) -> int:
    """The RISC OS Latin1 (alphabet 101) byte value for a single
    Unicode character, or '?' if it has none at all."""
    byte = encode_byte_or_none(ch)
    return byte if byte is not None else _NO_REPRESENTATION


def encode(text: str) -> bytes:
    """Encode a string as RISC OS Latin1 (alphabet 101) bytes."""
    return bytes(encode_byte(ch) for ch in text)
