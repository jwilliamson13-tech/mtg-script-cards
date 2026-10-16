"""Turn raw transcript text into timed segments.

Formats are sniffed from content, not the file extension: plain text, SRT and
WebVTT. Caption formats give one segment per cue, stamped with the cue start.
YouTube-style rolling captions, where every cue repeats the line before it,
are de-duplicated so each spoken line appears once.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Segment:
    start: str | None
    """Segment start as ``HH:MM:SS.mmm``, or None when the format has no timing."""
    text: str


# A timestamp as either format writes it: ``00:01:02.345`` (VTT), ``00:01:02,345``
# (SRT), or ``01:02.345`` (VTT may leave out the hours).
_TIME = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_TIMING_RE = re.compile(rf"^\s*({_TIME})\s*-->\s*{_TIME}")
_SRT_INDEX_RE = re.compile(r"^\s*\d+\s*$")


def sniff(text: str) -> str:
    """Return ``"vtt"``, ``"srt"`` or ``"txt"`` from the content alone.

    VTT starts with ``WEBVTT``. SRT starts with a cue number on its own line
    followed by a ``-->`` timing line. Anything else is plain text.
    """
    text = text.lstrip("﻿")
    if text.startswith("WEBVTT"):
        return "vtt"
    lines = text.lstrip().splitlines()[:2]
    if len(lines) == 2 and _SRT_INDEX_RE.match(lines[0]) and _TIMING_RE.match(lines[1]):
        return "srt"
    return "txt"


def parse(text: str) -> list[Segment]:
    """Split a transcript into segments, in order."""
    text = text.lstrip("﻿")
    fmt = sniff(text)
    if fmt == "txt":
        return _parse_plain(text)
    return _parse_captions(text)


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


# --- SRT / VTT ---------------------------------------------------------------


@dataclass(slots=True)
class _Cue:
    start: str
    lines: list[str] = field(default_factory=list)


_TAG_RE = re.compile(r"<[^>]*>")  # <00:00:00.400>, <c>, </c>, <i>, <font ...>, <v Speaker>
_ASS_TAG_RE = re.compile(r"\{\\[^}]*\}")  # {\an8} positioning codes some SRT exporters leave in
_SKIPPED_BLOCKS = ("NOTE", "STYLE", "REGION")


def _parse_captions(text: str) -> list[Segment]:
    cues = [cue for cue in _read_cues(text) if cue.lines]
    if _is_rolling(cues):
        return _dedupe_rolling(cues)
    return [Segment(cue.start, " ".join(cue.lines)) for cue in cues]


def _read_cues(text: str) -> list[_Cue]:
    """Cues of an SRT or VTT file, with markup stripped and blank lines dropped.

    Line-driven rather than block-driven: rolling captions put a line holding a
    single space inside a cue, which a split on blank lines would cut in two.
    A timing line opens a cue; the line just before it (SRT number, VTT cue id)
    is an identifier, not text. The VTT header and NOTE/STYLE/REGION blocks
    come before or between cues and run until a truly empty line.
    """
    cues: list[_Cue] = []
    cue: _Cue | None = None
    skipping = False
    after_empty = True
    last_text_index = -2  # input line number of the most recent text line kept
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, raw in enumerate(lines):
        timing = _TIMING_RE.match(raw)
        if timing:
            if cue is not None and cue.lines and last_text_index == index - 1:
                cue.lines.pop()  # this cue's identifier, read as the last cue's text
            cue = _Cue(_clock(timing.group(1)))
            cues.append(cue)
            skipping = False
        elif raw == "":
            skipping = False
        elif cue is None or skipping:
            pass
        elif after_empty and (raw.split() or [""])[0] in _SKIPPED_BLOCKS:
            skipping = True
        else:
            line = _clean(raw)
            if line:
                cue.lines.append(line)
                last_text_index = index
        after_empty = raw == ""
    return cues


def _clean(line: str) -> str:
    """Strip inline timing and styling tags, unescape entities, squeeze spaces."""
    line = _ASS_TAG_RE.sub("", _TAG_RE.sub("", line))
    return " ".join(html.unescape(line).split())


def _clock(stamp: str) -> str:
    """``"1:02,5"`` -> ``"00:01:02.500"``: always ``HH:MM:SS.mmm``."""
    clock, millis = stamp.replace(",", ".").split(".")
    parts = [int(p) for p in clock.split(":")]
    hours, minutes, seconds = [0] * (3 - len(parts)) + parts
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis.ljust(3, '0')}"


def _overlap(previous: list[str], current: list[str]) -> int:
    """How many leading lines of ``current`` repeat the trailing lines of ``previous``."""
    for k in range(min(len(previous), len(current)), 0, -1):
        if previous[-k:] == current[:k]:
            return k
    return 0


def _is_rolling(cues: list[_Cue]) -> bool:
    """True for YouTube-style rolling captions.

    There every cue opens by repeating the previous cue's last line: the line
    scrolls up a slot while the new one arrives below, and a 10 ms "hold" cue in
    between repeats it once more. Ordinary exports (an editor's SRT, Whisper
    output) almost never repeat a line across cues, so de-duplication only runs
    when most cues do, and a host who really says "Island. Island." in two
    ordinary cues keeps both mentions.
    """
    pairs = list(zip(cues, cues[1:]))
    if not pairs:
        return False
    repeats = sum(1 for a, b in pairs if _overlap(a.lines, b.lines))
    return repeats * 2 >= len(pairs)


def _dedupe_rolling(cues: list[_Cue]) -> list[Segment]:
    """Keep only the lines each cue adds, stamped with the start of that cue.

    Each line then appears once, at the first cue that showed it: for YouTube
    captions, the cue whose inline word timings begin with that line.
    """
    segments: list[Segment] = []
    previous: list[str] = []
    for cue in cues:
        new = cue.lines[_overlap(previous, cue.lines) :]
        if new:
            segments.append(Segment(cue.start, " ".join(new)))
        previous = cue.lines
    return segments
