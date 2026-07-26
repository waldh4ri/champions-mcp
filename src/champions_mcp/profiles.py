"""Derived profiles: what a Pokémon's typing and stat spread make it *good at*.

Everything here is computed from the mirror, never authored: type profiles come
from the 18x18 chart, stat profiles from percentiles across the actual roster.
That relativity is the point — Champions rescaled base stats (Speed runs 40-162,
median ~98), so "fast" or "bulky" only mean something measured against this pool,
and a threshold remembered from another game is noise.

The output is a set of ``namespace:tag`` strings per Pokémon, so the same
vocabulary can be listed, explained, and searched. Two independent axes:

* **Type profile** — a typing has a defensive profile (what resists//is weak to
  it) and an offensive one (what its STAB reaches). They are frequently
  opposites: Ice/Rock has one of the roster's best offensive spreads and its
  single worst defensive one, which is exactly the trade a builder needs told.
* **Stat profile** — where each base stat, and the two *bulk products*
  (HP x Def, HP x SpD), sit as percentiles of the roster, plus the build
  archetypes those imply.

Bulk is scored as a product rather than an average of percentiles because HP
multiplies both defences: 100/100 is far bulkier than 160/40, and averaging the
two would call them equal.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from .db import Db
from .normalize import slugify

TYPES_18 = (
    "Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting",
    "Poison", "Ground", "Flying", "Psychic", "Bug", "Rock", "Ghost",
    "Dragon", "Dark", "Steel", "Fairy",
)

# --------------------------------------------------------------------------- #
# Tag vocabulary
# --------------------------------------------------------------------------- #

TAG_MEANINGS: dict[str, str] = {
    # --- defensive typing ---
    "def:fortress": "Top 10% defensive typing — many resistances/immunities, few weaknesses.",
    "def:solid": "Top 25% defensive typing.",
    "def:average": "Middle of the pack defensively.",
    "def:frail-typing": "Bottom 25% defensive typing — more weaknesses than it can pay for.",
    "def:liability": "Bottom 10% defensive typing. Needs bulk or support to be usable at all.",
    "def:has-immunity": "Immune to at least one attacking type — a free switch-in.",
    "def:multi-immunity": "Immune to two or more attacking types.",
    "def:double-weakness": "Has a 4x weakness — one move can end it regardless of bulk.",
    "def:many-resistances": "Resists six or more attacking types.",
    # --- offensive typing ---
    "off:elite-coverage": "Top 10% of all 171 typings for STAB coverage.",
    "off:strong-coverage": "Top 25% of all typings for STAB coverage.",
    "off:average-coverage": "Middle of the pack offensively.",
    "off:narrow-coverage": "Bottom 25% of all typings for STAB coverage — it needs "
                           "non-STAB coverage moves to threaten anything. Most "
                           "mono-typed Pokémon land here by construction (one type "
                           "reaches at most ~4 targets super-effectively, a pair up "
                           "to 9), so this is common rather than damning.",
    "off:unresisted": "No type resists this STAB combination outright.",
    "off:walled": "Five or more types resist or are immune to this STAB — expect to be walled.",
    # --- individual stats (percentile of the roster) ---
    "stat:hp-elite": "HP in the roster's top 10%.",
    "stat:hp-high": "HP in the roster's top 25%.",
    "stat:hp-low": "HP in the roster's bottom 25%.",
    "stat:atk-elite": "Attack in the roster's top 10%.",
    "stat:atk-high": "Attack in the roster's top 25%.",
    "stat:atk-low": "Attack in the roster's bottom 25%.",
    "stat:def-elite": "Defense in the roster's top 10%.",
    "stat:def-high": "Defense in the roster's top 25%.",
    "stat:def-low": "Defense in the roster's bottom 25%.",
    "stat:spa-elite": "Special Attack in the roster's top 10%.",
    "stat:spa-high": "Special Attack in the roster's top 25%.",
    "stat:spa-low": "Special Attack in the roster's bottom 25%.",
    "stat:spd-elite": "Special Defense in the roster's top 10%.",
    "stat:spd-high": "Special Defense in the roster's top 25%.",
    "stat:spd-low": "Special Defense in the roster's bottom 25%.",
    "stat:spe-elite": "Speed in the roster's top 10%.",
    "stat:spe-high": "Speed in the roster's top 25%.",
    "stat:spe-low": "Speed in the roster's bottom 25%.",
    "stat:bst-elite": "Base stat total in the roster's top 10%.",
    "stat:bst-low": "Base stat total in the roster's bottom 25%.",
    # --- build archetypes (derived) ---
    "build:physical-attacker": "Attack is the clear offensive stat — build physical.",
    "build:special-attacker": "Special Attack is the clear offensive stat — build special.",
    "build:mixed-attacker": "Both offences are strong and close — a genuine mixed option.",
    "build:physically-bulky": "HP x Defense in the roster's top 25% — takes physical hits.",
    "build:specially-bulky": "HP x Special Defense in the roster's top 25% — takes special hits.",
    "build:bulky-both-sides": "Top 25% bulk on BOTH sides. Note this is a body "
                              "check, not a role: a high-BST attacker can qualify "
                              "too — read the build:*-attacker tags alongside it.",
    "build:glass-cannon": "Strong offence, bottom-third bulk — it hits first or it dies.",
    "build:bulky-attacker": "Real offence AND above-average bulk — survives to attack twice.",
    "build:very-fast": "Speed in the roster's top 10% — wins most Speed races unaided.",
    "build:fast": "Speed in the roster's top 25%.",
    "build:slow": "Speed between the roster's 25th and 40th percentile — below "
                  "average, but still fast enough to invest in and win some races.",
    "build:trick-room": "Speed in the roster's bottom 25% (base ~80 or less): built "
                        "with 0 Speed SP and a minus-Speed nature it underspeeds "
                        "nearly the whole field, so Trick Room is a genuine plan for "
                        "it. Not a verdict — it may instead be slow on purpose (slow "
                        "pivot, Baton Pass, Gyro Ball, a wall that never races), or "
                        "be built fast anyway. This band is where the meta actually "
                        "runs minus-Speed natures.",
    "build:frail": "Bulk in the roster's bottom 25% on both sides.",
    "build:no-offence": "Neither offensive stat reaches the roster's top half — it must "
                        "earn its slot with utility, not damage.",
}

_STATS = (("hp", "hp"), ("atk", "attack"), ("def", "defense"),
          ("spa", "special_attack"), ("spd", "special_defense"), ("spe", "speed"))


# --------------------------------------------------------------------------- #
# Type profiles
# --------------------------------------------------------------------------- #

def _pctile(sorted_vals: list[float], v: float) -> int:
    """Percent of the population strictly below ``v``."""
    return round(sum(1 for x in sorted_vals if x < v) / len(sorted_vals) * 100)


def _raw_type_profile(db: Db, types: tuple[str, ...]) -> dict[str, Any]:
    """Un-ranked defensive/offensive scores and type buckets for one typing."""
    incoming = {}
    for atk in TYPES_18:
        mult = 1.0
        for d in types:
            mult *= db.type_multiplier(atk, d)
        incoming[atk] = mult

    weak4 = [t for t, v in incoming.items() if v >= 4]
    weak2 = [t for t, v in incoming.items() if v == 2]
    resists = [t for t, v in incoming.items() if 0 < v < 1]
    immune = [t for t, v in incoming.items() if v == 0]

    # An immunity is worth more than a resistance (a free switch-in, not just
    # reduced damage); a 4x weakness is worth more than two 2x ones because a
    # single move removes the Pokémon regardless of investment.
    defensive_score = len(immune) * 2 + len(resists) - len(weak2) - len(weak4) * 2.5

    super_eff = [o for o in TYPES_18
                 if max(db.type_multiplier(t, o) for t in types) >= 2]
    resisted_by = [o for o in TYPES_18
                   if max(db.type_multiplier(t, o) for t in types) < 1]
    offensive_score = len(super_eff) - 0.5 * len(resisted_by)

    return {
        "types": list(types),
        "defensive": {
            "score": round(defensive_score, 2),
            "weak_4x": weak4, "weak_2x": weak2,
            "resists": resists, "immune": immune,
        },
        "offensive": {
            "score": round(offensive_score, 2),
            "super_effective": super_eff, "resisted_by": resisted_by,
        },
    }


_TYPE_CACHE: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}


def _all_type_profiles(db: Db) -> dict[tuple[str, ...], dict[str, Any]]:
    """Every single type and every unordered type pair (18 + 153 = 171), graded
    against each other. Cached per DB file — the chart never changes at runtime.
    """
    key = str(db.path)
    if key in _TYPE_CACHE:
        return _TYPE_CACHE[key]

    # Key on the alphabetically sorted tuple: combinations() yields pairs in
    # TYPES_18 order (Ground before Dragon), while every lookup sorts the
    # Pokémon's own types, so keying on the raw combo would silently miss.
    combos = [(t,) for t in TYPES_18] + [
        tuple(sorted(c)) for c in combinations(TYPES_18, 2)
    ]
    profiles = {c: _raw_type_profile(db, c) for c in combos}

    d_sorted = sorted(p["defensive"]["score"] for p in profiles.values())
    o_sorted = sorted(p["offensive"]["score"] for p in profiles.values())
    for p in profiles.values():
        d, o = p["defensive"], p["offensive"]
        d["percentile"] = _pctile(d_sorted, d["score"])
        o["percentile"] = _pctile(o_sorted, o["score"])
        p["tags"] = _type_tags(d, o)
    _TYPE_CACHE[key] = profiles
    return profiles


def _type_tags(d: dict[str, Any], o: dict[str, Any]) -> list[str]:
    """Derive ``def:*``/``off:*`` tags from a type's defensive/offensive scores."""
    tags: list[str] = []
    dp, op = d["percentile"], o["percentile"]
    tags.append("def:fortress" if dp >= 90 else "def:solid" if dp >= 75
                else "def:liability" if dp <= 10 else "def:frail-typing" if dp <= 25
                else "def:average")
    if len(d["immune"]) >= 2:
        tags.append("def:multi-immunity")
    elif d["immune"]:
        tags.append("def:has-immunity")
    if d["weak_4x"]:
        tags.append("def:double-weakness")
    if len(d["resists"]) >= 6:
        tags.append("def:many-resistances")

    tags.append("off:elite-coverage" if op >= 90 else "off:strong-coverage" if op >= 75
                else "off:narrow-coverage" if op <= 25 else "off:average-coverage")
    if not o["resisted_by"]:
        tags.append("off:unresisted")
    elif len(o["resisted_by"]) >= 5:
        tags.append("off:walled")
    return tags


def type_profile(db: Db, type1: str, type2: str | None = None) -> dict[str, Any]:
    """Defensive + offensive profile for a type or type pair, graded against
    every other typing.

    Args:
        db: Open Champions database handle.
        type1: A type name.
        type2: An optional second type, for a dual-type combination.

    Returns:
        The typing's defensive/offensive scores, percentiles and tags, or
        ``{"error": ...}`` if a type is invalid or has no computed profile.
    """
    from .pokedex import _normalize_types  # shared validation/de-duplication
    types, error = _normalize_types(type1, type2)
    if error:
        return {"error": error}
    key = tuple(sorted(types))
    profile = _all_type_profiles(db).get(key)
    if not profile:
        return {"error": f"No profile for {types}."}
    return {**profile, "label": "/".join(types)}


# --------------------------------------------------------------------------- #
# Stat profiles
# --------------------------------------------------------------------------- #

_ENTITY_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def _entities(db: Db) -> dict[str, dict[str, Any]]:
    """Every battle-relevant *form*, keyed by slug: the base roster plus every
    Mega form as its own entry.

    A Mega is a different Pokémon for every purpose this module computes — it
    has its own typing (Mega Charizard X is Fire/Dragon, not Fire/Flying, so
    Rock hits it 2x instead of 4x), its own stats and its own ability. Profiling
    only base forms would be wrong for precisely the Pokémon that matter most,
    since several top-usage species hold their stone in nearly every game.
    """
    ckey = str(db.path)
    if ckey in _ENTITY_CACHE:
        return _ENTITY_CACHE[ckey]
    ents: dict[str, dict[str, Any]] = {}
    for slug in db.all_pokemon_slugs():
        r = db.get_pokemon_row(slug)
        ents[slug] = {
            "key": slug, "name": r["battle_name"], "types": r.get("types", []),
            "stats": r["base_stats"], "bst": r.get("bst") or sum(r["base_stats"].values()),
            "species_slug": slug, "is_mega": False, "mega_stone": None, "ability": None,
        }
    for m in db.list_mega_forms():
        ents[slugify(m["form_name"])] = {
            "key": slugify(m["form_name"]), "name": m["form_name"],
            "types": m["types"], "stats": m["base_stats"],
            "bst": m["bst"] or sum(m["base_stats"].values()),
            "species_slug": m["species_slug"], "is_mega": True,
            "mega_stone": m["mega_stone"], "ability": m["ability"],
        }
    _ENTITY_CACHE[ckey] = ents
    return ents


def resolve_entity(db: Db, name: str) -> str | None:
    """Resolve a species name/slug OR a Mega form name to an entity key.

    Needed because the ordinary name resolver deliberately maps Mega names back
    to their base species (and fails outright on suffixed forms like "Mega
    Charizard X") — correct for legality, wrong for profiling.

    Args:
        db: Open Champions database handle.
        name: A species name/slug or a Mega form name.

    Returns:
        The entity key, or ``None`` if ``name`` cannot be resolved.
    """
    if not name:
        return None
    ents = _entities(db)
    key = slugify(name)
    if key in ents:
        return key
    from .names import try_resolve
    return try_resolve(db, name)


_STAT_CACHE: dict[str, dict[str, Any]] = {}


def _stat_population(db: Db) -> dict[str, Any]:
    """Sorted distributions for each base stat, the two bulk products and BST.

    The population is the **base roster only**, deliberately: it is the pool you
    actually pick from, and holding it fixed means a Mega's percentiles are
    directly comparable with its base form's (that comparison is the whole point
    of asking whether the stone is worth it). Adding 75 inflated Mega rows would
    depress every base form and muddy exactly that read. A Mega can therefore
    score at the very top of the scale, which is the honest answer.
    """
    key = str(db.path)
    if key in _STAT_CACHE:
        return _STAT_CACHE[key]
    rows = []
    for slug in db.all_pokemon_slugs():
        r = db.get_pokemon_row(slug)
        bs = r["base_stats"]
        rows.append({
            "slug": slug,
            "stats": bs,
            "phys_bulk": bs["hp"] * bs["defense"],
            "spec_bulk": bs["hp"] * bs["special_defense"],
            "bst": r.get("bst") or sum(bs.values()),
        })
    pop = {
        "bst": sorted(r["bst"] for r in rows),
        "phys_bulk": sorted(r["phys_bulk"] for r in rows),
        "spec_bulk": sorted(r["spec_bulk"] for r in rows),
    }
    for abbr, full in _STATS:
        pop[abbr] = sorted(r["stats"][full] for r in rows)
    _STAT_CACHE[key] = pop
    return pop


def _stat_tags(p: dict[str, int]) -> list[str]:
    """Derive stat and build tags from a percentile map."""
    tags: list[str] = []
    for abbr in ("hp", "atk", "def", "spa", "spd", "spe"):
        v = p[abbr]
        if v >= 90:
            tags.append(f"stat:{abbr}-elite")
        elif v >= 75:
            tags.append(f"stat:{abbr}-high")
        elif v <= 25:
            tags.append(f"stat:{abbr}-low")
    if p["bst"] >= 90:
        tags.append("stat:bst-elite")
    elif p["bst"] <= 25:
        tags.append("stat:bst-low")

    # Offence: which side to build, and whether there is one at all.
    atk, spa = p["atk"], p["spa"]
    best_off = max(atk, spa)
    if atk >= 65 and spa >= 65 and abs(atk - spa) <= 15:
        tags.append("build:mixed-attacker")
    elif atk >= 70 and atk - spa >= 15:
        tags.append("build:physical-attacker")
    elif spa >= 70 and spa - atk >= 15:
        tags.append("build:special-attacker")
    if best_off < 50:
        tags.append("build:no-offence")

    # Bulk, from the HP-multiplied products rather than raw defences.
    pb, sb = p["phys_bulk"], p["spec_bulk"]
    if pb >= 75:
        tags.append("build:physically-bulky")
    if sb >= 75:
        tags.append("build:specially-bulky")
    if pb >= 75 and sb >= 75:
        tags.append("build:bulky-both-sides")
    best_bulk = max(pb, sb)
    if best_bulk <= 25:
        tags.append("build:frail")
    if best_off >= 80 and best_bulk <= 33:
        tags.append("build:glass-cannon")
    if best_off >= 65 and min(pb, sb) >= 55:
        tags.append("build:bulky-attacker")

    # Speed bands are a partition, and the Trick Room cut is empirical rather
    # than a round number: across the roster's usage data, species the meta
    # actually builds with a minus-Speed nature cluster densely from p0 to p24
    # (base Speed <= 80) and then jump to p31. p25 sits in that gap — it covers
    # ~79% of genuinely slow-built species while tagging 30% of the roster,
    # where p10 caught only 40% and p33+ tags over a third of everything for
    # little extra recall. Speed here is *base* Speed: a minus nature and 0 SP
    # move the final number, not the ordering, so the percentile is the signal.
    spe = p["spe"]
    if spe >= 90:
        tags.append("build:very-fast")
    elif spe >= 75:
        tags.append("build:fast")
    elif spe <= 25:
        tags.append("build:trick-room")
    elif spe <= 40:
        tags.append("build:slow")
    return tags


def stat_profile(db: Db, key: str) -> dict[str, Any]:
    """Stat percentiles + build tags for a base species or a Mega form.

    Args:
        db: Open Champions database handle.
        key: A species name/slug or a Mega form name.

    Returns:
        Per-stat percentiles (plus BST and the two bulk products) and their
        derived tags, or ``{"error": ...}`` if ``key`` cannot be resolved.
    """
    ent = _entities(db).get(resolve_entity(db, key) or "")
    if not ent:
        return {"error": f"Unknown Pokémon or form {key!r}."}
    pop, bs = _stat_population(db), ent["stats"]
    pct = {abbr: _pctile(pop[abbr], bs[full]) for abbr, full in _STATS}
    pct["bst"] = _pctile(pop["bst"], ent["bst"])
    pct["phys_bulk"] = _pctile(pop["phys_bulk"], bs["hp"] * bs["defense"])
    pct["spec_bulk"] = _pctile(pop["spec_bulk"], bs["hp"] * bs["special_defense"])
    return {"percentiles": pct, "tags": _stat_tags(pct)}


# --------------------------------------------------------------------------- #
# Combined per-Pokémon profile
# --------------------------------------------------------------------------- #

_PROFILE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def pokemon_profile(db: Db, key: str) -> dict[str, Any]:
    """Type + stat profile for a base species or a Mega form, as tags plus the
    numbers behind them. Deliberately compact: the full 18-type breakdown
    belongs to get_pokemon_weaknesses, not here.

    Cached per (DB, key): nothing feeding this changes at runtime, and the
    listing/search paths walk every form on every call.

    Args:
        db: Open Champions database handle.
        key: A species name/slug or a Mega form name.

    Returns:
        Combined type + stat tags and their backing numbers, or
        ``{"error": ...}`` if ``key`` cannot be resolved.
    """
    ekey = resolve_entity(db, key)
    cached = _PROFILE_CACHE.get((str(db.path), ekey or key))
    if cached is not None:
        return cached
    ent = _entities(db).get(ekey or "")
    if not ent:
        return {"error": f"Unknown Pokémon or form {key!r}."}

    tp = _all_type_profiles(db).get(tuple(sorted(ent["types"])), {})
    sp = stat_profile(db, ekey)

    out: dict[str, Any] = {
        "form": ent["name"],
        "tags": list(tp.get("tags", [])) + list(sp.get("tags", [])),
    }
    if ent["is_mega"]:
        out["is_mega"] = True
        out["mega_stone"] = ent["mega_stone"]
        out["base_species"] = ent["species_slug"]
    if tp:
        d, o = tp["defensive"], tp["offensive"]
        out["typing"] = {
            # the form's own ordering, not the alphabetically sorted cache key
            "types": ent["types"],
            "defensive_percentile": d["percentile"],
            "offensive_percentile": o["percentile"],
            "weak_4x": d["weak_4x"], "immune": d["immune"],
            "super_effective_count": len(o["super_effective"]),
            "resisted_by_count": len(o["resisted_by"]),
        }
    if "percentiles" in sp:
        out["stat_percentiles"] = sp["percentiles"]
    _PROFILE_CACHE[(str(db.path), ekey)] = out
    return out


# --------------------------------------------------------------------------- #
# Coverage against your own weaknesses
# --------------------------------------------------------------------------- #

# Kept to one line on purpose: the full rationale and its caveats live in the
# get_pokemon_moves tool description, which the client sends once in the cached
# prompt prefix. A team build calls that tool once per member, so a paragraph
# here would be re-paid six times and sit in context for the rest of the session.
_COVERAGE_GUIDANCE = (
    "Soft consideration for the last move or two, NOT a rule — a move that hits "
    "back the type you are weak to punishes its best switch-in. Weigh against "
    "power, STAB and what teammates already cover (see this tool's description)."
)


def coverage_for_weaknesses(
    db: Db, slug: str, per_weakness: int = 3,
) -> dict[str, Any]:
    """For each of a Pokémon's defensive weaknesses, the legal moves it owns
    that hit that type back super-effectively.

    Ordered so the most useful suggestions come first: moves on the Pokémon's
    stronger offensive side, then by power. 4x weaknesses lead, since those are
    the ones that actually remove it.

    Args:
        db: Open Champions database handle.
        slug: A species name/slug or a Mega form name.
        per_weakness: Maximum number of move suggestions per weakness.

    Returns:
        Per-weakness move suggestions plus guidance text, or
        ``{"error": ...}`` if ``slug`` cannot be resolved or has no type
        profile.
    """
    ekey = resolve_entity(db, slug)
    ent = _entities(db).get(ekey or "")
    if not ent:
        return {"error": f"Unknown Pokémon or form {slug!r}."}
    types = ent["types"]
    tp = _all_type_profiles(db).get(tuple(sorted(types)))
    if not tp:
        return {"error": f"No type profile for {types}."}

    sp = stat_profile(db, ekey).get("percentiles", {})
    preferred = "physical" if sp.get("atk", 0) >= sp.get("spa", 0) else "special"

    # A Mega has its own typing and stats but shares the base species' movepool.
    legal = db.learnable_attacking_moves(ent["species_slug"])
    weaknesses = ([(t, "4x") for t in tp["defensive"]["weak_4x"]]
                  + [(t, "2x") for t in tp["defensive"]["weak_2x"]])

    out = []
    for weak_type, mult in weaknesses:
        # Which attacking types beat a Pokémon *of* the type we're weak to.
        answering = [a for a in TYPES_18 if db.type_multiplier(a, weak_type) >= 2]
        moves = [m for m in legal if m["type"].capitalize() in answering]
        moves.sort(key=lambda m: (m["damage_class"].lower() != preferred,
                                  -(m["power"] or 0)))
        entry: dict[str, Any] = {
            "weak_to": weak_type,
            "multiplier": mult,
            "answered_by_types": answering,
            "legal_moves": [
                {"name": m["name"], "type": m["type"].capitalize(),
                 "category": m["damage_class"], "power": m["power"]}
                for m in moves[:max(1, per_weakness)]
            ],
        }
        if not moves:
            entry["note"] = ("No legal damaging move covers this — it must be "
                             "answered by a teammate instead.")
        out.append(entry)

    result = {
        "pokemon": ent["name"],
        "types": types,
        "prefers": preferred,
        "weaknesses": out,
        "guidance": _COVERAGE_GUIDANCE,
    }
    if ent["is_mega"]:
        result["is_mega"] = True
        result["note"] = (
            "Computed on the Mega's typing, which differs from the base form — "
            "its weaknesses, and therefore the coverage worth running, are not "
            "the same. Moves come from the base species' movepool (shared)."
        )
    return result


# --------------------------------------------------------------------------- #
# Listing & search
# --------------------------------------------------------------------------- #

_SORTS = {
    "defensive": lambda p: -p["defensive"]["score"],
    "offensive": lambda p: -p["offensive"]["score"],
    "worst_defensive": lambda p: p["defensive"]["score"],
    "worst_offensive": lambda p: p["offensive"]["score"],
    "balanced": lambda p: -(p["defensive"]["score"] + p["offensive"]["score"]),
}


def list_type_profiles(
    db: Db, sort: str = "defensive", type: str | None = None,
    dual_only: bool = False, limit: int = 30,
) -> dict[str, Any]:
    """Rank every typing (18 singles + 153 pairs) defensively or offensively.

    Args:
        db: Open Champions database handle.
        sort: One of ``defensive``/``offensive``/``worst_defensive``/
            ``worst_offensive``/``balanced``.
        type: Optional filter to typings including this type.
        dual_only: If true, only rank dual typings.
        limit: Maximum number of entries to return.

    Returns:
        The ranked, capped list of typings with their scores and tags, or
        ``{"error": ...}`` if ``sort`` or ``type`` is invalid.
    """
    profiles = list(_all_type_profiles(db).values())
    if type:
        from .pokedex import _normalize_types
        wanted, error = _normalize_types(type)
        if error:
            return {"error": error}
        profiles = [p for p in profiles if wanted[0] in p["types"]]
    if dual_only:
        profiles = [p for p in profiles if len(p["types"]) == 2]

    key = _SORTS.get(sort)
    if key is None:
        return {"error": f"Unknown sort {sort!r}. Valid: {sorted(_SORTS)}"}
    profiles.sort(key=key)
    shown = profiles[:max(1, limit)]

    out: dict[str, Any] = {
        "sort": sort, "matched": len(profiles), "returned": len(shown),
        "total_typings": len(_all_type_profiles(db)),
        "profiles": [
            {
                "typing": "/".join(p["types"]),
                "defensive_percentile": p["defensive"]["percentile"],
                "offensive_percentile": p["offensive"]["percentile"],
                "weak_4x": p["defensive"]["weak_4x"],
                "resists": len(p["defensive"]["resists"]),
                "immune": len(p["defensive"]["immune"]),
                "super_effective": len(p["offensive"]["super_effective"]),
                "resisted_by": len(p["offensive"]["resisted_by"]),
                "tags": p["tags"],
            }
            for p in shown
        ],
    }
    if len(shown) < len(profiles):
        out["truncated"] = (
            f"Showing {len(shown)} of {len(profiles)} — raise `limit`, filter "
            "with `type`, or change `sort`."
        )
    return out


def list_profile_tags(db: Db, namespace: str | None = None) -> dict[str, Any]:
    """The tag vocabulary, each with its meaning and how many roster Pokémon
    carry it — so the model can see what is actually findable before searching.

    Args:
        db: Open Champions database handle.
        namespace: Optional filter to one tag namespace (``def``/``off``/
            ``stat``/``build``).

    Returns:
        The namespace descriptions and the tag vocabulary grouped by
        namespace, each entry carrying its meaning and roster count.
    """
    counts: dict[str, int] = {}
    for k in _entities(db):
        for t in pokemon_profile(db, k).get("tags", []):
            counts[t] = counts.get(t, 0) + 1

    groups: dict[str, list[dict[str, Any]]] = {}
    for tag, meaning in TAG_MEANINGS.items():
        ns = tag.split(":", 1)[0]
        if namespace and ns != namespace.strip().lower():
            continue
        groups.setdefault(ns, []).append(
            {"tag": tag, "meaning": meaning, "forms": counts.get(tag, 0)}
        )
    return {
        "namespaces": {
            "def": "Defensive typing quality.",
            "off": "Offensive (STAB) coverage quality.",
            "stat": "A single base stat's percentile within the roster.",
            "build": "Derived build archetype from the whole stat spread.",
        },
        "count": sum(len(v) for v in groups.values()),
        "tags_by_namespace": groups,
        "note": "`forms` counts base species AND Mega forms, which are profiled "
                "separately because a Mega has its own typing and stats. "
                "Percentiles are relative to the Champions base roster, not to "
                "other games — this game rescaled base stats. Search with "
                "search_pokemon_by_profile(tags=[…], include_megas=True).",
    }


def search_pokemon_by_profile(
    db: Db, tags: list[str] | None = None, match: str = "all",
    format: str | None = None, limit: int = 40, include_megas: bool = True,
) -> dict[str, Any]:
    """Find roster Pokémon by profile tags. ``match='all'`` requires every tag
    (the useful default — "fast AND physical AND fragile typing"); ``'any'``
    returns the union.

    Args:
        db: Open Champions database handle.
        tags: Tag names to match against (see :func:`list_profile_tags`).
        match: ``"all"`` (default) or ``"any"``.
        format: Optional format (``vgc``/``bss``) to sort by usage rank.
        limit: Maximum number of entries to return.
        include_megas: Whether Mega forms are searched alongside base species.

    Returns:
        Matching Pokémon with their matched tags, or ``{"error": ...}`` if no
        tags were given or a tag is unknown.
    """
    wanted = [t.strip() for t in (tags or []) if t and t.strip()]
    if not wanted:
        return {"error": "Provide at least one tag — see list_profile_tags().",
                "known_tags": sorted(TAG_MEANINGS)}
    unknown = [t for t in wanted if t not in TAG_MEANINGS]
    if unknown:
        return {"error": f"Unknown tag(s): {unknown}.",
                "known_tags": sorted(TAG_MEANINGS)}

    from .formats import game_type
    gt = game_type(format) if format else None
    want, mode = set(wanted), (match or "all").strip().lower()

    hits = []
    for key, ent in _entities(db).items():
        if ent["is_mega"] and not include_megas:
            continue
        prof = pokemon_profile(db, key)
        have = set(prof.get("tags", []))
        ok = want <= have if mode == "all" else bool(want & have)
        if not ok:
            continue
        # A Mega has no usage rank of its own — it inherits its species', which
        # is what the meta actually ranks.
        row = db.get_pokemon_row(ent["species_slug"]) or {}
        rank = row.get("rank_doubles") if gt == "doubles" else \
            row.get("rank_singles") if gt == "singles" else None
        hit = {
            "name": ent["name"], "slug": key,
            "types": ent["types"],
            "base_stat_total": ent["bst"],
            "usage_rank": rank,
            "matched_tags": sorted(want & have),
        }
        if ent["is_mega"]:
            hit["is_mega"] = True
            hit["mega_stone"] = ent["mega_stone"]
            hit["ability"] = ent["ability"]
        hits.append(hit)

    hits.sort(key=lambda h: (h["usage_rank"] is None, h["usage_rank"] or 0,
                             h["name"]) if gt else (h["name"],))
    shown = hits[:max(1, limit)]
    out: dict[str, Any] = {
        "tags": wanted, "match": mode, "format": format,
        "include_megas": include_megas,
        "count": len(hits), "returned": len(shown), "pokemon": shown,
    }
    if not hits:
        out["hint"] = ("No Pokémon carries all of those tags. Try match='any', "
                       "or drop the most restrictive tag — list_profile_tags() "
                       "shows how many Pokémon each tag has.")
    if len(shown) < len(hits):
        out["truncated"] = (f"Showing {len(shown)} of {len(hits)} — raise "
                            "`limit` or add a tag to narrow.")
    return out
