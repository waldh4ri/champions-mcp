"""PokeAPI enrichment for the pull step: type chart + localized names.

pokechamp.gg already gives move/ability/item reference text natively, so
PokeAPI is only needed for two things it doesn't offer: the 18×18 type chart
(game-invariant) and localized species names (``pull.builds``' pokedb
ingestion resolves Japanese held-item and species names against them).
Backed by a permanent SQLite cache so re-runs are nearly free. Nothing here
runs at MCP serve time.
"""

from __future__ import annotations

import asyncio

import httpx

from ..cache import Cache
from ..config import Settings
from ..normalize import normalize_key, slugify

# The 18 standard types (Champions uses the same chart).
TYPES = (
    "normal", "fire", "water", "electric", "grass", "ice", "fighting",
    "poison", "ground", "flying", "psychic", "bug", "rock", "ghost",
    "dragon", "dark", "steel", "fairy",
)


class PokeApiEnricher:
    """Fetches reference resources from PokeAPI, cached permanently."""

    def __init__(self, settings: Settings, cache: Cache, *, concurrency: int = 8) -> None:
        """Open an HTTP client against PokeAPI, backed by `cache`.

        Args:
            settings: Runtime settings (PokeAPI base URL, timeout, user agent).
            cache: Permanent response cache to read/write through.
            concurrency: Maximum number of in-flight PokeAPI requests.
        """
        self._s = settings
        self._cache = cache
        self._http = httpx.AsyncClient(
            base_url=settings.pokeapi_base,
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
        self._sem = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _get(self, path: str) -> dict | None:
        """Cached GET against PokeAPI; a cached 404 is remembered as `None`.

        Args:
            path: PokeAPI path relative to `pokeapi_base` (e.g. "/move/tackle").

        Returns:
            The parsed JSON response, or None if the resource doesn't exist.
        """
        key = f"pokeapi:{path}"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached or None  # cached {} sentinel => known-missing
        async with self._sem:
            try:
                resp = await self._http.get(path)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    await self._cache.set(key, {}, ttl=None)  # remember the 404
                    return None
                raise
        data = resp.json()
        await self._cache.set(key, data, ttl=None)
        return data

    # ---- typed extractors --------------------------------------------- #
    async def item_ja_name(self, name: str) -> tuple[str, str | None]:
        """Fetch one item's Japanese display name.

        pokedb reports held items in Japanese, so the observed-build ingest
        needs a Japanese -> slug map. Champions-exclusive Mega Stones have no
        PokeAPI entry and come back as None; ``pull.builds`` derives those from
        the ``<species>ナイト`` naming rule instead.

        Args:
            name: The item's display name (as stored in the ``items`` table).

        Returns:
            A tuple of (item slug, Japanese name or None).
        """
        it = await self._get(f"/item/{normalize_key(name)}")
        if not it:
            return slugify(name), None
        for n in it.get("names", []):
            if n.get("language", {}).get("name") in ("ja-Hrkt", "ja"):
                return slugify(name), n["name"]
        return slugify(name), None

    async def type_relations(self, name: str) -> dict[str, list[str]]:
        """Fetch one type's offensive damage relations.

        Args:
            name: A lowercase type name (see `TYPES`).

        Returns:
            A dict of ``double``/``half``/``no`` -> list of defending type
            names this attacking type hits at that multiplier.
        """
        t = await self._get(f"/type/{name}")
        rel = (t or {}).get("damage_relations", {})
        return {
            "double": [x["name"] for x in rel.get("double_damage_to", [])],
            "half": [x["name"] for x in rel.get("half_damage_to", [])],
            "no": [x["name"] for x in rel.get("no_damage_to", [])],
        }

    async def species_names(self, species_slug: str) -> dict[str, str]:
        """Fetch a species' localized display names.

        Args:
            species_slug: A PokeAPI pokemon-species slug.

        Returns:
            A mapping of language code (fr/de/es/it/ja/ko) to localized name;
            empty if the species has no PokeAPI entry.
        """
        s = await self._get(f"/pokemon-species/{species_slug}")
        if not s:
            return {}
        return {
            n["language"]["name"]: n["name"]
            for n in s.get("names", [])
            if n.get("language", {}).get("name") in ("fr", "de", "es", "it", "ja", "ko")
        }

    # ---- batch helpers ------------------------------------------------- #
    async def gather(self, coros: list) -> list:
        """Run a batch of enrichment coroutines concurrently (bounded by
        `concurrency`) and return their results in order."""
        return await asyncio.gather(*coros)
