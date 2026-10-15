"""Card matching. Pure: no network, no filesystem beyond :func:`load_cards`.

The seam is :func:`scan`: raw transcript text and an in-memory card index in,
ordered mentions out. Everything that decides *which card was said* lives here.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import transcript


@dataclass(frozen=True, slots=True)
class Card:
    name: str
    faces: list[str]
    scryfall_id: str
    scryfall_url: str
    image_urls: list[str] = field(default_factory=list)
    type_line: str = ""


CardIndex = dict[str, Card]
"""Cards keyed by Oracle name (``"Fire // Ice"`` for multi-face cards)."""


def load_cards(path: str | os.PathLike[str]) -> CardIndex:
    """Load ``data/cards.json`` (or its ``.gz`` twin) into a name-keyed index."""
    p = Path(path)
    if not p.exists() and p.with_name(p.name + ".gz").exists():
        p = p.with_name(p.name + ".gz")
    opener = gzip.open if p.name.endswith(".gz") else open
    with opener(p, "rt", encoding="utf-8") as fh:
        entries = json.load(fh)
    return {entry["name"]: Card(**entry) for entry in entries}


# --- normalization ---------------------------------------------------------

# A word is a run of letters/digits, with apostrophes kept inside it so that
# "Judge's" stays one word. Hyphens, commas, slashes and the rest split words,
# so "Kiki-Jiki" reads the same as the spoken "kiki jiki".
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_FOLD_MAP = str.maketrans({"æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ß": "ss"})


def fold_ascii(text: str) -> str:
    """Strip accents and fold ligatures: ``"Lim-Dûl's Æther"`` -> ``"Lim-Dul's aether"``."""
    text = text.translate(_FOLD_MAP)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalize_word(word: str) -> str:
    word = fold_ascii(word).lower().replace("'", "").replace("’", "")
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def _words(text: str) -> list[tuple[str, int, int]]:
    """Normalized words of ``text`` with their character span in the original."""
    out = []
    for m in _WORD_RE.finditer(text):
        word = _normalize_word(m.group())
        if word:
            out.append((word, m.start(), m.end()))
    return out


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, fold accents and a trailing plural on each word.

    Applied identically to card names and transcript text, so
    ``"Jace, the Mind Sculptor"`` and ``"jace the mind sculptors"`` both become
    ``"jace the mind sculptor"``.
    """
    return " ".join(word for word, _, _ in _words(text))


# --- lookup ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lookup:
    """Normalized name -> card, plus the longest name length in words."""

    by_name: dict[str, Card]
    max_words: int


def build_lookup(cards: Mapping[str, Card] | Iterable[Card]) -> Lookup:
    """Key every card by its normalized full name and each normalized face name.

    A card's own name wins over another card's face name when the two collide
    (a face named "Stomp" never shadows a card named "Stomp"). Within the same
    tier the first card seen keeps the key.
    """
    values = list(cards.values()) if isinstance(cards, Mapping) else list(cards)
    by_name: dict[str, Card] = {}
    for card in values:
        key = normalize(card.name)
        if key:
            by_name.setdefault(key, card)
    for card in values:
        for face in card.faces:
            key = normalize(face)
            if key:
                by_name.setdefault(key, card)
    max_words = max((key.count(" ") + 1 for key in by_name), default=0)
    return Lookup(by_name, max_words)


# --- scan ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mention:
    card: Card
    matched_text: str
    timestamp: str | None
    confidence: str  # "exact" | "decklist" | "nickname" | "fuzzy"
    flagged: bool
    context: str
    score: float | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    mentions: list[Mention]
    """Every mention, in order of appearance in the transcript."""

    def confident(self) -> list[Mention]:
        return [m for m in self.mentions if not m.flagged]

    def flagged(self) -> list[Mention]:
        return [m for m in self.mentions if m.flagged]

    def ordered_cards(self) -> list[Card]:
        """Distinct confident cards in order of first appearance: the PNG numbering."""
        seen: set[str] = set()
        out: list[Card] = []
        for m in self.confident():
            if m.card.name not in seen:
                seen.add(m.card.name)
                out.append(m.card)
        return out


CONTEXT_WORDS = 6


def scan(
    transcript_text: str,
    cards: CardIndex,
    *,
    decklists=(),
    nicknames=None,
    include=(),
    fuzzy_threshold=88,
) -> ScanResult:
    """Find every card mentioned in ``transcript_text``.

    Exact pass: within each transcript segment, at each word take the longest
    normalized card name that starts there, consume its words, move on.
    ``decklists``, ``nicknames``, ``include`` and ``fuzzy_threshold`` are part of
    the seam for later passes and are not used yet.
    """
    lookup = build_lookup(cards)
    mentions: list[Mention] = []
    for segment in transcript.parse(transcript_text):
        mentions.extend(_exact_pass(segment, lookup))
    return ScanResult(mentions)


def _exact_pass(segment: transcript.Segment, lookup: Lookup) -> list[Mention]:
    text = segment.text
    words = _words(text)
    tokens = [w for w, _, _ in words]
    found: list[Mention] = []
    i = 0
    while i < len(tokens):
        for n in range(min(lookup.max_words, len(tokens) - i), 0, -1):
            card = lookup.by_name.get(" ".join(tokens[i : i + n]))
            if card is not None:
                start, end = words[i][1], words[i + n - 1][2]
                lo = words[max(0, i - CONTEXT_WORDS)][1]
                hi = words[min(len(words), i + n + CONTEXT_WORDS) - 1][2]
                found.append(
                    Mention(
                        card=card,
                        matched_text=text[start:end],
                        timestamp=segment.start,
                        confidence="exact",
                        flagged=False,
                        context=" ".join(text[lo:hi].split()),
                    )
                )
                i += n
                break
        else:
            i += 1
    return found
