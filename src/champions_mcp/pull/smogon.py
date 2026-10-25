"""Ingest Smogon/Pokémon Showdown sets as a historical-builds fallback.

Populates ``smogon_sets``: every generation/tier Showdown publishes, matched
against the current Champions roster by species name and split only into
``doubles``/``singles`` (Champions has no equivalent of Showdown's many
tiers, so the specific tier/generation beyond that split doesn't matter —
this is a *fallback* pool, not tournament data). See
:mod:`champions_mcp.historical_sets` for how it's read back out.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..cache import Cache
from ..champions_stats import canonical_stat
from ..config import Settings
from ..db import Db
from .smogon_source import SmogonClient

_DOUBLES_MARKERS = ("doubles", "vgc")


def classify_format(format_id: str) -> str:
    """'doubles' if a Showdown format id names a doubles/VGC tier, else 'singles'."""
    lowered = format_id.lower()
    return "doubles" if any(m in lowered for m in _DOUBLES_MARKERS) else "singles"


def _set_id(slug: str, fmt: str, source_format: str, set_name: str) -> str:
    payload = f"{slug}|{fmt}|{source_format}|{set_name}"
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _set_hash(
    slug: str, item: str | None, ability: str | None,
    nature: str | None, moves: list[str] | None,
) -> str:
    """Same joint key used by the observed-team corpus (see ``pull.builds``),
    so an identical build re-appearing across generations/tiers collapses to
    one row at read time instead of showing as N near-duplicate entries."""
    payload = "|".join([
        slug, (item or "").lower(), (ability or "").lower(), (nature or "").lower(),
        ",".join(sorted(m.lower() for m in (moves or []))),
    ])
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _species_lookup(db: Db) -> dict[str, str]:
    """display/showdown name (lowercased) -> slug, for every roster entry.

    Built once from the ~268-row `pokemon` table so matching thousands of
    Smogon species entries is an O(1) dict lookup rather than a per-entry
    call into `db.resolve_slug` (whose fallback path does a full-table scan
    per miss — ruinous here, since most Smogon species aren't in Champions
    at all). Only base (non-Mega) roster entries are covered; Showdown's
    Mega-species naming ("Charizard-Mega-Y") doesn't line up with this
    project's own Mega naming (`mega_forms.form_name`, "Mega Charizard Y"),
    so Mega-specific Smogon sets are intentionally left unmatched.
    """
    lookup: dict[str, str] = {}
    for row in db.query("SELECT slug, battle_name, showdown_name FROM pokemon"):
        lookup.setdefault(row["showdown_name"].strip().lower(), row["slug"])
        lookup.setdefault(row["battle_name"].strip().lower(), row["slug"])
    return lookup


def _evs_json(raw: dict[str, Any] | None) -> str | None:
    """Canonicalize a Showdown EVs dict's keys (spa/spd/spe/...) without
    touching the values — see historical_sets.py for why these are surfaced
    as "which stats are invested", never converted into Stat Points."""
    if not raw:
        return None
    out = {canonical_stat(k) or k: v for k, v in raw.items()}
    return json.dumps(out)


async def ingest_smogon_sets(db: Db, settings: Settings) -> dict[str, int]:
    """Fetch every generation's sets file and write matching rows to
    ``smogon_sets``.

    Args:
        db: Open database handle, already populated with the `pokemon` roster.
        settings: Runtime settings (HTTP timeout, user agent, cache path).

    Returns:
        ``{"smogon_sets": n, "skipped_unresolved_species": n}``.
    """
    cache = Cache(settings.cache_db)
    client = SmogonClient(settings, cache)
    try:
        by_gen = await client.all_generations()
    finally:
        await client.aclose()
        cache.close()

    species_slug = _species_lookup(db)
    rows: dict[str, tuple] = {}  # id -> row, de-duplicates same id across passes
    skipped_species = 0

    for gen, data in by_gen.items():
        for source_format, block in (data or {}).items():
            fmt = classify_format(source_format)
            for species_name, sets in (block.get("dex") or {}).items():
                slug = species_slug.get(species_name.strip().lower())
                if not slug:
                    skipped_species += 1
                    continue
                for set_name, s in sets.items():
                    moves = list(s.get("moves") or [])
                    ability = s.get("ability")
                    item = s.get("item")
                    nature = s.get("nature")
                    row_id = _set_id(slug, fmt, source_format, set_name)
                    rows[row_id] = (
                        row_id, slug, fmt, source_format, gen, set_name,
                        ability, item, nature, json.dumps(moves),
                        _evs_json(s.get("evs")),
                        _set_hash(slug, item, ability, nature, moves),
                    )

    db.reset_smogon_sets()
    db.executemany(
        "INSERT OR REPLACE INTO smogon_sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        list(rows.values()),
    )
    return {
        "smogon_sets": len(rows),
        "skipped_unresolved_species": skipped_species,
    }
