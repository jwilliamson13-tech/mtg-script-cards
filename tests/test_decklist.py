"""Reading a decklist in the deck-site export format."""

from decklist import parse_decklist, unknown_names
from matcher import Card

# The shape MTGGoldfish exports (from the fixture's Faeries list).
GOLDFISH = """\
4 Mistbind Clique
4 Scion of Oona
2 Thoughtseize

Sideboard
1 Agony Warp
2 Thoughtseize
"""


def test_quantity_and_name_lines_with_sideboard_header():
    assert parse_decklist(GOLDFISH) == [
        "Mistbind Clique",
        "Scion of Oona",
        "Thoughtseize",
        "Agony Warp",
    ]


def test_quantity_with_x():
    assert parse_decklist("4x Bitterblossom\n2 x Cryptic Command\n1X Mutavault") == [
        "Bitterblossom",
        "Cryptic Command",
        "Mutavault",
    ]


def test_name_starting_with_x_is_not_a_quantity_suffix():
    assert parse_decklist("1 Xathrid Necromancer") == ["Xathrid Necromancer"]


def test_section_headers_any_case_with_or_without_colon():
    text = (
        "Deck\n4 Island\nSIDEBOARD:\n1 Negate\n"
        "commander:\n1 Ragavan, Nimble Pilferer\nCompanion\n1 Lurrus of the Dream-Den"
    )
    assert parse_decklist(text) == [
        "Island",
        "Negate",
        "Ragavan, Nimble Pilferer",
        "Lurrus of the Dream-Den",
    ]


def test_comment_lines_are_ignored():
    text = "// Faeries, 2008\n# maindeck\n4 Spellstutter Sprite"
    assert parse_decklist(text) == ["Spellstutter Sprite"]


def test_set_and_collector_number_suffix_is_dropped():
    text = (
        "4 Thoughtseize (LRW) 145\n"
        "2 Fable of the Mirror-Breaker // Reflection of Kiki-Jiki (NEO) 141\n"
        "1 Island (lrw)"
    )
    assert parse_decklist(text) == [
        "Thoughtseize",
        "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki",
        "Island",
    ]


def test_line_without_a_quantity_is_a_bare_name():
    assert parse_decklist("Snapcaster Mage\n\n") == ["Snapcaster Mage"]


def test_unknown_names_are_reported_and_face_names_are_known():
    fable = Card(
        name="Fable of the Mirror-Breaker // Reflection of Kiki-Jiki",
        faces=["Fable of the Mirror-Breaker", "Reflection of Kiki-Jiki"],
        scryfall_id="id",
        scryfall_url="url",
    )
    cards = {fable.name: fable, "Island": Card("Island", ["Island"], "id2", "url2")}
    names = ["Island", "island", "Reflection of Kiki-Jiki", "Islnad", "Mistbind Clique"]
    assert unknown_names(names, cards) == ["Islnad", "Mistbind Clique"]
