from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from champions_mcp.cache import Cache  # noqa: E402
from champions_mcp.champions_items import ItemCatalog, item_key  # noqa: E402
from champions_mcp.champions_movesets import ChampionsMovesets  # noqa: E402
from champions_mcp.champions_roster import (  # noqa: E402
    ChampionsRoster,
    parse_entry,
)
from champions_mcp.models import Item, Pokemon, Stats  # noqa: E402
from champions_mcp.names import NameIndex  # noqa: E402

# Minimal offline Pokédex used by the legality/name tests.
_POKEMON: dict[str, Pokemon] = {
    "araquanid": Pokemon(
        slug="araquanid", name="Araquanid", types=["water", "bug"],
        abilities=["water-bubble"], base_stats=Stats(hp=68, attack=70, speed=42),
        names_by_locale={"en": "Araquanid", "fr": "Tarenbulle"},
    ),
    "miraidon": Pokemon(
        slug="miraidon", name="Miraidon", types=["electric", "dragon"],
        abilities=["hadron-engine"], base_stats=Stats(hp=100, attack=85, speed=135),
        is_legendary=True,
    ),
    "incineroar": Pokemon(
        slug="incineroar", name="Incineroar", types=["fire", "dark"],
        abilities=["intimidate"], base_stats=Stats(hp=95, attack=115, speed=60),
    ),
    "venusaur": Pokemon(
        slug="venusaur", name="Venusaur", types=["grass", "poison"],
        abilities=["overgrow"], base_stats=Stats(hp=80, attack=82, speed=80),
        mega_forms=["venusaur-mega"],
    ),
    "sandslash-alola": Pokemon(
        slug="sandslash-alola", name="Sandslash", types=["ice", "steel"],
        abilities=["slush-rush"], base_stats=Stats(hp=75, attack=100, speed=65),
        names_by_locale={"en": "Sandslash"},
    ),
}
_ITEMS: dict[str, Item] = {
    "choice-scarf": Item(slug="choice-scarf", name="Choice Scarf",
                         category="held-items"),
    "focus-sash": Item(slug="focus-sash", name="Focus Sash",
                       category="held-items"),
    "life-orb": Item(slug="life-orb", name="Life Orb",
                     category="held-items"),
    "leftovers": Item(slug="leftovers", name="Leftovers",
                      category="held-items"),
    "venusaurite": Item(slug="venusaurite", name="Venusaurite",
                        category="mega-stones", is_mega_stone=True),
}

# Champions-legal subset for tests: Life Orb is deliberately NOT here
# (it does not exist in Pokémon Champions, though PokeAPI knows it).
_CHAMPIONS_ITEMS = ["Focus Sash", "Choice Scarf", "Leftovers", "Venusaurite"]


class FakePokeAPI:
    async def raw_pokemon(self, name_or_id):
        slug = str(name_or_id).lower()
        if slug in _POKEMON:
            return {"name": slug}
        raise KeyError(slug)

    async def all_pokemon_slugs(self):
        return list(_POKEMON)

    async def get_pokemon(self, name_or_id) -> Pokemon:
        return _POKEMON[str(name_or_id).lower()]

    async def get_item(self, name_or_id) -> Item:
        slug = str(name_or_id).lower().replace(" ", "-")
        if slug in _ITEMS:
            return _ITEMS[slug]
        raise KeyError(slug)

    async def _get(self, path):  # used by NameIndex.build (not exercised here)
        return {"results": []}

    async def raw_species(self, name_or_id):
        return {}


@pytest.fixture
def fake_api() -> FakePokeAPI:
    return FakePokeAPI()


@pytest.fixture
def cache(tmp_path) -> Cache:
    c = Cache(tmp_path / "cache.sqlite")
    yield c
    c.close()


@pytest.fixture
def names(fake_api, cache) -> NameIndex:
    return NameIndex(fake_api, cache)  # type: ignore[arg-type]


@pytest.fixture
def roster() -> ChampionsRoster:
    # Araquanid + Incineroar are in Champions; Venusaur/Miraidon are not.
    entries = [
        {**parse_entry("Araquanid", "araquanid", ["water", "bug"]),
         "is_legendary": False, "is_mythical": False},
        {**parse_entry("Incineroar", "incineroar", ["fire", "dark"]),
         "is_legendary": False, "is_mythical": False},
    ]
    return ChampionsRoster(entries, verified=True, source_url="test")


@pytest.fixture
def movesets() -> ChampionsMovesets:
    return ChampionsMovesets(
        {
            "araquanid": ["Liquidation", "Sticky Web", "Protect", "Bug Buzz"],
            "incineroar": ["Flare Blitz", "Fake Out", "Parting Shot",
                           "Will-O-Wisp"],
        },
        verified=True,
        source_url="test",
    )


@pytest.fixture
def catalog() -> ItemCatalog:
    return ItemCatalog(
        keys={item_key(n) for n in _CHAMPIONS_ITEMS},
        verified=True,
        source_url="test",
    )


@pytest.fixture(autouse=True)
def _isolate_cache_env(tmp_path, monkeypatch):
    # Keep the real data/regulations dir (so M-A.json loads) but route the
    # cache db to a temp file so tests never touch the shared mirror.
    monkeypatch.setenv("CHAMPIONS_MCP_CACHE_DB", str(tmp_path / "env-cache.sqlite"))
    yield
