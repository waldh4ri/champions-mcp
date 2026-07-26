"""Team legality: roster membership, movepool, Item/Species Clause, SP budget.

Roster + movepool come from the single source; the clauses and SP budget are
format/game rules from ``formats.json`` + the Champions SP math.
"""

from __future__ import annotations

from typing import Any

from .champions_stats import (
    MAX_TOTAL_SP,
    NATURES,
    looks_like_evs,
    member_stat_points,
    validate_spread,
)
from .db import Db
from .formats import resolve_format, rules
from .names import try_resolve
from .normalize import normalize_key, slugify


def validate_team(db: Db, team: list[dict[str, Any]], format: str = "vgc") -> dict[str, Any]:
    """Check a team against roster membership, movepool, clauses and SP budget.

    Args:
        db: Open database handle.
        team: One dict per member: `species`, and optionally `item`,
            `ability`, `moves`, `nature`, `stat_points`/`evs`.
        format: A format name/alias (see `formats.resolve_format`).

    Returns:
        A dict with `legal`, `format`, `season`, `violations` and `warnings`.
        `violations` are legality failures; `warnings` flag likely mistakes
        that aren't illegal (e.g. unspent SP).
    """
    fmt = resolve_format(format)
    r = rules(fmt)
    season = db.meta("season", "Current")
    violations: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if len(team) > r["team_size"]:
        violations.append({"rule": "team_size",
                           "detail": f"{len(team)} members exceeds the max of {r['team_size']}."})
    if not team:
        violations.append({"rule": "team_size", "detail": "Team is empty."})

    base_species_seen: dict[str, str] = {}
    items_seen: dict[str, str] = {}

    for m in team:
        species = m.get("species") or "?"
        slug = try_resolve(db, species)
        if not slug:
            violations.append({"member": species, "rule": "roster",
                               "detail": f"{species!r} is not in the current Champions roster ({season})."})
            continue
        row = db.get_pokemon_row(slug)
        label = row["battle_name"]

        # Species Clause (base species)
        base = row["base_name"]
        if r["species_clause"] and base in base_species_seen:
            violations.append({"member": label, "rule": "species_clause",
                               "detail": f"Duplicate species line {base!r} (also {base_species_seen[base]})."})
        base_species_seen[base] = label

        # Item Clause
        item = (m.get("item") or "").strip()
        if item:
            ik = normalize_key(item)
            if r["item_clause"] and ik in items_seen:
                violations.append({"member": label, "rule": "item_clause",
                                   "detail": f"Duplicate item {item!r} (also on {items_seen[ik]})."})
            items_seen[ik] = label
            if not db.get_item(item):
                warnings.append({"member": label, "rule": "item",
                                 "detail": f"Item {item!r} not in the Champions item set (typo?)."})

        # Moves vs legal movepool
        for mv in (m.get("moves") or []):
            if not db.is_learnable(slug, mv):
                violations.append({"member": label, "rule": "move",
                                   "detail": f"{label} cannot learn {mv!r} in Champions."})

        # Ability legality (any form)
        ability = (m.get("ability") or "").strip()
        if ability:
            ok = db.query_one(
                "SELECT 1 FROM pokemon_abilities WHERE slug = ? AND ability_slug = ?",
                (slug, slugify(ability)),
            )
            if not ok:
                warnings.append({"member": label, "rule": "ability",
                                 "detail": f"{ability!r} is not a listed ability for {label}."})

        # Nature
        nat = m.get("nature")
        if nat and nat.strip().lower() not in NATURES:
            violations.append({"member": label, "rule": "nature",
                               "detail": f"Unknown nature {nat!r}."})

        # SP spread — accepted under "stat_points" or the reflexive "evs"
        sp = member_stat_points(m)
        chk = validate_spread(sp)
        for v in chk.violations:
            violations.append({"member": label, "rule": "stat_points", "detail": v})
        if looks_like_evs(sp):
            # Turn a bare "over the cap" complaint into the actual diagnosis.
            violations.append({
                "member": label, "rule": "stat_points",
                "detail": "This spread looks like EVs. Champions uses Stat "
                          f"Points: {MAX_TOTAL_SP} total, 32 per stat, 1 SP = +1 "
                          "stat (EVs are 510/252 and 4 EVs = +1). Re-scale it — "
                          "the values are not interchangeable.",
            })
        # Under-budget isn't illegal (unlike over-budget above), but it's
        # almost always a mistake worth flagging — unspent SP is free stats
        # left on the table.
        if chk.ok and chk.total < MAX_TOTAL_SP:
            left = MAX_TOTAL_SP - chk.total
            warnings.append({
                "member": label, "rule": "stat_points",
                "detail": f"Only {chk.total}/{MAX_TOTAL_SP} SP allocated — "
                          f"{left} SP left unspent (free stats being wasted).",
            })

    return {
        "legal": not violations,
        "format": fmt,
        "season": season,
        "violations": violations,
        "warnings": warnings,
    }
