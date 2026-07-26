"""One-time PokeAPI text enrichment for the pull step.

championsbattledata has names but no move metadata, ability/item descriptions,
or a type chart. This client fetches that bounded universe (≈539 moves,
≈200 abilities, ≈148 items, 18 types + ≈210 species for localized names) once,
backed by a permanent SQLite cache so re-runs are nearly free. Nothing here
runs at MCP serve time.
"""

from __future__ import annotations

import asyncio
from typing import Any

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


def _en(entries: list[dict[str, Any]], *keys: str) -> str | None:
    """First English-language value found in `entries` under any of `keys`."""
    for e in entries:
        if e.get("language", {}).get("name") == "en":
            for k in keys:
                if e.get(k):
                    return e[k]
    return None


def _en_name(resource: dict[str, Any], fallback: str) -> str:
    """The English display name from a PokeAPI ``names`` list, or `fallback`."""
    for n in resource.get("names", []):
        if n.get("language", {}).get("name") == "en":
            return n["name"]
    return fallback


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

    async def _get(self, path: str) -> dict[str, Any] | None:
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
    async def move(self, name: str) -> dict[str, Any]:
        """Fetch one move's reference row.

        Args:
            name: The championsbattledata move name.

        Returns:
            A DB-ready dict (slug, name, type, damage_class, power, accuracy,
            pp, priority, target, effect). Falls back to just slug/name if
            PokeAPI has no entry for it.
        """
        slug = slugify(name)  # storage key (joins learnable_moves / usage)
        m = await self._get(f"/move/{normalize_key(name)}")  # PokeAPI slug strips apostrophes
        if not m:
            return {"slug": slug, "name": name}
        return {
            "slug": slug,
            "name": _en_name(m, name),
            "type": (m.get("type") or {}).get("name"),
            "damage_class": (m.get("damage_class") or {}).get("name"),
            "power": m.get("power"),
            "accuracy": m.get("accuracy"),
            "pp": m.get("pp"),
            "priority": m.get("priority", 0),
            "target": (m.get("target") or {}).get("name"),
            "effect": _en(m.get("effect_entries", []), "short_effect", "effect"),
        }

    async def item(self, name: str) -> dict[str, Any]:
        """Fetch one item's reference row.

        Args:
            name: The championsbattledata item name.

        Returns:
            A DB-ready dict (slug, name, category, effect, is_mega_stone).
            Champions-exclusive items with no PokeAPI entry get a heuristic
            Mega Stone flag (name ends in "ite", optionally " X"/" Y").
        """
        slug = slugify(name)
        it = await self._get(f"/item/{normalize_key(name)}")
        if not it:
            # championsbattledata item with no PokeAPI entry: still keep the name,
            # flag Mega Stones heuristically (Champions stones end in "ite",
            # optionally suffixed " X"/" Y" for dual-mega species like Raichu).
            base_name = name[:-2] if name[-2:] in (" X", " Y") else name
            return {
                "slug": slug, "name": name, "category": None, "effect": None,
                "is_mega_stone": 1 if base_name.lower().endswith("ite") else 0,
            }
        category = (it.get("category") or {}).get("name")
        return {
            "slug": slug,
            "name": _en_name(it, name),
            "category": category,
            "effect": _en(it.get("effect_entries", []), "short_effect", "effect"),
            "is_mega_stone": 1 if category == "mega-stones" else 0,
        }

    async def ability(self, name: str) -> dict[str, Any]:
        """Fetch one ability's reference row.

        Args:
            name: The championsbattledata ability name.

        Returns:
            A DB-ready dict (slug, name, effect, short_effect). Falls back to
            just slug/name if PokeAPI has no entry for it.
        """
        slug = slugify(name)
        a = await self._get(f"/ability/{normalize_key(name)}")
        if not a:
            return {"slug": slug, "name": name, "effect": None, "short_effect": None}
        return {
            "slug": slug,
            "name": _en_name(a, name),
            "effect": _en(a.get("effect_entries", []), "effect"),
            "short_effect": _en(a.get("effect_entries", []), "short_effect"),
        }

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
