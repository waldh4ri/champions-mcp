"""Roster + movepool read-model: who is legal, who has a type/ability/move,
and each Pokémon's full Champions-legal movepool (``learnableMoveNames``).
"""

from __future__ import annotations

from typing import Any

from . import profiles
from .db import Db
from .names import try_resolve


def _name_map(db: Db) -> dict[str, str]:
    return {r["slug"]: r["battle_name"] for r in db.query("SELECT slug, battle_name FROM pokemon")}


def _names(db: Db, slugs: list[str]) -> list[dict[str, str]]:
    nm = _name_map(db)
    return [{"slug": s, "name": nm.get(s, s)} for s in slugs]


def list_pokemon(
    db: Db, query: str | None = None, type: str | None = None,
    format: str | None = None, limit: int = 60,
) -> dict[str, Any]:
    """Roster pick list. Every entry is legal in the current season; filter by
    name substring and/or a single type. Sorted by usage rank when a format is
    given, else alphabetically.

    ``limit`` defaults to 60 rather than the whole 235-strong roster: the full
    list is ~27k characters, which exceeds what a typical MCP client will pass
    back in one tool result, so an unbounded dump arrived silently truncated —
    worse than a bounded one, because the caller can't tell what is missing.
    ``count`` always reports the true number of matches so the caller knows to
    narrow the filters or raise the limit.

    Args:
        db: Open Champions database handle.
        query: Optional substring filter over Pokémon name or slug.
        type: Optional single type filter.
        format: Optional format (``vgc``/``bss``) to sort by usage rank.
        limit: Maximum number of entries to return.

    Returns:
        The matching roster entries, with ``count`` (total matches) and
        ``returned`` (entries actually included).
    """
    from .formats import game_type

    rows = db.query("SELECT slug, battle_name, types_json, bst, rank_doubles, rank_singles FROM pokemon")
    out = []
    qn = query.strip().lower() if query else None
    tcap = type.capitalize() if type else None
    fmt_key = game_type(format) if format else None
    import json
    for r in rows:
        if qn and qn not in r["battle_name"].lower() and qn not in r["slug"]:
            continue
        types = json.loads(r["types_json"])
        if tcap and tcap not in types:
            continue
        rank = r["rank_doubles"] if fmt_key == "doubles" else r["rank_singles"] if fmt_key == "singles" else None
        out.append({"slug": r["slug"], "name": r["battle_name"], "types": types,
                    "base_stat_total": r["bst"], "usage_rank": rank})
    if fmt_key:
        out.sort(key=lambda e: (e["usage_rank"] is None, e["usage_rank"] or 0))
    else:
        out.sort(key=lambda e: e["name"])
    shown = out[:max(1, limit)]
    result = {"count": len(out), "returned": len(shown), "format": format,
              "pokemon": shown}
    if len(shown) < len(out):
        result["truncated"] = (
            f"Showing {len(shown)} of {len(out)} matches"
            + (" (best usage ranks first)" if fmt_key else " (alphabetical)")
            + " — raise `limit`, or filter with `query`/`type`. For the most-used "
              "Pokémon prefer get_usage_stats; for the least-used, "
              "get_usage_stats(order='bottom')."
        )
    return result


def pokemons_by_type(db: Db, type1: str, type2: str | None = None) -> dict[str, Any]:
    """Roster Pokémon matching a type or exact type pair.

    Args:
        db: Open Champions database handle.
        type1: A type name.
        type2: An optional second type, for an exact dual-type match.

    Returns:
        The matching type label, count, and list of ``{"slug", "name"}``.
    """
    slugs = db.pokemons_by_type(type1, type2)
    label = type1.capitalize() + (f"/{type2.capitalize()}" if type2 else "")
    return {"type": label, "count": len(slugs), "pokemon": _names(db, slugs)}


def pokemons_by_ability(db: Db, ability: str) -> dict[str, Any]:
    """Roster Pokémon (and the specific form) that can have ``ability``.

    Args:
        db: Open Champions database handle.
        ability: An ability name.

    Returns:
        The ability name, match count, and per-Pokémon entries with the form
        the ability is available on.
    """
    rows = db.pokemons_by_ability(ability)
    nm = _name_map(db)
    seen, out = set(), []
    for r in rows:
        if r["slug"] in seen:
            continue
        seen.add(r["slug"])
        out.append({"slug": r["slug"], "name": nm.get(r["slug"], r["slug"]),
                    "on_form": r.get("form_name")})
    return {"ability": ability, "count": len(out), "pokemon": out}


def pokemons_by_move(db: Db, move: str) -> dict[str, Any]:
    """Roster Pokémon that can legally learn ``move``.

    Args:
        db: Open Champions database handle.
        move: A move name.

    Returns:
        The move name, match count, and list of ``{"slug", "name"}``.
    """
    slugs = db.pokemons_by_move(move)
    return {"move": move, "count": len(slugs), "pokemon": _names(db, slugs)}


def get_pokemon_moves(db: Db, name: str) -> dict[str, Any]:
    """A Pokémon's full Champions-legal movepool, plus move suggestions for
    covering its type weaknesses.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to resolve.

    Returns:
        The movepool and coverage suggestions, or ``{"error": ...}`` if
        ``name`` cannot be resolved.
    """
    slug = try_resolve(db, name)
    if not slug:
        return {"error": f"Could not resolve Pokémon {name!r}."}
    row = db.get_pokemon_row(slug)
    moves = [m["move_name"] for m in db.learnable_moves(slug)]
    out = {
        "pokemon": row["battle_name"] if row else name,
        "slug": slug,
        "count": len(moves),
        "moves": moves,
        "note": "Full Champions-legal movepool. Assign only moves from this list.",
    }
    # Surfaced here rather than as a separate tool because this is the moment
    # moves get chosen — the suggestion is worthless one round later.
    if row:
        out["coverage_for_weaknesses"] = profiles.coverage_for_weaknesses(db, slug)
    return out


def is_legal_move(db: Db, name: str, move: str) -> dict[str, Any]:
    """Whether ``move`` is in a Pokémon's Champions-legal movepool.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to resolve.
        move: A move name.

    Returns:
        ``{"pokemon", "move", "legal"}``, or ``{"error": ...}`` if ``name``
        cannot be resolved.
    """
    slug = try_resolve(db, name)
    if not slug:
        return {"error": f"Could not resolve Pokémon {name!r}."}
    return {"pokemon": name, "move": move, "legal": db.is_learnable(slug, move)}


def is_legal_pokemon(db: Db, name: str) -> dict[str, Any]:
    """Whether a name/slug resolves to a Pokémon in the current Champions roster.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to check.

    Returns:
        ``{"name", "slug", "legal", "battle_name"}`` when legal, otherwise
        ``{"name", "legal": False, "reason"}``.
    """
    slug = db.resolve_slug(name)
    if slug:
        row = db.get_pokemon_row(slug)
        return {"name": name, "slug": slug, "legal": True, "battle_name": row["battle_name"]}
    return {"name": name, "legal": False,
            "reason": "Not in the current Champions roster (or unrecognized name)."}
