"""Export gate: a paste is the finished deliverable, so it must be complete.

A member missing its ability, nature or spread still renders as a perfectly
valid-looking Showdown block — the omission is invisible in the output and
silently becomes an in-game default for whoever imports it. So the gap has to be
caught before publishing, not after.
"""

from __future__ import annotations

import asyncio

from champions_mcp import legality, pokepaste

FULL = {
    "species": "Garchomp", "item": "Life Orb", "ability": "Rough Skin",
    "nature": "jolly", "moves": ["Earthquake", "Dragon Claw", "Rock Slide", "Protect"],
    "stat_points": {"atk": 32, "spe": 32, "hp": 2},
}


def test_complete_member_passes():
    assert pokepaste.check_team_complete([FULL]) == []


def test_every_missing_field_is_named():
    missing = pokepaste.check_team_complete([{"species": "Garchomp"}])[0]["missing"]
    assert {"ability", "item", "nature", "moves"} <= set(missing)
    assert "stat_points" in missing

    # a partial moveset is reported with its count, not silently accepted
    partial = pokepaste.check_team_complete([
        {**FULL, "moves": ["Earthquake", "Dragon Claw"]}])[0]["missing"]
    assert any("2/4" in x for x in partial)

    # under-spent SP is a gap too — the rest is free stats being thrown away
    under = pokepaste.check_team_complete([
        {**FULL, "stat_points": {"atk": 32}}])[0]["missing"]
    assert any("32/66" in x for x in under)

    # the member is named so the model knows which one to fix
    assert pokepaste.check_team_complete([{"species": "Garchomp"}])[0]["member"] \
        == "Garchomp"
    assert pokepaste.check_team_complete([{}])[0]["member"] == "member 1"


def test_incomplete_team_is_not_published(db):
    out = asyncio.run(pokepaste.create_pokepaste(db, [{"species": "Garchomp"}]))
    assert "error" in out and "url" not in out and "paste" not in out
    assert out["incomplete_members"]
    assert "call create_pokepaste again" in out["next_step"]
    assert asyncio.run(pokepaste.create_pokepaste(db, []))["error"]


def test_evs_key_is_accepted_but_ev_values_are_not(db):
    """The key is the reflex from other games; the values are a different scale."""
    as_evs = {k: v for k, v in FULL.items() if k != "stat_points"}
    as_evs["evs"] = FULL["stat_points"]
    assert pokepaste.check_team_complete([as_evs]) == []
    assert "EVs: 2 HP / 32 Atk / 32 Spe" in pokepaste.member_to_showdown(db, as_evs)

    ev_scale = {**FULL, "stat_points": {"atk": 252, "spe": 252, "hp": 4}}
    assert any("look like EVs" in x
               for x in pokepaste.check_team_complete([ev_scale])[0]["missing"])
    report = legality.validate_team(db, [ev_scale], "vgc")
    assert any("looks like EVs" in v["detail"] for v in report["violations"])
    assert not report["legal"]
