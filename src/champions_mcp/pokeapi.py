from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .cache import Cache
from .config import Settings
from .models import Item, Move, Pokemon, Stats
from .normalize import slugify, strip_accents  # noqa: F401 — re-exported


class PokeAPIError(RuntimeError):
    pass


class PokeAPIClient:
    """PokeAPI client backed by a permanent local cache (acts as a growing mirror).

    Pokédex data is immutable, so every successful fetch is cached forever; after
    pre-warming (or organic use) the server runs without depending on PokeAPI uptime.
    """

    def __init__(self, settings: Settings, cache: Cache) -> None:
        self._s = settings
        self._cache = cache
        self._http = httpx.AsyncClient(
            base_url=settings.pokeapi_base,
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str) -> dict[str, Any]:
        key = f"pokeapi:{path}"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached
        try:
            resp = await self._http.get(path)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise PokeAPIError(f"PokeAPI request failed for {path}: {exc}") from exc
        data = resp.json()
        await self._cache.set(key, data, ttl=None)
        return data

    # ---- raw resources -------------------------------------------------

    async def raw_pokemon(self, name_or_id: str | int) -> dict[str, Any]:
        return await self._get(f"/pokemon/{slugify(str(name_or_id))}")

    async def raw_species(self, name_or_id: str | int) -> dict[str, Any]:
        return await self._get(f"/pokemon-species/{slugify(str(name_or_id))}")

    async def raw_move(self, name_or_id: str | int) -> dict[str, Any]:
        return await self._get(f"/move/{slugify(str(name_or_id))}")

    async def raw_item(self, name_or_id: str | int) -> dict[str, Any]:
        return await self._get(f"/item/{slugify(str(name_or_id))}")

    async def raw_type(self, name_or_id: str | int) -> dict[str, Any]:
        return await self._get(f"/type/{slugify(str(name_or_id))}")

    # ---- typed accessors ----------------------------------------------

    async def get_pokemon(self, name_or_id: str | int) -> Pokemon:
        p = await self.raw_pokemon(name_or_id)
        try:
            species = await self.raw_species(p["species"]["name"])
        except PokeAPIError:
            species = {}

        stat_map = {s["stat"]["name"]: s["base_stat"] for s in p.get("stats", [])}
        abilities, hidden = [], None
        for a in p.get("abilities", []):
            nm = a["ability"]["name"]
            if a.get("is_hidden"):
                hidden = nm
            else:
                abilities.append(nm)

        names_by_locale = {
            n["language"]["name"]: n["name"] for n in species.get("names", [])
        }
        mega_forms = [
            v["pokemon"]["name"]
            for v in species.get("varieties", [])
            if "-mega" in v["pokemon"]["name"]
        ]
        return Pokemon(
            slug=p["name"],
            name=names_by_locale.get("en", p["name"]),
            names_by_locale=names_by_locale,
            types=[t["type"]["name"] for t in p.get("types", [])],
            abilities=abilities,
            hidden_ability=hidden,
            base_stats=Stats(
                hp=stat_map.get("hp", 0),
                attack=stat_map.get("attack", 0),
                defense=stat_map.get("defense", 0),
                special_attack=stat_map.get("special-attack", 0),
                special_defense=stat_map.get("special-defense", 0),
                speed=stat_map.get("speed", 0),
            ),
            is_legendary=bool(species.get("is_legendary")),
            is_mythical=bool(species.get("is_mythical")),
            is_baby=bool(species.get("is_baby")),
            generation=(species.get("generation") or {}).get("name"),
            mega_forms=mega_forms,
        )

    async def get_move(self, name_or_id: str | int) -> Move:
        m = await self.raw_move(name_or_id)
        effect = ""
        for e in m.get("effect_entries", []):
            if e["language"]["name"] == "en":
                effect = e.get("short_effect") or e.get("effect") or ""
                break
        return Move(
            slug=m["name"],
            name=_en_name(m, m["name"]),
            type=(m.get("type") or {}).get("name"),
            damage_class=(m.get("damage_class") or {}).get("name"),
            power=m.get("power"),
            accuracy=m.get("accuracy"),
            pp=m.get("pp"),
            priority=m.get("priority", 0),
            target=(m.get("target") or {}).get("name"),
            effect=effect or None,
        )

    async def get_item(self, name_or_id: str | int) -> Item:
        it = await self.raw_item(name_or_id)
        category = (it.get("category") or {}).get("name")
        effect = ""
        for e in it.get("effect_entries", []):
            if e["language"]["name"] == "en":
                effect = e.get("short_effect") or e.get("effect") or ""
                break
        return Item(
            slug=it["name"],
            name=_en_name(it, it["name"]),
            category=category,
            effect=effect or None,
            is_mega_stone=category == "mega-stones",
        )

    async def type_matchups(self, type_name: str) -> dict[str, list[str]]:
        t = await self.raw_type(type_name)
        rel = t.get("damage_relations", {})
        return {
            k: [x["name"] for x in rel.get(k, [])]
            for k in (
                "double_damage_to",
                "half_damage_to",
                "no_damage_to",
                "double_damage_from",
                "half_damage_from",
                "no_damage_from",
            )
        }

    async def all_pokemon_slugs(self) -> list[str]:
        key = "pokeapi:index:pokemon-slugs"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached
        data = await self._get("/pokemon?limit=20000")
        slugs = [r["name"] for r in data.get("results", [])]
        await self._cache.set(key, slugs, ttl=None)
        return slugs

    async def search_pokemon(self, query: str, limit: int = 25) -> list[str]:
        q = slugify(query)
        slugs = await self.all_pokemon_slugs()
        starts = [s for s in slugs if s.startswith(q)]
        contains = [s for s in slugs if q in s and s not in starts]
        return (starts + contains)[:limit]


def _en_name(resource: dict[str, Any], fallback: str) -> str:
    for n in resource.get("names", []):
        if n["language"]["name"] == "en":
            return n["name"]
    return fallback
