"""Pokédex read-model: species, types, abilities, Champions stats, weaknesses.

All values are Champions-accurate (from pokechamp.gg), including Mega and
regional forms with their own stats/types/abilities.
"""

from __future__ import annotations

from typing import Any

from . import profiles
from .db import Db
from .names import NameError, resolve

TYPES_18 = (
    "Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting",
    "Poison", "Ground", "Flying", "Psychic", "Bug", "Rock", "Ghost",
    "Dragon", "Dark", "Steel", "Fairy",
)


def _form_view(form: dict[str, Any]) -> dict[str, Any]:
    """Map a raw form record from ``pokemon.forms_json`` to the public view shape."""
    return {
        "name": form.get("form_name") or form.get("saved_name"),
        "form_kind": form.get("form_kind"),
        "types": form.get("types", []),
        "abilities": [a for a in (form.get("abilities", "") or "").split("|") if a],
        "base_stats": {
            "hp": form.get("hp"), "attack": form.get("attack"),
            "defense": form.get("defense"), "special_attack": form.get("sp_attack"),
            "special_defense": form.get("sp_defense"), "speed": form.get("speed"),
        },
        "base_stat_total": form.get("base_stat_total"),
    }


def _mega_view(db: Db, form_name: str | None) -> dict[str, Any]:
    """Attach Mega Stone + stat changes (looked up from the pre-computed
    ``mega_forms`` table) to a form already rendered by ``_form_view``."""
    row = db.get_mega_form(form_name) if form_name else None
    if not row:
        return {"mega_stone": None, "stat_changes": {}, "base_stat_total_change": None}
    return {
        "mega_stone": row["mega_stone"],
        "stat_changes": row["stat_changes"],
        "base_stat_total_change": row["bst_change"],
    }


_SPEED_DIST_CACHE: dict[str, tuple[int, ...]] = {}


def _speed_distribution(db: Db) -> tuple[int, ...]:
    """Sorted base Speed of every roster species, cached per DB file.

    Champions rescaled base stats, so Speed numbers carried over from other
    games mean nothing here: this roster runs 40-162 with a median near 98.
    "Slow" has to be judged against *this* distribution, not a remembered one.
    """
    key = str(db.path)
    if key not in _SPEED_DIST_CACHE:
        _SPEED_DIST_CACHE[key] = tuple(sorted(
            db.get_pokemon_row(s)["base_stats"]["speed"]
            for s in db.all_pokemon_slugs()
        ))
    return _SPEED_DIST_CACHE[key]


def speed_context(db: Db, base_speed: int) -> dict[str, Any]:
    """Where a base Speed sits in the Champions roster, plus the build
    implication of being at the bottom of it.

    A species in the slowest decile is nearly always either a Trick Room piece
    (setter or abuser) or slow *on purpose* — a slow pivot, a Baton Passer, a
    Gyro Ball user, or a wall that never intended to move first. Being merely
    slow with no plan is the common teambuilding mistake this flags.

    Args:
        db: Open Champions database handle.
        base_speed: The species' base Speed stat.

    Returns:
        The Speed's roster percentile, the roster median, and (for the
        bottom quartile) a build implication string.
    """
    speeds = _speed_distribution(db)
    n = len(speeds)
    below = sum(1 for s in speeds if s < base_speed)
    pct = round(below / n * 100)
    ctx: dict[str, Any] = {
        "base_speed": base_speed,
        "roster_percentile": pct,
        "roster_median_speed": speeds[n // 2],
    }
    if pct <= 10:
        ctx["implication"] = (
            "Slowest ~10% of the roster — it cannot win a Speed race however you "
            "build it. Either Trick Room is the plan, or it is slow on purpose "
            "(slow pivot, Baton Passer, Gyro Ball user, a wall that never races). "
            "Otherwise it simply moves second every turn: spend the SP on bulk, "
            "not on Speed it cannot win with."
        )
    elif pct <= 25:
        ctx["implication"] = (
            "Bottom quarter of the roster — this is the band where Champions' "
            "meta actually runs minus-Speed natures. It can go either way: built "
            "with 0 Speed SP and a minus nature it underspeeds nearly the whole "
            "field and makes a real Trick Room piece, or it can invest and "
            "contest the middle. Decide which, and commit — the worst outcome is "
            "partial Speed investment that beats nothing."
        )
    return ctx


def get_pokemon(db: Db, name: str) -> dict[str, Any]:
    """Full Champions-accurate record for one Pokémon: types, abilities, base
    stats, Speed context, derived profile, and any Mega forms it has.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to resolve.

    Returns:
        The species record, or ``{"error": ...}`` if ``name`` cannot be
        resolved or is not in the roster.
    """
    try:
        slug = resolve(db, name)
    except NameError as exc:
        return {"error": str(exc)}
    row = db.get_pokemon_row(slug)
    if not row:
        return {"error": f"{name!r} not found"}
    forms = row.get("forms", [])
    mega_forms = []
    for f in forms:
        if not (f.get("form_kind") or "").startswith("Mega"):
            continue
        view = {**_form_view(f), **_mega_view(db, f.get("form_name"))}
        # A Mega is a different Pokémon defensively and offensively — its own
        # typing, stats and speed tier — so it carries its own profile rather
        # than inheriting the base form's.
        mkey = profiles.resolve_entity(db, f.get("form_name") or "")
        if mkey:
            view["profile"] = profiles.pokemon_profile(db, mkey)
            mstats = profiles._entities(db)[mkey]["stats"]
            view["speed_context"] = speed_context(db, mstats["speed"])
        mega_forms.append(view)
    return {
        "slug": slug,
        "name": row["battle_name"],
        "showdown_name": row["showdown_name"],
        "form_kind": row.get("form_kind"),
        "types": row.get("types", []),
        "abilities": row.get("abilities", []),
        "base_stats": row.get("base_stats", {}),
        "base_stat_total": row.get("bst"),
        "speed_context": speed_context(db, (row.get("base_stats") or {}).get("speed", 0)),
        "profile": profiles.pokemon_profile(db, slug),
        "mega_forms": mega_forms,
        "sprite": row.get("sprite"),
        "names_by_locale": row.get("names", {}),
        "usage_rank": {
            "doubles": row.get("rank_doubles"),
            "singles": row.get("rank_singles"),
        },
        "note": "Base stats are Champions Lv-50 base values; use calc_stats for "
                "final stats from an SP spread + nature.",
    }


def get_random_pokemon(db: Db) -> dict[str, Any]:
    """A uniformly random Pokémon from the current Champions roster.

    Args:
        db: Open Champions database handle.

    Returns:
        The same record shape as :func:`get_pokemon`, or ``{"error": ...}``
        if the roster table is empty.
    """
    slug = db.random_pokemon_slug()
    if not slug:
        return {"error": "No Pokémon found in the roster."}
    return get_pokemon(db, slug)


def list_megas(
    db: Db, query: str | None = None, limit: int = 100,
) -> dict[str, Any]:
    """Species with a Mega Evolution in Champions: required Mega Stone,
    typing/ability change, and the stat deltas vs. the base form.

    Champions has 80+ Mega forms (a number that only grows as regulations add
    more), and the full per-Mega record (both the absolute base stats *and*
    the deltas) totals tens of thousands of characters — more than a typical
    MCP client will accept in one tool result, so an unfiltered dump came back
    silently truncated. Unfiltered calls therefore return a compact index; pass
    ``query`` to narrow to specific Megas and get the full stat breakdown for
    those. ``limit`` bounds either mode.

    Args:
        db: Open Champions database handle.
        query: Optional substring filter over species/Mega name. Narrowing
            with this switches the result to full detail mode.
        limit: Maximum number of entries to return.

    Returns:
        A compact index (unfiltered) or full-detail list (filtered) of Megas,
        with match/total counts.
    """
    rows = db.list_mega_forms()
    total = len(rows)
    if query:
        needle = query.strip().lower()
        rows = [
            r for r in rows
            if needle in r["species_name"].lower() or needle in r["form_name"].lower()
        ]
    matched = len(rows)
    shown = rows[:max(1, limit)]

    def entry(r: dict[str, Any]) -> dict[str, Any]:
        e = {
            "species": r["species_name"],
            "mega_name": r["form_name"],
            "mega_stone": r["mega_stone"],
            "types": r["types"],
            "ability": r["ability"],
            "base_stat_total": r["bst"],
        }
        # Full numbers only when the caller narrowed the set — that's when they
        # are actually comparing Megas rather than browsing what exists. Keeps
        # the compact index (all Megas, growing every regulation) inside a
        # single MCP tool result. mega_kind is redundant in the index
        # (form_name already reads "Mega ... X"/"Primal ..."), so it rides
        # along with the detail too.
        if query:
            e["mega_kind"] = r["mega_kind"]
            e["base_stats"] = r["base_stats"]
            e["base_stat_total_change"] = r["bst_change"]
            e["stat_changes"] = r["stat_changes"]
        return e

    out: dict[str, Any] = {
        "count": len(shown),
        "matched": matched,
        "total_megas": total,
        "detail": "full" if query else "compact",
        "megas": [entry(r) for r in shown],
    }
    if not query:
        out["note"] = (
            f"Compact index of all {total} Megas (stat deltas omitted). Call "
            "list_megas(query='<species or mega name>') for full base stats and "
            "per-stat changes, or get_pokemon(species) for one species' Megas."
        )
    if matched > len(shown):
        out["truncated"] = (
            f"Showing {len(shown)} of {matched} matches — raise `limit` or "
            "narrow `query`."
        )
    return out


def search_pokemon(db: Db, query: str, limit: int = 25) -> list[dict[str, str]]:
    """Fuzzy name search over the roster.

    Args:
        db: Open Champions database handle.
        query: Substring/fuzzy match against Pokémon names.
        limit: Maximum number of matches to return.

    Returns:
        A list of ``{"slug", "name"}`` matches.
    """
    return [
        {"slug": r["slug"], "name": r["battle_name"]}
        for r in db.search_pokemon(query, limit)
    ]


def _normalize_types(*raw: str | None) -> tuple[list[str], str | None]:
    """Validate and de-duplicate type arguments -> (types, error).

    Both are load-bearing. ``db.type_multiplier`` returns a neutral 1.0 for a
    type it doesn't recognise, so an unvalidated typo silently yields an
    all-neutral chart rather than an error — a confidently wrong answer. And
    passing the same type twice would square every multiplier (Fire/Fire would
    report a 4x Water weakness instead of 2x), because the defensive table
    multiplies once per listed type.
    """
    types: list[str] = []
    for value in raw:
        if not value:
            continue
        t = str(value).strip().capitalize()
        if t not in TYPES_18:
            return [], f"Unknown type {value!r}. Valid: {list(TYPES_18)}"
        if t not in types:          # a Pokémon is never dual-typed with itself
            types.append(t)
    if not types:
        return [], "Provide at least one type."
    return types, None


def get_type_matchups(
    db: Db, type1: str, type2: str | None = None,
) -> dict[str, Any]:
    """Offensive and defensive matchups for a type or type pair.

    Args:
        db: Open Champions database handle.
        type1: A type name.
        type2: An optional second type, for a dual-type combination.

    Returns:
        ``offensive`` (super-effective/not-very-effective/no-effect targets)
        and ``defensive`` (incoming multiplier buckets) breakdowns, or
        ``{"error": ...}`` if a type is invalid.
    """
    types, error = _normalize_types(type1, type2)
    if error:
        return {"error": error}

    # Offensive: a dual-type attacker carries STAB on either type and picks the
    # better move per target, so combined coverage is the best multiplier either
    # type lands on each defending type. (For a single type this is just its own
    # multiplier — 1× neutral targets are omitted, as before.)
    offensive: dict[str, list[str]] = {"super_effective": [], "not_very_effective": [], "no_effect": []}
    for other in TYPES_18:
        best = max(db.type_multiplier(t, other) for t in types)
        if best >= 2.0:
            offensive["super_effective"].append(other)
        elif best == 0.0:
            offensive["no_effect"].append(other)
        elif best < 1.0:
            offensive["not_very_effective"].append(other)

    # Defensive: incoming multipliers stack across both types (4×/2×/1×/½×/¼×/0×).
    defensive = _weakness_table(db, types)

    return {
        "type": "/".join(types),
        "types": types,
        "offensive": offensive,
        "defensive": defensive,
    }


def _weakness_table(db: Db, types: list[str]) -> dict[str, Any]:
    """Bucket incoming type multipliers (4x/2x/1x/0.5x/0.25x/0x) for ``types``."""
    buckets: dict[str, list[str]] = {"4x": [], "2x": [], "1x": [], "0.5x": [], "0.25x": [], "0x": []}
    for atk in TYPES_18:
        mult = 1.0
        for dfn in types:
            mult *= db.type_multiplier(atk, dfn)
        label = {4.0: "4x", 2.0: "2x", 1.0: "1x", 0.5: "0.5x", 0.25: "0.25x", 0.0: "0x"}.get(
            mult, f"{mult}x"
        )
        buckets.setdefault(label, []).append(atk)
    return buckets


def get_pokemon_weaknesses(
    db: Db, name: str | None = None,
    type1: str | None = None, type2: str | None = None,
) -> dict[str, Any]:
    """Defensive weakness chart for a named Pokémon (including Mega forms)
    or for one or two raw types.

    Args:
        db: Open Champions database handle.
        name: A Pokémon or Mega form name. Takes precedence over ``type1``/
            ``type2`` when given.
        type1: A type name, used when ``name`` is not given.
        type2: An optional second type.

    Returns:
        The target's typing and its incoming-multiplier buckets, or
        ``{"error": ...}`` if neither a resolvable name nor a valid type was
        given.
    """
    if name:
        # Try Mega forms first: the ordinary resolver maps "Mega X" back to its
        # base species (and fails on suffixed forms like "Mega Charizard X"),
        # which would report the wrong typing entirely — Mega Charizard X is
        # Fire/Dragon, so Rock hits it 2x where the base form takes 4x.
        ekey = profiles.resolve_entity(db, name)
        ent = profiles._entities(db).get(ekey or "")
        if ent and ent["is_mega"]:
            return {"target": ent["name"], "types": ent["types"], "is_mega": True,
                    "by_multiplier": _weakness_table(db, ent["types"]),
                    "note": "Mega typing — differs from the base form."}
        try:
            slug = resolve(db, name)
        except NameError as exc:
            return {"error": str(exc)}
        row = db.get_pokemon_row(slug)
        if not row:
            return {"error": f"{name!r} not found"}
        types = row.get("types", [])
        label = row["battle_name"]
    else:
        # Same validation/de-duplication as get_type_matchups: without it a
        # typo'd type silently produced an all-neutral chart, and the same type
        # passed twice squared every multiplier.
        types, error = _normalize_types(type1, type2)
        if error:
            return {"error": error.replace("Provide at least one type.",
                                           "Provide a Pokémon name or at least one type.")}
        label = "/".join(types)
    return {"target": label, "types": types, "by_multiplier": _weakness_table(db, types)}
