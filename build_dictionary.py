"""Build the compact card dictionary (``data/cards.json``) from Scryfall bulk data.

Run ``python build_dictionary.py`` as a maintainer, or ``python mtgcards.py --refresh``
as a user. Both download Scryfall's ``oracle_cards`` bulk file (one object per
Oracle name), keep the handful of fields the matcher and downloader need, and
write a minified JSON list.

Excluded at build time: tokens, emblems, art series, cards that were never
printed on paper, Alchemy rebalances (names starting with ``A-``) and joke sets
(``set_type == "funny"``).

On "digital-only": the bulk file's ``digital`` flag describes the *printing*
Scryfall chose to represent the card, not the card. Scryfall often represents
old paper cards by their Magic Online reprint (Vintage Masters, Masters Edition,
Tempest Remastered), so Tropical Island, Tundra and most of the Reserved List
arrive flagged ``digital: true``. Dropping on that flag would lose exactly the
cards a video about old decks mentions. Instead the build asks Scryfall which
cards have no paper printing at all (search ``-in:paper``) and drops those.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import scryfall

DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "cards.json"
GZIP_THRESHOLD_BYTES = 20 * 1024 * 1024

# front_card: Jumpstart pack-theme cards ("Cats", "Vehicles") that are not Magic cards.
EXCLUDED_LAYOUTS = frozenset({"token", "double_faced_token", "emblem", "art_series", "front_card"})
EXCLUDED_SET_TYPES = frozenset({"funny"})
EXCLUDED_NAME_PREFIXES = ("A-",)
# Tokens are mostly caught by layout, but Role tokens use the "flip" layout and a
# few one-offs use "normal"; the type line catches those. "Card" is the type line
# of checklist and placeholder cards.
EXCLUDED_TYPE_LINE_PREFIXES = ("Token",)
EXCLUDED_TYPE_LINES = frozenset({"Card"})
# When two Oracle cards share a name, prefer the one that is not from a
# memorabilia set (challenge-deck cards reuse real card names).
LOW_PRIORITY_SET_TYPES = frozenset({"memorabilia"})


def oracle_bulk_uri() -> str:
    """Return the download URI of the current ``oracle_cards`` bulk file."""
    response = scryfall.get(f"{scryfall.API}/bulk-data")
    response.raise_for_status()
    for entry in response.json()["data"]:
        if entry.get("type") == "oracle_cards":
            uri = entry.get("jsonl_download_uri") or entry.get("download_uri")
            if uri:
                return uri
    raise RuntimeError("Scryfall bulk-data listing has no oracle_cards download")


def iter_bulk_file(path: Path) -> Iterator[dict]:
    """Yield card objects from a downloaded bulk file, line by line when it is JSONL."""
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)
    else:
        with open(path, encoding="utf-8") as fh:
            yield from json.load(fh)


def digital_only_oracle_ids() -> set[str]:
    """Oracle IDs of cards that have never been printed on paper."""
    return {
        card["oracle_id"]
        for card in scryfall.search("-in:paper", unique="cards", include_extras="true")
        if "oracle_id" in card
    }


def _clean_url(url: str) -> str:
    return url.split("?", 1)[0]


def compact(card: dict, digital_only: set[str]) -> dict | None:
    """Reduce one Scryfall card object to the dictionary entry, or None to exclude it."""
    name = card["name"]
    type_line = card.get("type_line", "")
    if (
        card.get("layout") in EXCLUDED_LAYOUTS
        or card.get("set_type") in EXCLUDED_SET_TYPES
        or name.startswith(EXCLUDED_NAME_PREFIXES)
        or type_line.startswith(EXCLUDED_TYPE_LINE_PREFIXES)
        or type_line in EXCLUDED_TYPE_LINES
        or card.get("oracle_id") in digital_only
    ):
        return None

    faces = card.get("card_faces") or []
    if "image_uris" in card:
        image_urls = [card["image_uris"]["png"]]
    else:
        image_urls = [face["image_uris"]["png"] for face in faces if "image_uris" in face]

    return {
        "name": name,
        "faces": [face["name"] for face in faces] or [name],
        "scryfall_id": card["id"],
        "scryfall_url": _clean_url(card["scryfall_uri"]),
        "image_urls": image_urls,
        "type_line": type_line,
    }


def build(out_path: str | os.PathLike[str] = DEFAULT_OUT, *, log=print) -> dict:
    """Download the bulk file and write the dictionary to ``out_path``.

    If the minified JSON would exceed 20 MB it is gzipped to ``<out_path>.gz``
    instead and the plain file is removed. Returns a summary dict with keys
    ``path``, ``cards`` and ``bytes``.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    uri = oracle_bulk_uri()
    suffix = ".jsonl.gz" if uri.endswith(".gz") else ".json"
    log(f"Downloading {uri}")
    with tempfile.TemporaryDirectory(prefix="mtg-bulk-") as tmp:
        bulk_path = scryfall.download(uri, Path(tmp) / f"oracle_cards{suffix}")
        log(f"Downloaded {bulk_path.stat().st_size / 1e6:.1f} MB; fetching digital-only list")
        digital_only = digital_only_oracle_ids()
        log(f"{len(digital_only)} cards have no paper printing; compacting")

        chosen: dict[str, tuple[bool, dict]] = {}
        total = 0
        for card in iter_bulk_file(bulk_path):
            total += 1
            entry = compact(card, digital_only)
            if entry is None:
                continue
            low_priority = card.get("set_type") in LOW_PRIORITY_SET_TYPES
            current = chosen.get(entry["name"])
            if current is None or (current[0] and not low_priority):
                chosen[entry["name"]] = (low_priority, entry)

    entries = [entry for _, entry in chosen.values()]
    entries.sort(key=lambda e: e["name"])
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    plain = out.with_suffix("") if out.suffix == ".gz" else out
    gz = plain.with_name(plain.name + ".gz")
    if len(payload) > GZIP_THRESHOLD_BYTES:
        with gzip.open(gz, "wb", compresslevel=9) as fh:
            fh.write(payload)
        if plain.exists():
            plain.unlink()
        written = gz
    else:
        plain.write_bytes(payload)
        if gz.exists():
            gz.unlink()
        written = plain

    size = written.stat().st_size
    log(f"Wrote {written} ({size / 1e6:.1f} MB): {len(entries)} cards kept of {total}")
    return {"path": written, "cards": len(entries), "bytes": size}


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
