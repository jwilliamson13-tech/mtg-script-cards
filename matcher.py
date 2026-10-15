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

from rapidfuzz import fuzz, process

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
    fuzzy_threshold=86,
    common_words: Collection[str] | None = None,
) -> ScanResult:
    """Find every card mentioned in ``transcript_text``.

    Exact pass: over each run of segments, at each word take the longest
    normalized card name that starts there, consume its words, move on.

    Fuzzy pass (:func:`_fuzzy_pass`): one-to-four-word windows over the words
    the exact pass left, compared space-stripped against space-stripped card
    names of eight or more characters; ``fuzzy_threshold`` is the minimum
    ``rapidfuzz.fuzz.ratio`` score. Fuzzy mentions carry ``confidence="fuzzy"``
    and their ``score``, and are always flagged. The default of 86 was tuned on
    the fixture transcript (issue #6): the highest value that still surfaces
    every misheard card the fuzzy pass can reach ("Mutault" scores 87.5,
    "thasa god of the" 86.7).

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

    ``decklists`` and ``nicknames`` are part of the seam for later passes and
    are not used yet.
    """
    lookup = build_lookup(cards)
    common = bundled_common_words() if common_words is None else common_words
    fuzzy_names = FuzzyNames(lookup, fuzzy_threshold)
    mentions: list[Mention] = []
    for run in _runs(transcript.parse(transcript_text)):
        found = _exact_pass(run, lookup)
        mentions.extend(_fuzzy_pass(run, found, fuzzy_names, common))
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


# --- fuzzy pass --------------------------------------------------------------

FUZZY_MIN_CHARS = 8
"""Shortest eligible card name, normalized and space-stripped. Shorter names
("Opt", "Hope", "Delay") are left to the exact pass: at that length one wrong
letter is a different word."""
FUZZY_MAX_WORDS = 4
_ANNOTATION_RE = re.compile(r"\[[^\[\]]{1,40}\]")
_BREAK_RE = re.compile(r">>|[.?!]")


class FuzzyNames:
    """The card names the fuzzy pass may land on, space-stripped, grouped by length.

    Every normalized card name and face name of at least ``FUZZY_MIN_CHARS``
    characters once spaces are removed, except the joined full name of a
    multi-face card ("fire ice"): people say one face. Built once per
    :func:`scan`, for one threshold, and remembers every query it has answered:
    a transcript says "the island" and "i think" hundreds of times.
    """

    def __init__(self, lookup: Lookup, threshold: float) -> None:
        self.threshold = threshold
        by_length: dict[int, list[tuple[str, Card]]] = {}
        seen: set[str] = set()
        for key, card in lookup.by_name.items():
            if len(card.faces) > 1 and key == normalize(card.name):
                continue
            stripped = key.replace(" ", "")
            if len(stripped) < FUZZY_MIN_CHARS or stripped in seen:
                continue
            seen.add(stripped)
            by_length.setdefault(len(stripped), []).append((stripped, card))
        self._by_length = by_length
        self._choices: dict[int, tuple[list[str], list[Card]]] = {}
        self._answers: dict[str, tuple[str, Card, float] | None] = {}

    def best(self, query: str) -> tuple[str, Card, float] | None:
        """``(stripped name, card, score)`` of the closest name scoring at least
        the threshold with ``fuzz.ratio``, or None."""
        if query in self._answers:
            return self._answers[query]
        answer = None
        names, cards = self._candidates(len(query))
        if names:
            hit = process.extractOne(
                query, names, scorer=fuzz.ratio, score_cutoff=self.threshold
            )
            if hit is not None:
                name, score, i = hit
                answer = (name, cards[i], score)
        self._answers[query] = answer
        return answer

    def _candidates(self, length: int) -> tuple[list[str], list[Card]]:
        # fuzz.ratio is 100 * (1 - indel / (len1 + len2)), and the indel distance
        # is at least the length difference, so names whose length is too far
        # from the query's can never reach the threshold. Skipping them is most
        # of what keeps a full transcript fast.
        if length not in self._choices:
            slack = 1 - self.threshold / 100
            names: list[str] = []
            cards: list[Card] = []
            for size, entries in self._by_length.items():
                if abs(size - length) <= slack * (size + length) + 1e-9:
                    names.extend(name for name, _ in entries)
                    cards.extend(card for _, card in entries)
            self._choices[length] = (names, cards)
        return self._choices[length]


def _word_stream(
    run: list[transcript.Segment],
) -> tuple[str, list[tuple[str, int, int]], list[str | None]]:
    """The run's text joined by spaces, its normalized words with character
    spans in that text, and the start time of the segment each word came from.
    Built the same way as in :func:`_exact_pass`."""
    text = " ".join(segment.text for segment in run)
    words: list[tuple[str, int, int]] = []
    starts: list[str | None] = []
    offset = 0
    for segment in run:
        for word, lo, hi in _words(segment.text):
            words.append((word, lo + offset, hi + offset))
            starts.append(segment.start)
        offset += len(segment.text) + 1
    return text, words, starts


def _locate(tokens: list[str], key: list[str], cursor: int) -> int | None:
    """First index at or after ``cursor`` where ``key`` occurs in ``tokens``."""
    n = len(key)
    for i in range(cursor, len(tokens) - n + 1):
        if tokens[i : i + n] == key:
            return i
    return None


def _fuzzy_pass(
    run: list[transcript.Segment],
    found: list[Mention],
    names: FuzzyNames,
    common_words: Collection[str],
) -> list[Mention]:
    """Add fuzzy mentions to the exact mentions ``found`` in ``run``; return
    all of them in order of appearance.

    Windows of one to four consecutive words are joined without spaces
    ("thought seize" -> "thoughtseize") and scored with ``fuzz.ratio``
    against every eligible name (:class:`FuzzyNames`). ``fuzz.ratio`` and not
    rapidfuzz's default ``WRatio``: WRatio rewards a window that is merely a
    substring of a name ("spell" in every "Spell ..." card), while ``ratio``
    is plain edit similarity that also punishes a length mismatch, which is
    what a mishearing of the whole name looks like.

    The rules, in order:

    1. A window never includes a captioner's annotation in square brackets
       ("[laughter]" is not Slaughter) and never spans a speaker change
       (``>>``) or the end of a sentence (``.``, ``?``, ``!``).
    2. A one-word window that is itself a common English word is never
       fuzzed: the captioner heard a real word ("information" is not
       Formation). Several common words together still are ("bitter blossom").
    3. A window may not include words of an exact match, with one exception:
       it may cover exactly one whole exact match plus at least one word next
       to it, when the exact match is a piece of the longer name it scores
       against (the match's space-stripped words occur inside that name). That
       longer fuzzy mention then replaces the exact one. This is how
       "spell stutter sprite" becomes Spellstutter Sprite although the exact
       pass already took "spell stutter" as the card Spell Stutter, and
       "omen speaker" becomes Omenspeaker rather than Omen.
    4. A window keeps its best name if that scores at least the threshold.
    5. Overlapping windows are settled best score first; at equal score the
       longer window wins, then the earlier one. So "bitter blossom" (100)
       beats "bitter blossom to" (93), and a window that only reaches the
       threshold by borrowing a neighbour's letters loses to a cleaner one.

    Exact mentions are found again in the word stream by their words, in
    order: the exact pass consumes left to right, so the first occurrence of a
    match's words at or after the previous match is where it was taken.
    """
    text, words, starts = _word_stream(run)
    tokens = [w for w, _, _ in words]

    # Where each exact mention sits: owner[i] is the index in `found` of the
    # mention that consumed word i.
    owner: list[int | None] = [None] * len(tokens)
    position: list[int] = []
    span: list[tuple[int, int] | None] = []
    cursor = 0
    for k, mention in enumerate(found):
        key = [w for w, _, _ in _words(mention.matched_text)]
        at = _locate(tokens, key, cursor) if key else None
        if at is None:
            position.append(cursor)
            span.append(None)
            continue
        for i in range(at, at + len(key)):
            owner[i] = k
        position.append(at)
        span.append((at, at + len(key)))
        cursor = at + len(key)

    # Words inside [brackets] are annotations, not speech; breaks[k] is True
    # when no window may span the gap between word k and word k + 1.
    annotations = [m.span() for m in _ANNOTATION_RE.finditer(text)]
    noise: list[bool] = []
    a = 0
    for _, lo, _ in words:
        while a < len(annotations) and annotations[a][1] <= lo:
            a += 1
        noise.append(a < len(annotations) and annotations[a][0] <= lo)
    breaks = [
        bool(_BREAK_RE.search(text, words[k][2], words[k + 1][1]))
        for k in range(len(words) - 1)
    ]

    candidates: list[tuple[float, int, int, Card]] = []  # (score, start, end, card)
    for i in range(len(tokens)):
        for j in range(i + 1, min(i + FUZZY_MAX_WORDS, len(tokens)) + 1):
            if noise[j - 1] or (j - 1 > i and breaks[j - 2]):
                break
            if j == i + 1 and tokens[i] in common_words:
                continue
            inside = {owner[x] for x in range(i, j) if owner[x] is not None}
            if len(inside) > 1:
                break
            exact = span[inside.pop()] if inside else None
            if exact is not None:
                lo, hi = exact
                if lo < i:
                    break  # starts inside an exact match
                if hi > j or (lo, hi) == (i, j):
                    continue  # ends inside it, or is exactly it
            hit = names.best("".join(tokens[i:j]))
            if hit is None:
                continue
            name, card, score = hit
            if exact is not None:
                piece = "".join(tokens[exact[0] : exact[1]])
                if piece not in name or len(piece) == len(name):
                    continue
            candidates.append((score, i, j, card))

    candidates.sort(key=lambda c: (-c[0], -(c[2] - c[1]), c[1]))
    taken = [False] * len(tokens)
    fuzzy: list[tuple[int, Mention]] = []
    absorbed: set[int] = set()
    for score, i, j, card in candidates:
        if any(taken[i:j]):
            continue
        for x in range(i, j):
            taken[x] = True
            if owner[x] is not None:
                absorbed.add(owner[x])
        lo = words[max(0, i - CONTEXT_WORDS)][1]
        hi = words[min(len(words), j + CONTEXT_WORDS) - 1][2]
        mention = Mention(
            card=card,
            matched_text=text[words[i][1] : words[j - 1][2]],
            timestamp=starts[i],
            confidence=FUZZY,
            flagged=False,
            context=" ".join(text[lo:hi].split()),
            score=score,
        )
        fuzzy.append((i, mention))

    kept = [(position[k], m) for k, m in enumerate(found) if k not in absorbed]
    return [m for _, m in sorted(kept + fuzzy, key=lambda pair: pair[0])]


# --- confidence --------------------------------------------------------------

# Confidence levels a mention can carry. The exact and fuzzy passes produce
# "exact" and "fuzzy"; decklists (#7) and nicknames (#8) set the others, and
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
