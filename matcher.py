"""Card matching. Pure: no network, no filesystem beyond :func:`load_cards`.

This ticket provides only the card index; ``scan`` arrives with ticket #3.
"""

from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


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
