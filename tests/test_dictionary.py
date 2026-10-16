"""Checks on the committed card dictionary (data/cards.json)."""

from pathlib import Path

import pytest

from matcher import Card, load_cards

CARDS_PATH = Path(__file__).resolve().parents[1] / "data" / "cards.json"


@pytest.fixture(scope="module")
def cards() -> dict[str, Card]:
    return load_cards(CARDS_PATH)


def is_png(url: str) -> bool:
    # Scryfall image URLs carry a cache-busting query string after the extension.
    return url.split("?", 1)[0].endswith(".png")


@pytest.mark.parametrize("name", ["Island", "Tropical Island", "Thoughtseize"])
def test_single_faced_cards_present(cards, name):
    card = cards[name]
    assert card.faces == [name]
    assert len(card.image_urls) == 1
    assert is_png(card.image_urls[0])
    assert card.scryfall_id
    assert card.scryfall_url.startswith("https://scryfall.com/card/")


def test_double_faced_card_has_both_faces_and_images(cards):
    card = cards["Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"]
    assert card.faces == ["Fable of the Mirror-Breaker", "Reflection of Kiki-Jiki"]
    assert len(card.image_urls) == 2
    assert all(is_png(url) for url in card.image_urls)


def test_split_card_has_both_faces_one_image(cards):
    card = cards["Fire // Ice"]
    assert card.faces == ["Fire", "Ice"]
    assert len(card.image_urls) == 1


def test_basic_land_type_line_kept(cards):
    assert cards["Island"].type_line.startswith("Basic Land")


def test_no_tokens(cards):
    assert not [c.name for c in cards.values() if c.type_line.startswith("Token")]
    assert "Treasure" not in cards
    assert "Koth of the Hammer Emblem" not in cards
    assert "Wicked // Cursed" not in cards  # Role token with the "flip" layout


def test_no_pack_theme_or_placeholder_cards(cards):
    assert "Cats" not in cards  # Jumpstart front card
    assert not [c.name for c in cards.values() if c.type_line == "Card"]


def test_no_un_set_cards(cards):
    assert "Blacker Lotus" not in cards
    assert "Urza's Hot Tub" not in cards


def test_no_alchemy_rebalances(cards):
    assert not [name for name in cards if name.startswith("A-")]


def test_no_digital_only_cards(cards):
    assert "Gitrog, Horror of Zhava" not in cards  # Alchemy original
    assert "Stuffy Doll Avatar" not in cards  # Magic Online Vanguard avatar


def test_every_card_has_an_image_per_face_or_one(cards):
    bad = [c.name for c in cards.values() if len(c.image_urls) not in (1, len(c.faces))]
    assert bad == []
