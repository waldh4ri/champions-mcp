"""Smogon monthly chaos stats client for Pokémon Champions formats.

Data source: https://www.smogon.com/stats/YYYY-MM/chaos/{format}-{rating}.json

The chaos JSON is published by Smogon on the 1st of each month for the
previous month.  It contains per-Pokémon usage, moves, items, abilities,
spreads (nature + stat values), teammates and checks/counters — all with
usage fractions.

In Champions the stat-point (SP) budget replaces EVs.  The spread strings
in chaos stats use the same ``Nature:HP/Atk/Def/SpA/SpD/Spe`` notation as
standard Showdown, but the numbers are SP (1 SP = +1 stat at level 50),
with a maximum of 32 per stat and 66 total.

Format identifiers (confirmed from https://www.smogon.com/stats/2026-04/):
  VGC (doubles) → gen9championsvgc2026regma
  BSS (singles) → gen9championsbssregma
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from ..cache import Cache
from ..config import Settings
from ..http import make_http_client
from ..models import (
    ChaosCounter,
    ChaosSpread,
    MetaEntry,
    PokemonChaosData,
    UsageEntry,
)
from .base import MetaSource
from .formats import FORMAT_CONFIG

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAOS_BASE = "https://www.smogon.com/stats"
DEFAULT_RATING = 1760  # highest ladder rating cutoff; use for competitive data
TOP_N = 15  # entries per category returned in detailed chaos data

# Derived from FORMAT_CONFIG — update format IDs there, not here.
CHAOS_FORMATS: dict[str, str] = {
    k: v["showdown_id"] for k, v in FORMAT_CONFIG.items()
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def candidate_months() -> list[str]:
    """Return candidate YYYY-MM strings to probe, newest-first (up to 3)."""
    today = date.today()
    months = []
    for i in range(3):
        d = today - timedelta(days=i * 31)
        months.append(f"{d.year:04d}-{d.month:02d}")
    return months


def _parse_usage(entry: dict) -> float:
    """Extract usage fraction (0–1) from a chaos entry, handling both formats."""
    val = entry.get("usage", 0)
    if isinstance(val, dict):
        # Newer chaos format: {"raw": ..., "raw%": ..., "weighted": ..., "weighted%": ...}
        return float(val.get("weighted%") or val.get("raw%") or 0.0)
    return float(val or 0.0)


def _sorted_pairs(mapping: dict, n: int, normalizer: float = 1.0) -> list[UsageEntry]:
    """Return top-n entries from a mapping as UsageEntry list.

    ``normalizer`` is the per-slot weight denominator (sum of all item values
    for the Pokémon).  In standard Smogon chaos, the raw values for moves/items/
    abilities/spreads are weighted counts, NOT fractions.  Dividing by the item
    sum converts them to a 0–100 percentage scale.
    """
    return [
        UsageEntry(name=k, usage_percent=round(float(v) / normalizer * 100, 1))
        for k, v in sorted(mapping.items(), key=lambda x: x[1], reverse=True)[:n]
    ]


def _top_names(mapping: dict, n: int) -> list[str]:
    """Return top-n keys from a fraction-valued mapping."""
    return [k for k, _ in sorted(mapping.items(), key=lambda x: x[1], reverse=True)[:n]]


def _entry_normalizer(entry: dict) -> float:
    """Compute the per-slot weight normalizer from an entry's item values.

    In Champions chaos the raw values for moves/items/abilities/spreads/teammates
    are *weighted counts*, not fractions.  Since a Pokémon holds exactly one item,
    the sum of all item values equals the total weighted appearances for that
    Pokémon.  Dividing any raw value by this normalizer gives a true fraction.

    Falls back to ability sum, then raw count, to stay robust.
    """
    items_sum = sum((entry.get("Items") or {}).values())
    if items_sum > 0:
        return items_sum
    abilities_sum = sum((entry.get("Abilities") or {}).values())
    if abilities_sum > 0:
        return abilities_sum
    return float(entry.get("Raw count") or 1) or 1.0


def _parse_spread(spread_str: str, usage_pct: float) -> "ChaosSpread | None":
    """Parse ``'Nature:HP/Atk/Def/SpA/SpD/Spe'`` into a :class:`ChaosSpread`.

    Returns ``None`` on malformed input.
    """
    try:
        nature, stats_part = spread_str.split(":", 1)
        parts = list(map(int, stats_part.split("/")))
        if len(parts) != 6:
            return None
        stat_names = ("hp", "attack", "defense", "special_attack", "special_defense", "speed")
        sp = {k: v for k, v in zip(stat_names, parts) if v > 0}
        return ChaosSpread(
            spread=spread_str,
            nature=nature.strip(),
            stat_points=sp,
            usage_percent=usage_pct,
        )
    except (ValueError, AttributeError):
        return None


def _parse_counters(mapping: dict, n: int) -> list[ChaosCounter]:
    """Parse the ``Checks and Counters`` mapping into :class:`ChaosCounter` list.

    Each value is ``[sample_size, ko_rate, switch_rate]``.  Sort by sample
    size descending so the most-tested checks appear first.
    """
    result = []
    for species, v in sorted(
        mapping.items(),
        key=lambda x: (x[1][0] if isinstance(x[1], list) else 0),
        reverse=True,
    )[:n]:
        if isinstance(v, list) and len(v) >= 3:
            result.append(
                ChaosCounter(
                    species=species,
                    sample_size=int(v[0]),
                    ko_rate=round(float(v[1]), 3),
                    switch_rate=round(float(v[2]), 3),
                )
            )
    return result


# ---------------------------------------------------------------------------
# Core chaos client
# ---------------------------------------------------------------------------


class SmogonChaosClient:
    """Client for Smogon monthly chaos JSON files.

    Used by :class:`SmogonChaosSource` (aggregator) and directly by the
    ``get_pokemon_sets`` MCP tool for per-Pokémon deep dives.

    Results are cached for one full meta TTL (same as snapshot caching) to
    avoid repeated network hits — chaos data only changes once a month.
    """

    def __init__(self, settings: Settings, cache: Cache) -> None:
        self._s = settings
        self._cache = cache

    def _client(self) -> httpx.AsyncClient:
        return make_http_client(self._s)

    async def _fetch_chaos(
        self, format_key: str, rating: int = DEFAULT_RATING
    ) -> tuple[str, dict] | None:
        """Fetch chaos JSON for *format_key* at *rating*.

        Probes candidate months newest-first and returns ``(month, data)``
        for the first successful hit.  Returns ``None`` if all probes fail.
        """
        chaos_id = CHAOS_FORMATS.get(format_key)
        if not chaos_id:
            return None

        cache_key = f"chaos:raw:{format_key}:{rating}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached["month"], cached["data"]

        async with self._client() as http:
            for month in candidate_months():
                url = f"{CHAOS_BASE}/{month}/chaos/{chaos_id}-{rating}.json"
                try:
                    resp = await http.get(url)
                    if resp.status_code == 200:
                        payload = resp.json()
                        await self._cache.set(
                            cache_key,
                            {"month": month, "data": payload},
                            ttl=self._s.meta_ttl,
                        )
                        return month, payload
                except Exception:  # noqa: BLE001
                    continue
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_usage_entries(
        self, format_key: str, rating: int = DEFAULT_RATING
    ) -> list[MetaEntry]:
        """Return a usage-sorted :class:`MetaEntry` list for the aggregator."""
        result = await self._fetch_chaos(format_key, rating)
        if result is None:
            return []
        _month, chaos = result

        entries: list[MetaEntry] = []
        for species, entry in chaos.get("data", {}).items():
            if not species:
                continue
            usage_pct = round(_parse_usage(entry) * 100, 4)
            entries.append(
                MetaEntry(
                    species=species,
                    usage_percent=usage_pct,
                    common_items=_top_names(entry.get("Items") or {}, TOP_N),
                    common_abilities=_top_names(entry.get("Abilities") or {}, TOP_N),
                    common_moves=_top_names(entry.get("Moves") or {}, TOP_N),
                    common_tera=_top_names(entry.get("Tera Types") or {}, TOP_N),
                    common_spreads=_top_names(entry.get("Spreads") or {}, TOP_N),
                    teammates=_top_names(entry.get("Teammates") or {}, TOP_N),
                    checks_counters=_top_names(entry.get("Checks and Counters") or {}, TOP_N),
                    sources=["smogon_chaos"],
                )
            )

        entries.sort(key=lambda e: e.usage_percent or 0.0, reverse=True)
        return entries

    async def get_pokemon_chaos(
        self,
        species: str,
        format_key: str,
        rating: int = DEFAULT_RATING,
        top_n: int = TOP_N,
    ) -> PokemonChaosData | None:
        """Return full chaos breakdown for a single Pokémon.

        Performs a case-insensitive name match, then falls back to a
        substring match (e.g. ``"Feraligatr"`` matches ``"Feraligatr-Mega"``).
        Returns ``None`` if the Pokémon is not found in the chaos data.
        """
        result = await self._fetch_chaos(format_key, rating)
        if result is None:
            return None
        month, chaos = result
        info = chaos.get("info", {})
        data: dict = chaos.get("data", {})

        # Exact case-insensitive match first, then partial.
        species_lower = species.strip().lower()
        matched = next((k for k in data if k.lower() == species_lower), None)
        if matched is None:
            matched = next((k for k in data if species_lower in k.lower()), None)
        if matched is None:
            return None

        entry = data[matched]
        normalizer = _entry_normalizer(entry)
        usage_pct = round(_parse_usage(entry) * 100, 2)

        # Parse spreads with usage %
        raw_spreads: dict = entry.get("Spreads") or {}
        parsed_spreads: list[ChaosSpread] = []
        for spread_str, raw_val in sorted(
            raw_spreads.items(), key=lambda x: x[1], reverse=True
        )[:top_n]:
            spread_pct = round(float(raw_val) / normalizer * 100, 1)
            cs = _parse_spread(spread_str, spread_pct)
            if cs is not None:
                parsed_spreads.append(cs)

        return PokemonChaosData(
            species=matched,
            format=format_key,
            month=month,
            rating_cutoff=rating,
            total_battles=int(info.get("number of battles", 0)),
            usage_percent=usage_pct,
            raw_count=int(entry.get("Raw count", 0)),
            moves=_sorted_pairs(entry.get("Moves") or {}, top_n, normalizer),
            items=_sorted_pairs(entry.get("Items") or {}, top_n, normalizer),
            abilities=_sorted_pairs(entry.get("Abilities") or {}, top_n, normalizer),
            spreads=parsed_spreads,
            teammates=_sorted_pairs(entry.get("Teammates") or {}, top_n, normalizer),
            checks_counters=_parse_counters(entry.get("Checks and Counters") or {}, top_n),
        )

    async def list_available_months(self, format_key: str, rating: int = DEFAULT_RATING) -> list[str]:
        """Probe candidate months and return those that have data."""
        chaos_id = CHAOS_FORMATS.get(format_key)
        if not chaos_id:
            return []
        found = []
        async with self._client() as http:
            for month in candidate_months():
                url = f"{CHAOS_BASE}/{month}/chaos/{chaos_id}-{rating}.json"
                try:
                    resp = await http.head(url)
                    if resp.status_code == 200:
                        found.append(month)
                except Exception:  # noqa: BLE001
                    continue
        return found


# ---------------------------------------------------------------------------
# MetaSource wrapper (for MetaAggregator compatibility)
# ---------------------------------------------------------------------------


class SmogonChaosSource(MetaSource):
    """Smogon chaos stats wrapped as a :class:`MetaSource` for the aggregator.

    Delegates all HTTP and parsing work to :class:`SmogonChaosClient`.
    """

    name = "smogon_chaos"

    def __init__(self, settings: Settings, client: SmogonChaosClient) -> None:
        super().__init__(settings)
        self._chaos = client

    async def _collect(self, regulation_id: str) -> list[MetaEntry]:
        # ``regulation_id`` here is already the normalised short key ("vgc"/"bss").
        return await self._chaos.get_usage_entries(regulation_id)
