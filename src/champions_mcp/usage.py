"""Usage / meta read-model — the single source's headline data.

Replaces v1's Smogon-chaos + Limitless layers entirely: rankings, per-Pokémon
sets (moves / items / abilities / natures / SP spreads / teammates with usage
%), teammate co-occurrence cores, and computed speed tiers.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .champions_stats import compute_stat
from .db import Db
from .formats import game_type
from .names import try_resolve

_SP_ABBREV = (
    ("hp_points", "HP"), ("attack_points", "Atk"), ("defense_points", "Def"),
    ("sp_atk_points", "SpA"), ("sp_def_points", "SpD"), ("speed_points", "Spe"),
)




def _pct_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": r["name"], "percent": r["percentage_value"], "usage": r["percentage"]}
        for r in rows if r["name"]
    ]


_BOTTOM_ALIASES = {"bottom", "worst", "least", "low", "underdog", "underdogs", "least-used", "least_used"}


# A species is treated as "played as its Mega" once its Mega Stones account for
# this much of its held-item usage. Above it, the base form is the exception:
# quoting base stats, base typing or a base-form Speed tier for it is simply
# wrong, and every damage calc built on those numbers is wrong with it.
MEGA_USAGE_THRESHOLD = 50.0


def mega_usage_index(db: Db, fmt: str) -> dict[str, dict[str, Any]]:
    """slug -> Mega-usage summary, for every species in the format whose
    held-item usage is dominated by its Mega Stone(s). One query for the format.

    Species with two Megas (Charizard X/Y, Raichu X/Y) keep a per-stone
    breakdown, because the stones are distinct items with different stats — the
    model has to pick one, not average them.

    Args:
        db: Open Champions database handle.
        fmt: The internal format key (``doubles``/``singles``).

    Returns:
        A mapping of species slug to its Mega-usage summary, for species
        whose Mega Stone usage clears :data:`MEGA_USAGE_THRESHOLD`.
    """
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for r in db.mega_stone_usage(fmt):
        by_slug.setdefault(r["slug"], []).append(r)

    out: dict[str, dict[str, Any]] = {}
    for slug, rows in by_slug.items():
        total = sum(r["percent"] or 0.0 for r in rows)
        if total < MEGA_USAGE_THRESHOLD:
            continue
        forms = [
            {
                "mega": r["form_name"],
                "stone": r["stone"],
                "percent": r["percent"],
                "base_stats": json.loads(r["base_stats_json"]) if r["base_stats_json"] else None,
                "types": json.loads(r["types_json"]) if r["types_json"] else None,
                "ability": r["ability"],
            }
            for r in rows if r["form_name"]
        ]
        if not forms:
            continue
        out[slug] = {
            "mega_usage_percent": round(total, 1),
            "forms": forms,
            "dominant": forms[0],
        }
    return out


def get_usage_stats(
    db: Db, format: str = "vgc", top: int = 30, order: str = "top",
    megas_only: bool = False,
) -> dict[str, Any]:
    """Usage ranking for the format. order='top' (default) returns the
    most-used Pokémon first; order='bottom' returns the least-used Pokémon
    first — the whole legal roster is ranked (no usage cutoff), so this is
    the way to find real off-meta/underdog picks rather than guessing.
    megas_only restricts the ranking to species with a Mega Evolution.

    Args:
        db: Open Champions database handle.
        format: A Champions format (``vgc``/``bss``).
        top: Number of entries to return.
        order: ``"top"`` (default) or ``"bottom"``/an alias thereof.
        megas_only: Restrict the ranking to species with a Mega Evolution.

    Returns:
        The ranking, with a ``played_as_mega`` entry per species that is
        mostly played as its Mega, plus a warning note when any are present.
    """
    fmt = game_type(format)
    direction = "bottom" if str(order).strip().lower() in _BOTTOM_ALIASES else "top"
    rows = db.usage_ranking(fmt, top, direction, megas_only)
    megas = mega_usage_index(db, fmt)

    ranking = []
    for r in rows:
        entry: dict[str, Any] = {
            "rank": r["rank"], "name": r["battle_name"],
            "slug": r["slug"], "base_stat_total": r["bst"],
        }
        m = megas.get(r["slug"])
        if m:
            # Threat assessment on the base form is wrong for these: they are
            # nearly always on the field as the Mega, with different stats,
            # typing and ability.
            entry["played_as_mega"] = {
                "percent": m["mega_usage_percent"],
                "forms": [
                    {"mega": f["mega"], "stone": f["stone"], "percent": f["percent"]}
                    for f in m["forms"]
                ],
            }
        ranking.append(entry)

    out = {
        "format": format, "game_type": fmt, "order": direction,
        "megas_only": megas_only, "count": len(ranking),
        "ranking": ranking,
    }
    if any("played_as_mega" in e for e in ranking):
        out["mega_warning"] = (
            "Entries with `played_as_mega` are usually NOT played in the base "
            "form shown here — their listed base_stat_total, and the base stats/"
            "typing/ability get_pokemon returns, do not describe what you will "
            "face. When checking these as threats, use the Mega's numbers: pass "
            "the Mega Stone as the item to calc_damage (it auto-Mega-evolves), "
            "and read the Mega's stats from list_megas(query=<species>)."
        )
    return out


# Each list below is a *marginal* frequency distribution for one slot, computed
# independently across every build of this Pokémon on the ladder. They are not
# joint sets: nothing guarantees the top four moves ever appeared together, or
# that the most-used spread belongs to the same build as the most-used item.
# Concatenating the top of each list therefore produces a set that may be
# internally incoherent and that nobody actually plays.
#
# The full explanation of that lives in this tool's *description* (see
# server.get_pokemon_sets), which the client sends once as part of the cached
# prompt prefix. Repeating it in every response would re-pay those tokens on
# each call and leave them in the conversation forever — a 6-Pokémon build calls
# this tool six times. Only this one-line reminder rides along with the data.
_SETS_REMINDER = (
    "Per-slot popularity across all builds — NOT a set. Don't paste the top of "
    "each list; pick the build that fits this slot's role, then tune it to the "
    "team (see this tool's description)."
)

# When the observed-team corpus is present, the marginals stop being the best
# available answer and this says so in the payload itself — the tool description
# alone is too easy to skim past once the numbers are on screen.
_JOINT_AVAILABLE = (
    "Complete builds for this Pokémon are available from real teams: call "
    "get_observed_sets(name, format) to see which moves actually go with which "
    "item, and which slots are genuinely the player's choice. Prefer it over "
    "assembling a set from the lists below."
)


def get_pokemon_sets(db: Db, name: str, format: str = "vgc") -> dict[str, Any]:
    """How a Pokémon is actually played: top moves/items/abilities/natures/
    spreads/teammates with usage %, from ranked ladder games.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to resolve.
        format: A Champions format (``vgc``/``bss``).

    Returns:
        Per-slot usage breakdowns and a ``note`` on how to read them, or
        ``{"error": ...}`` if ``name`` cannot be resolved.
    """
    fmt = game_type(format)
    slug = try_resolve(db, name)
    if not slug:
        return {"error": f"Could not resolve Pokémon {name!r}."}
    row = db.get_pokemon_row(slug)

    natures = [
        {"nature": r["name"], "percent": r["percentage_value"],
         "raises": r["stat_up"], "lowers": r["stat_down"]}
        for r in db.usage_rows(slug, fmt, "stat_alignment") if r["name"]
    ]
    # One representation, not two: the old payload carried both a "HP 2 / Atk 32
    # / ..." string and an all-six stat_points dict holding the same numbers.
    # The dict survives because it can be passed straight to calc_stats /
    # calc_damage / validate_team; zeros are omitted since they're the default.
    spreads = [
        {"percent": r["percentage_value"],
         "stat_points": {abbr.lower(): r[col] for col, abbr in _SP_ABBREV if r.get(col)}}
        for r in db.usage_rows(slug, fmt, "stat_points")
    ]
    rank = row.get("rank_doubles") if fmt == "doubles" else row.get("rank_singles")
    moves = _pct_list(db.usage_rows(slug, fmt, "move"))
    items = _pct_list(db.usage_rows(slug, fmt, "held_item"))
    abilities = _pct_list(db.usage_rows(slug, fmt, "ability"))
    teammates = [{"name": r["teammate_name"]} for r in db.teammates(slug, fmt)]

    note = ("Spreads are Champions Stat Points (SP, budget 66), not EVs; "
            "stats omitted from stat_points are 0.")
    has_data = any((moves, items, abilities, natures, spreads, teammates))
    if rank is None and not has_data:
        # Every legal Champions Pokémon is usage-ranked in this data source, so
        # an empty set here always means the local mirror is missing/stale data
        # for it — never "this Pokémon has low usage" (there is no such case).
        note = (
            "No usage data available for this Pokémon in this format. Every "
            "legal Champions Pokémon is usage-ranked in this data source, so "
            "this means the local data is missing or stale for it right now — "
            "it does NOT mean the Pokémon is low-usage or rarely played. State "
            "plainly that usage data is unavailable; do not guess at its play "
            "rate or infer that it is niche."
        )

    out = {
        "pokemon": row["battle_name"], "slug": slug, "format": format,
        "usage_rank": rank,
        "moves": moves,
        "items": items,
        "abilities": abilities,
        "natures": natures,
        "spreads": spreads,
        "teammates": teammates,
        "note": note,
    }
    if has_data:
        out["reading_this_data"] = _SETS_REMINDER
        observed = db.observed_members(slug, fmt)
        if observed:
            out["joint_data_available"] = {
                "tool": "get_observed_sets",
                "observations": len(observed),
                "evidence": "full_set" if any(
                    r["moves_json"] for r in observed) else "team_and_item",
                "why": _JOINT_AVAILABLE,
            }
        m = mega_usage_index(db, fmt).get(slug)
        if m:
            dom = m["dominant"]
            out["played_as_mega"] = {
                "percent": m["mega_usage_percent"],
                "forms": [{"mega": f["mega"], "stone": f["stone"],
                           "percent": f["percent"]} for f in m["forms"]],
                "warning": (
                    f"{row['battle_name']} holds a Mega Stone in "
                    f"{m['mega_usage_percent']}% of games — it is played as "
                    f"{dom['mega']}, not the base form. The base stats, typing "
                    f"and ability from get_pokemon do NOT apply. Build and calc "
                    f"it with the stone as its item (calc_damage auto-Mega-"
                    f"evolves); see list_megas(query=…) for the Mega's stats."
                ),
            }
    return out


def get_top_teammates(db: Db, name: str, format: str = "vgc") -> dict[str, Any]:
    """Most common ladder teammates for a Pokémon, ranked.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to resolve.
        format: A Champions format (``vgc``/``bss``).

    Returns:
        The ranked teammate list, or ``{"error": ...}`` if ``name`` cannot be
        resolved.
    """
    fmt = game_type(format)
    slug = try_resolve(db, name)
    if not slug:
        return {"error": f"Could not resolve Pokémon {name!r}."}
    row = db.get_pokemon_row(slug)
    return {
        "pokemon": row["battle_name"], "format": format,
        "teammates": [
            {"rank": r["rank"], "name": r["teammate_name"], "slug": r["teammate_slug"]}
            for r in db.teammates(slug, fmt)
        ],
    }


def suggest_cores(db: Db, format: str = "vgc", limit: int = 15) -> dict[str, Any]:
    """Most frequent Pokémon pairs.

    Prefers the observed-team corpus, where a "core" is a pair that genuinely
    shared a team and can be reported as a share of real teams. Falls back to
    co-occurrence across the per-Pokémon usage teammate lists, which only counts
    how many top-teammate lists two Pokémon both appear in — a much weaker
    signal, and not a percentage of anything.

    Args:
        db: Open Champions database handle.
        format: A Champions format (``vgc``/``bss``).
        limit: Maximum number of pairs to return.

    Returns:
        The most common pairs, with ``basis`` naming which of the two
        computations produced them.
    """
    fmt = game_type(format)
    nm = {r["slug"]: r["battle_name"] for r in db.query("SELECT slug, battle_name FROM pokemon")}

    rosters = db.observed_rosters(fmt)
    if rosters:
        pair_teams: Counter = Counter()
        for roster in rosters.values():
            unique = sorted(set(roster))
            for i, a in enumerate(unique):
                for b in unique[i + 1:]:
                    pair_teams[(a, b)] += 1
        total = len(rosters)
        cores = [
            {"pair": [nm.get(a, a), nm.get(b, b)], "teams": c,
             "percent_of_teams": round(100.0 * c / total, 1)}
            for (a, b), c in pair_teams.most_common(limit)
        ]
        return {
            "format": format, "basis": "observed_teams",
            "teams_analysed": total, "count": len(cores), "cores": cores,
            "note": (
                "Share of real teams each pair appeared on together. These are "
                "the field's default answers — well tested, and equally well "
                "known to the people you will play against."
            ),
        }

    pair_counts: Counter = Counter()
    for r in db.all_teammate_pairs(fmt):
        a, b = r["slug"], r["teammate_slug"]
        if a == b:
            continue
        pair_counts[tuple(sorted((a, b)))] += 1
    cores = [
        {"pair": [nm.get(a, a), nm.get(b, b)], "co_occurrences": c}
        for (a, b), c in pair_counts.most_common(limit)
    ]
    return {
        "format": format, "basis": "usage_teammate_lists",
        "count": len(cores), "cores": cores,
        "note": ("Counts of shared top-teammate lists, not a share of teams — "
                 "run champions-mcp-pull to ingest the observed-team corpus for "
                 "the stronger version."),
    }


def get_speed_tiers(db: Db, format: str = "vgc", limit: int = 40) -> dict[str, Any]:
    """Speed tiers by base Speed, with neutral and max (32 SP + Speed nature)
    Lv-50 values. Ranked by the format's usage so the relevant mons surface.

    Args:
        db: Open Champions database handle.
        format: A Champions format (``vgc``/``bss``).
        limit: Number of top-usage Pokémon to tier.

    Returns:
        Speed tiers sorted by max Speed, with a note on Mega-tiered rows when
        present.
    """
    fmt = game_type(format)
    rows = db.query(
        "SELECT p.slug, p.battle_name, p.base_stats_json, ur.rank "
        "FROM pokemon p JOIN usage_rank ur ON ur.slug = p.slug AND ur.format = ? "
        "ORDER BY ur.rank LIMIT ?",
        (fmt, limit),
    )
    megas = mega_usage_index(db, fmt)
    tiers = []
    for r in rows:
        base_spe = json.loads(r["base_stats_json"])["speed"]
        name, mega_note = r["battle_name"], None
        m = megas.get(r["slug"])
        if m and m["dominant"].get("base_stats"):
            # This species is almost always on the field as its Mega, so its
            # base-form Speed is not a tier anyone actually plays against.
            # Tier it on the Mega — that is the number you must outrun.
            dom = m["dominant"]
            mega_spe = dom["base_stats"]["speed"]
            mega_note = {
                "as_mega": dom["mega"],
                "mega_usage_percent": m["mega_usage_percent"],
                "base_form_speed": base_spe,
            }
            # Flag it even when the Mega's Speed is unchanged: the row must not
            # be labelled with a form that never shows up, and the model still
            # needs to know the rest of its stats/typing/ability are different.
            base_spe = mega_spe
            name = dom["mega"]
        entry = {
            "name": name, "usage_rank": r["rank"],
            "base_speed": base_spe,
            "neutral_max": compute_stat(base_spe, 32, "speed", "serious"),
            "positive_max": compute_stat(base_spe, 32, "speed", "jolly"),
        }
        if mega_note:
            entry.update(mega_note)
            # A second Mega with a different Speed is a genuinely different
            # tier — surface it rather than silently picking the popular one.
            others = [
                f for f in m["forms"]
                if f is not m["dominant"] and f.get("base_stats")
                and f["base_stats"]["speed"] != base_spe
            ]
            if others:
                entry["other_mega_speeds"] = [
                    {"mega": f["mega"], "base_speed": f["base_stats"]["speed"],
                     "percent": f["percent"]}
                    for f in others
                ]
        tiers.append(entry)
    tiers.sort(key=lambda t: t["positive_max"], reverse=True)

    out = {"format": format, "count": len(tiers), "speed_tiers": tiers}
    if any("as_mega" in t for t in tiers):
        out["note"] = (
            "Rows carrying `as_mega` are tiered on the Mega's base Speed, not "
            "the base form's, because that species is played as its Mega in "
            "`mega_usage_percent`% of games — the base-form tier would be a "
            "benchmark you never actually face. `base_form_speed` is kept for "
            "the rare unevolved case."
        )
    return out
