"""Tests for the derived type/stat profile tagging.

The scoring is validated against typings whose competitive reputation is settled
(Steel/Fairy is elite defensively, Ice/Rock is the canonical worst), so a change
that quietly inverts a weighting fails here rather than in a team.
"""

from __future__ import annotations

import json

from champions_mcp import pokedex, profiles


def test_type_profile_scoring_matches_known_reputations(db):
    ice_rock = profiles.type_profile(db, "Ice", "Rock")
    # the textbook "great offence, terrible defence" typing
    assert ice_rock["defensive"]["percentile"] <= 5
    assert ice_rock["offensive"]["percentile"] >= 75
    assert "def:liability" in ice_rock["tags"]
    assert "def:double-weakness" in ice_rock["tags"]
    assert len(ice_rock["offensive"]["super_effective"]) >= 6

    steel_fairy = profiles.type_profile(db, "Steel", "Fairy")
    assert steel_fairy["defensive"]["percentile"] >= 90
    assert "def:fortress" in steel_fairy["tags"]
    assert steel_fairy["defensive"]["weak_4x"] == []

    # order must not matter, and the label reflects what was asked
    assert profiles.type_profile(db, "Rock", "Ice")["defensive"] == \
        ice_rock["defensive"]


def test_type_profiles_cover_every_typing_and_reuse_pokedex_validation(db):
    all_p = profiles._all_type_profiles(db)
    assert len(all_p) == 18 + (18 * 17 // 2)          # 18 singles + 153 pairs
    # every roster Pokémon's typing must resolve to a profile — the cache is
    # keyed on the sorted tuple, and a mismatch there silently drops type tags
    for slug in db.all_pokemon_slugs():
        types = db.get_pokemon_row(slug).get("types", [])
        assert tuple(sorted(types)) in all_p, (slug, types)
        tags = profiles.pokemon_profile(db, slug)["tags"]
        assert any(t.startswith("def:") for t in tags), slug
        assert any(t.startswith("off:") for t in tags), slug

    assert "error" in profiles.type_profile(db, "Frie")
    assert profiles.type_profile(db, "Fire", "Fire")["types"] == ["Fire"]


def test_stat_profiles_are_relative_to_this_roster(db):
    """Champions rescaled base stats, so tags must come from roster percentiles
    rather than absolute thresholds carried in from another game."""
    fast = profiles.stat_profile(db, "sneasler")
    assert fast["percentiles"]["spe"] >= 90
    assert "build:very-fast" in fast["tags"]

    slow = profiles.stat_profile(db, "hatterene")
    assert slow["percentiles"]["spe"] <= 10
    assert "build:trick-room" in slow["tags"]

    # The Trick Room band is the bottom QUARTILE, calibrated against the species
    # the meta actually builds with a minus-Speed nature — those cluster from p0
    # to p24 and then jump to p31, so a bottom-decile cut missed most of them.
    edge = profiles.stat_profile(db, "primarina")        # base Speed 80 ~ p24
    assert 10 < edge["percentiles"]["spe"] <= 25
    assert "build:trick-room" in edge["tags"]

    # the speed bands partition rather than overlap
    for slug in db.all_pokemon_slugs():
        speed_tags = {t for t in profiles.pokemon_profile(db, slug)["tags"]
                      if t in {"build:very-fast", "build:fast", "build:slow",
                               "build:trick-room"}}
        assert len(speed_tags - {"build:very-fast", "build:fast"}) <= 1, slug
        assert not ({"build:slow", "build:trick-room"} <= speed_tags)

    wall = profiles.stat_profile(db, "toxapex")
    assert "build:bulky-both-sides" in wall["tags"]
    assert "build:no-offence" in wall["tags"]

    # bulk is a product, not an average: HP multiplies both defences
    pop = profiles._stat_population(db)
    bs = profiles._entities(db)["toxapex"]["stats"]
    assert bs["hp"] * bs["defense"] in pop["phys_bulk"]

    # the population is the base roster only, so a Mega is measured on the same
    # yardstick as its base form — that comparison is the point
    assert len(pop["spe"]) == len(db.all_pokemon_slugs())


def test_every_tag_is_documented_and_none_is_vacuous(db):
    vocab = profiles.list_profile_tags(db)
    documented = set(profiles.TAG_MEANINGS)
    emitted = set()
    for key in profiles._entities(db):          # base forms AND Megas
        emitted |= set(profiles.pokemon_profile(db, key)["tags"])
    assert emitted <= documented, f"undocumented tags: {emitted - documented}"

    # every documented tag must be reachable, or it is dead vocabulary that
    # sends the model searching for nothing
    counts = {t["tag"]: t["forms"]
              for group in vocab["tags_by_namespace"].values() for t in group}
    assert [t for t, n in counts.items() if n == 0] == []
    # and no archetype tag may be so broad it carries no signal
    builds = {t: n for t, n in counts.items() if t.startswith("build:")}
    assert max(builds.values()) < len(profiles._entities(db)) * 0.45


def test_search_by_profile(db):
    tr = profiles.search_pokemon_by_profile(
        db, ["build:trick-room", "stat:atk-elite"])
    assert tr["count"] >= 1
    for hit in tr["pokemon"]:
        tags = profiles.pokemon_profile(db, hit["slug"])["tags"]
        assert {"build:trick-room", "stat:atk-elite"} <= set(tags)

    # 'any' is a superset of 'all'
    any_hits = profiles.search_pokemon_by_profile(
        db, ["build:trick-room", "stat:atk-elite"], match="any")
    assert any_hits["count"] > tr["count"]

    # unknown tags are rejected, not silently ignored
    bad = profiles.search_pokemon_by_profile(db, ["build:not-a-tag"])
    assert "error" in bad and bad["known_tags"]
    assert "error" in profiles.search_pokemon_by_profile(db, [])

    # a contradictory combination explains itself rather than returning bare []
    empty = profiles.search_pokemon_by_profile(
        db, ["build:trick-room", "build:very-fast"])
    assert empty["count"] == 0 and "hint" in empty

    # format sorts by usage rank
    ranked = profiles.search_pokemon_by_profile(
        db, ["build:physical-attacker"], format="vgc")
    ranks = [p["usage_rank"] for p in ranked["pokemon"] if p["usage_rank"]]
    assert ranks == sorted(ranks)


def test_coverage_for_weaknesses_suggests_real_legal_moves(db):
    """Weak to Ice -> carry a Rock/Fighting/Steel/Fire move. The value is that
    the suggestion is drawn from the Pokémon's own Champions movepool, not from
    an abstract 'you want Rock coverage'."""
    from champions_mcp import roster

    c = profiles.coverage_for_weaknesses(db, "garchomp")
    ice = next(w for w in c["weaknesses"] if w["weak_to"] == "Ice")
    assert ice["multiplier"] == "4x"
    # 4x weaknesses lead, since those are the ones that actually remove it
    assert c["weaknesses"][0]["multiplier"] == "4x"
    assert set(ice["answered_by_types"]) == {"Fire", "Fighting", "Rock", "Steel"}

    legal = {m["move_name"] for m in db.learnable_moves("garchomp")}
    for w in c["weaknesses"]:
        for mv in w["legal_moves"]:
            assert mv["name"] in legal, mv           # never suggest an illegal move
            assert mv["type"] in w["answered_by_types"]
            assert (mv["power"] or 0) > 0            # damaging moves only

    # ordering prefers the Pokémon's stronger offensive side
    assert c["prefers"] == "physical"                 # Garchomp
    assert profiles.coverage_for_weaknesses(db, "charizard")["prefers"] == "special"
    first = ice["legal_moves"][0]
    assert first["category"] == "physical"

    # it is advisory, and says so
    assert "NOT a rule" in c["guidance"]

    # surfaced at the moment moves are chosen
    mp = roster.get_pokemon_moves(db, "Garchomp")
    assert mp["coverage_for_weaknesses"]["weaknesses"]


def test_coverage_admits_when_a_weakness_cannot_be_answered(db):
    """A Pokémon with no legal move covering a weakness must say so rather than
    return an empty list the model could read as 'nothing needed'."""
    seen_gap = False
    for slug in db.all_pokemon_slugs():
        for w in profiles.coverage_for_weaknesses(db, slug)["weaknesses"]:
            if not w["legal_moves"]:
                assert "teammate" in w["note"]
                seen_gap = True
    assert seen_gap, "expected at least one uncoverable weakness on the roster"


def test_get_pokemon_exposes_the_profile(db):
    p = pokedex.get_pokemon(db, "Garchomp")
    assert p["profile"]["tags"]
    assert p["profile"]["typing"]["types"] == ["Dragon", "Ground"]
    assert "Ice" in p["profile"]["typing"]["weak_4x"]
    assert "Electric" in p["profile"]["typing"]["immune"]
    assert set(p["profile"]["stat_percentiles"]) >= {"hp", "atk", "spe", "bst"}


def test_megas_are_profiled_as_their_own_pokemon(db):
    """A Mega has its own typing, stats and speed tier. Profiling it as its base
    form is wrong for exactly the Pokémon that matter most, since several
    top-usage species hold their stone in nearly every game."""
    ents = profiles._entities(db)
    assert len(ents) == len(db.all_pokemon_slugs()) + db.count("mega_forms")

    # Mega Charizard X is Fire/Dragon, not Fire/Flying: Rock hits it 2x, not 4x
    base = pokedex.get_pokemon_weaknesses(db, "Charizard")
    mega = pokedex.get_pokemon_weaknesses(db, "Mega Charizard X")
    assert base["types"] == ["Fire", "Flying"] and "Rock" in base["by_multiplier"]["4x"]
    assert mega["types"] == ["Fire", "Dragon"] and mega["is_mega"]
    assert "Rock" in mega["by_multiplier"]["2x"]

    # the profile follows the Mega's own numbers
    mprof = profiles.pokemon_profile(db, "Mega Charizard X")
    assert mprof["is_mega"] and mprof["mega_stone"] == "Charizardite X"
    assert mprof["typing"]["types"] == ["Fire", "Dragon"]
    assert mprof["tags"] != profiles.pokemon_profile(db, "charizard")["tags"]

    # Mega Metagross is faster than its base form, and the percentile says so
    assert profiles.stat_profile(db, "Mega Metagross")["percentiles"]["spe"] > \
        profiles.stat_profile(db, "metagross")["percentiles"]["spe"]

    # coverage is recomputed on the Mega's weaknesses, from the shared movepool
    cov = profiles.coverage_for_weaknesses(db, "Mega Charizard X")
    assert cov["is_mega"] and cov["types"] == ["Fire", "Dragon"]
    legal = {m["move_name"] for m in db.learnable_moves("charizard")}
    for w in cov["weaknesses"]:
        for mv in w["legal_moves"]:
            assert mv["name"] in legal

    # get_pokemon carries a profile per Mega form
    forms = pokedex.get_pokemon(db, "Charizard")["mega_forms"]
    assert forms and all("profile" in f and "speed_context" in f for f in forms)

    # and Megas are findable by search, tagged with the stone to hold
    hits = profiles.search_pokemon_by_profile(
        db, ["build:very-fast"], include_megas=True)
    megas = [h for h in hits["pokemon"] if h.get("is_mega")]
    assert megas and all(h["mega_stone"] for h in megas)
    assert not any(h.get("is_mega") for h in profiles.search_pokemon_by_profile(
        db, ["build:very-fast"], include_megas=False)["pokemon"])


def test_profile_payloads_fit_a_tool_result_budget(db):
    for label, payload in (
        ("list_type_profiles", profiles.list_type_profiles(db, limit=30)),
        ("list_profile_tags", profiles.list_profile_tags(db)),
        ("search", profiles.search_pokemon_by_profile(
            db, ["build:physical-attacker"], limit=40)),
        ("get_pokemon", pokedex.get_pokemon(db, "Garchomp")),
    ):
        size = len(json.dumps(payload, ensure_ascii=False))
        assert size < 16000, f"{label} is {size} chars"

    listing = profiles.list_type_profiles(db, limit=30)
    assert listing["returned"] == 30 < listing["matched"]
    assert "truncated" in listing
    assert "error" in profiles.list_type_profiles(db, sort="sideways")
