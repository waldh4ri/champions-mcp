"""Tests for the Smogon/Showdown historical-sets fallback.

Split like test_builds.py: pure ingestion-logic tests run against a synthetic
in-memory database and always execute; a couple of read-model checks use the
session `db` fixture and skip when the local mirror has no smogon_sets yet.
"""

from __future__ import annotations

import json

import pytest

from champions_mcp import builds as builds_read
from champions_mcp import historical_sets
from champions_mcp.db import Db
from champions_mcp.pull.smogon import classify_format, ingest_smogon_sets


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #

@pytest.fixture()
def mini_db(tmp_path) -> Db:
    """A minimal roster with the reference tables historical_sets checks
    legality against."""
    db = Db(tmp_path / "mini.sqlite", create=True)
    db.executemany(
        "INSERT INTO pokemon VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("garchomp", "Garchomp", "Garchomp", "Garchomp", None, "[]", "[]",
             '{"hp":108,"attack":130,"defense":95,"special_attack":80,'
             '"special_defense":85,"speed":102}', 600, "[]", None, "{}",
             None, None),
            ("wobbuffet", "Wobbuffet", "Wobbuffet", "Wobbuffet", None, "[]", "[]",
             '{"hp":190,"attack":33,"defense":58,"special_attack":33,'
             '"special_defense":58,"speed":33}', 405, "[]", None, "{}",
             None, None),
        ],
    )
    db.executemany(
        "INSERT INTO learnable_moves VALUES (?,?,?)",
        [
            ("garchomp", "earthquake", "Earthquake"),
            ("garchomp", "dragon-claw", "Dragon Claw"),
            ("garchomp", "swords-dance", "Swords Dance"),
            # Deliberately no "outrage" — used to test an illegal move.
        ],
    )
    db.executemany(
        "INSERT INTO items VALUES (?,?,?,?,?)",
        [
            ("choice-scarf", "Choice Scarf", "choice", None, 0),
            ("life-orb", "Life Orb", "held-items", None, 0),
            # Deliberately no "assault-vest" — used to test an illegal item.
        ],
    )
    db.executemany(
        "INSERT INTO pokemon_abilities VALUES (?,?,?,?)",
        [("garchomp", "rough-skin", "Rough Skin", None)],
    )
    return db


def _set_row(
    slug: str, fmt: str, source_format: str, generation: int, set_name: str,
    *, ability: str, item: str, nature: str, moves: list[str],
) -> tuple:
    from champions_mcp.pull.smogon import _set_hash, _set_id
    return (
        _set_id(slug, fmt, source_format, set_name), slug, fmt, source_format,
        generation, set_name, ability, item, nature, json.dumps(moves),
        json.dumps({"atk": 252, "spe": 252}),
        _set_hash(slug, item, ability, nature, moves),
    )


# --------------------------------------------------------------------- #
# Format classification
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("format_id", "expected"),
    [
        ("gen9ou", "singles"),
        ("gen9ubers", "singles"),
        ("gen9lc", "singles"),
        ("gen91v1", "singles"),
        ("gen9monotype", "singles"),
        ("gen9doublesou", "doubles"),
        ("gen6vgc2016", "doubles"),
        ("gen7battlespotdoubles", "doubles"),
        ("gen8vgc2020", "doubles"),
    ],
)
def test_classify_format(format_id, expected):
    assert classify_format(format_id) == expected


# --------------------------------------------------------------------- #
# Legality flagging + dedup
# --------------------------------------------------------------------- #

def test_historical_sets_flags_champions_legality(mini_db):
    mini_db.executemany(
        "INSERT INTO smogon_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _set_row(
                "garchomp", "singles", "gen9ou", 9, "Choice Scarf",
                ability="Rough Skin", item="Choice Scarf", nature="Jolly",
                moves=["Earthquake", "Dragon Claw", "Swords Dance", "Outrage"],
            ),
        ],
    )
    out = historical_sets.get_historical_sets(mini_db, "Garchomp", "bss")
    assert out["evidence"] == "smogon_historical"
    assert out["count"] == 1
    s = out["sets"][0]
    assert s["item_legal_in_champions"] is True
    assert s["ability_legal_in_champions"] is True
    moves_by_name = {m["name"]: m["legal_in_champions"] for m in s["moves"]}
    assert moves_by_name["Earthquake"] is True
    assert moves_by_name["Outrage"] is False           # not in learnable_moves
    assert s["legal_in_champions"] is False            # one illegal move sinks it
    assert s["invested_stats"][:2] == ["attack", "speed"]


def test_historical_sets_flags_illegal_item_and_ability(mini_db):
    mini_db.executemany(
        "INSERT INTO smogon_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _set_row(
                "garchomp", "singles", "gen9ou", 9, "Assault Vest",
                ability="Sand Veil", item="Assault Vest", nature="Careful",
                moves=["Earthquake", "Dragon Claw"],
            ),
        ],
    )
    out = historical_sets.get_historical_sets(mini_db, "garchomp", "bss")
    s = out["sets"][0]
    assert s["item_legal_in_champions"] is False       # not in `items`
    assert s["ability_legal_in_champions"] is False    # not in `pokemon_abilities`
    assert s["legal_in_champions"] is False


def test_historical_sets_dedups_across_generations(mini_db):
    """The same build published under three tiers/generations collapses to
    one entry, with every source format listed and the newest gen's values
    kept as canonical."""
    common = dict(
        ability="Rough Skin", item="Choice Scarf", nature="Jolly",
        moves=["Earthquake", "Dragon Claw"],
    )
    mini_db.executemany(
        "INSERT INTO smogon_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _set_row("garchomp", "singles", "gen7ou", 7, "Choice Scarf", **common),
            _set_row("garchomp", "singles", "gen8ou", 8, "Choice Scarf", **common),
            _set_row("garchomp", "singles", "gen9ou", 9, "Choice Scarf", **common),
        ],
    )
    out = historical_sets.get_historical_sets(mini_db, "garchomp", "bss")
    assert out["count"] == 1
    s = out["sets"][0]
    assert s["newest_generation"] == 9
    assert s["seen_in"] == ["gen7ou", "gen8ou", "gen9ou"]


def test_historical_sets_respects_format(mini_db):
    mini_db.executemany(
        "INSERT INTO smogon_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _set_row(
                "garchomp", "doubles", "gen9doublesou", 9, "Doubles Support",
                ability="Rough Skin", item="Life Orb", nature="Jolly",
                moves=["Earthquake", "Swords Dance"],
            ),
        ],
    )
    assert historical_sets.get_historical_sets(mini_db, "garchomp", "vgc")["count"] == 1
    assert historical_sets.get_historical_sets(mini_db, "garchomp", "bss")["count"] == 0


def test_historical_sets_unresolved_pokemon(mini_db):
    out = historical_sets.get_historical_sets(mini_db, "Notamon", "vgc")
    assert "error" in out


def test_historical_sets_empty_is_not_an_error(mini_db):
    out = historical_sets.get_historical_sets(mini_db, "wobbuffet", "vgc")
    assert out["count"] == 0
    assert out["sets"] == []
    assert "note" in out


# --------------------------------------------------------------------- #
# get_observed_sets fallback wiring
# --------------------------------------------------------------------- #

def test_get_observed_sets_falls_back_to_historical_when_corpus_is_empty(mini_db):
    mini_db.executemany(
        "INSERT INTO smogon_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _set_row(
                "garchomp", "doubles", "gen9doublesou", 9, "Choice Scarf",
                ability="Rough Skin", item="Choice Scarf", nature="Jolly",
                moves=["Earthquake", "Dragon Claw"],
            ),
        ],
    )
    # No build_teams/build_members rows at all -> builds_available() is None.
    out = builds_read.get_observed_sets(mini_db, "garchomp", "vgc")
    assert "historical_sets" in out
    assert out["historical_sets"][0]["item"] == "Choice Scarf"
    assert "historical_sets_note" in out


def test_get_observed_sets_omits_historical_fallback_with_no_smogon_data(mini_db):
    out = builds_read.get_observed_sets(mini_db, "garchomp", "vgc")
    assert "historical_sets" not in out
