"""Consistency tests: champions_*.json data files vs the active regulation's allowed_species.

Rules:
- Every pickable slug in champions_pokemon.json must appear in allowed_species (slugified).
- Every slug in champions_movesets.json must appear in allowed_species (slugified).
- Every slug in allowed_species must have a base-species match in champions_pokemon.json
  and champions_movesets.json (form variants like rotom-heat or raichu-alola are accepted
  when their base, rotom / raichu, is present).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from champions_mcp.config import Settings
from champions_mcp.pokeapi import slugify
from champions_mcp.regulations import RegulationRegistry

_DATA = Path(__file__).resolve().parent.parent / "data"

_DATA_FILES = [
    _DATA / "champions_items.json",
    _DATA / "champions_pokemon.json",
    _DATA / "champions_movesets.json",
]


@pytest.fixture(scope="module", autouse=True)
def _ensure_data_files():
    """Run the Serebii prewarm if any champions_*.json file is missing."""
    if not all(p.is_file() for p in _DATA_FILES):
        from champions_mcp.scripts.prewarm import _run
        asyncio.run(_run())


def _base_in(slug: str, key_set: set[str]) -> bool:
    """Return True if *slug* itself, or any prefix obtained by progressively
    stripping the last hyphen-separated segment, is found in *key_set*.

    This allows form variants (e.g. ``rotom-heat``, ``raichu-alola``) to
    match their base species (``rotom``, ``raichu``) when the form is not
    listed separately in the data file.
    """
    if slug in key_set:
        return True
    parts = slug.split("-")
    for i in range(len(parts) - 1, 0, -1):
        if "-".join(parts[:i]) in key_set:
            return True
    return False


@pytest.fixture(scope="module")
def regulation():
    return RegulationRegistry(Settings.load()).get("M-A")


@pytest.fixture(scope="module")
def roster_keys():
    doc = json.loads((_DATA / "champions_pokemon.json").read_text(encoding="utf-8"))
    return {slugify(e["slug"]) for e in doc["entries"] if not e.get("is_mega") and e.get("slug")}


@pytest.fixture(scope="module")
def moveset_keys():
    doc = json.loads((_DATA / "champions_movesets.json").read_text(encoding="utf-8"))
    return {slugify(k) for k in doc["movesets"]}


@pytest.fixture(scope="module")
def allowed_set(regulation):
    return {slugify(s) for s in regulation.allowed_species}


def test_roster_slugs_all_in_allowed(roster_keys, allowed_set):
    """No pickable Pokémon in champions_pokemon.json is outside the regulation."""
    extra = sorted(roster_keys - allowed_set)
    assert extra == [], f"Roster entries not covered by allowed_species: {extra}"


def test_moveset_slugs_all_in_allowed(moveset_keys, allowed_set):
    """No species in champions_movesets.json is outside the regulation."""
    extra = sorted(moveset_keys - allowed_set)
    assert extra == [], f"Moveset entries not covered by allowed_species: {extra}"


def test_allowed_species_all_in_roster(regulation, roster_keys):
    """Every allowed_species slug (or its base form) exists in champions_pokemon.json."""
    missing = sorted(
        s for s in (slugify(x) for x in regulation.allowed_species)
        if not _base_in(s, roster_keys)
    )
    assert missing == [], f"allowed_species slugs with no roster base: {missing}"


def test_allowed_species_all_have_movesets(regulation, moveset_keys):
    """Every allowed_species slug (or its base form) has moves in champions_movesets.json."""
    missing = sorted(
        s for s in (slugify(x) for x in regulation.allowed_species)
        if not _base_in(s, moveset_keys)
    )
    assert missing == [], f"allowed_species slugs with no moveset entry: {missing}"
