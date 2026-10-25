"""Historical-builds read model: curated Smogon/Showdown sets.

Champions is a new game with no native Smogon dex, so ``smogon_sets`` (see
``pull.smogon``) is cross-game evidence — a Pokémon's Smogon history, not a
Champions tournament. It exists purely as a fallback for when the real
observed-team corpus (``champions_mcp.builds``, backed by Limitless/pokedb)
is thin or empty, which it is right after a regulation rollover.

Every set is checked against the *current* Champions data before it's
returned: a move, item or ability that doesn't exist in Champions' movepool/
item pool/ability list for that Pokémon is flagged, never silently dropped or
presented as if it were legal. EVs are surfaced as which stats were invested
(ranked), not converted into a Stat Point number — Champions' 66-budget/
32-per-stat system doesn't map 1:1 onto the 510/252 EV system, the same
reason ``champions_stats.member_stat_points`` refuses to auto-convert an
incoming EV-scale spread.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .champions_stats import canonical_stat
from .db import Db
from .formats import game_type
from .names import try_resolve
from .normalize import slugify

_EVIDENCE = "smogon_historical"

_NOTE = (
    "Cross-game historical evidence (Smogon/Pokémon Showdown sets), not "
    "Champions tournament data — Champions predates none of these, so treat "
    "them as inspiration to adapt, not builds to copy. `legal_in_champions` "
    "is false whenever a move, item or ability isn't in this Pokémon's "
    "current Champions data; adapt around it rather than using it anyway. "
    "`invested_stats` names which stats the original EV spread favoured — "
    "use calc_stats/compute_speed_threshold to turn that into a real Stat "
    "Point spread; Champions' 66-SP budget doesn't map 1:1 onto 510 EVs."
)


def _invested_stats(evs_json: str | None) -> list[str]:
    """Stats an EV spread favoured, ranked by investment (highest first)."""
    if not evs_json:
        return []
    evs = json.loads(evs_json)
    ranked = sorted(evs.items(), key=lambda kv: -kv[1])
    return [canonical_stat(k) or k for k, v in ranked if v]


def _legal_move(db: Db, slug: str, move: str) -> bool:
    return db.is_learnable(slug, move)


def _legal_item(db: Db, item: str | None) -> bool | None:
    if not item:
        return None
    return db.get_item(item) is not None


def _legal_ability(db: Db, slug: str, ability: str | None) -> bool | None:
    if not ability:
        return None
    return bool(db.query_one(
        "SELECT 1 FROM pokemon_abilities WHERE slug = ? AND ability_slug = ?",
        (slug, slugify(ability)),
    ))


def _historical_sets_for(
    db: Db, slug: str, fmt: str, limit: int,
) -> list[dict[str, Any]]:
    """The deduplicated, legality-checked Smogon sets for one slug+format.

    Args:
        db: Open Champions database handle.
        slug: Resolved Pokémon slug.
        fmt: Internal format key (``doubles``/``singles``).
        limit: Maximum number of distinct sets to return.

    Returns:
        Sets ordered by highest generation seen, then by how many source
        tiers/generations carried the exact same build.
    """
    rows = db.query(
        "SELECT * FROM smogon_sets WHERE slug = ? AND format = ? "
        "ORDER BY generation DESC",
        (slug, fmt),
    )
    if not rows:
        return []

    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = grouped.setdefault(r["set_hash"], {"rows": [], "best": r})
        entry["rows"].append(r)
        if r["generation"] > entry["best"]["generation"]:
            entry["best"] = r
    ordered = sorted(
        grouped.values(),
        key=lambda e: (e["best"]["generation"], len(e["rows"])),
        reverse=True,
    )

    out = []
    for entry in ordered[:limit]:
        best = entry["best"]
        moves = json.loads(best["moves_json"])
        move_legality = {m: _legal_move(db, slug, m) for m in moves}
        item_legal = _legal_item(db, best["item"])
        ability_legal = _legal_ability(db, slug, best["ability"])
        out.append({
            "set_name": best["set_name"],
            "ability": best["ability"],
            "ability_legal_in_champions": ability_legal,
            "item": best["item"],
            "item_legal_in_champions": item_legal,
            "nature": best["nature"],
            "moves": [
                {"name": m, "legal_in_champions": move_legality[m]} for m in moves
            ],
            "invested_stats": _invested_stats(best["evs_json"]),
            "legal_in_champions": (
                all(move_legality.values())
                and item_legal is not False
                and ability_legal is not False
            ),
            "seen_in": sorted({r["source_format"] for r in entry["rows"]}),
            "newest_generation": best["generation"],
        })
    return out


def get_historical_sets(
    db: Db, name: str, format: str = "vgc", limit: int = 8,
) -> dict[str, Any]:
    """Curated Smogon/Showdown sets for a Pokémon, filtered to this format's
    battle type (doubles/singles) and cross-checked against current Champions
    legality.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to resolve.
        format: A Champions format (``vgc``/``bss``).
        limit: Maximum number of distinct sets to return.

    Returns:
        Legality-flagged historical sets, or ``{"error": ...}`` if ``name``
        cannot be resolved.
    """
    fmt = game_type(format)
    slug = try_resolve(db, name)
    if not slug:
        return {"error": f"Could not resolve Pokémon {name!r}."}
    row = db.get_pokemon_row(slug)

    sets = _historical_sets_for(db, slug, fmt, limit)
    out: dict[str, Any] = {
        "pokemon": row["battle_name"], "slug": slug, "format": format,
        "evidence": _EVIDENCE, "count": len(sets), "sets": sets,
    }
    if not sets:
        out["note"] = (
            "No Smogon/Showdown sets exist for this Pokémon in this battle "
            "type across any generation Smogon has published. That says "
            "nothing about viability in Champions specifically."
        )
    else:
        out["how_to_use"] = _NOTE
    return out
