"""The SRT writer and the decklist prompt. No model is loaded here."""

from dataclasses import dataclass

import transcript
import transcribe


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str


SEGMENTS = [
    FakeSegment(0.0, 4.2, " Welcome back to the bracket, today it's Faeries."),
    FakeSegment(4.2, 9.75, " Turn one Thoughtseize, take the Mistbind Clique."),
    FakeSegment(9.75, 10.0, "   "),
    FakeSegment(3725.5, 3729.001, " Bitterblossom into Nightveil Specter."),
]


def test_srt_is_read_back_as_srt_with_its_timestamps():
    srt = transcribe.format_srt(SEGMENTS)

    assert transcript.sniff(srt) == "srt"
    assert transcript.parse(srt) == [
        transcript.Segment("00:00:00.000", "Welcome back to the bracket, today it's Faeries."),
        transcript.Segment("00:00:04.200", "Turn one Thoughtseize, take the Mistbind Clique."),
        transcript.Segment("01:02:05.500", "Bitterblossom into Nightveil Specter."),
    ]


def test_srt_cues_are_numbered_from_one_without_gaps():
    srt = transcribe.format_srt(SEGMENTS)

    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,200\n")
    assert "\n3\n01:02:05,500 --> 01:02:09,001\nBitterblossom into Nightveil Specter.\n\n" in srt


def test_prompt_names_are_unique_in_order_with_faces_split():
    names = transcribe.prompt_names(
        [
            ["Mistbind Clique", "Bitterblossom", "Delver of Secrets // Insectile Aberration"],
            ["Thassa, God of the Sea", "Bitterblossom"],
        ]
    )

    assert names == [
        "Mistbind Clique",
        "Bitterblossom",
        "Delver of Secrets",
        "Insectile Aberration",
        "Thassa, God of the Sea",
    ]


def test_prompt_keeps_whole_names_from_the_start_within_the_budget():
    names = ["Mistbind Clique", "Bitterblossom", "Thoughtseize"]
    fits = lambda text: len(text) <= 32  # noqa: E731

    assert transcribe.build_prompt(names, fits) == "Mistbind Clique, Bitterblossom"
    assert transcribe.build_prompt(names, lambda text: True) == (
        "Mistbind Clique, Bitterblossom, Thoughtseize"
    )
    assert transcribe.build_prompt(names, lambda text: False) is None
    assert transcribe.build_prompt([], fits) is None
