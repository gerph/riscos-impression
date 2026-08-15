"""EPS (Encapsulated PostScript) embedded objects.

See docs/impression-documents.xml, "EPS embedded object data". Unlike
the other formats/ modules, this one is fully confirmed from the
Impression-specific conversion source, not general RISC OS knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass

from riscos_impression import binary

_LENGTH_OFFSET = 12
_NAME_OFFSET = 68


@dataclass(frozen=True)
class EPSObject:
    """An embedded EPS object's source name and raw EPS content. Bytes
    0-11, 16-51, and 52-67 of the surrounding on-disk header are not
    read here, matching the conversion source; see
    docs/impression-documents.xml for why the last of those ranges in
    particular cannot be recovered at all."""

    name: str
    data: bytes

    @classmethod
    def from_bytes(cls, raw: bytes) -> "EPSObject":
        length = binary.u32(raw, _LENGTH_OFFSET)
        name, _ = binary.nul_string(raw, _NAME_OFFSET)
        name_field_size = (len(name) + 1 + 3) & ~3  # NUL-terminated, padded to 4 bytes
        content_offset = _NAME_OFFSET + name_field_size
        return cls(name=name, data=raw[content_offset : content_offset + length])
