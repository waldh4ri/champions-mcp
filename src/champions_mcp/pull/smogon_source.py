"""Reads curated competitive sets from Pokémon Showdown's static data files.

``https://play.pokemonshowdown.com/data/sets/gen{N}.json`` publishes, per
generation, a set of named movesets (moves/ability/item/nature/EVs/IVs) for
every tier Smogon curates — one static JSON file per generation, no scraping
and no auth. Champions has no native Smogon dex (it's a new game), so this is
used purely as cross-game historical evidence: a Pokémon's Champions-legal
roster slot might carry decades of Smogon sets under names Showdown itself
established, which is what makes matching them possible at all (see
``pull.build.SHOWDOWN_OVERRIDES``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..cache import Cache
from ..config import Settings

# Generation files are effectively static (each generation's sets stop
# changing once the next one ships) but gen9's are still occasionally
# revised, so a day's ttl is plenty rather than caching forever.
_TTL = 86_400

GENERATIONS = range(1, 10)


class SmogonClient:
    """Reads Pokémon Showdown's per-generation sets data."""

    def __init__(self, settings: Settings, cache: Cache) -> None:
        """Open an HTTP client against play.pokemonshowdown.com, backed by
        ``cache``.

        Args:
            settings: Runtime settings (timeout, user agent).
            cache: Pull cache to read/write through.
        """
        self._http = httpx.AsyncClient(
            base_url="https://play.pokemonshowdown.com/data/sets",
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
        self._cache = cache

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def generation(self, gen: int) -> dict[str, Any]:
        """Fetch one generation's full sets file.

        Args:
            gen: Generation number (1-9).

        Returns:
            ``{format_id: {"dex": {species: {set_name: set_dict}}}}``, or an
            empty dict if the generation has no published sets file.
        """
        key = f"smogon:gen{gen}"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached
        resp = await self._http.get(f"/gen{gen}.json")
        if resp.status_code == 404:
            await self._cache.set(key, {}, ttl=_TTL)
            return {}
        resp.raise_for_status()
        data = resp.json()
        await self._cache.set(key, data, ttl=_TTL)
        return data

    async def all_generations(self) -> dict[int, dict[str, Any]]:
        """Fetch every generation's sets file.

        Returns:
            Generation number -> its sets file (see :meth:`generation`).
        """
        results = await asyncio.gather(*(self.generation(g) for g in GENERATIONS))
        return dict(zip(GENERATIONS, results))
