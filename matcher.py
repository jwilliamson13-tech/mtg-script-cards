"""Card matching. Pure: no network, no filesystem beyond the loaders
(:func:`load_cards`, :func:`load_common_words`, :func:`load_nicknames` and the
``bundled_*`` helpers), which only the CLI and tests call. :func:`scan` and
everything it calls work on what they are given.

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
import types
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


NICKNAMES_PATH = Path(__file__).resolve().parent / "data" / "nicknames.json"


def load_nicknames(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Read a nickname file: one JSON object mapping nickname to Oracle name.

    ``None`` reads the bundled ``data/nicknames.json``. Raises ``ValueError``
    (``json.JSONDecodeError`` is one) when the file is not a JSON object.
    Entries come back as written: :func:`nickname_warnings` checks them
    against the card list, and :func:`scan` skips the ones that fail.
    """
    p = NICKNAMES_PATH if path is None else Path(path)
    with open(p, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(
            f'expected one JSON object like {{"trop": "Tropical Island"}}, '
            f"found a {type(data).__name__}"
        )
    return data


@functools.cache
def bundled_nicknames() -> Mapping[str, str]:
    """The bundled ``data/nicknames.json``, read once per process."""
    return types.MappingProxyType(load_nicknames(NICKNAMES_PATH))


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
    """Normalized name -> card, normalized nickname -> card, the longest key
    length in words across both, and the decklist table (:func:`decklist_keys`):
    space-stripped key -> decklist card."""

    by_name: dict[str, Card]
    max_words: int
    nicknames: dict[str, Card] = field(default_factory=dict)
    decklist: dict[str, Card] = field(default_factory=dict)


def build_lookup(
    cards: Mapping[str, Card] | Iterable[Card], nicknames: Mapping[str, str] | None = None
) -> Lookup:
    """Key every card by its normalized full name and each normalized face name.

    A card's own name wins over another card's face name when the two collide
    (a face named "Stomp" never shadows a card named "Stomp"). Within the same
    tier the first card seen keeps the key.

    ``nicknames`` (nickname -> Oracle name) go in a table of their own; entries
    naming no card in ``cards`` are left out (:func:`nickname_warnings` says why).
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
    nicks, _ = _resolve_nicknames(nicknames or {}, values, by_name)
    max_words = max((key.count(" ") + 1 for key in [*by_name, *nicks]), default=0)
    return Lookup(by_name, max_words, nicks)


def _resolve_nicknames(
    nicknames: Mapping[str, str], cards: Iterable[Card], by_name: Mapping[str, Card]
) -> tuple[dict[str, Card], list[str]]:
    """Normalized nickname -> card, and one warning per entry left out.

    The Oracle name is looked up as written, then normalized, so "lightning
    bolt" or a face name like "Delver of Secrets" also works.
    """
    named = {card.name: card for card in cards}
    table: dict[str, Card] = {}
    first_spelling: dict[str, str] = {}
    warnings: list[str] = []
    for nickname, oracle in nicknames.items():
        key = normalize(str(nickname))
        if not key:
            warnings.append(f'nickname "{nickname}" has no letters or digits; skipped')
            continue
        if not isinstance(oracle, str):
            warnings.append(
                f'nickname "{nickname}": expected a card name, found {oracle!r}; skipped'
            )
            continue
        card = named.get(oracle) or by_name.get(normalize(oracle))
        if card is None:
            warnings.append(
                f'nickname "{nickname}": no card named "{oracle}" in the card list; skipped'
            )
            continue
        if key in table:
            if table[key].name != card.name:
                warnings.append(
                    f'nickname "{nickname}" reads the same as "{first_spelling[key]}", '
                    f'which already means {table[key].name}; skipped'
                )
            continue
        table[key] = card
        first_spelling[key] = str(nickname)
    return table, warnings


def nickname_warnings(nicknames: Mapping[str, str], cards: CardIndex) -> list[str]:
    """One message per nickname entry that :func:`scan` will skip, in file order.

    An entry is skipped when its Oracle name is not in ``cards``, when the
    nickname has no letters or digits, or when it repeats an earlier nickname
    (after normalizing) for a different card.
    """
    return _resolve_nicknames(nicknames, cards.values(), build_lookup(cards).by_name)[1]


# --- decklists -------------------------------------------------------------

DECKLIST_MIN_WORD_CHARS = 3
"""Shortest single word of a decklist card name that matches the card on its own."""
DECKLIST_MAX_CARDS_PER_WORD = 20
"""A word found in more card names than this (counting the whole card index) is
Magic vocabulary rather than a name: "mage" (107 cards), "faerie" (38), "jace"
(32), "scion" (29). "thassa" (12) and "ragavan" (2) still stand for a card."""
DECKLIST_MAX_PIECES = 4
"""Most transcript words the exact pass joins to find a decklist key ("snap caster")."""


def _as_decklists(decklists: Iterable[Iterable[str]] | Iterable[str]) -> list[list[str]]:
    """Accept a list of decklists, or one decklist given as a flat list of names."""
    if isinstance(decklists, str):
        return [[decklists]]
    items = list(decklists)
    if any(isinstance(item, str) for item in items):
        return [[item for item in items if isinstance(item, str)]]
    return [list(item) for item in items]


def decklist_cards(
    decklists: Iterable[Iterable[str]] | Iterable[str], cards: Mapping[str, Card], lookup: Lookup
) -> dict[str, Card]:
    """Every card named in ``decklists``, keyed by Oracle name, in list order.

    A name is looked up as written, then normalized, so a face name or
    lowercase works. Names that are no card are skipped here; the CLI warns
    about them (``decklist.unknown_names``).
    """
    deck: dict[str, Card] = {}
    for names in _as_decklists(decklists):
        for name in names:
            card = cards.get(name) or lookup.by_name.get(normalize(name))
            if card is not None:
                deck.setdefault(card.name, card)
    return deck


def distinctive_words(
    deck: Iterable[Card], common_words: Collection[str], by_name: Mapping[str, Card] | None = None
) -> dict[str, Card]:
    """Normalized word -> card, for each word of a decklist card's name that
    can stand for the card on its own: not a common English word, not a word
    of another decklist card's name, at least ``DECKLIST_MIN_WORD_CHARS`` long,
    not a number, and (when ``by_name``, the lookup over the whole card
    index, is given) in at most ``DECKLIST_MAX_CARDS_PER_WORD`` card names.
    "snapcaster" -> Snapcaster Mage, but not "mage" (107 cards), and not
    "jace" when the lists hold two Jaces."""
    owners: dict[str, set[str]] = {}
    first: dict[str, Card] = {}
    for card in deck:
        for word in normalize(card.name).split():
            owners.setdefault(word, set()).add(card.name)
            first.setdefault(word, card)
    words = {
        word: first[word]
        for word, names in owners.items()
        if len(names) == 1
        and word not in common_words
        and len(word) >= DECKLIST_MIN_WORD_CHARS
        and not word.isdigit()
    }
    if by_name:
        named: dict[str, set[str]] = {word: set() for word in words}
        for key, card in by_name.items():
            for word in key.split():
                if word in named:
                    named[word].add(card.name)
        words = {w: c for w, c in words.items() if len(named[w]) <= DECKLIST_MAX_CARDS_PER_WORD}
    return words


def decklist_keys(
    deck: Iterable[Card], common_words: Collection[str], by_name: Mapping[str, Card] | None = None
) -> dict[str, Card]:
    """The exact pass's decklist table, keyed by space-stripped text.

    Each decklist card's full name and face names with the spaces taken out,
    when at least ``FUZZY_MIN_CHARS`` long, and its distinctive words
    (:func:`distinctive_words`). The exact pass joins up to
    ``DECKLIST_MAX_PIECES`` transcript words and looks the result up here, so
    "spell stutter" is Spellstutter Sprite, "snap caster" is Snapcaster Mage
    and "omen speaker" is Omenspeaker when those are in a list.
    """
    deck = list(deck)
    table: dict[str, Card] = {}
    for card in deck:
        # A multi-face card by each face, never by the faces run together.
        for name in card.faces if len(card.faces) > 1 else [card.name]:
            key = normalize(name).replace(" ", "")
            if len(key) >= FUZZY_MIN_CHARS:
                table.setdefault(key, card)
    for word, card in distinctive_words(deck, common_words, by_name).items():
        table.setdefault(word, card)
    return table


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
    decklist_fuzzy_threshold=77,
    common_words: Collection[str] | None = None,
) -> ScanResult:
    """Find every card mentioned in ``transcript_text``.

    Exact pass: over each run of segments, at each word take the longest
    normalized card name or nickname that starts there, consume its words,
    move on. Nickname matches carry ``confidence="nickname"``.

    ``nicknames`` maps nickname to Oracle name; None or ``{}`` means no
    nicknames (the CLI passes :func:`bundled_nicknames`). Entries whose
    Oracle name is not in ``cards`` are skipped (:func:`nickname_warnings`
    lists them). When a nickname and a real card name are the same words, the
    nickname wins: someone put it in the nickname file on purpose. A longer
    real name still beats a shorter nickname ("jace the mind sculptor" over a
    nickname "jace").

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
    an empty list (the CLI passes :func:`bundled_common_words`). ``scan``
    reads no files: the caller loads both lists.

    ``include`` lists cards the user has accepted: names present in ``cards``,
    :class:`Card` objects (which need not be in ``cards``, e.g. a joke-set card
    resolved through Scryfall), or ``(text, Card)`` pairs carrying what the user
    typed. Every mention of an included card is confident, and an included card
    never mentioned is appended after the transcript mentions, with no
    timestamp and ``matched_text`` set to the include text (the Oracle name for
    a bare Card).

    ``decklists`` is a sequence of decklists, each a sequence of Oracle names
    (a face name or a lowercase name also works; names that are no card in
    ``cards`` are skipped). Decklists boost matching and never filter it:
    cards outside every list still match under the rules above. For a card in
    any list:

    - every mention of it, exact, nickname or fuzzy, carries
      ``confidence="decklist"`` (keeping a fuzzy ``score``) and is confident;
    - each distinctive word of its name (:func:`distinctive_words`) matches it
      on its own in the exact pass: "snapcaster" -> Snapcaster Mage. Up to
      ``DECKLIST_MAX_PIECES`` transcript words are also joined and compared
      without spaces against those words and the card's names
      (:func:`decklist_keys`), so "spell stutter" -> Spellstutter Sprite. At
      the same number of words a decklist key beats a nickname, which beats
      a real card name: the editor said which deck the video is about;
    - the fuzzy pass also scores each window against the decklist cards'
      names and distinctive words alone, at the lower
      ``decklist_fuzzy_threshold``, so "thought seas" (82) -> Thoughtseize
      and "misbinding" (78, against "mistbind") -> Mistbind Clique. A window
      keeps whichever of its two hits scores higher, the decklist one on a
      tie. The default of 77 was tuned on the fixture transcript with both of
      its decklists (issue #7): at 72 "same thing" became Aetherling and
      "Jace better" Jace Beleren; at 77 the only wrong hit left is
      "thought I" as Thoughtseize, a card the video does show.
    """
    lookup = build_lookup(cards, nicknames)
    common = frozenset() if common_words is None else common_words
    deck = decklist_cards(decklists, cards, lookup)
    deck_names = None
    if deck:
        lookup = dataclasses.replace(
            lookup,
            decklist=decklist_keys(deck.values(), common, lookup.by_name),
            max_words=max(lookup.max_words, DECKLIST_MAX_PIECES),
        )
        deck_names = FuzzyNames.for_decklist(deck.values(), lookup, decklist_fuzzy_threshold)
    fuzzy_names = FuzzyNames(lookup, fuzzy_threshold)
    mentions: list[Mention] = []
    for run in _runs(transcript.parse(transcript_text)):
        found = _exact_pass(run, lookup)
        mentions.extend(_fuzzy_pass(run, found, fuzzy_names, common, deck_names, deck))
    mentions = [
        dataclasses.replace(m, confidence=DECKLIST)
        if m.card.name in deck and m.confidence != DECKLIST
        else m
        for m in mentions
    ]
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
            key = " ".join(tokens[i : i + n])
            # Same words: a decklist key beats a nickname beats a real name
            # (see scan's docstring).
            card, confidence = lookup.decklist.get(key.replace(" ", "")), DECKLIST
            if card is None:
                card, confidence = lookup.nicknames.get(key), NICKNAME
            if card is None:
                card, confidence = lookup.by_name.get(key), EXACT
            if card is not None:
                start, end = words[i][1], words[i + n - 1][2]
                lo = words[max(0, i - CONTEXT_WORDS)][1]
                hi = words[min(len(words), i + n + CONTEXT_WORDS) - 1][2]
                found.append(
                    Mention(
                        card=card,
                        matched_text=text[start:end],
                        timestamp=starts[i],
                        confidence=confidence,
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
DECKLIST_FUZZY_MIN_CHARS = 6
"""Shortest decklist name or distinctive word the decklist fuzzy pass scores
against: "Biden" -> Bident of Thassa. A name shorter than ``FUZZY_MIN_CHARS``
is only taken for a single word with no digits ("is an" is not Island)."""
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
        self._setup(lookup.by_name.items(), threshold, FUZZY_MIN_CHARS)

    @classmethod
    def for_decklist(cls, deck: Iterable[Card], lookup: Lookup, threshold: float) -> FuzzyNames:
        """Only the decklist cards: their names and face names under the same
        rules, plus their distinctive words (``lookup.decklist``), so
        "misbinding" can reach Mistbind Clique through "mistbind". Names as
        short as ``DECKLIST_FUZZY_MIN_CHARS`` take part."""
        entries: list[tuple[str, Card]] = []
        for card in deck:
            names = dict.fromkeys([card.name, *card.faces])
            entries.extend((normalize(name), card) for name in names)
        entries.extend(lookup.decklist.items())
        fuzzy_names = cls.__new__(cls)
        fuzzy_names._setup(entries, threshold, DECKLIST_FUZZY_MIN_CHARS)
        return fuzzy_names

    def _setup(
        self, entries: Iterable[tuple[str, Card]], threshold: float, min_chars: int
    ) -> None:
        self.threshold = threshold
        by_length: dict[int, list[tuple[str, Card]]] = {}
        seen: set[str] = set()
        for key, card in entries:
            if len(card.faces) > 1 and key == normalize(card.name):
                continue
            stripped = key.replace(" ", "")
            if len(stripped) < min_chars or stripped in seen:
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
    deck_names: FuzzyNames | None = None,
    deck: Collection[str] = (),
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
       "omen speaker" becomes Omenspeaker rather than Omen. A nickname or
       decklist match, or a match of a card named in ``deck``, is never
       replaced: a window may not touch it at all.
    4. A window keeps its best name if that scores at least the threshold.
       With ``deck_names`` (the decklist cards, at their own lower
       threshold) it keeps the better of the two hits, the decklist hit on a
       tie, and a decklist hit carries ``confidence="decklist"``. A decklist
       name shorter than ``FUZZY_MIN_CHARS`` counts only for a one-word
       window of letters alone.
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

    protected = [
        m.confidence in (NICKNAME, DECKLIST) or m.card.name in deck for m in found
    ]
    # (score, start, end, card, confidence)
    candidates: list[tuple[float, int, int, Card, str]] = []
    for i in range(len(tokens)):
        for j in range(i + 1, min(i + FUZZY_MAX_WORDS, len(tokens)) + 1):
            if noise[j - 1] or (j - 1 > i and breaks[j - 2]):
                break
            if j == i + 1 and tokens[i] in common_words:
                continue
            inside = {owner[x] for x in range(i, j) if owner[x] is not None}
            if len(inside) > 1:
                break
            k = inside.pop() if inside else None
            if k is not None and protected[k]:
                break  # touches a match no window may replace
            exact = span[k] if k is not None else None
            if exact is not None:
                lo, hi = exact
                if lo < i:
                    break  # starts inside an exact match
                if hi > j or (lo, hi) == (i, j):
                    continue  # ends inside it, or is exactly it
            query = "".join(tokens[i:j])
            hit, confidence = names.best(query), FUZZY
            if deck_names is not None:
                deck_hit = deck_names.best(query)
                if (
                    deck_hit is not None
                    and len(deck_hit[0]) < FUZZY_MIN_CHARS
                    and (j > i + 1 or not query.isalpha())
                ):
                    deck_hit = None
                if deck_hit is not None and (hit is None or deck_hit[2] >= hit[2]):
                    hit, confidence = deck_hit, DECKLIST
            if hit is None:
                continue
            name, card, score = hit
            if exact is not None:
                piece = "".join(tokens[exact[0] : exact[1]])
                if piece not in name or len(piece) == len(name):
                    continue
            candidates.append((score, i, j, card, confidence))

    candidates.sort(key=lambda c: (-c[0], -(c[2] - c[1]), c[1]))
    taken = [False] * len(tokens)
    fuzzy: list[tuple[int, Mention]] = []
    absorbed: set[int] = set()
    for score, i, j, card, confidence in candidates:
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
            confidence=confidence,
            flagged=False,
            context=" ".join(text[lo:hi].split()),
            score=score,
        )
        fuzzy.append((i, mention))

    kept = [(position[k], m) for k, m in enumerate(found) if k not in absorbed]
    return [m for _, m in sorted(kept + fuzzy, key=lambda pair: pair[0])]


# --- confidence --------------------------------------------------------------

# Confidence levels a mention can carry. The exact pass produces "exact" and
# "nickname", the fuzzy pass "fuzzy"; decklists (#7) set the last one, and
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
