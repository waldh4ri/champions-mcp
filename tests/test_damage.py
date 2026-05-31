from __future__ import annotations

from champions_mcp.config import Settings
from champions_mcp.damage import CHAMPIONS_GEN, DamageCalculator


def test_champions_gen_is_zero():
    assert CHAMPIONS_GEN == 0  # @smogon/calc native Champions mode


def _dc(fake_api, names) -> DamageCalculator:
    return DamageCalculator(Settings.load(), names, fake_api)


def test_side_options_passes_raw_sp(fake_api, names):
    dc = _dc(fake_api, names)
    opts, warns = dc._side_options(
        "jolly", {"attack": 32, "speed": 16, "hp": 0},
        {"attack": 1}, "Intimidate", "Choice Scarf", "par",
    )
    # gen-0: SP passed straight through; no EV conversion, no 252 cap.
    assert opts["evs"] == {"atk": 32, "spe": 16}   # hp:0 omitted
    # Lv 50 / 31 IVs are forced by the library for gen 0 — don't send them.
    assert "level" not in opts
    assert "ivs" not in opts
    assert opts["nature"] == "Jolly"
    assert opts["boosts"] == {"atk": 1}
    assert opts["ability"] == "Intimidate"
    assert opts["item"] == "Choice Scarf"
    assert opts["status"] == "par"
    assert warns == []


def test_side_options_flags_over_budget_spread(fake_api, names):
    dc = _dc(fake_api, names)
    # 40 > 32 per-stat cap AND 40+32 = 72 > 66 SP budget.
    _, warns = dc._side_options(
        None, {"speed": 40, "attack": 32}, None, None, None, None
    )
    assert any("32" in w for w in warns)   # per-stat cap reported
    assert any("66" in w for w in warns)   # SP budget reported


async def test_calc_species_name_base_and_regional(fake_api, names):
    dc = _dc(fake_api, names)
    assert await dc._calc_species_name("Venusaur") == "Venusaur"
    assert await dc._calc_species_name("Tarenbulle") == "Araquanid"  # FR seed
    assert (
        await dc._calc_species_name("Sandslash-Alola") == "Sandslash-Alola"
    )


async def test_calculate_builds_champions_payload(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)
    captured = {}

    async def fake_run(payload):
        captured.update(payload)
        return {"ok": True, "min_damage": 50, "max_damage": 60,
                "defender_max_hp": 200, "description": "x", "ko_chance": "2HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    out = await dc.calculate({
        "attacker": "Tarenbulle", "move": "Liquidation",
        "defender": "Incineroar",
        "attacker_stat_points": {"attack": 32},
        "attacker_nature": "adamant",
        "weather": "sand", "game_type": "doubles",
    })
    assert captured["gen"] == 0                       # native Champions mode
    assert captured["attacker"]["name"] == "Araquanid"  # FR -> EN
    assert captured["attacker"]["options"]["evs"] == {"atk": 32}  # raw SP
    assert captured["field"]["gameType"] == "Doubles"
    assert captured["field"]["weather"] == "Sand"
    assert "ok" not in out
    assert out["champions"]["sp_budget"] == 66
    assert out["game_type"] == "Doubles"


async def test_calculate_unresolved_species(fake_api, names):
    dc = _dc(fake_api, names)
    out = await dc.calculate({
        "attacker": "Notamon", "move": "Tackle", "defender": "Incineroar",
    })
    assert "error" in out


async def test_calc_species_name_mega_returns_base(fake_api, names, monkeypatch):
    # Mega slugs should return the base Pokémon name so the calc handles the form
    # automatically from the held stone.
    from champions_mcp.models import Pokemon, Stats

    mega_mon = Pokemon(
        slug="venusaur-mega", name="Venusaur",
        types=["grass", "poison"],
        abilities=["thick-fat"],
        base_stats=Stats(hp=80, attack=100, speed=80),
    )

    async def fake_resolve(name):
        return "venusaur-mega"

    async def fake_get_pokemon(slug):
        return mega_mon

    dc = _dc(fake_api, names)
    monkeypatch.setattr(names, "resolve", fake_resolve)
    monkeypatch.setattr(fake_api, "get_pokemon", fake_get_pokemon)

    result = await dc._calc_species_name("Venusaur-mega")
    assert result == "Venusaur"  # mega suffix stripped; base name returned


async def test_calculate_terrain_in_field(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)
    captured = {}

    async def fake_run(payload):
        captured.update(payload)
        return {"ok": True, "min_damage": 10, "max_damage": 20,
                "defender_max_hp": 100, "description": "x", "ko_chance": "3HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar", "terrain": "grassy",
    })
    assert captured["field"]["terrain"] == "Grassy"


async def test_calculate_unknown_weather_warns(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)

    async def fake_run(payload):
        return {"ok": True, "min_damage": 10, "max_damage": 20,
                "defender_max_hp": 100, "description": "x", "ko_chance": "3HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    out = await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar", "weather": "blizzard_fog",
    })
    assert "warnings" in out
    assert any("blizzard_fog" in w for w in out["warnings"])


async def test_calculate_unknown_terrain_warns(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)

    async def fake_run(payload):
        return {"ok": True, "min_damage": 10, "max_damage": 20,
                "defender_max_hp": 100, "description": "x", "ko_chance": "3HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    out = await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar", "terrain": "swampy",
    })
    assert "warnings" in out
    assert any("swampy" in w for w in out["warnings"])


async def test_calculate_crit_and_move_hits(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)
    captured = {}

    async def fake_run(payload):
        captured.update(payload)
        return {"ok": True, "min_damage": 10, "max_damage": 20,
                "defender_max_hp": 100, "description": "x", "ko_chance": "3HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar", "crit": True, "move_hits": 3,
    })
    assert captured["move"]["options"]["isCrit"] is True
    assert captured["move"]["options"]["hits"] == 3


async def test_calculate_helping_hand_in_attacker_side(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)
    captured = {}

    async def fake_run(payload):
        captured.update(payload)
        return {"ok": True, "min_damage": 10, "max_damage": 20,
                "defender_max_hp": 100, "description": "x", "ko_chance": "3HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar", "helping_hand": True,
    })
    assert captured["field"]["attackerSide"]["isHelpingHand"] is True


async def test_calculate_singles_game_type(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)
    captured = {}

    async def fake_run(payload):
        captured.update(payload)
        return {"ok": True, "min_damage": 10, "max_damage": 20,
                "defender_max_hp": 100, "description": "x", "ko_chance": "3HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    out = await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar", "game_type": "singles",
    })
    assert captured["field"]["gameType"] == "Singles"
    assert out["game_type"] == "Singles"


async def test_calculate_attacker_tailwind(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)
    captured = {}

    async def fake_run(payload):
        captured.update(payload)
        return {"ok": True, "min_damage": 10, "max_damage": 20,
                "defender_max_hp": 100, "description": "x", "ko_chance": "3HKO"}

    monkeypatch.setattr(dc, "_run", fake_run)
    await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar", "attacker_tailwind": True,
    })
    assert captured["field"]["attackerSide"]["isTailwind"] is True


async def test_calculate_run_failure_returns_error(fake_api, names, monkeypatch):
    dc = _dc(fake_api, names)

    async def fake_run_fail(payload):
        return {"ok": False, "error": "calc engine crashed"}

    monkeypatch.setattr(dc, "_run", fake_run_fail)
    out = await dc.calculate({
        "attacker": "Araquanid", "move": "Liquidation",
        "defender": "Incineroar",
    })
    assert "error" in out
    assert "calc engine crashed" in out["error"]
