"""Find Magic cards in a video transcript and download their images in order.

Usage:
    python mtgcards.py transcript.txt
    python mtgcards.py transcript.txt --out some/folder
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


def write_manifest(path: Path, result: matcher.ScanResult, numbers: dict[str, int]) -> None:
    # utf-8-sig so Excel shows accented card names correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(MANIFEST_COLUMNS)
        for m in result.mentions:
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


def run(transcript_path: Path, out_dir: Path) -> int:
    text = transcript_path.read_text(encoding="utf-8-sig", errors="replace")
    cards = matcher.load_cards(CARDS_PATH)
    result = matcher.scan(text, cards)
    ordered = result.ordered_cards()
    numbers = {card.name: i for i, card in enumerate(ordered, start=1)}

    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = skipped = failed = 0
    for order, card in enumerate(ordered, start=1):
        for filename, url in image_files(order, card):
            dest = out_dir / filename
            if dest.exists():
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
        f"{len(ordered)} cards, {len(result.mentions)} mentions: "
        f"{downloaded} images downloaded, {skipped} already there"
    )
    if failed:
        summary += f", {failed} failed"
    print(f"{summary}. Output: {out_dir}")
    return 1 if failed else 0


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
    return run(transcript_path, out_dir)


if __name__ == "__main__":
    sys.exit(main())
