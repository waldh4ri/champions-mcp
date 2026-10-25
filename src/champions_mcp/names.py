"""Resolve a (possibly localized / form) Pokémon name to its DB slug.

The DB already indexes slug, battle name, Showdown name and localized names
(``Db.resolve_slug``); this adds a fuzzy fallback over the 235-entry slug
universe so small typos still resolve. No network — everything is local.
"""

from __future__ import annotations

import difflib

from .db import Db
from .normalize import base_species, slugify


class NameError(KeyError):
    """Raised when a Pokémon name cannot be resolved to a roster slug."""


def resolve(db: Db, name: str) -> str:
    """Resolve a name to its Champions roster slug.

    Tries, in order: the DB's exact/localized lookup, a Mega/Primal prefix
    strip, a base-species fallback, then a fuzzy match over all slugs.

    Args:
        db: Open database handle.
        name: Pokémon name, form name, or slug as given by the caller.

    Returns:
        The resolved slug.

    Raises:
        NameError: No slug could be resolved for ``name``.
    """
    slug = db.resolve_slug(name)
    if slug:
        return slug

    # Mega / Primal forms are not separate roster entries (they are triggered by
    # the held stone): resolve to the base species, whose record carries the Mega
    # form data. Handles "Mega Garchomp", "Primal Groudon", "Garchomp-Mega".
    low = name.strip().lower()
    for prefix in ("mega ", "primal "):
        if low.startswith(prefix):
            base = db.resolve_slug(name.strip()[len(prefix):])
            if base:
                return base
    bs = base_species(slugify(name))
    if bs != slugify(name) and db.query_one("SELECT 1 FROM pokemon WHERE slug = ?", (bs,)):
        return bs

    match = difflib.get_close_matches(slugify(name), db.all_pokemon_slugs(), n=1, cutoff=0.8)
    if match:
        return match[0]
    # Say plainly that it is not in the roster and offer the nearest names: a
    # model that misremembers a name ("Noivade") otherwise loops on spellings,
    # and one that asks for a species Champions doesn't have keeps retrying it.
    near = difflib.get_close_matches(slugify(name), db.all_pokemon_slugs(), n=3, cutoff=0.6)
    names = [db.get_pokemon_row(s)["battle_name"] for s in near]
    hint = f" Closest roster names: {', '.join(names)}." if names else ""
    raise NameError(
        f"No Pokémon {name!r} in the current Champions roster — it is either "
        f"misspelled or not in this game.{hint} Find candidates with "
        f"search_pokemon / search_pokemon_by_profile rather than recalling names."
    )


def try_resolve(db: Db, name: str) -> str | None:
    try:
        return resolve(db, name)
    except NameError:
        return None
