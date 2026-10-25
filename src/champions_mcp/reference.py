"""Reference read-model: move / item / ability descriptions.

The pull step mirrors these from pokechamp.gg into the DB once. Move/item/
ability *legality* and *usage* come from the same source — this layer is
purely descriptive text.
"""

from __future__ import annotations

from typing import Any

from .db import Db


def get_move(db: Db, name: str) -> dict[str, Any]:
    """PokeAPI-enriched details for one move.

    Args:
        db: Open Champions database handle.
        name: A move name.

    Returns:
        The move's type/category/power/accuracy/effect text, or
        ``{"error": ...}`` if it is not in the Champions move set.
    """
    m = db.get_move(name)
    if not m:
        return {"error": f"Move {name!r} not found in the Champions move set."}
    return m


def search_moves(
    db: Db,
    query: str | None = None,
    type: str | None = None,
    category: str | None = None,
    legal_only: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Search moves. ``legal_only`` restricts to moves learnable by some
    Champions Pokémon (default); ``category`` is physical/special/status.

    Args:
        db: Open Champions database handle.
        query: Optional substring filter over move name.
        type: Optional type filter.
        category: Optional damage category filter (physical/special/status).
        legal_only: Restrict to moves learnable by some Champions Pokémon.
        limit: Maximum number of results to return.

    Returns:
        The matching moves with count and ``legal_only`` echoed back.
    """
    rows = db.search_moves(query, type, category, legal_only, limit)
    return {
        "count": len(rows),
        "legal_only": legal_only,
        "moves": [
            {"name": r["name"], "type": r["type"], "category": r["damage_class"],
             "power": r["power"], "accuracy": r["accuracy"]}
            for r in rows
        ],
    }


def get_item(db: Db, name: str) -> dict[str, Any]:
    """PokeAPI-enriched details for one item.

    Args:
        db: Open Champions database handle.
        name: An item name.

    Returns:
        The item's category, effect text, and whether it is a Mega Stone, or
        ``{"error": ...}`` if it is not found.
    """
    it = db.get_item(name)
    if not it:
        return {"error": f"Item {name!r} not found."}
    return {
        "name": it["name"], "category": it["category"],
        "effect": it["effect"], "is_mega_stone": bool(it["is_mega_stone"]),
    }


_ITEM_CATEGORY_LABELS = {
    "held-items": "General held items (Leftovers, Life Orb, Focus Sash…).",
    "type-enhancement": "Boost one move type by a fixed multiplier.",
    "type-protection": "Berries that soften one super-effective hit, then vanish.",
    "medicine": "Berries and healing items, most consumed on a trigger.",
    "choice": "Choice items — lock into one move for a stat boost.",
    "bad-held-items": "Items with a drawback, used for specific tricks.",
    "species-specific": "Only works on one species.",
    "mega-stones": "Unlock a Mega Evolution for one species — see list_megas.",
}


def list_items(
    db: Db,
    category: str | None = None,
    query: str | None = None,
    include_mega_stones: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """The Champions item pool, grouped by category, with effect text.

    Mega Stones are excluded by default: they are around half of the item pool,
    they are not a choice you browse (the stone is decided by which Mega you
    want, via list_megas or get_pokemon), and including them buries the items
    that are an actual item decision.

    Args:
        db: Open Champions database handle.
        category: Optional category filter (see ``_ITEM_CATEGORY_LABELS``).
        query: Optional substring filter over item name.
        include_mega_stones: Whether to include the Mega Stones.
        limit: Maximum number of items to return.

    Returns:
        Items grouped by category, with counts and category labels.
    """
    rows = db.query("SELECT name, category, effect, is_mega_stone FROM items "
                    "ORDER BY name")
    stones = [r for r in rows if r["is_mega_stone"]]
    pool = rows if include_mega_stones else [r for r in rows if not r["is_mega_stone"]]

    q = (query or "").strip().lower() or None
    if q:
        pool = [r for r in pool if q in r["name"].lower()]
    cat = (category or "").strip().lower() or None
    if cat:
        pool = [r for r in pool if _item_category(r) == cat]

    shown = pool[:max(1, limit)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in shown:
        entry: dict[str, Any] = {"name": r["name"]}
        # Stone effect text is uniformly "Mega Evolves X" — no signal, and
        # dozens of them would dominate the payload.
        if not r["is_mega_stone"] and r["effect"]:
            entry["effect"] = r["effect"]
        grouped.setdefault(_item_category(r), []).append(entry)

    out: dict[str, Any] = {
        "count": len(pool),
        "returned": len(shown),
        "include_mega_stones": include_mega_stones,
        "categories": {c: _ITEM_CATEGORY_LABELS.get(c, "") for c in grouped},
        "items_by_category": grouped,
    }
    if not include_mega_stones:
        out["mega_stones_hidden"] = len(stones)
        out["note"] = (
            f"{len(stones)} Mega Stones hidden — pass include_mega_stones=True, "
            "or use list_megas to see which stone unlocks which Mega. Champions' "
            "item pool is much smaller than other games': Choice Scarf is the "
            "only Choice item, and there is no Assault Vest, Heavy-Duty Boots, "
            "Covert Cloak or Safety Goggles. If an item is not in this list it "
            "does not exist here."
        )
    if len(shown) < len(pool):
        out["truncated"] = (f"Showing {len(shown)} of {len(pool)} — raise `limit` "
                            "or filter with `category`/`query`.")
    if cat and not grouped:
        out["warning"] = (f"No items in category {category!r}. Valid: "
                          f"{sorted(_ITEM_CATEGORY_LABELS)}.")
    return out


def _item_category(row: dict[str, Any]) -> str:
    """Normalise the stored category.

    34 of the Champions-original Mega Stones carry a NULL category in the source
    data while the other 41 say "mega-stones"; grouping them apart would be an
    artefact of the pull, not a real distinction.
    """
    if row["is_mega_stone"]:
        return "mega-stones"
    return row["category"] or "other"


def search_items(db: Db, query: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Search items by name.

    Args:
        db: Open Champions database handle.
        query: Optional substring filter over item name.
        limit: Maximum number of results to return.

    Returns:
        Matching items with count.
    """
    rows = db.search_items(query, limit)
    return {
        "count": len(rows),
        "items": [
            {"name": r["name"], "category": r["category"],
             "is_mega_stone": bool(r["is_mega_stone"])}
            for r in rows
        ],
    }


def get_ability(db: Db, name: str) -> dict[str, Any]:
    """PokeAPI-enriched details for one ability.

    Args:
        db: Open Champions database handle.
        name: An ability name.

    Returns:
        The ability's effect and short-effect text, or ``{"error": ...}`` if
        it is not found.
    """
    a = db.get_ability(name)
    if not a:
        return {"error": f"Ability {name!r} not found."}
    return {
        "name": a["name"],
        "effect": a["effect"] or a["short_effect"],
        "short_effect": a["short_effect"],
    }
