"""Turn raw transcript text into timed segments.

Formats are sniffed from content, not the file extension. Only plain text is
parsed so far; SRT and VTT arrive with ticket #4 and keep these shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Segment:
    start: str | None
    """Segment start as ``HH:MM:SS.mmm``, or None when the format has no timing."""
    text: str


def sniff(text: str) -> str:
    """Return ``"txt"``, ``"srt"`` or ``"vtt"``. Stub: everything is plain text for now."""
    return "txt"


def parse(text: str) -> list[Segment]:
    """Split a transcript into segments, in order."""
    text = text.lstrip("﻿")
    fmt = sniff(text)
    if fmt == "txt":
        return _parse_plain(text)
    raise ValueError(f"unsupported transcript format: {fmt}")


_BLANK_LINES = re.compile(r"\n\s*\n")


def _parse_plain(text: str) -> list[Segment]:
    """One segment per paragraph (blank-line separated), lines joined with spaces.

    Paragraphs rather than lines: auto-caption exports wrap mid-sentence, so a
    card name often straddles a line break ("Glen Elendra / Archmage").
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    segments = []
    for para in _BLANK_LINES.split(text):
        joined = " ".join(line.strip() for line in para.split("\n") if line.strip())
        if joined:
            segments.append(Segment(None, joined))
    return segments
