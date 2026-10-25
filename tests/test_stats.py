"""SP math — pure, no DB. Ported invariants from the Champions stat model.

Covers the stat-point spread model: the EV-alias/SP-scale distinction, the
stat-calculation formula (including HP and the Shedinja edge case), spread
validation against the SP budget, stat-key canonicalisation, and
Speed-related helpers such as item/status modifiers and the minimum SP
needed to outspeed a given target.
"""

from __future__ import annotations

from champions_mcp.champions_stats import looks_like_evs, member_stat_points


def test_spread_accepts_evs_as_an_alias_without_accepting_ev_values():
    """'EVs' is the reflex from every other game, so the key is accepted — but
    the SCALES differ (SP: 66 total / 32 per stat / 1 SP = +1; EVs: 510 / 252 /
    4 EVs = +1), so EV-scale numbers must not be silently taken as SP."""
    sp = {"atk": 32, "spe": 32, "hp": 2}
    canonical = {"attack": 32, "speed": 32, "hp": 2}
    # both keys reach the same canonicalised spread
    assert member_stat_points({"stat_points": sp}) == canonical
    assert member_stat_points({"evs": sp}) == canonical
    assert member_stat_points({}) == {}
    assert member_stat_points({"evs": {"Atk": 32}}) == {"attack": 32}
    # stat_points wins if both are somehow present
    assert member_stat_points({"stat_points": {"atk": 4}, "evs": {"atk": 8}}) \
        == {"attack": 4}

    assert not looks_like_evs(sp)
    assert looks_like_evs({"atk": 252, "spe": 252, "hp": 4})
    assert not looks_like_evs({})

from champions_mcp.champions_stats import (
    MAX_TOTAL_SP,
    canonical_stat,
    compute_stat,
    effective_speed,
    min_sp_to_outspeed,
    normalize_spread,
    validate_spread,
)


def test_garchomp_speed_benchmarks():
    # The listed stat is already the Lv-50 value at 0 SP (classic base 102 ->
    # listed 122); SP adds directly and the nature multiplies — no second
    # level formula.
    listed = 122
    assert compute_stat(listed, 0, "speed", "serious") == 122
    assert compute_stat(listed, 32, "speed", "jolly") == 169


def test_hp_formula_and_shedinja():
    assert compute_stat(183, 0, "hp", "serious") == 183      # Garchomp listed HP
    assert compute_stat(183, 32, "hp", "adamant") == 215     # nature never touches HP
    assert compute_stat(1, 32, "hp", "serious") == 1  # Shedinja-style


def test_budget_validation():
    assert validate_spread({"speed": 32, "attack": 32, "hp": 2}).ok
    over = validate_spread({"speed": 32, "attack": 32, "hp": 32})
    assert not over.ok and over.total > MAX_TOTAL_SP
    assert not validate_spread({"speed": 40}).ok  # per-stat cap


def test_canonical_and_normalize_keys():
    assert canonical_stat("spa") == "special-attack"
    assert canonical_stat("Speed") == "speed"
    sp = normalize_spread({"spa": 32, "Spe": 32, "hp": 2})
    assert sp == {"special-attack": 32, "speed": 32, "hp": 2}


def test_effective_speed_modifier_order():
    base = 100
    raw = compute_stat(base, 0, "speed", "serious")
    assert effective_speed(base, 0, "serious", choice_scarf=True) == raw * 3 // 2
    assert effective_speed(base, 0, "serious", paralyzed=True) == raw // 2


def test_min_sp_to_outspeed():
    res = {r.nature: r for r in min_sp_to_outspeed(102, 130, natures=("serious", "jolly"))}
    assert res["jolly"].min_sp is not None
    assert res["jolly"].resulting_speed > 130
