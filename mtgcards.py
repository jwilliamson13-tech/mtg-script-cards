"""Find Magic cards in a video transcript and download their images in order.

Usage:
    python mtgcards.py transcript.txt
    python mtgcards.py transcript.txt --out some/folder
    python mtgcards.py transcript.txt --include "Hope" --include "thought seas"
    python mtgcards.py transcript.txt --nicknames my_nicknames.json
    python mtgcards.py transcript.txt --decklist faeries.txt --decklist devotion.txt
    python mtgcards.py --audio episode.mp4                  # needs requirements-whisper.txt
    python mtgcards.py --audio episode.mp4 --model large-v3 # bigger model, best on a GPU
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
import decklist
import matcher
import scryfall
import transcribe

DATA_DIR = Path(__file__).resolve().parent / "data"
CARDS_PATH = DATA_DIR / "cards.json"
MANIFEST_COLUMNS = ["order", "card", "timestamp", "matched_text", "confidence", "scryfall_url"]
FACE_SUFFIXES = ["front", "back"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mtgcards",
        description="Find Magic cards in a transcript and download their images in order of first mention.",
    )
    parser.add_argument(
        "transcript", nargs="?", help="transcript file (.txt, .srt or .vtt); not needed with --audio"
    )
    parser.add_argument(
        "--out",
        help="output folder (default: <transcript or audio name>_cards next to the input)",
    )
    parser.add_argument(
        "--audio",
        metavar="FILE",
        help="transcribe this video or audio file instead of reading a transcript "
        "(needs the extra: pip install -r requirements-whisper.txt). The transcript is "
        "saved as <name>.srt next to the output folder and reused on reruns",
    )
    parser.add_argument(
        "--model",
        default="small",
        help="Whisper model for --audio: tiny, base, small (default), medium, large-v3, ... "
        "Larger is more accurate and much slower without a GPU",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="where --audio runs the model (default: auto, the GPU if one works, else the CPU)",
    )
    parser.add_argument(
        "--compute-type",
        metavar="TYPE",
        help="CTranslate2 compute type for --audio (default: int8, which works on any GPU or CPU)",
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
        "--decklist",
        action="append",
        default=[],
        metavar="FILE",
        help="a decklist as deck sites export it (\"4 Mistbind Clique\" per line); repeatable. "
        "Its cards are matched with confidence, even misheard; other cards still match",
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
    if not args.refresh and args.transcript is None and args.audio is None:
        parser.error("a transcript file (or --audio FILE) is required unless --refresh is given")
    if args.transcript is not None and args.audio is not None:
        parser.error("give a transcript file or --audio FILE, not both")
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


def load_decklists(
    paths: list[str], cards: matcher.CardIndex, lookup: matcher.Lookup
) -> list[list[str]] | None:
    """Each ``--decklist`` file's card names, with a warning on stderr for each
    name that is no card (it is ignored). None if a file is unreadable."""
    decklists = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            print(f"Could not read the decklist {path}: {exc.strerror or exc}", file=sys.stderr)
            return None
        names = decklist.parse_decklist(text)
        for name in decklist.unknown_names(names, cards, lookup):
            print(
                f'{Path(path).name}: no card named "{name}" in the card list; ignored',
                file=sys.stderr,
            )
        decklists.append(names)
    return decklists


def prompt_spellings(
    names: list[str], cards: matcher.CardIndex, lookup: matcher.Lookup
) -> list[str]:
    """A decklist's names as the transcriber should spell them: the Oracle
    name, or each face name of a multi-face card. Unknown names are left out."""
    spellings = []
    for name in names:
        card = cards.get(name) or lookup.by_name.get(matcher.normalize(name))
        if card is not None:
            spellings += card.faces if len(card.faces) > 1 else [card.name]
    return spellings


def transcribe_audio(
    audio: Path,
    srt_path: Path,
    decklists: list[list[str]],
    cards: matcher.CardIndex,
    lookup: matcher.Lookup,
    args: argparse.Namespace,
) -> bool:
    """Make ``srt_path`` from ``audio`` unless an earlier run already did.
    False (after a one-line message on stderr) when it could not."""
    if srt_path.is_file():
        print(
            f"Using the transcript from an earlier run, {srt_path} "
            "(delete it to transcribe again, e.g. with another --model).",
            file=sys.stderr,
        )
        return True
    names = transcribe.prompt_names(
        [prompt_spellings(deck, cards, lookup) for deck in decklists]
    )
    try:
        transcribe.transcribe(
            audio,
            srt_path,
            model=args.model,
            initial_prompt=names or None,
            device=args.device,
            compute_type=args.compute_type,
        )
    except transcribe.TranscriberMissing as exc:
        print(exc, file=sys.stderr)
        return False
    except (RuntimeError, ValueError, OSError) as exc:
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        print(f"Could not transcribe {audio}: {reason}", file=sys.stderr)
        return False
    print(f"Transcript saved: {srt_path}", file=sys.stderr)
    return True


def run(
    transcript_path: Path,
    out_dir: Path,
    include: list[str],
    argv: list[str],
    nicknames_path: str | None = None,
    decklist_paths: list[str] | None = None,
    audio_args: argparse.Namespace | None = None,
) -> int:
    """Scan ``transcript_path`` and fill ``out_dir``. With ``audio_args``, the
    transcript is first made from ``audio_args.audio`` if it does not exist."""
    cards = matcher.load_cards(CARDS_PATH)
    nicknames = load_nicknames(nicknames_path, cards)
    if nicknames is None:
        return 2
    lookup = matcher.build_lookup(cards)
    decklists = load_decklists(decklist_paths or [], cards, lookup)
    if decklists is None:
        return 2
    if audio_args is not None and not transcribe_audio(
        Path(audio_args.audio), transcript_path, decklists, cards, lookup, audio_args
    ):
        return 2
    text = transcript_path.read_text(encoding="utf-8-sig", errors="replace")

    included: list[tuple[str, matcher.Card]] = []
    unresolved = 0
    if include:
        for name in split_include_values(include, lookup):
            card, note = resolve_include(name, cards, lookup)
            if card is None:
                unresolved += 1
                print(f"--include: {note}", file=sys.stderr)
                continue
            if note:
                print(f"--include: {note}")
            included.append((name, card))

    result = matcher.scan(
        text,
        cards,
        nicknames=nicknames,
        include=included,
        decklists=decklists,
        common_words=matcher.bundled_common_words(),
    )
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
    source = Path(args.audio or args.transcript)
    if not source.is_file():
        what = "Audio file" if args.audio else "Transcript"
        print(f"{what} not found: {source}", file=sys.stderr)
        return 2
    if args.out:
        out_dir = Path(args.out)
    else:
        out_dir = source.with_name(f"{source.stem}_cards")
    # With --audio the transcript is made next to the output folder.
    transcript_path = out_dir.parent / f"{source.stem}.srt" if args.audio else source
    return run(
        transcript_path,
        out_dir,
        args.include,
        sys.argv[1:] if argv is None else argv,
        nicknames_path=args.nicknames,
        decklist_paths=args.decklist,
        audio_args=args if args.audio else None,
    )


if __name__ == "__main__":
    sys.exit(main())
