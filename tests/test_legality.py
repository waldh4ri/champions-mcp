from __future__ import annotations

from champions_mcp.config import Settings
from champions_mcp.models import Team
from champions_mcp.regulations import MegaRule, Regulation, RegulationRegistry
from champions_mcp.services.legality import LegalityService, _base_species


def _ma():
    return RegulationRegistry(Settings.load()).get("M-A")


def _svc(fake_api, names, catalog):
    return LegalityService(fake_api, names, catalog)


async def test_clean_team_is_legal(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Araquanid"}, {"species": "Incineroar"}])
    report = await svc.validate(team, _ma())
    assert report.legal is True
    assert report.roster_verified is True
    assert all(w.rule != "roster-unverified" for w in report.warnings)


async def test_legendary_and_restricted_flagged(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(Team(members=[{"species": "Miraidon"}]), _ma())
    assert report.legal is False
    rules = {v.rule for v in report.violations}
    assert "not-in-roster" in rules


async def test_mega_stone_is_legal_and_in_catalog(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    team = Team(
        members=[
            {"species": "Venusaur", "item": "Venusaurite"},
            {"species": "Araquanid"},
        ]
    )
    report = await svc.validate(team, _ma())
    assert report.legal is True
    assert all(v.rule != "item-not-in-champions" for v in report.violations)


async def test_team_size_violation(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(
        Team(members=[{"species": "Araquanid"}] * 7), _ma()
    )
    assert report.legal is False
    assert any(v.rule == "team-size" for v in report.violations)


# --- regression tests for the two reported MCP bugs -------------------


async def test_item_not_in_champions_rejected(fake_api, names, catalog):
    # Life Orb resolves in PokeAPI but does NOT exist in Champions.
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Araquanid", "item": "Life Orb"}])
    report = await svc.validate(team, _ma())
    assert report.legal is False
    assert any(v.rule == "item-not-in-champions" for v in report.violations)


async def test_item_clause_duplicate_item(fake_api, names, catalog):
    # Two Pokémon holding the same (legal) item violates the Item Clause.
    svc = _svc(fake_api, names, catalog)
    team = Team(
        members=[
            {"species": "Araquanid", "item": "Focus Sash"},
            {"species": "Incineroar", "item": "Focus Sash"},
        ]
    )
    report = await svc.validate(team, _ma())
    assert report.legal is False
    assert any(v.rule == "item-clause" for v in report.violations)
    assert all(v.rule != "item-not-in-champions" for v in report.violations)


async def test_species_clause_duplicate_species(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Araquanid"}, {"species": "Tarenbulle"}])
    report = await svc.validate(team, _ma())
    assert report.legal is False
    assert any(v.rule == "species-clause" for v in report.violations)


async def test_reported_example_pattern(fake_api, names, catalog):
    # Mirrors the user's report: Life Orb x2 + Focus Sash x2.
    svc = _svc(fake_api, names, catalog)
    team = Team(
        members=[
            {"species": "Araquanid", "item": "Life Orb"},
            {"species": "Incineroar", "item": "Life Orb"},
            {"species": "Venusaur", "item": "Focus Sash"},
            {"species": "Miraidon", "item": "Focus Sash"},
        ]
    )
    report = await svc.validate(team, _ma())
    rules = [v.rule for v in report.violations]
    assert report.legal is False
    assert rules.count("item-clause") == 2          # Life Orb + Focus Sash
    assert "item-not-in-champions" in rules         # Life Orb


async def test_illegal_spread_and_nature_flagged(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{
        "species": "Araquanid",
        "nature": "spicy",
        "stat_points": {"speed": 40, "attack": 32},
    }])
    report = await svc.validate(team, _ma())
    rules = {v.rule for v in report.violations}
    assert report.legal is False
    assert "stat-points" in rules   # 40 > 32 cap (and total > 66)
    assert "nature" in rules        # 'spicy' is not a real nature


async def test_species_not_in_roster_flagged(
    fake_api, names, catalog, roster
):
    svc = LegalityService(fake_api, names, catalog, roster)
    # Venusaur is resolvable but absent from the test roster.
    report = await svc.validate(Team(members=[{"species": "Venusaur"}]), _ma())
    assert report.legal is False
    assert any(v.rule == "not-in-champions" for v in report.violations)


async def test_verified_roster_suppresses_unverified_warning(
    fake_api, names, catalog, roster
):
    svc = LegalityService(fake_api, names, catalog, roster)
    report = await svc.validate(
        Team(members=[{"species": "Araquanid"}]), _ma()
    )
    assert report.legal is True
    assert all(w.rule != "roster-unverified" for w in report.warnings)
    assert report.roster_verified is True


async def test_valid_spread_and_nature_pass(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{
        "species": "Araquanid",
        "nature": "Jolly",
        "stat_points": {"speed": 32, "hp": 32, "attack": 2},
    }])
    report = await svc.validate(team, _ma())
    assert all(
        v.rule not in ("stat-points", "nature") for v in report.violations
    )


async def test_illegal_move_flagged(fake_api, names, catalog, roster, movesets):
    svc = LegalityService(fake_api, names, catalog, roster, movesets)
    team = Team(members=[{
        "species": "Tarenbulle",  # FR -> Araquanid (in roster + movesets)
        "moves": ["Liquidation", "Knock Off"],  # Knock Off not in its set
    }])
    report = await svc.validate(team, _ma())
    bad = [v for v in report.violations if v.rule == "illegal-move"]
    assert len(bad) == 1
    assert "Knock Off" in bad[0].detail


async def test_legal_moves_pass(fake_api, names, catalog, roster, movesets):
    svc = LegalityService(fake_api, names, catalog, roster, movesets)
    team = Team(members=[{
        "species": "Incineroar",
        "moves": ["Flare Blitz", "Fake Out", "Parting Shot", "Will-O-Wisp"],
    }])
    report = await svc.validate(team, _ma())
    assert all(v.rule != "illegal-move" for v in report.violations)


# ---------------------------------------------------------------------------
# _base_species helper
# ---------------------------------------------------------------------------


def test_base_species_strips_mega_suffixes():
    assert _base_species("venusaur-mega") == "venusaur"
    assert _base_species("charizard-mega-x") == "charizard"
    assert _base_species("charizard-mega-y") == "charizard"
    assert _base_species("groudon-primal") == "groudon"
    assert _base_species("garchomp") == "garchomp"


# ---------------------------------------------------------------------------
# Empty team
# ---------------------------------------------------------------------------


async def test_empty_team_is_illegal(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(Team(members=[]), _ma())
    assert report.legal is False
    assert any(v.rule == "team-size" for v in report.violations)


# ---------------------------------------------------------------------------
# Banned / mythical species
# ---------------------------------------------------------------------------


def _reg_with_banned(**kwargs) -> Regulation:
    """Return a minimal regulation with the given overrides applied."""
    base = dict(
        id="TEST",
        name="Test",
        format="doubles",
        team_size=6,
        level_cap=50,
        ban_categories=[],
        mega=MegaRule(allowed=True, max_per_battle=1),
    )
    base.update(kwargs)
    return Regulation.model_validate(base)


async def test_banned_species_flagged(fake_api, names, catalog):
    reg = _reg_with_banned(banned_species=["Araquanid"])
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(Team(members=[{"species": "Araquanid"}]), reg)
    assert report.legal is False
    assert any(v.rule == "banned-species" for v in report.violations)


async def test_mythical_in_ban_categories(fake_api, names, catalog):
    # Miraidon is is_legendary=True; using a mythical flag requires a test mon.
    # Re-use the legendary check path which is already tested, and also exercise
    # a mythical ban through the 'mythical' category on Miraidon's legendary check.
    reg = _reg_with_banned(ban_categories=["mythical"])
    svc = _svc(fake_api, names, catalog)
    # Miraidon is flagged legendary=True but not mythical, so no violation.
    report = await svc.validate(Team(members=[{"species": "Miraidon"}]), reg)
    assert all(v.rule != "mythical" for v in report.violations)


# ---------------------------------------------------------------------------
# Level cap
# ---------------------------------------------------------------------------


async def test_level_cap_violated(fake_api, names, catalog):
    reg = _reg_with_banned(level_cap=50)
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Araquanid", "level": 100}])
    report = await svc.validate(team, reg)
    assert report.legal is False
    assert any(v.rule == "level-cap" for v in report.violations)


async def test_level_within_cap_passes(fake_api, names, catalog):
    reg = _reg_with_banned(level_cap=50)
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(Team(members=[{"species": "Araquanid", "level": 50}]), reg)
    assert all(v.rule != "level-cap" for v in report.violations)


# ---------------------------------------------------------------------------
# Banned moves
# ---------------------------------------------------------------------------


async def test_banned_move_flagged(fake_api, names, catalog):
    reg = _reg_with_banned(banned_moves=["Liquidation"])
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Araquanid", "moves": ["Liquidation"]}])
    report = await svc.validate(team, reg)
    assert report.legal is False
    assert any(v.rule == "banned-move" for v in report.violations)


async def test_legal_move_not_banned(fake_api, names, catalog):
    reg = _reg_with_banned(banned_moves=["Earthquake"])
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Araquanid", "moves": ["Liquidation"]}])
    report = await svc.validate(team, reg)
    assert all(v.rule != "banned-move" for v in report.violations)


# ---------------------------------------------------------------------------
# Mega rules
# ---------------------------------------------------------------------------


async def test_mega_not_allowed_raises_violation(fake_api, names, catalog):
    reg = _reg_with_banned(mega=MegaRule(allowed=False))
    svc = _svc(fake_api, names, catalog)
    # Venusaurite is a mega stone -> triggers mega path
    team = Team(members=[{"species": "Venusaur", "item": "Venusaurite"}])
    report = await svc.validate(team, reg)
    assert any(v.rule == "mega" for v in report.violations)


async def test_mega_ineligible_species_flagged(fake_api, names, catalog):
    reg = _reg_with_banned(
        mega=MegaRule(allowed=True, max_per_battle=1, eligible_species=["Charizard"])
    )
    svc = _svc(fake_api, names, catalog)
    # Venusaurite makes Venusaur try to Mega-evolve, but only Charizard is eligible.
    team = Team(members=[{"species": "Venusaur", "item": "Venusaurite"}])
    report = await svc.validate(team, reg)
    assert any(v.rule == "mega-ineligible" for v in report.violations)


async def test_mega_battle_limit_warning(fake_api, names, catalog):
    # max_per_battle=1 but two Mega-capable Pokémon on the team -> warning (not violation).
    reg = _reg_with_banned(
        mega=MegaRule(allowed=True, max_per_battle=1, max_per_team=None)
    )
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[
        {"species": "Venusaur", "item": "Venusaurite"},
        {"species": "Venusaur", "item": "Venusaurite"},  # species-clause will fire too
    ])
    report = await svc.validate(team, reg)
    assert any(w.rule == "mega-battle-limit" for w in report.warnings)


# ---------------------------------------------------------------------------
# Unknown item -> warning (unverified catalog)
# ---------------------------------------------------------------------------


async def test_unknown_item_warning_without_catalog(fake_api, names):
    svc = LegalityService(fake_api, names, catalog=None)
    # "Blorg Device" is not in FakePokeAPI -> should produce unknown-item warning
    team = Team(members=[{"species": "Araquanid", "item": "Blorg Device"}])
    report = await svc.validate(team, _ma())
    assert any(w.rule == "unknown-item" for w in report.warnings)


async def test_unverified_catalog_warns_not_violates(fake_api, names, catalog):
    # Build an unverified catalog that does NOT contain Life Orb.
    from champions_mcp.champions_items import ItemCatalog, item_key
    unverified = ItemCatalog(keys={"focus-sash"}, verified=False, source_url="test")
    svc = LegalityService(fake_api, names, catalog=unverified)
    team = Team(members=[{"species": "Araquanid", "item": "Life Orb"}])
    report = await svc.validate(team, _ma())
    # Unverified: should warn, not hard-violate.
    assert all(v.rule != "item-not-in-champions" for v in report.violations)
    assert any(w.rule == "item-maybe-not-in-champions" for w in report.warnings)


# ---------------------------------------------------------------------------
# Allowlist (not-in-roster)
# ---------------------------------------------------------------------------


async def test_allowlist_rejects_unlisted_species(fake_api, names, catalog):
    reg = _reg_with_banned(allowed_species=["Incineroar"])
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(Team(members=[{"species": "Araquanid"}]), reg)
    assert report.legal is False
    assert any(v.rule == "not-in-roster" for v in report.violations)


async def test_allowlist_permits_listed_species(fake_api, names, catalog):
    reg = _reg_with_banned(allowed_species=["araquanid", "incineroar"])
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(
        Team(members=[{"species": "Araquanid"}, {"species": "Incineroar"}]), reg
    )
    assert all(v.rule != "not-in-roster" for v in report.violations)


# ---------------------------------------------------------------------------
# Unknown species -> unknown-species rule
# ---------------------------------------------------------------------------


async def test_unknown_species_flagged(fake_api, names, catalog):
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Xyzbogusmon99999"}])
    report = await svc.validate(team, _ma())
    assert report.legal is False
    assert any(v.rule == "unknown-species" for v in report.violations)


# ---------------------------------------------------------------------------
# Mythical Pokémon
# ---------------------------------------------------------------------------


async def test_mythical_in_ban_categories_flagged(fake_api, names, catalog, monkeypatch):
    from champions_mcp.models import Pokemon, Stats

    mew = Pokemon(
        slug="mew", name="Mew", types=["psychic"],
        abilities=["synchronize"],
        base_stats=Stats(hp=100, attack=100, speed=130),
        is_mythical=True,
    )

    async def patched_get_pokemon(slug):
        if slug == "mew":
            return mew
        from tests.conftest import _POKEMON
        return _POKEMON[slug]

    async def patched_raw_pokemon(name_or_id):
        if str(name_or_id).lower() == "mew":
            return {"name": "mew"}
        raise KeyError(name_or_id)

    monkeypatch.setattr(fake_api, "get_pokemon", patched_get_pokemon)
    monkeypatch.setattr(fake_api, "raw_pokemon", patched_raw_pokemon)

    reg = _reg_with_banned(ban_categories=["mythical"])
    svc = _svc(fake_api, names, catalog)
    report = await svc.validate(Team(members=[{"species": "Mew"}]), reg)
    assert report.legal is False
    assert any(v.rule == "mythical" for v in report.violations)


# ---------------------------------------------------------------------------
# Banned item
# ---------------------------------------------------------------------------


async def test_banned_item_flagged(fake_api, names, catalog):
    reg = _reg_with_banned(banned_items=["Choice Scarf"])
    svc = _svc(fake_api, names, catalog)
    team = Team(members=[{"species": "Araquanid", "item": "Choice Scarf"}])
    report = await svc.validate(team, reg)
    assert report.legal is False
    assert any(v.rule == "banned-item" for v in report.violations)


# ---------------------------------------------------------------------------
# Mega count (max_per_team exceeded)
# ---------------------------------------------------------------------------


async def test_mega_count_exceeded(fake_api, names, catalog):
    reg = _reg_with_banned(
        mega=MegaRule(allowed=True, max_per_team=1, max_per_battle=2)
    )
    svc = _svc(fake_api, names, catalog)
    # Two mega-stone holders -> mega_count = 2 > max_per_team = 1
    team = Team(members=[
        {"species": "Venusaur", "item": "Venusaurite"},
        {"species": "Venusaur", "item": "Venusaurite"},  # species-clause fires too
    ])
    report = await svc.validate(team, reg)
    assert any(v.rule == "mega-count" for v in report.violations)
