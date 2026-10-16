"""Small Scryfall HTTP client shared by the dictionary build and the CLI.

Scryfall asks clients to identify themselves and to space requests out.
Everything that talks to Scryfall goes through :func:`get` or :func:`download`,
which share one session and enforce a minimum gap between requests.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import requests

VERSION = "0.1.0"
USER_AGENT = f"mtg-script-cards/{VERSION}"
API = "https://api.scryfall.com"

MIN_GAP_SECONDS = 0.1
# /cards/named and /cards/search are limited to 2 requests a second; both go
# through _wait_for_slow_gap, which spaces them by this much.
SLOW_GAP_SECONDS = 0.5
DEFAULT_TIMEOUT = 60

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

_last_request_at = 0.0
_last_slow_at = float("-inf")


def _wait_for_gap() -> None:
    global _last_request_at
    now = time.monotonic()
    remaining = MIN_GAP_SECONDS - (now - _last_request_at)
    if remaining > 0:
        time.sleep(remaining)
    _last_request_at = time.monotonic()


def _wait_for_slow_gap() -> None:
    """Space /cards/named and /cards/search requests ``SLOW_GAP_SECONDS`` apart."""
    global _last_slow_at
    remaining = SLOW_GAP_SECONDS - (time.monotonic() - _last_slow_at)
    if remaining > 0:
        time.sleep(remaining)
    _last_slow_at = time.monotonic()


def get(url: str, **kwargs) -> requests.Response:
    """GET ``url`` through the shared session, at most one request per 100 ms.

    Keyword arguments go straight to :meth:`requests.Session.get`. The
    response is returned unchecked so callers can treat 404 as "not found".
    """
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    _wait_for_gap()
    return session.get(url, **kwargs)


def download(url: str, dest_path: str | os.PathLike[str]) -> Path:
    """Stream a binary file (an image, a bulk archive) to ``dest_path``.

    Writes to a temporary sibling first, so an interrupted download never
    leaves a truncated file at the final path. Returns the final path.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    response = get(url, stream=True, headers={"Accept": "*/*"})
    response.raise_for_status()
    try:
        with open(partial, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
        os.replace(partial, dest)
    finally:
        if partial.exists():
            partial.unlink()
    return dest


def named_fuzzy(name: str) -> dict | None:
    """Look a card up by approximate name. Returns the card JSON, or None on 404.

    A 404 means no card or several cards matched (``"cryptic"``). Spaced at
    least 500 ms apart, as Scryfall asks for this endpoint. The result can be a
    token (``"jace"``, ``"ragavan"``); check :func:`is_game_card` before using it.
    """
    _wait_for_slow_gap()
    response = get(f"{API}/cards/named", params={"fuzzy": name})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


NOT_GAME_CARD_LAYOUTS = frozenset({"token", "double_faced_token", "emblem", "art_series"})


def is_game_card(card: dict) -> bool:
    """False for tokens, emblems and art-series cards, which have no place in a deck."""
    type_line = card.get("type_line") or ""
    return (
        card.get("layout") not in NOT_GAME_CARD_LAYOUTS
        and not type_line.startswith("Token")
        and "Emblem" not in type_line
    )


def search(query: str, **params) -> Iterator[dict]:
    """Yield every card matching a Scryfall search, following pagination.

    A query that matches nothing yields nothing (Scryfall answers 404).
    Pages are fetched at least 500 ms apart, as Scryfall asks for this endpoint.
    """
    url: str | None = f"{API}/cards/search"
    params = {"q": query, **params}
    while url:
        _wait_for_slow_gap()
        response = get(url, params=params)
        params = {}
        if response.status_code == 404:
            return
        response.raise_for_status()
        page = response.json()
        yield from page.get("data", [])
        url = page.get("next_page") if page.get("has_more") else None
