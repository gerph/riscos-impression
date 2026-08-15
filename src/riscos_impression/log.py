"""Structured logging for best-effort conversion.

Every place a converter can't do a fully faithful job -- an unimplemented
picture format, an unsupported numbering style, a control code with no
reproducible effect in the target format -- records that fact here rather
than silently dropping it or raising. See PLAN.md's "Ground rules".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class LogLevel(Enum):
    #: Purely informational; no loss of fidelity.
    INFO = "info"
    #: A reasonable approximation was used in place of the exact effect.
    BEST_EFFORT = "best_effort"
    #: Nothing was produced for this; the source data is simply not
    #: representable (yet, or at all) in the target format.
    UNSUPPORTED = "unsupported"
    #: Something that should have worked did not; investigate.
    ERROR = "error"


@dataclass(frozen=True)
class LogEntry:
    level: LogLevel
    area: str
    message: str
    location: Optional[str] = None


class ConversionLog:
    """An ordered record of conversion events, groupable into a human
    -readable summary."""

    def __init__(self) -> None:
        self.entries: list[LogEntry] = []

    def _add(
        self, level: LogLevel, area: str, message: str, location: Optional[str]
    ) -> None:
        self.entries.append(LogEntry(level, area, message, location))

    def info(self, area: str, message: str, location: Optional[str] = None) -> None:
        self._add(LogLevel.INFO, area, message, location)

    def best_effort(
        self, area: str, message: str, location: Optional[str] = None
    ) -> None:
        self._add(LogLevel.BEST_EFFORT, area, message, location)

    def unsupported(
        self, area: str, message: str, location: Optional[str] = None
    ) -> None:
        self._add(LogLevel.UNSUPPORTED, area, message, location)

    def error(self, area: str, message: str, location: Optional[str] = None) -> None:
        self._add(LogLevel.ERROR, area, message, location)

    def __len__(self) -> int:
        return len(self.entries)

    def has_errors(self) -> bool:
        return any(entry.level is LogLevel.ERROR for entry in self.entries)

    def counts(self) -> dict[LogLevel, int]:
        counts = {level: 0 for level in LogLevel}
        for entry in self.entries:
            counts[entry.level] += 1
        return counts

    def summary(self) -> str:
        """A human-readable report: totals by level, then every distinct
        (area, message) grouped with an occurrence count and a couple of
        example locations."""
        lines = [f"Conversion log: {len(self.entries)} entries"]
        counts = self.counts()
        for level in LogLevel:
            if counts[level]:
                lines.append(f"  {level.value}: {counts[level]}")

        grouped: dict[tuple, list[Optional[str]]] = defaultdict(list)
        for entry in self.entries:
            grouped[(entry.level, entry.area, entry.message)].append(entry.location)

        for level in LogLevel:
            keys = [key for key in grouped if key[0] is level]
            if not keys:
                continue
            lines.append("")
            lines.append(f"{level.value.upper()}:")
            for level_, area, message in keys:
                locations = grouped[(level_, area, message)]
                suffix = f" (x{len(locations)})" if len(locations) > 1 else ""
                lines.append(f"  [{area}] {message}{suffix}")
                examples = [loc for loc in locations if loc][:3]
                if examples:
                    lines.append(f"      e.g. {', '.join(examples)}")

        return "\n".join(lines)
