"""Offline tests against the built champions.sqlite mirror.

Covers the data layer broadly: name resolution, the type chart and weakness
stacking, movepool/legality lookups, usage statistics (including Mega-form
usage and speed tiers), team-building analysis (dependency gaps, mega
threats), and the size budgets that keep catalogue-style tool results within
MCP client limits.
"""

from __future__ import annotations

from champions_mcp import (
    legality, pokedex, reference, roles, roster, teambuilder, usage,
)
from champions_mcp.champions_stats import compute_stat
from champions_mcp.names import resolve, try_resolve


def test_db_counts(db):
    total = db.count("pokemon")
    assert total > 200
    assert db.count("type_chart") == 324  # 18 x 18
    assert db.count("moves") >= 500
    assert db.count("usage_rank") == total * 2  # one row per format


def test_name_resolution(db):
    assert resolve(db, "Garchomp") == "garchomp"
    assert resolve(db, "Carchacrok") == "garchomp"          # French
    assert resolve(db, "Alolan Ninetales") == "alolan-ninetales"
    assert resolve(db, "Mega Garchomp") == "garchomp"        # Mega -> base
    assert try_resolve(db, "Notamon") is None


def test_random_pokemon(db):
    seen = {pokedex.get_random_pokemon(db)["slug"] for _ in range(20)}
    all_slugs = set(db.all_pokemon_slugs())
    assert seen <= all_slugs
    assert len(seen) > 1  # extremely unlikely to draw the same slug 20/20 times


def test_pokemon_champions_stats_and_megas(db):
    p = pokedex.get_pokemon(db, "Garchomp")
    assert p["types"] == ["Dragon", "Ground"]
    assert p["base_stats"]["hp"] > 0
    assert any(f["name"] == "Mega Garchomp" for f in p["mega_forms"])


def test_weaknesses(db):
    w = pokedex.get_pokemon_weaknesses(db, "Garchomp")["by_multiplier"]
    assert "Ice" in w["4x"]
    assert "Electric" in w["0x"]


def test_type_chart_matches_the_canonical_gen6_chart(db):
    """Champions rebalanced movepools, items and stats but kept the standard
    18-type chart — pin every non-neutral cell so a bad data pull can't quietly
    change a matchup."""
    canonical = {
        "Normal": {"Rock": .5, "Ghost": 0, "Steel": .5},
        "Fire": {"Fire": .5, "Water": .5, "Grass": 2, "Ice": 2, "Bug": 2,
                 "Rock": .5, "Dragon": .5, "Steel": 2},
        "Water": {"Fire": 2, "Water": .5, "Grass": .5, "Ground": 2, "Rock": 2,
                  "Dragon": .5},
        "Electric": {"Water": 2, "Electric": .5, "Grass": .5, "Ground": 0,
                     "Flying": 2, "Dragon": .5},
        "Grass": {"Fire": .5, "Water": 2, "Grass": .5, "Poison": .5, "Ground": 2,
                  "Flying": .5, "Bug": .5, "Rock": 2, "Dragon": .5, "Steel": .5},
        "Ice": {"Fire": .5, "Water": .5, "Grass": 2, "Ice": .5, "Ground": 2,
                "Flying": 2, "Dragon": 2, "Steel": .5},
        "Fighting": {"Normal": 2, "Ice": 2, "Poison": .5, "Flying": .5,
                     "Psychic": .5, "Bug": .5, "Rock": 2, "Ghost": 0, "Dark": 2,
                     "Steel": 2, "Fairy": .5},
        "Poison": {"Grass": 2, "Poison": .5, "Ground": .5, "Rock": .5,
                   "Ghost": .5, "Steel": 0, "Fairy": 2},
        "Ground": {"Fire": 2, "Electric": 2, "Grass": .5, "Poison": 2,
                   "Flying": 0, "Bug": .5, "Rock": 2, "Steel": 2},
        "Flying": {"Electric": .5, "Grass": 2, "Fighting": 2, "Bug": 2,
                   "Rock": .5, "Steel": .5},
        "Psychic": {"Fighting": 2, "Poison": 2, "Psychic": .5, "Dark": 0,
                    "Steel": .5},
        "Bug": {"Fire": .5, "Grass": 2, "Fighting": .5, "Poison": .5,
                "Flying": .5, "Psychic": 2, "Ghost": .5, "Dark": 2, "Steel": .5,
                "Fairy": .5},
        "Rock": {"Fire": 2, "Ice": 2, "Fighting": .5, "Ground": .5, "Flying": 2,
                 "Bug": 2, "Steel": .5},
        "Ghost": {"Normal": 0, "Psychic": 2, "Ghost": 2, "Dark": .5},
        "Dragon": {"Dragon": 2, "Steel": .5, "Fairy": 0},
        "Dark": {"Fighting": .5, "Psychic": 2, "Ghost": 2, "Dark": .5,
                 "Fairy": .5},
        "Steel": {"Fire": .5, "Water": .5, "Electric": .5, "Ice": 2, "Rock": 2,
                  "Steel": .5, "Fairy": 2},
        "Fairy": {"Fire": .5, "Fighting": 2, "Poison": .5, "Dragon": 2,
                  "Dark": 2, "Steel": .5},
    }
    assert sorted(db.all_types()) == sorted(pokedex.TYPES_18)
    wrong = [
        (a, d, canonical[a].get(d, 1.0), db.type_multiplier(a, d))
        for a in pokedex.TYPES_18 for d in pokedex.TYPES_18
        if abs(db.type_multiplier(a, d) - canonical[a].get(d, 1.0)) > 1e-9
    ]
    assert wrong == []


def test_dual_type_stacking_is_correct_for_every_typing(db):
    """All 171 dual typings x 18 attackers, against multipliers derived straight
    from the chart — catches bucket-labelling and stacking errors."""
    from itertools import combinations
    labels = {4.0: "4x", 2.0: "2x", 1.0: "1x", 0.5: "0.5x", 0.25: "0.25x", 0.0: "0x"}
    for t1, t2 in combinations(pokedex.TYPES_18, 2):
        table = pokedex.get_pokemon_weaknesses(db, None, t1, t2)["by_multiplier"]
        for atk in pokedex.TYPES_18:
            expected = db.type_multiplier(atk, t1) * db.type_multiplier(atk, t2)
            bucket = [k for k, v in table.items() if atk in v]
            assert bucket == [labels[expected]], (t1, t2, atk, bucket)
    # an immunity on either half wins outright
    assert "Ground" in pokedex.get_pokemon_weaknesses(
        db, None, "Flying", "Fire")["by_multiplier"]["0x"]


def test_type_arguments_are_validated_and_deduplicated(db):
    """db.type_multiplier returns a neutral 1.0 for an unknown type, so an
    unvalidated typo would produce an all-neutral chart instead of an error;
    and the same type twice would square every multiplier."""
    typo = pokedex.get_pokemon_weaknesses(db, None, "Frie")
    assert "Unknown type" in typo["error"] and "by_multiplier" not in typo
    assert "Unknown type" in pokedex.get_type_matchups(db, "Frie")["error"]

    # Fire/Fire must behave as mono-Fire, not squared
    dupe = pokedex.get_pokemon_weaknesses(db, None, "Fire", "Fire")
    mono = pokedex.get_pokemon_weaknesses(db, None, "Fire")
    assert dupe["types"] == ["Fire"]
    assert dupe["by_multiplier"] == mono["by_multiplier"]
    assert "Water" in dupe["by_multiplier"]["2x"]     # not 4x
    assert pokedex.get_type_matchups(db, "Fire", "Fire")["types"] == ["Fire"]

    # whitespace and casing normalise
    assert pokedex.get_pokemon_weaknesses(db, None, " fire ", "WATER")["types"] \
        == ["Fire", "Water"]
    assert "error" in pokedex.get_pokemon_weaknesses(db)


def test_movepool_and_legality(db):
    moves = roster.get_pokemon_moves(db, "Garchomp")
    assert moves["count"] > 0
    assert any(m == "Earthquake" for m in moves["moves"])
    assert roster.is_legal_move(db, "Garchomp", "Earthquake")["legal"]
    assert not roster.is_legal_move(db, "Garchomp", "Moonblast")["legal"]


def test_by_type_ability_move(db):
    assert "garchomp" in db.pokemons_by_type("Dragon", "Ground")
    assert roster.pokemons_by_ability(db, "Intimidate")["count"] > 0
    assert roster.pokemons_by_move(db, "Protect")["count"] > 50


def test_catalogue_tools_fit_a_tool_result_budget(db):
    """MCP clients cap a single tool result (docker/chat/main.py uses
    MAX_TOOL_RESULT_CHARS). A silently truncated catalogue is worse than a
    bounded one — the model can't tell what it didn't see — so the browse-all
    calls must fit on their own."""
    import json
    budget = 16000
    for label, payload in (
        ("list_megas", pokedex.list_megas(db)),
        ("list_pokemon", roster.list_pokemon(db)),
        ("list_pokemon(vgc)", roster.list_pokemon(db, None, None, "vgc")),
        ("list_roles(vgc)", roles.list_roles(format="vgc")),
    ):
        size = len(json.dumps(payload, ensure_ascii=False))
        assert size < budget, f"{label} is {size} chars"


def test_list_megas_compact_index_then_detail(db):
    allm = pokedex.list_megas(db)
    assert allm["count"] == allm["total_megas"] == db.count("mega_forms")
    assert allm["detail"] == "compact"
    assert "truncated" not in allm            # the whole index fits
    assert "stat_changes" not in allm["megas"][0]  # deltas are the detail view

    detail = pokedex.list_megas(db, "charizard")
    assert detail["detail"] == "full"
    assert detail["matched"] < allm["total_megas"]
    assert detail["megas"] and "stat_changes" in detail["megas"][0]
    assert "base_stats" in detail["megas"][0]

    assert pokedex.list_megas(db, limit=5)["count"] == 5
    assert "truncated" in pokedex.list_megas(db, limit=5)


def test_list_pokemon_reports_the_true_match_count(db):
    page = roster.list_pokemon(db)
    assert page["count"] == db.count("pokemon")     # true total, not page size
    assert page["returned"] == len(page["pokemon"]) < page["count"]
    assert "truncated" in page

    # a narrow filter fits in one page and drops the notice
    dragons = roster.list_pokemon(db, None, "Dragon")
    assert dragons["returned"] == dragons["count"]
    assert "truncated" not in dragons

    # the resource contract still gets the whole roster
    assert roster.list_pokemon(db, limit=10_000)["returned"] == db.count("pokemon")


def test_list_items_groups_the_pool_and_hides_stones(db):
    """Mega Stones are roughly half the item pool and are chosen by which Mega
    you want, not browsed — including them by default would bury the items
    that are an actual item decision."""
    listing = reference.list_items(db)
    assert listing["mega_stones_hidden"] == db.count("items") - listing["count"]
    assert listing["count"] + listing["mega_stones_hidden"] == db.count("items")
    assert "mega-stones" not in listing["items_by_category"]

    # the pool's defining fact, stated where it will be read
    assert "only Choice item" in listing["note"]
    names = {r["name"] for r in db.query("SELECT name FROM items")}
    assert "Choice Scarf" in names
    assert not ({"Choice Band", "Choice Specs"} & names)

    # non-stone entries carry their effect text where the source has it
    non_stone = [i for group, items in listing["items_by_category"].items()
                 for i in items]
    assert sum("effect" in i for i in non_stone) >= len(non_stone) - 1

    withstones = reference.list_items(db, include_mega_stones=True)
    assert withstones["count"] == db.count("items")
    assert all("effect" not in i
               for i in withstones["items_by_category"]["mega-stones"])

    # every Mega Stone is grouped under one category regardless of its own
    # source category (a stone's category label carries no real distinction)
    assert (len(withstones["items_by_category"]["mega-stones"])
            == db.count("items") - listing["count"])

    assert reference.list_items(db, category="berry")["count"] > 20
    assert "warning" in reference.list_items(db, category="nonsense")
    assert reference.list_items(db, query="berry")["count"] > 20
    assert "truncated" in reference.list_items(db, limit=5)


def test_reference_enrichment(db):
    eq = reference.get_move(db, "Earthquake")
    assert eq["type"] == "ground" and eq["power"] == 100
    assert reference.get_item(db, "Abomasite")["is_mega_stone"]
    assert reference.get_ability(db, "Intimidate")["effect"]


def test_usage(db):
    stats = usage.get_usage_stats(db, "vgc", 5)
    assert stats["ranking"][0]["rank"] == 1

    total = db.count("pokemon")
    bottom = usage.get_usage_stats(db, "vgc", 5, order="bottom")
    assert bottom["order"] == "bottom"
    # The worst rank should be at (or very near) the roster size — a handful
    # of brand-new/zero-usage Pokémon can tie for last, so this isn't exact.
    assert total - 20 <= bottom["ranking"][0]["rank"] <= total
    assert [r["rank"] for r in bottom["ranking"]] == sorted(
        (r["rank"] for r in bottom["ranking"]), reverse=True
    )
    # order aliases both resolve to the same underlying direction
    assert usage.get_usage_stats(db, "vgc", 5, order="underdogs")["order"] == "bottom"
    assert usage.get_usage_stats(db, "vgc", 5, order="weird")["order"] == "top"

    mega_species = {row["species_slug"] for row in db.list_mega_forms()}
    megas = usage.get_usage_stats(db, "vgc", 15, megas_only=True)
    assert megas["megas_only"] is True
    assert megas["ranking"]
    assert all(r["slug"] in mega_species for r in megas["ranking"])
    # non-mega species are excluded, not just deprioritized
    assert "garchomp" in mega_species  # sanity: fixture data actually has megas
    non_megas = usage.get_usage_stats(db, "vgc", 15, megas_only=False)
    assert any(r["slug"] not in mega_species for r in non_megas["ranking"])

    sets = usage.get_pokemon_sets(db, "Garchomp", "vgc")
    assert sets["moves"] and sets["spreads"]
    assert sets["moves"][0]["percent"] is not None
    # The lists are per-slot popularity, not joint sets — the payload must say
    # so, or a model will paste the top of each list into a build nobody plays.
    # Kept to one line: the full explanation lives in the tool description, so
    # it isn't re-paid on each of the ~6 calls a team build makes.
    reminder = sets["reading_this_data"]
    assert isinstance(reminder, str) and len(reminder) < 300
    assert "NOT a set" in reminder
    assert usage.get_speed_tiers(db, "vgc", 5)["speed_tiers"]
    assert usage.suggest_cores(db, "vgc", 5)["cores"]


def test_mega_usage_is_surfaced_on_threat_data(db):
    """Several top-usage species hold a Mega Stone in almost every game, so they
    are never on the field with the base stats/typing/ability get_pokemon
    returns. Threat data must say so, or every calc against them is wrong."""
    idx = usage.mega_usage_index(db, "doubles")
    assert idx, "expected some species to be predominantly played as Megas"
    zard = idx["charizard"]
    assert zard["mega_usage_percent"] > 90
    # two Megas stay separated — they have different stats, so averaging or
    # silently picking one would be wrong
    assert {f["mega"] for f in zard["forms"]} == {"Mega Charizard X", "Mega Charizard Y"}
    assert zard["dominant"]["percent"] >= zard["forms"][-1]["percent"]

    ranked = usage.get_usage_stats(db, "vgc", 30)
    flagged = [e for e in ranked["ranking"] if "played_as_mega" in e]
    assert flagged and "mega_warning" in ranked

    sets = usage.get_pokemon_sets(db, "Charizard", "vgc")
    assert sets["played_as_mega"]["percent"] > 90
    assert "not the base form" in sets["played_as_mega"]["warning"]


def test_speed_tiers_use_the_mega_speed_when_that_is_what_is_played(db):
    """The concrete failure this prevents: a species' base Speed can be far
    below its Mega's — tiering it on the base form hands the model a
    benchmark it never actually faces."""
    tiers = usage.get_speed_tiers(db, "vgc", 30)
    by_name = {t["name"]: t for t in tiers["speed_tiers"]}

    meta = by_name["Mega Salamence"]
    assert "Salamence" not in by_name          # base form is not listed as a tier
    assert meta["as_mega"] == "Mega Salamence"
    assert meta["base_speed"] > meta["base_form_speed"]
    assert meta["positive_max"] == compute_stat(
        meta["base_speed"], 32, "speed", "jolly")
    assert "note" in tiers

    # a species with two Megas at different Speeds keeps the alternative visible
    raichu = by_name["Mega Raichu Y"]
    assert any(o["mega"] == "Mega Raichu X" for o in raichu["other_mega_speeds"])

    # a non-Mega species is untouched
    assert "as_mega" not in by_name["Sneasler"]


def test_analyze_team_flags_mega_threats(db):
    a = teambuilder.analyze_team(db, [{"species": "Garchomp"}], "vgc")
    mence = next(t for t in a["top_meta_threats_not_on_team"]
                 if t["slug"] == "salamence")
    assert mence["faced_as"] == "Mega Salamence"
    assert mence["mega_stone"] == "Salamencite"   # pass this to calc_damage
    assert "faced_as" in a["note"]


def test_speed_context_locates_trick_room_candidates(db):
    """Champions rescaled base stats (roster 40-162, median ~98), so a remembered
    'sub-60 is slow' threshold doesn't transfer. The percentile does."""
    slow = pokedex.get_pokemon(db, "Hatterene")["speed_context"]
    assert slow["roster_percentile"] <= 10
    assert "Trick Room" in slow["implication"]

    fast = pokedex.get_pokemon(db, "Sneasler")["speed_context"]
    assert fast["roster_percentile"] > 25
    assert "implication" not in fast          # nothing to warn about

    assert 90 <= slow["roster_median_speed"] <= 110


def test_protect_is_flagged_on_singles_teams_only(db):
    """Protect is near-mandatory in doubles and usually a wasted turn in singles
    — the Champions usage split (250/268 species vs 48/268) says so."""
    bss = teambuilder.analyze_team(db, [
        {"species": "Garchomp", "moves": ["Earthquake", "Protect"]},
    ], "bss")
    flagged = bss["protect_in_singles"]
    assert flagged["members"][0]["moves"] == ["Protect"]
    assert "Speed Boost" in flagged["warning"]   # the legitimate exceptions

    vgc = teambuilder.analyze_team(db, [
        {"species": "Garchomp", "moves": ["Earthquake", "Protect"]},
    ], "vgc")
    assert "protect_in_singles" not in vgc

    clean = teambuilder.analyze_team(db, [
        {"species": "Garchomp", "moves": ["Earthquake", "Dragon Claw"]},
    ], "bss")
    assert "protect_in_singles" not in clean


def test_mode_dependency_gaps_are_detected(db):
    """Regression: a model built a Trick Room core with no Trick Room setter and
    shipped it with 'assumes TR is already up'. A mode does not exist until a
    teammate sets it, so that has to surface as a gap, not a footnote."""
    broken = teambuilder.analyze_team(db, [
        {"species": "Rhyperior", "moves": ["Earthquake", "Rock Slide"]},
        {"species": "Hatterene", "moves": ["Psychic", "Dazzling Gleam"]},
    ], "bss")
    gap = next(g for g in broken["dependency_gaps"]["gaps"]
               if g["gap"] == "speed_control")
    assert "Rhyperior" in gap["members"] and "Trick Room" in gap["fix"]
    assert "BUG TO FIX" in broken["dependency_gaps"]["warning"]

    # providing the setter closes it
    fixed = teambuilder.analyze_team(db, [
        {"species": "Rhyperior", "moves": ["Earthquake", "Rock Slide"]},
        {"species": "Hatterene", "moves": ["Trick Room", "Psychic"]},
    ], "bss")
    assert "dependency_gaps" not in fixed

    # ...and so does any other speed control, not just Trick Room
    tw = teambuilder.analyze_team(db, [
        {"species": "Rhyperior", "moves": ["Earthquake", "Icy Wind"]},
    ], "bss")
    assert "dependency_gaps" not in tw

    # a slow *wall* is not a stranded attacker — no false positive
    wall = teambuilder.analyze_team(db, [
        {"species": "Toxapex", "moves": ["Recover", "Toxic"]},
    ], "bss")
    assert "dependency_gaps" not in wall


def test_stranded_check_reads_the_actual_build_not_just_base_speed(db):
    """The trick-room band is the roster's bottom quartile, which still contains
    Pokémon that can be built fast. Flagging those would be a false positive, so
    the check consults the member's nature/spread."""
    def stranded(member):
        rep = teambuilder.analyze_team(db, [member], "bss").get("dependency_gaps")
        return bool(rep and any(g["gap"] == "speed_control" for g in rep["gaps"]))

    # base Speed 80 (p24): slow build strands it, fast build does not
    assert stranded({"species": "Primarina", "nature": "modest",
                     "stat_points": {"spa": 32, "hp": 32}})
    assert not stranded({"species": "Primarina", "nature": "timid",
                         "stat_points": {"spa": 32, "spe": 32}})
    # with no nature/spread yet, don't guess for a mid-band Pokémon...
    assert not stranded({"species": "Primarina"})
    # ...but the bottom decile can never win a race however it is built
    assert stranded({"species": "Rhyperior"})


def test_weather_and_terrain_dependencies(db):
    rain = teambuilder.analyze_team(db, [
        {"species": "Kingdra", "ability": "Swift Swim", "moves": ["Waterfall"]},
    ], "vgc")
    assert [g["gap"] for g in rain["dependency_gaps"]["gaps"]] == ["weather"]

    supported = teambuilder.analyze_team(db, [
        {"species": "Kingdra", "ability": "Swift Swim"},
        {"species": "Pelipper", "ability": "Drizzle"},
    ], "vgc")
    assert "dependency_gaps" not in supported


def test_validate_team_clauses(db):
    rep = legality.validate_team(db, [
        {"species": "Garchomp", "item": "Life Orb",
         "moves": ["Earthquake"], "nature": "jolly",
         "stat_points": {"spe": 32, "atk": 32, "hp": 2}},
        {"species": "Garchomp"},  # species clause violation
    ], "vgc")
    assert not rep["legal"]
    assert any(v["rule"] == "species_clause" for v in rep["violations"])

    bad_move = legality.validate_team(db, [
        {"species": "Incineroar", "moves": ["Moonblast"]}], "vgc")
    assert any(v["rule"] == "move" for v in bad_move["violations"])


def test_validate_team_item_clause(db):
    dupe = legality.validate_team(db, [
        {"species": "Garchomp", "item": "Leftovers"},
        {"species": "Dragonite", "item": "Leftovers"},
    ], "vgc")
    assert not dupe["legal"]
    item_violations = [v for v in dupe["violations"] if v["rule"] == "item_clause"]
    assert len(item_violations) == 1
    assert item_violations[0]["member"] == "Dragonite"

    # Spelling/casing differences still resolve to the same item.
    variant = legality.validate_team(db, [
        {"species": "Garchomp", "item": "Life Orb"},
        {"species": "Dragonite", "item": "life-orb"},
    ], "vgc")
    assert any(v["rule"] == "item_clause" for v in variant["violations"])

    # Distinct items, and multiple Pokémon holding no item, are both legal.
    fine = legality.validate_team(db, [
        {"species": "Garchomp", "item": "Life Orb"},
        {"species": "Dragonite", "item": "Leftovers"},
        {"species": "Incineroar"},
        {"species": "Rillaboom"},
    ], "vgc")
    assert not any(v["rule"] == "item_clause" for v in fine["violations"])


def test_validate_team_flags_unspent_sp(db):
    full = legality.validate_team(db, [
        {"species": "Garchomp", "stat_points": {"spe": 32, "atk": 32, "hp": 2}},
    ], "vgc")
    assert not any(w["rule"] == "stat_points" for w in full["warnings"])

    partial = legality.validate_team(db, [
        {"species": "Garchomp", "stat_points": {"spe": 32}},
    ], "vgc")
    sp_warnings = [w for w in partial["warnings"] if w["rule"] == "stat_points"]
    assert len(sp_warnings) == 1
    assert "34" in sp_warnings[0]["detail"]  # 66 - 32 left unspent
    assert partial["legal"]  # under-budget is a warning, not a violation

    unset = legality.validate_team(db, [{"species": "Garchomp"}], "vgc")
    assert any(
        w["rule"] == "stat_points" and "0/66" in w["detail"] for w in unset["warnings"]
    )
