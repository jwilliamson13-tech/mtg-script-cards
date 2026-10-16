"""The CLI's output files and printed commands."""

import argparse
import csv

import matcher
import mtgcards


def card(name: str) -> matcher.Card:
    slug = name.lower().replace(" ", "-")
    return matcher.Card(
        name=name,
        faces=[name],
        scryfall_id=f"id-{slug}",
        scryfall_url=f"https://scryfall.com/card/test/1/{slug}",
        image_urls=[f"https://img.test/{slug}.png"],
    )


def mention(c: matcher.Card, text: str, confidence: str, flagged: bool) -> matcher.Mention:
    return matcher.Mention(
        card=c, matched_text=text, timestamp="00:00:01.000", confidence=confidence,
        flagged=flagged, context=text,
    )


def test_manifest_has_a_row_per_mention_with_blank_order_when_held(tmp_path):
    seize, hope = card("Thoughtseize"), card("Hope")
    result = matcher.ScanResult(
        [
            mention(hope, "hope", "exact", True),
            mention(seize, "thoughtseize", "exact", False),
            mention(seize, "thought seas", "fuzzy", True),
        ]
    )
    path = tmp_path / "manifest.csv"
    mtgcards.write_manifest(path, result, {"Thoughtseize": 1})
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == mtgcards.MANIFEST_COLUMNS
    assert [(r[0], r[1], r[3], r[4]) for r in rows[1:]] == [
        ("", "Hope", "hope", "exact"),
        ("1", "Thoughtseize", "thoughtseize", "exact"),
        ("", "Thoughtseize", "thought seas", "fuzzy"),
    ]


def test_any_transcriber_error_is_one_line_not_a_traceback(tmp_path, monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise TypeError("expected str, got NoneType\nmore detail")

    monkeypatch.setattr(mtgcards.transcribe, "transcribe", broken)
    args = argparse.Namespace(model="small", device="auto", compute_type=None)
    audio = tmp_path / "episode.mp4"
    ok = mtgcards.transcribe_audio(
        audio, tmp_path / "episode.srt", [], {}, matcher.build_lookup({}), args
    )
    assert ok is False
    assert capsys.readouterr().err.strip() == (
        f"Could not transcribe {audio}: expected str, got NoneType"
    )


def test_rerun_command_drops_double_quotes_from_a_name_and_still_finds_the_card(monkeypatch):
    monkeypatch.setattr(mtgcards.sys, "argv", ["mtgcards.py"])
    kongming = card('Kongming, "Sleeping Dragon"')
    command = mtgcards.rerun_command(["talk.srt"], [kongming.name, "Hope"])
    assert command == (
        'python mtgcards.py talk.srt --include "Kongming, Sleeping Dragon" --include "Hope"'
    )
    lookup = matcher.build_lookup({kongming.name: kongming})
    assert mtgcards.split_include_values(["Kongming, Sleeping Dragon"], lookup) == [
        "Kongming, Sleeping Dragon"
    ]
    assert lookup.by_name[matcher.normalize("Kongming, Sleeping Dragon")] is kongming
