"""Card matching. Pure: no network, no filesystem beyond the two loaders
(:func:`load_cards`, :func:`load_common_words`).

The seam is :func:`scan`: raw transcript text and an in-memory card index in,
ordered mentions out. Everything that decides *which card was said* lives here.
"""

from __future__ import annotations

import dataclasses
import functools
import gzip
import json
import os
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
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


COMMON_WORDS_PATH = Path(__file__).resolve().parent / "data" / "common_words.txt"


def load_common_words(path: str | os.PathLike[str] = COMMON_WORDS_PATH) -> frozenset[str]:
    """Read a word list (one word per line, ``#`` comments) as normalized words.

    Entries are normalized the same way card names are, so the list's "clears"
    and "angels" line up with the card keys "clear" and "angel". Entries that
    normalize to nothing or to several words (``mm-hmm``) are dropped.
    """
    words = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            word = normalize(line)
            if word and " " not in word:
                words.add(word)
    return frozenset(words)


@functools.cache
def bundled_common_words() -> frozenset[str]:
    """The bundled ``data/common_words.txt``, read once per process."""
    return load_common_words(COMMON_WORDS_PATH)


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
    include: Sequence[Card | str | tuple[str, Card]] = (),
    fuzzy_threshold=88,
    common_words: Collection[str] | None = None,
) -> ScanResult:
    """Find every card mentioned in ``transcript_text``.

    Exact pass: over each run of segments, at each word take the longest
    normalized card name that starts there, consume its words, move on.

    Every mention then gets its confidence rule applied (:func:`is_doubtful`):
    doubtful ones come back with ``flagged=True`` and are never downloaded.
    ``common_words`` is the normalized word list behind that rule; None means
    the bundled ``data/common_words.txt``.

    ``include`` lists cards the user has accepted: names present in ``cards``,
    :class:`Card` objects (which need not be in ``cards``, e.g. a joke-set card
    resolved through Scryfall), or ``(text, Card)`` pairs carrying what the user
    typed. Every mention of an included card is confident, and an included card
    never mentioned is appended after the transcript mentions, with no
    timestamp and ``matched_text`` set to the include text (the Oracle name for
    a bare Card).

    ``decklists``, ``nicknames`` and ``fuzzy_threshold`` are part of the seam
    for later passes and are not used yet.
    """
    lookup = build_lookup(cards)
    mentions: list[Mention] = []
    for run in _runs(transcript.parse(transcript_text)):
        mentions.extend(_exact_pass(run, lookup))
    common = bundled_common_words() if common_words is None else common_words
    mentions = [_with_confidence_rule(m, common) for m in mentions]
    return ScanResult(_apply_includes(mentions, include, cards, lookup))


def _runs(segments: list[transcript.Segment]) -> list[list[transcript.Segment]]:
    """Group segments that a card name may straddle.

    Consecutive timed segments (SRT/VTT cues) form one run: cue breaks fall
    wherever the captioner wrapped, often mid-name ("Cloudfin" / "Raptor").
    Untimed segments are plain-text paragraphs, deliberate breaks, so each
    stands alone.
    """
    runs: list[list[transcript.Segment]] = []
    for segment in segments:
        if runs and segment.start is not None and runs[-1][-1].start is not None:
            runs[-1].append(segment)
        else:
            runs.append([segment])
    return runs


def _exact_pass(run: list[transcript.Segment], lookup: Lookup) -> list[Mention]:
    # One word stream over the whole run, each word remembering the segment it
    # came from, so a match can cross a cue break and still take the timestamp
    # of the cue it starts in.
    text = " ".join(segment.text for segment in run)
    words: list[tuple[str, int, int]] = []
    starts: list[str | None] = []
    offset = 0
    for segment in run:
        for word, lo, hi in _words(segment.text):
            words.append((word, lo + offset, hi + offset))
            starts.append(segment.start)
        offset += len(segment.text) + 1
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
                        timestamp=starts[i],
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


# --- confidence --------------------------------------------------------------

# Confidence levels a mention can carry. Only "exact" is produced so far; the
# fuzzy pass (#6), decklists (#7) and nicknames (#8) set the others, and
# is_doubtful already knows what each one means.
EXACT, DECKLIST, NICKNAME, FUZZY = "exact", "decklist", "nickname", "fuzzy"
ALWAYS_CONFIDENT = frozenset({DECKLIST, NICKNAME})
ALWAYS_FLAGGED = frozenset({FUZZY})


def is_basic_land(card: Card) -> bool:
    return "Basic" in card.type_line


def is_doubtful(mention: Mention, common_words: Collection[str]) -> bool:
    """The confidence rule: should this mention be held back for review?

    - fuzzy matches always are; decklist and nickname matches never are;
    - exact matches of a basic land never are ("island" in a Magic video is Island);
    - an exact match is doubtful when the name it matched on (the card name or
      the face name, normalized) is a single common English word: "hope",
      "opt", or "take" from Give // Take. Multi-word exact matches are confident.
    """
    if mention.confidence in ALWAYS_FLAGGED:
        return True
    if mention.confidence in ALWAYS_CONFIDENT:
        return False
    if is_basic_land(mention.card):
        return False
    key = normalize(mention.matched_text)
    return " " not in key and key in common_words


def _with_confidence_rule(mention: Mention, common_words: Collection[str]) -> Mention:
    flagged = is_doubtful(mention, common_words)
    if flagged == mention.flagged:
        return mention
    return dataclasses.replace(mention, flagged=flagged)


def _resolve_include(
    item: Card | str | tuple[str, Card], cards: Mapping[str, Card], lookup: Lookup
) -> tuple[Card, str]:
    """``(card, matched_text)`` for one include entry."""
    if isinstance(item, Card):
        return item, item.name
    if isinstance(item, tuple):
        text, card = item
        return card, text
    card = cards.get(item) or lookup.by_name.get(normalize(item))
    if card is None:
        raise KeyError(f"included card not in the card index: {item!r}")
    return card, item


def _apply_includes(
    mentions: list[Mention],
    include: Sequence[Card | str | tuple[str, Card]],
    cards: Mapping[str, Card],
    lookup: Lookup,
) -> list[Mention]:
    if isinstance(include, (str, Card)):
        include = [include]
    resolved = [_resolve_include(item, cards, lookup) for item in include]
    if not resolved:
        return mentions
    included = {card.name for card, _ in resolved}
    out = [
        dataclasses.replace(m, flagged=False) if m.flagged and m.card.name in included else m
        for m in mentions
    ]
    seen = {m.card.name for m in out}
    for card, text in resolved:
        if card.name not in seen:
            seen.add(card.name)
            out.append(
                Mention(
                    card=card,
                    matched_text=text,
                    timestamp=None,
                    confidence=EXACT,
                    flagged=False,
                    context="",
                )
            )
    return out
