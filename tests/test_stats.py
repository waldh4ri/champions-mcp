from __future__ import annotations

import pytest

from champions_mcp.champions_stats import (
    StatError,
    compute_stat,
    effective_speed,
    min_sp_to_outspeed,
    normalize_nature,
    validate_spread,
)

# Garchomp base: Spe 102, HP 108  (cross-checked vs the old 252 EV system)


def test_speed_formula_matches_old_ev_system():
    assert compute_stat(102, 0, "speed", "serious") == 122
    assert compute_stat(102, 32, "speed", "serious") == 154   # 252 neutral
    assert compute_stat(102, 32, "speed", "jolly") == 169      # 252+ Jolly
    assert compute_stat(102, 0, "speed", "jolly") == 134
    assert compute_stat(102, 0, "speed", "brave") == 109       # -Spe nature


def test_hp_formula_and_nature_never_touches_hp():
    assert compute_stat(108, 0, "hp", "serious") == 183
    assert compute_stat(108, 32, "hp", "serious") == 215
    assert compute_stat(108, 32, "hp", "jolly") == 215  # nature ignored on HP


def test_shedinja_hp_is_one():
    assert compute_stat(1, 32, "hp", "serious") == 1


def test_compute_stat_rejects_out_of_range_sp():
    with pytest.raises(StatError):
        compute_stat(100, 33, "speed", "serious")


def test_validate_spread_rules():
    assert validate_spread({"speed": 32, "attack": 32, "hp": 2}).ok
    assert not validate_spread({"speed": 33}).ok                 # per-stat cap
    assert not validate_spread({"speed": 32, "attack": 32, "hp": 3}).ok  # 67
    assert not validate_spread({"speed": -4}).ok
    assert not validate_spread({"speed": 10.5}).ok               # not int
    assert not validate_spread({"spede": 4}).ok                  # bad stat
    chk = validate_spread({"speed": 32, "hp": 34})
    assert chk.total == 66 and not chk.ok and len(chk.violations) >= 1


def test_effective_speed_modifier_order():
    base_spd = compute_stat(102, 0, "speed", "serious")  # 122
    assert effective_speed(102, 0, "serious", choice_scarf=True) == 183
    assert effective_speed(102, 0, "serious", tailwind=True) == 244
    assert effective_speed(102, 0, "serious", paralyzed=True) == 61
    assert effective_speed(102, 0, "serious", stage=1) == 183
    assert effective_speed(102, 0, "serious", stage=-1) == 81
    assert base_spd == 122


def test_min_sp_to_outspeed_neutral_vs_plus_nature():
    # Target: a neutral 0-SP Dragapult (base 142) -> 162 Speed.
    target = effective_speed(142, 0, "serious")
    assert target == 162
    res = {r.nature: r for r in min_sp_to_outspeed(
        102, target, natures=("serious", "jolly"))}
    # Neutral Garchomp tops out at 122+32=154 < 162 -> impossible.
    assert res["serious"].min_sp is None
    # Jolly needs 27 SP -> 163, leaving 39 SP of the 66 budget.
    assert res["jolly"].min_sp == 27
    assert res["jolly"].resulting_speed == 163
    assert res["jolly"].sp_remaining_budget == 39


def test_tie_is_enough_changes_threshold():
    target = effective_speed(102, 0, "serious")  # mirror Garchomp = 122
    faster = min_sp_to_outspeed(102, target, natures=("serious",))[0]
    tie = min_sp_to_outspeed(
        102, target, natures=("serious",), tie_is_enough=True)[0]
    assert faster.min_sp == 1   # need 123 to beat 122
    assert tie.min_sp == 0      # 122 == 122 is enough


def test_normalize_nature():
    assert normalize_nature(None) == "serious"
    assert normalize_nature("Jolly") == "jolly"
    with pytest.raises(StatError):
        normalize_nature("spicy")
