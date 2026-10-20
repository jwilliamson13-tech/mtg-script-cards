"""Read a decklist in the format deck sites export.

    4 Mistbind Clique
    4x Scion of Oona
    2 Thoughtseize (LRW) 145

    Sideboard
    2 Flashfreeze

One card per line, quantity first. Blank lines, section headers (Deck,
Sideboard, Commander, Companion, with or without a colon) and comment lines
starting with ``//`` or ``#`` are ignored, as is the ``(SET) 123`` suffix that
MTG Arena and MTGGoldfish exports put after the name. A line with no quantity
is read as a bare card name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

import matcher

SECTION_HEADERS = frozenset({"deck", "sideboard", "commander", "companion"})
_QUANTITY = re.compile(r"(\d+)\s*[xX]?\s+(.+)")
_SET_SUFFIX = re.compile(r"\s+\([A-Za-z0-9]{2,8}\)(?:\s+\S+)?$")


def parse_decklist(text: str) -> list[str]:
    """The card names in ``text``, each once, in the order they first appear."""
    names: dict[str, None] = {}
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith(("//", "#")):
            continue
        if line.rstrip(":").strip().lower() in SECTION_HEADERS:
            continue
        match = _QUANTITY.fullmatch(line)
        name = match.group(2) if match else line
        name = _SET_SUFFIX.sub("", name).strip()
        if name:
            names.setdefault(name, None)
    return list(names)


def unknown_names(
    names: Iterable[str], cards: Mapping[str, matcher.Card], lookup: matcher.Lookup | None = None
) -> list[str]:
    """The names in ``names`` that are no card in ``cards``: neither an Oracle
    name as written nor, once normalized, a card or face name. ``scan`` skips
    these; the CLI warns about them."""
    lookup = lookup or matcher.build_lookup(cards)
    return [name for name in names if lookup.find(name) is None]
