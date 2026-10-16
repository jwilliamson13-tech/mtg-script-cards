"""Behaviour of the ``scan`` seam: which cards come out, in what order, how confident."""

import builtins
import io
from pathlib import Path

import matcher
from matcher import Card, load_cards, load_common_words, load_nicknames, nickname_warnings

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# The bundled word list, read once here: scan itself reads no files.
COMMON_WORDS = load_common_words(DATA_DIR / "common_words.txt")


def scan(text, cards, **knobs):
    """``matcher.scan`` with the bundled common-word list unless a test passes
    its own. Nicknames stay off unless a test passes some."""
    knobs.setdefault("common_words", COMMON_WORDS)
    return matcher.scan(text, cards, **knobs)


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
CARDS["Island"] = card("Island", "Basic Land — Island")


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
    # "fire" and "ice" are common words; switch that rule off to see the matching alone.
    result = scan("I cast fire, and later ice", CARDS, common_words=frozenset())
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


# --- confidence and the review step (#5) -----------------------------------

REVIEW_CARDS = {
    **CARDS,
    **index_of("Hope", "Opt", "Give // Take"),
}


def test_common_word_card_is_flagged_not_confident():
    result = scan("I hope this works", REVIEW_CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Hope"
    assert mention.confidence == "exact"
    assert mention.flagged is True
    assert result.flagged() == [mention]
    assert result.ordered_cards() == []


def test_opt_is_flagged():
    # Opt is the spec's rarest example (rank ~29k in the word list).
    result = scan("you could opt into that line", REVIEW_CARDS)
    assert [(m.card.name, m.flagged) for m in result.mentions] == [("Opt", True)]


def test_basic_land_is_confident_although_a_common_word():
    result = scan("island", REVIEW_CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Island"
    assert mention.flagged is False
    assert [c.name for c in result.ordered_cards()] == ["Island"]


def test_split_card_half_that_is_a_common_word_is_flagged():
    # Verbatim from the fixture transcript.
    result = scan("You might want to take it.", REVIEW_CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Give // Take"
    assert mention.matched_text == "take"
    assert mention.flagged is True


def test_multi_word_exact_match_is_confident_even_when_every_word_is_common():
    result = scan("even cryptic command for disruption", REVIEW_CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Cryptic Command"
    assert mention.flagged is False


def test_single_word_name_not_in_word_list_is_confident():
    result = scan("game one, turn one thoughtseize", REVIEW_CARDS)
    [mention] = result.mentions
    assert mention.card.name == "Thoughtseize"
    assert mention.flagged is False


def test_include_of_unmatched_card_is_appended_as_confident():
    result = scan("I hope this works, then thoughtseize", REVIEW_CARDS, include=["Bitterblossom"])
    assert names(result) == ["Hope", "Thoughtseize", "Bitterblossom"]
    included = result.mentions[-1]
    assert included.matched_text == "Bitterblossom"
    assert included.confidence == "exact"
    assert included.flagged is False
    assert included.timestamp is None
    assert [c.name for c in result.ordered_cards()] == ["Thoughtseize", "Bitterblossom"]


def test_include_accepts_a_card_outside_the_index():
    blacker_lotus = card("Blacker Lotus")
    result = scan("nothing to see here", REVIEW_CARDS, include=[blacker_lotus])
    assert [c.name for c in result.ordered_cards()] == ["Blacker Lotus"]


def test_included_card_flagged_in_text_becomes_confident():
    result = scan("I hope this works. Island, and hope again", REVIEW_CARDS, include=["Hope"])
    assert names(result) == ["Hope", "Island", "Hope"]
    assert result.flagged() == []
    # Numbered at its first mention, not appended at the end.
    assert [c.name for c in result.ordered_cards()] == ["Hope", "Island"]


def test_include_of_split_card_unflags_the_half_that_was_said():
    result = scan("You might want to take it.", REVIEW_CARDS, include=["Give // Take"])
    assert [(m.card.name, m.flagged) for m in result.mentions] == [("Give // Take", False)]


def test_include_pair_keeps_what_the_user_typed():
    result = scan("nothing to see here", REVIEW_CARDS, include=[("thought seas", CARDS["Thoughtseize"])])
    [mention] = result.mentions
    assert (mention.card.name, mention.matched_text, mention.flagged) == ("Thoughtseize", "thought seas", False)


# --- nicknames (#8) ------------------------------------------------------------

NICK_CARDS = {
    **CARDS,
    **index_of(
        "Lightning Bolt",
        "Tarmogoyf",
        "Kolaghan's Command",
        "Library",
        "Library of Alexandria",
        "Jace, Vryn's Prodigy // Jace, Telepath Unbound",
    ),
}
NICKS = {
    "trop": "Tropical Island",
    "bolt": "Lightning Bolt",
    "goyf": "Tarmogoyf",
    "k command": "Kolaghan's Command",
}


def test_nickname_trop_yields_tropical_island_confident():
    result = scan("then I fetch the trop", NICK_CARDS, nicknames=NICKS)
    [mention] = result.mentions
    assert mention.card.name == "Tropical Island"
    assert mention.matched_text == "trop"
    assert mention.confidence == "nickname"
    assert mention.flagged is False
    assert [c.name for c in result.ordered_cards()] == ["Tropical Island"]


def test_nickname_that_is_a_common_word_is_still_confident():
    # "bolt" is in the common-word list; a nickname entry is a deliberate choice.
    result = scan("I cast bolt on his goyf and then trop", NICK_CARDS, nicknames=NICKS)
    assert [(m.card.name, m.confidence, m.flagged) for m in result.mentions] == [
        ("Lightning Bolt", "nickname", False),
        ("Tarmogoyf", "nickname", False),
        ("Tropical Island", "nickname", False),
    ]


def test_multi_word_nickname_matches_as_a_phrase():
    result = scan("K-Command your graveyard. The command zone is different", NICK_CARDS, nicknames=NICKS)
    assert [(m.card.name, m.matched_text, m.confidence) for m in result.mentions] == [
        ("Kolaghan's Command", "K-Command", "nickname")
    ]


def test_nickname_beats_a_real_card_name_of_the_same_words():
    # "library" is also the real card Library; the nickname file says otherwise.
    result = scan("activate library", NICK_CARDS, nicknames={"library": "Library of Alexandria"})
    [mention] = result.mentions
    assert (mention.card.name, mention.confidence, mention.flagged) == (
        "Library of Alexandria",
        "nickname",
        False,
    )


def test_longer_real_name_beats_a_shorter_nickname():
    nicks = {"library": "Library of Alexandria", "lightning": "Tarmogoyf"}
    result = scan("with library of alexandria and lightning bolt", NICK_CARDS, nicknames=nicks)
    assert [(m.card.name, m.confidence) for m in result.mentions] == [
        ("Library of Alexandria", "exact"),
        ("Lightning Bolt", "exact"),
    ]


def test_nickname_may_name_a_card_by_a_face_or_in_lowercase():
    nicks = {"jvp": "Jace, Vryn's Prodigy", "bolt": "lightning bolt"}
    result = scan("jvp into bolt", NICK_CARDS, nicknames=nicks)
    assert [m.card.name for m in result.mentions] == [
        "Jace, Vryn's Prodigy // Jace, Telepath Unbound",
        "Lightning Bolt",
    ]


def test_nickname_with_unknown_oracle_name_warns_and_is_skipped():
    nicks = {"trop": "Tropical Island", "bolt": "Lightnin Bolt", "": "Tarmogoyf"}
    assert nickname_warnings(nicks, NICK_CARDS) == [
        'nickname "bolt": no card named "Lightnin Bolt" in the card list; skipped',
        'nickname "" has no letters or digits; skipped',
    ]
    result = scan("bolt the trop", NICK_CARDS, nicknames=nicks)
    assert [(m.card.name, m.confidence) for m in result.mentions] == [("Tropical Island", "nickname")]


def test_two_spellings_of_one_nickname_keep_the_first_and_warn():
    nicks = {"bolt": "Lightning Bolt", "Bolt!": "Tarmogoyf"}
    assert nickname_warnings(nicks, NICK_CARDS) == [
        'nickname "Bolt!" reads the same as "bolt", which already means Lightning Bolt; skipped'
    ]
    result = scan("bolt", NICK_CARDS, nicknames=nicks)
    assert [m.card.name for m in result.mentions] == ["Lightning Bolt"]


def test_empty_nicknames_switch_them_off():
    result = scan("I fetch the trop", NICK_CARDS, nicknames={})
    assert result.mentions == []


def test_default_is_no_nicknames():
    assert scan("then I fetch the trop", NICK_CARDS).mentions == []


def test_bundled_nickname_file_has_trop():
    result = scan("then I fetch the trop", NICK_CARDS, nicknames=matcher.bundled_nicknames())
    assert [(m.card.name, m.confidence) for m in result.mentions] == [("Tropical Island", "nickname")]


def test_bundled_nickname_file_names_only_real_cards():
    cards = load_cards(DATA_DIR / "cards.json")
    assert nickname_warnings(load_nicknames(), cards) == []


def test_scan_with_default_knobs_reads_no_files(monkeypatch):
    def no_files(*args, **kwargs):
        raise AssertionError("scan touched the filesystem")

    monkeypatch.setattr(builtins, "open", no_files)
    monkeypatch.setattr(io, "open", no_files)
    monkeypatch.setattr(Path, "open", no_files)
    monkeypatch.setattr(matcher, "bundled_nicknames", no_files)
    monkeypatch.setattr(matcher, "bundled_common_words", no_files)
    result = matcher.scan("I hope I fetch the trop and a tropical island", NICK_CARDS)
    assert [m.card.name for m in result.mentions] == ["Tropical Island"]


# --- fuzzy pass (#6) ---------------------------------------------------------

FUZZY_CARDS = {
    **CARDS,
    **index_of(
        "Cloudfin Raptor",
        "Secluded Glen",
        "Spell Stutter",
        "Spellstutter Sprite",
        "Omen",
        "Omenspeaker",
        "Hope",
        "Give // Take",
        "Formation",
        "Slaughter",
        "Cryptic Coat",
    ),
}


def test_misheard_name_from_fixture_is_a_flagged_fuzzy_match():
    # Verbatim from the fixture transcript: "Cloudfin Raptor" as the captioner heard it.
    result = scan("Uh, it was it was a cloud finaptor to go\nwith the Biden and the island. Um, I", FUZZY_CARDS)
    [raptor] = [m for m in result.mentions if m.card.name == "Cloudfin Raptor"]
    assert raptor.confidence == "fuzzy"
    assert raptor.flagged is True
    assert raptor.matched_text == "cloud finaptor"
    assert raptor in result.flagged()
    assert result.ordered_cards() == [FUZZY_CARDS["Island"]]


def test_another_misheard_name_from_fixture():
    result = scan(">> I'll just play secluded Glenn. Pass.", FUZZY_CARDS)
    assert [(m.card.name, m.confidence, m.flagged) for m in result.mentions] == [
        ("Secluded Glen", "fuzzy", True)
    ]


def test_fuzzy_match_carries_a_numeric_score():
    result = scan("a cloud finaptor to go", FUZZY_CARDS)
    [mention] = result.mentions
    assert isinstance(mention.score, float)
    assert 86 <= mention.score < 100


def test_fuzzy_threshold_is_a_knob():
    # "cloud finaptor" scores about 96 against Cloudfin Raptor.
    assert scan("a cloud finaptor to go", FUZZY_CARDS, fuzzy_threshold=97).mentions == []


def test_space_split_name_beats_a_shorter_real_card():
    # Verbatim from the fixture transcript. "spell stutter" alone is the card
    # Spell Stutter, but the speaker said Spellstutter Sprite.
    result = scan("single counter spell, spell spell\nstutter sprite, blah blah blah. It's", FUZZY_CARDS)
    assert [(m.card.name, m.confidence, m.matched_text) for m in result.mentions] == [
        ("Spellstutter Sprite", "fuzzy", "spell stutter sprite")
    ]


def test_single_word_card_inside_a_longer_name_gives_way():
    # Verbatim from the fixture transcript: Omen is a card, Omenspeaker was meant.
    result = scan("We got Omen Speaker. We got whatever", FUZZY_CARDS)
    assert [(m.card.name, m.confidence) for m in result.mentions] == [("Omenspeaker", "fuzzy")]


def test_short_common_words_do_not_fuzz_into_cards():
    result = scan("I hope you take the information", FUZZY_CARDS)
    assert [(m.card.name, m.confidence) for m in result.mentions] == [
        ("Hope", "exact"),
        ("Give // Take", "exact"),
    ]


def test_captioner_annotations_are_not_speech():
    result = scan(">> Let me play the swamp. [laughter] One,", FUZZY_CARDS)
    assert result.mentions == []


def test_exact_matches_are_unaffected_by_the_fuzzy_pass():
    result = scan("even cryptic command for disruption, then thoughtseize", FUZZY_CARDS)
    assert [(m.card.name, m.confidence, m.flagged, m.score) for m in result.mentions] == [
        ("Cryptic Command", "exact", False, None),
        ("Thoughtseize", "exact", False, None),
    ]


def test_fuzzy_match_in_vtt_carries_the_cue_timestamp():
    cards = {**CAPTION_CARDS, **index_of("Bitterblossom")}
    result = scan(CRYPTIC_VTT, cards)
    assert [(m.card.name, m.confidence, m.timestamp) for m in result.mentions] == [
        ("Bitterblossom", "fuzzy", "00:00:35.760"),
        ("Cryptic Command", "exact", "00:00:37.360"),
    ]


# --- decklists (#7) ----------------------------------------------------------

DECK_CARDS = {
    **FUZZY_CARDS,
    **index_of(
        "Snapcaster Mage",
        "Mistbind Clique",
        "Jace Beleren",
        "Jace, Architect of Thought",
        "Bident of Thassa",
        "Aetherling",
    ),
}


def described(result) -> list[tuple[str, str, bool]]:
    return [(m.card.name, m.confidence, m.flagged) for m in result.mentions]


def test_decklist_promotes_a_fuzzy_match_to_confident():
    result = scan("a cloud finaptor to go", DECK_CARDS, decklists=[["Cloudfin Raptor"]])
    [mention] = result.mentions
    assert (mention.card.name, mention.confidence, mention.flagged) == (
        "Cloudfin Raptor",
        "decklist",
        False,
    )
    assert 86 <= mention.score < 100
    assert result.ordered_cards() == [DECK_CARDS["Cloudfin Raptor"]]


def test_exact_and_nickname_mentions_of_a_decklist_card_are_decklist():
    result = scan(
        "cryptic now, cryptic command later",
        DECK_CARDS,
        decklists=[["Cryptic Command"]],
        nicknames={"cryptic": "Cryptic Command"},
    )
    assert described(result) == [
        ("Cryptic Command", "decklist", False),
        ("Cryptic Command", "decklist", False),
    ]


def test_distinctive_single_word_matches_a_decklist_card():
    # Nicknames off: the bundled file maps "snapcaster" too.
    text = "then snapcaster flashes it back"
    deck = [["Snapcaster Mage"]]
    assert described(scan(text, DECK_CARDS, decklists=deck, nicknames={})) == [
        ("Snapcaster Mage", "decklist", False)
    ]
    assert scan(text, DECK_CARDS, nicknames={}).mentions == []


def test_card_outside_the_decklist_still_matches_with_its_normal_confidence():
    result = scan(
        "I hope you see the cryptic command",
        DECK_CARDS,
        decklists=[["Snapcaster Mage"]],
        nicknames={},
    )
    assert described(result) == [
        ("Hope", "exact", True),
        ("Cryptic Command", "exact", False),
    ]


def test_misheard_decklist_card_below_the_fuzzy_threshold_is_found():
    # Verbatim from the fixture transcript; "thought seas" scores 82, under 86.
    text = "We got thought seas. All right."
    assert scan(text, DECK_CARDS).mentions == []
    result = scan(text, DECK_CARDS, decklists=[["Thoughtseize"]])
    assert [(m.card.name, m.confidence, m.flagged, m.matched_text) for m in result.mentions] == [
        ("Thoughtseize", "decklist", False, "thought seas")
    ]


def test_decklist_fuzzy_threshold_is_a_knob():
    text = "We got thought seas. All right."
    result = scan(text, DECK_CARDS, decklists=[["Thoughtseize"]], decklist_fuzzy_threshold=90)
    assert result.mentions == []


def test_decklist_fuzzy_reaches_a_card_through_a_distinctive_word():
    # Verbatim from the fixture transcript. "through the" is not Thoughtseize.
    text = "pump this big enough to get through the misbine click. I'm going to lose"
    result = scan(text, DECK_CARDS, decklists=[["Mistbind Clique", "Thoughtseize"]])
    assert described(result) == [("Mistbind Clique", "decklist", False)]


def test_short_decklist_name_matches_one_misheard_word_only():
    # Verbatim from the fixture transcript: the Bident of Thassa as heard.
    text = "with the Biden and the island. Um, I"
    result = scan(text, DECK_CARDS, decklists=[["Bident of Thassa"]])
    assert described(result) == [
        ("Bident of Thassa", "decklist", False),
        ("Island", "exact", False),
    ]
    assert scan("this is an upgrade", DECK_CARDS, decklists=[["Island"]]).mentions == []


def test_decklist_card_wins_over_a_real_card_spelled_with_a_space():
    # Verbatim from the fixture transcript: "spell stutter" alone is the card
    # Spell Stutter, but in a Faeries video it is Spellstutter Sprite.
    text = ">> Uh, spell stutter. Okay, sure. And"
    assert described(scan(text, DECK_CARDS)) == [("Spell Stutter", "exact", False)]
    result = scan(text, DECK_CARDS, decklists=[["Spellstutter Sprite"]])
    assert described(result) == [("Spellstutter Sprite", "decklist", False)]


def test_word_shared_by_two_decklist_cards_matches_neither():
    decklists = [["Jace Beleren"], ["Jace, Architect of Thought"]]
    assert scan("then jace comes down", DECK_CARDS, decklists=decklists).mentions == []
    assert described(scan("then beleren comes down", DECK_CARDS, decklists=decklists)) == [
        ("Jace Beleren", "decklist", False)
    ]


def test_common_word_from_a_decklist_name_does_not_match():
    # "clique" is in the bundled common-word list.
    assert scan("what a clique", DECK_CARDS, decklists=[["Mistbind Clique"]]).mentions == []
    # "mage" is too, when the list says so.
    result = scan(
        "the mage is good",
        DECK_CARDS,
        decklists=[["Snapcaster Mage"]],
        common_words={"the", "is", "good", "mage"},
    )
    assert result.mentions == []


def test_word_found_in_many_card_names_does_not_match():
    # "mage" is not in the bundled word list, but it is in over a hundred card
    # names: a speaker saying "mage" has not named Snapcaster Mage.
    many_mages = index_of(*(f"Mage of {chr(97 + i) * 4}" for i in range(21)))
    cards = {**DECK_CARDS, **many_mages}
    assert scan("the mage is good", cards, decklists=[["Snapcaster Mage"]]).mentions == []
    assert described(scan("the mage is good", DECK_CARDS, decklists=[["Snapcaster Mage"]])) == [
        ("Snapcaster Mage", "decklist", False)
    ]


def test_decklist_never_filters_and_skips_unknown_names():
    result = scan(
        "thoughtseize into bitterblossom", DECK_CARDS, decklists=[["Not A Card", "Bitterblossom"]]
    )
    assert described(result) == [
        ("Thoughtseize", "exact", False),
        ("Bitterblossom", "decklist", False),
    ]


def test_one_decklist_may_be_given_as_a_flat_list_of_names():
    result = scan("then snapcaster", DECK_CARDS, decklists=["Snapcaster Mage"])
    assert described(result) == [("Snapcaster Mage", "decklist", False)]


def test_fuzzy_window_never_swallows_a_nickname():
    cards = index_of("Lightning Bolt", "Bolt Hound")
    result = scan("bolt hand", cards, nicknames={"bolt": "Lightning Bolt"}, fuzzy_threshold=80)
    assert described(result) == [("Lightning Bolt", "nickname", False)]
