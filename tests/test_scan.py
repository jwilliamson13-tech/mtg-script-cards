"""Behaviour of the ``scan`` seam: which cards come out, in what order, how confident."""

from matcher import Card, scan

FABLE = "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"


def card(name: str, type_line: str = "") -> Card:
    faces = name.split(" // ")
    slug = name.lower().replace(" ", "-")
    return Card(
        name=name,
        faces=faces,
        scryfall_id=f"id-{slug}",
        scryfall_url=f"https://scryfall.com/card/test/1/{slug}",
        image_urls=[f"https://img.test/{slug}-{i}.png" for i in range(len(faces))],
        type_line=type_line,
    )


def index_of(*names: str) -> dict[str, Card]:
    return {name: card(name) for name in names}


CARDS = index_of(
    "Island",
    "Tropical Island",
    "Thoughtseize",
    "Jace, the Mind Sculptor",
    "Underground River",
    "Cryptic Command",
    "Bitterblossom",
    "Frostburn Weird",
    FABLE,
    "Fire // Ice",
)


def names(result) -> list[str]:
    return [m.card.name for m in result.mentions]


def test_longest_match_wins():
    result = scan("I fetch a tropical island", CARDS)
    assert names(result) == ["Tropical Island"]


def test_single_word_island_alone():
    result = scan("play an island and pass", CARDS)
    assert names(result) == ["Island"]
    assert result.mentions[0].matched_text == "island"


def test_plural_folding_on_fixture_snippet():
    # Verbatim from the fixture transcript.
    result = scan(">> I have all islands. At least you got\nsome underground rivers. Huh?", CARDS)
    assert names(result) == ["Island", "Underground River"]
    assert [m.matched_text for m in result.mentions] == ["islands", "underground rivers"]


def test_punctuation_and_case():
    result = scan("is this Jace\nbetter than Jace the Mind Sculptor in", CARDS)
    assert names(result) == ["Jace, the Mind Sculptor"]
    assert result.mentions[0].matched_text == "Jace the Mind Sculptor"


def test_name_split_across_caption_lines():
    snippet = "with bitter blossom to back them up and\neven cryptic command for disruption. The"
    result = scan(snippet, CARDS)
    assert [c.name for c in result.ordered_cards()] == ["Cryptic Command"]


def test_double_faced_card_matches_back_face():
    result = scan("then i flip it into reflection of kiki-jiki", CARDS)
    assert names(result) == [FABLE]


def test_double_faced_card_matches_front_face_spoken_without_hyphen():
    result = scan("fable of the mirror breaker on two", CARDS)
    assert names(result) == [FABLE]


def test_split_card_matches_each_half():
    result = scan("I cast fire, and later ice", CARDS)
    assert names(result) == ["Fire // Ice", "Fire // Ice"]
    assert [c.name for c in result.ordered_cards()] == ["Fire // Ice"]


def test_order_of_first_appearance_with_repeat():
    result = scan("island, thoughtseize, island", CARDS)
    assert names(result) == ["Island", "Thoughtseize", "Island"]
    assert [c.name for c in result.ordered_cards()] == ["Island", "Thoughtseize"]


def test_exact_mentions_are_confident_with_no_timestamp_for_plain_text():
    result = scan("literally frostburn weird, just because", CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Frostburn Weird"
    assert mention.confidence == "exact"
    assert mention.flagged is False
    assert mention.timestamp is None
    assert result.confident() == [mention]
    assert result.flagged() == []


def test_no_cards_no_mentions():
    result = scan("nothing to see here", CARDS)
    assert result.mentions == []
    assert result.ordered_cards() == []


# --- SRT and VTT -------------------------------------------------------------
# Snippets are verbatim from the fixture (YouTube auto-captions, rolling style).
# They are built from line lists because the rolling format puts lines holding a
# single space inside cues, which an editor would silently strip.

CAPTION_CARDS = index_of("Swamp", "Cloudfin Raptor", "Cryptic Command", "Island")
VTT_HEADER = ["WEBVTT", "Kind: captions", "Language: en", ""]


def lines(*rows: str) -> str:
    return "\n".join(rows) + "\n"


CRYPTIC_VTT = lines(
    *VTT_HEADER,
    "00:00:35.760 --> 00:00:37.350 align:start position:0%",
    "&gt;&gt; Powered by a flutter of flashy fairies",
    "with<00:00:35.920><c> bitter</c><00:00:36.239><c> blossom</c><00:00:36.559><c> to</c><00:00:36.719><c> back</c>"
    "<00:00:36.960><c> them</c><00:00:37.040><c> up</c><00:00:37.200><c> and</c>",
    "",
    "00:00:37.350 --> 00:00:37.360 align:start position:0%",
    "with bitter blossom to back them up and",
    " ",
    "",
    "00:00:37.360 --> 00:00:39.510 align:start position:0%",
    "with bitter blossom to back them up and",
    "even<00:00:37.600><c> cryptic</c><00:00:38.079><c> command</c><00:00:38.320><c> for</c><00:00:38.559><c> disruption.</c>"
    "<00:00:39.360><c> The</c>",
    "",
    "00:00:39.510 --> 00:00:39.520 align:start position:0%",
    "even cryptic command for disruption. The",
    " ",
    "",
    "00:00:39.520 --> 00:00:41.190 align:start position:0%",
    "even cryptic command for disruption. The",
    "deck<00:00:39.680><c> took</c><00:00:39.920><c> down</c><00:00:40.160><c> Teumor</c><00:00:40.559><c> Energy</c><00:00:40.800><c> in</c>"
    "<00:00:40.960><c> round</c>",
)

SWAMP_SRT = lines(
    "2866",
    "00:49:33,510 --> 00:49:33,520",
    "you get one for you get them for free.",
    " ",
    "",
    "2867",
    "00:49:33,520 --> 00:49:35,750",
    "you get one for you get them for free.",
    ">> Let me play the swamp. [laughter] One,",
    "",
    "2868",
    "00:49:35,750 --> 00:49:35,760",
    ">> Let me play the swamp. [laughter] One,",
    " ",
    "",
    "2869",
    "00:49:35,760 --> 00:49:38,069",
    ">> Let me play the swamp. [laughter] One,",
    "two,",
)

CLOUDFIN_VTT = lines(
    *VTT_HEADER,
    "00:19:24.799 --> 00:19:26.630 align:start position:0%",
    " ",
    "you<00:19:24.960><c> can</c><00:19:25.120><c> still</c><00:19:25.280><c> trump</c><00:19:25.679><c> with</c>"
    "<00:19:26.080><c> Cloudfin</c>",
    "",
    "00:19:26.630 --> 00:19:26.640 align:start position:0%",
    "you can still trump with Cloudfin",
    " ",
    "",
    "00:19:26.640 --> 00:19:27.590 align:start position:0%",
    "you can still trump with Cloudfin",
    "Raptor.",
    "",
    "00:19:27.590 --> 00:19:27.600 align:start position:0%",
    "Raptor.",
    " ",
)


def test_vtt_snippet_yields_card_at_cue_start():
    result = scan(CRYPTIC_VTT, CAPTION_CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Cryptic Command"
    assert mention.matched_text == "cryptic command"
    # The cue whose inline word timings introduce the line, not the hold cue after it.
    assert mention.timestamp == "00:00:37.360"
    assert mention.confidence == "exact"


def test_srt_snippet_yields_card_at_cue_start():
    result = scan(SWAMP_SRT, CAPTION_CARDS)
    assert [(m.card.name, m.timestamp) for m in result.mentions] == [("Swamp", "00:49:33.520")]


def test_rolling_caption_repeats_do_not_duplicate_mentions():
    # "Let me play the swamp" is in three consecutive cues and "cryptic command"
    # in three; each was said once.
    assert len(scan(SWAMP_SRT, CAPTION_CARDS).mentions) == 1
    assert len(scan(CRYPTIC_VTT, CAPTION_CARDS).mentions) == 1


def test_name_split_across_cues_matches_once_with_first_cue_timestamp():
    result = scan(CLOUDFIN_VTT, CAPTION_CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Cloudfin Raptor"
    assert mention.matched_text == "Cloudfin Raptor"
    assert mention.timestamp == "00:19:24.799"


def test_plain_text_still_has_no_timestamp():
    result = scan("you can still trump with Cloudfin\nRaptor.\n", CAPTION_CARDS)
    assert [(m.card.name, m.timestamp) for m in result.mentions] == [("Cloudfin Raptor", None)]


def test_ordinary_srt_keeps_a_line_said_twice():
    # Not rolling captions (an editor's export, or Whisper): a repeated line is
    # a second mention, not a duplicate.
    srt = lines(
        "1", "00:00:01,000 --> 00:00:02,000", "I'm going to play a land.", "",
        "2", "00:00:02,000 --> 00:00:03,000", "Island.", "",
        "3", "00:00:03,500 --> 00:00:04,000", "Island.", "",
        "4", "00:00:04,000 --> 00:00:05,000", "Go.",
    )
    result = scan(srt, CAPTION_CARDS)
    assert [(m.card.name, m.timestamp) for m in result.mentions] == [
        ("Island", "00:00:02.000"),
        ("Island", "00:00:03.500"),
    ]


def test_vtt_note_blocks_cue_ids_and_entities_are_not_text():
    vtt = lines(
        "WEBVTT",
        "",
        "NOTE exported by an editor, mentions island",
        "",
        "intro",
        "01:02.500 --> 01:04.000 line:90%",
        "<v Host>Rock &amp; roll, then <i>cryptic command</i>",
        "",
        "2",
        "00:01:04.000 --> 00:01:05.000",
        "that's all",
    )
    result = scan(vtt, CAPTION_CARDS)
    assert [(m.card.name, m.timestamp) for m in result.mentions] == [
        ("Cryptic Command", "00:01:02.500")
    ]
    assert "Rock & roll" in result.mentions[0].context
