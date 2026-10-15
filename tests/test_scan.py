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
