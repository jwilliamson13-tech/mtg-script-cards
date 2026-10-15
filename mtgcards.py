"""Find Magic cards in a video transcript and download their images in order.

Usage:
    python mtgcards.py transcript.txt
    python mtgcards.py --refresh        # update the bundled card list from Scryfall
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import build_dictionary

DATA_DIR = Path(__file__).resolve().parent / "data"
CARDS_PATH = DATA_DIR / "cards.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mtgcards",
        description="Find Magic cards in a transcript and download their images in order of first mention.",
    )
    parser.add_argument("transcript", nargs="?", help="transcript file (.txt, .srt or .vtt)")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="rebuild the bundled card list from Scryfall, then exit",
    )
    args = parser.parse_args(argv)
    if not args.refresh and args.transcript is None:
        parser.error("a transcript file is required unless --refresh is given")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.refresh:
        build_dictionary.build(CARDS_PATH)
        return 0
    print(
        "Transcript scanning is not implemented yet (ticket #3). "
        "Only --refresh works in this version.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
