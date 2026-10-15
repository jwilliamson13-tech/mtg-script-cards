"""Find Magic cards in a video transcript and download their images in order.

Usage:
    python mtgcards.py transcript.txt
    python mtgcards.py transcript.txt --out some/folder
    python mtgcards.py transcript.txt --include "Hope" --include "thought seas"
    python mtgcards.py transcript.txt --nicknames my_nicknames.json
    python mtgcards.py --refresh        # update the bundled card list from Scryfall
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import requests

import build_dictionary
import matcher
import scryfall

DATA_DIR = Path(__file__).resolve().parent / "data"
CARDS_PATH = DATA_DIR / "cards.json"
MANIFEST_COLUMNS = ["order", "card", "timestamp", "matched_text", "confidence", "scryfall_url"]
FACE_SUFFIXES = ["front", "back"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mtgcards",
        description="Find Magic cards in a transcript and download their images in order of first mention.",
    )
    parser.add_argument("transcript", nargs="?", help="transcript file (.txt, .srt or .vtt)")
    parser.add_argument(
        "--out",
        help="output folder (default: <transcript name>_cards next to the transcript)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="NAME",
        help="download this card whether or not it was matched; repeatable, or comma-separated. "
        "Misspellings are resolved through Scryfall",
    )
    parser.add_argument(
        "--nicknames",
        metavar="FILE",
        help="nickname file to use instead of the bundled data/nicknames.json "
        '(a JSON object: {"trop": "Tropical Island", ...})',
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="rebuild the bundled card list from Scryfall, then exit",
    )
    args = parser.parse_args(argv)
    if not args.refresh and args.transcript is None:
        parser.error("a transcript file is required unless --refresh is given")
    return args


def slugify(name: str) -> str:
    """``"Jace, the Mind Sculptor"`` -> ``"jace-the-mind-sculptor"``.

    Apostrophes are dropped rather than turned into a dash, as Scryfall's own
    URLs do: ``"Judge's Familiar"`` -> ``"judges-familiar"``.
    """
    name = matcher.fold_ascii(name).lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def image_files(order: int, card: matcher.Card) -> list[tuple[str, str]]:
    """``(filename, url)`` per image. Multi-image cards get a face suffix."""
    stem = f"{order:03d}-{slugify(card.name)}"
    if len(card.image_urls) == 1:
        return [(f"{stem}.png", card.image_urls[0])]
    files = []
    for i, url in enumerate(card.image_urls):
        suffix = FACE_SUFFIXES[i] if i < len(FACE_SUFFIXES) else f"face{i + 1}"
        files.append((f"{stem}-{suffix}.png", url))
    return files


_NUMBERED_PNG = re.compile(r"\d{3,}-(.+\.png)")


def _unnumbered(filename: str) -> str:
    match = _NUMBERED_PNG.fullmatch(filename)
    return match.group(1) if match else filename


def earlier_images(out_dir: Path) -> dict[str, Path]:
    """Images from an earlier run in ``out_dir``, keyed by file name without the number."""
    return {
        _unnumbered(p.name): p for p in out_dir.glob("*.png") if _NUMBERED_PNG.fullmatch(p.name)
    }


def write_manifest(path: Path, result: matcher.ScanResult, numbers: dict[str, int]) -> None:
    """One row per confident mention. Flagged mentions are left out: the
    manifest describes the folder, and the review printout covers the rest."""
    # utf-8-sig so Excel shows accented card names correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(MANIFEST_COLUMNS)
        for m in result.confident():
            order = numbers.get(m.card.name)
            writer.writerow(
                [
                    "" if order is None else order,
                    m.card.name,
                    m.timestamp or "",
                    m.matched_text,
                    m.confidence,
                    m.card.scryfall_url,
                ]
            )


# --- --include ----------------------------------------------------------------


def split_include_values(values: list[str], lookup: matcher.Lookup) -> list[str]:
    """Flatten repeated and comma-separated ``--include`` values.

    Commas inside a card name ("Jace, the Mind Sculptor") are kept: adjacent
    comma-separated pieces are rejoined when together they form a known name.
    """
    names: list[str] = []
    for value in values:
        parts = value.split(",")
        i = 0
        while i < len(parts):
            for j in range(len(parts), i + 1, -1):
                joined = ",".join(parts[i:j]).strip()
                if matcher.normalize(joined) in lookup.by_name:
                    names.append(joined)
                    i = j
                    break
            else:
                if parts[i].strip():
                    names.append(parts[i].strip())
                i += 1
    return names


def resolve_include(
    name: str, cards: matcher.CardIndex, lookup: matcher.Lookup
) -> tuple[matcher.Card | None, str]:
    """Find the card an ``--include`` name means: ``(card, note)`` or ``(None, why not)``.

    The bundled dictionary first (Oracle name, then normalized name or face
    name), then Scryfall's fuzzy lookup, which also reaches cards the
    dictionary leaves out (joke sets).
    """
    card = cards.get(name) or lookup.by_name.get(matcher.normalize(name))
    if card is not None:
        return card, ""
    try:
        found = scryfall.named_fuzzy(name)
    except requests.RequestException as exc:
        return None, f'could not ask Scryfall about "{name}": {exc}'
    if found is None:
        return None, (
            f'no card matches "{name}" (Scryfall found none, or more than one; '
            "try more of the name)"
        )
    if not scryfall.is_game_card(found):
        kind = (found.get("layout") or "").replace("_", " ") or "non-card"
        return None, (
            f'"{name}" only matches the {kind} "{found.get("name")}" on Scryfall, not a card; '
            "try more of the name"
        )
    if found["name"] in cards:
        return cards[found["name"]], f'"{name}" is {found["name"]} (Scryfall)'
    card = matcher.Card(**build_dictionary.entry(found))
    return card, f'"{name}" is {card.name} (Scryfall; not in the bundled card list)'


# --- review printout -----------------------------------------------------------

_PLAIN_ARG = re.compile(r"[\w./:\\=+-]+")


def _quote(arg: str, always: bool = False) -> str:
    """Quote an argument so it pastes into PowerShell, cmd or a POSIX shell."""
    if not always and _PLAIN_ARG.fullmatch(arg):
        return arg
    return '"' + arg.replace('"', '\\"') + '"'


def rerun_command(argv: list[str], names: list[str]) -> str:
    """The original command line plus ``--include "Name"`` for each name."""
    words = ["python", _quote(sys.argv[0] or "mtgcards.py")]
    words += [_quote(arg) for arg in argv]
    for name in names:
        words += ["--include", _quote(name, always=True)]
    return " ".join(words)


def print_review(result: matcher.ScanResult, argv: list[str]) -> None:
    """List each distinct flagged card once, then a rerun command accepting all of them."""
    first: dict[str, matcher.Mention] = {}
    counts: dict[str, int] = {}
    for m in result.flagged():
        first.setdefault(m.card.name, m)
        counts[m.card.name] = counts.get(m.card.name, 0) + 1
    if not first:
        return
    width = min(max(len(name) for name in first), 32)
    print()
    print(
        f"Held back for review, not downloaded: {len(first)} cards "
        f"({len(result.flagged())} mentions). Either the name is also an ordinary word, "
        f"or the words only sound like it (fuzzy, with a 0-100 similarity score)."
    )
    for name, m in first.items():
        more = counts[name] - 1
        tail = f"  (+{more} more)" if more else ""
        how = f"  fuzzy {m.score:.0f}" if m.confidence == matcher.FUZZY and m.score is not None else ""
        print(
            f'  {name:<{width}}  "{m.matched_text}"{how}  {m.timestamp or "-"}  '
            f"...{m.context}...{tail}"
        )
    print()
    print("To download them, run this, after deleting the --include for any card you do not want:")
    print(rerun_command(argv, list(first)))


# --- run -----------------------------------------------------------------------


def load_nicknames(path: str | None, cards: matcher.CardIndex) -> dict[str, str] | None:
    """The nickname file (``--nicknames`` or the bundled one), with a warning on
    stderr for each entry that will be skipped. None if the file is unreadable."""
    shown = Path(path) if path else matcher.NICKNAMES_PATH
    try:
        nicknames = matcher.load_nicknames(path)
    except OSError as exc:
        print(f"Could not read the nickname file {shown}: {exc.strerror or exc}", file=sys.stderr)
        return None
    except ValueError as exc:
        print(f"The nickname file {shown} is not valid: {exc}", file=sys.stderr)
        return None
    for warning in matcher.nickname_warnings(nicknames, cards):
        print(f"{shown.name}: {warning}", file=sys.stderr)
    return nicknames


def run(
    transcript_path: Path,
    out_dir: Path,
    include: list[str],
    argv: list[str],
    nicknames_path: str | None = None,
) -> int:
    text = transcript_path.read_text(encoding="utf-8-sig", errors="replace")
    cards = matcher.load_cards(CARDS_PATH)
    nicknames = load_nicknames(nicknames_path, cards)
    if nicknames is None:
        return 2

    included: list[tuple[str, matcher.Card]] = []
    unresolved = 0
    if include:
        lookup = matcher.build_lookup(cards)
        for name in split_include_values(include, lookup):
            card, note = resolve_include(name, cards, lookup)
            if card is None:
                unresolved += 1
                print(f"--include: {note}", file=sys.stderr)
                continue
            if note:
                print(f"--include: {note}")
            included.append((name, card))

    result = matcher.scan(text, cards, nicknames=nicknames, include=included)
    ordered = result.ordered_cards()
    numbers = {card.name: i for i, card in enumerate(ordered, start=1)}

    out_dir.mkdir(parents=True, exist_ok=True)
    earlier = earlier_images(out_dir)
    downloaded = skipped = failed = 0
    for order, card in enumerate(ordered, start=1):
        for filename, url in image_files(order, card):
            dest = out_dir / filename
            if dest.exists():
                skipped += 1
                continue
            # A rerun with --include renumbers the cards after an accepted one:
            # move the earlier file to its new number instead of keeping both.
            previous = earlier.get(_unnumbered(filename))
            if previous is not None and previous.exists():
                previous.rename(dest)
                skipped += 1
                continue
            try:
                scryfall.download(url, dest)
                downloaded += 1
            except requests.RequestException as exc:
                failed += 1
                print(f"Could not download {card.name} ({filename}): {exc}", file=sys.stderr)

    write_manifest(out_dir / "manifest.csv", result, numbers)

    summary = (
        f"{len(ordered)} cards, {len(result.confident())} mentions: "
        f"{downloaded} images downloaded, {skipped} already there"
    )
    if failed:
        summary += f", {failed} failed"
    print(f"{summary}. Output: {out_dir}")
    print_review(result, argv)
    return 1 if failed or unresolved else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.refresh:
        build_dictionary.build(CARDS_PATH)
        return 0
    transcript_path = Path(args.transcript)
    if not transcript_path.is_file():
        print(f"Transcript not found: {transcript_path}", file=sys.stderr)
        return 2
    if args.out:
        out_dir = Path(args.out)
    else:
        out_dir = transcript_path.with_name(f"{transcript_path.stem}_cards")
    return run(
        transcript_path,
        out_dir,
        args.include,
        sys.argv[1:] if argv is None else argv,
        nicknames_path=args.nicknames,
    )


if __name__ == "__main__":
    sys.exit(main())
