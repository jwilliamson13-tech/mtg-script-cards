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
