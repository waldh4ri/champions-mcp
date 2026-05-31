from __future__ import annotations

import difflib
import re

from .cache import Cache
from .normalize import REGION_MAP
from .pokeapi import PokeAPIClient, slugify

# Natural-language regional forms -> PokeAPI slug suffix. LLMs commonly write
# "Alolan Ninetales" / "Ninetales (Galarian Form)" instead of the slug
# "ninetales-alola"; normalize so the form resolves (and downstream roster
# checks can correctly report it as in/out of Champions).
_REGION_RE = re.compile(
    r"^(alolan|galarian|hisuian|paldean)\s+(.+)$", re.IGNORECASE
)
_REGION_PAREN_RE = re.compile(
    r"^(.+?)\s*\((?:an?\s+)?(alolan|galarian|hisuian|paldean)\b", re.IGNORECASE
)


def regional_slug(name: str) -> str | None:
    """Map 'Alolan Ninetales' / 'Ninetales (Galarian Form)' -> 'ninetales-alola'."""
    s = name.strip()
    m = _REGION_RE.match(s)
    if m:
        return f"{slugify(m.group(2))}-{REGION_MAP[m.group(1).lower()]}"
    m = _REGION_PAREN_RE.match(s)
    if m:
        return f"{slugify(m.group(1))}-{REGION_MAP[m.group(2).lower()]}"
    return None

# Small offline seed so the most common cross-locale lookups (and tests) work
# without a pre-warmed index. The full FR/de/... index is built lazily from
# PokeAPI by build_locale_index() and cached permanently.
_SEED_FR_TO_SLUG: dict[str, str] = {
    "tarenbulle": "araquanid",
    "carchacrok": "garchomp",
    "scalproie": "kingambit",
    "lougaroc": "lycanroc",
    "amovenus": "amoonguss",
    "torgal": "incineroar",
    "flagadoss": "slowbro",
    "ossatueur": "marowak",
    "noctunoir": "dusknoir",
    "elecsprint": "boltund",
}

_NORMALIZE_CACHE_KEY = "names:fr-to-slug"


class NameIndex:
    """Resolves a Pokémon name in any supported locale to its PokeAPI slug.

    Resolution order: explicit slug match -> locale index (seed + cached
    PokeAPI-built) -> fuzzy match against the known slug list. This is what lets
    the model accept French names (e.g. "Tarenbulle" -> "araquanid").
    """

    def __init__(self, pokeapi: PokeAPIClient, cache: Cache) -> None:
        self._api = pokeapi
        self._cache = cache
        self._fr: dict[str, str] = dict(_SEED_FR_TO_SLUG)
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        cached = await self._cache.get(_NORMALIZE_CACHE_KEY)
        if cached:
            self._fr.update(cached)
        self._loaded = True

    async def build_locale_index(self, locale: str = "fr") -> int:
        """Build and permanently cache a {localized name -> slug} map.

        Network-bound (one request per species); intended for the prewarm
        script, not the hot path.
        """
        index: dict[str, str] = {}
        species = await self._api._get("/pokemon-species?limit=20000")  # noqa: SLF001
        for entry in species.get("results", []):
            slug = entry["name"]
            try:
                data = await self._api.raw_species(slug)
            except Exception:
                continue
            for n in data.get("names", []):
                if n["language"]["name"] == locale:
                    index[slugify(n["name"])] = slug
        await self._cache.set(_NORMALIZE_CACHE_KEY, index, ttl=None)
        self._fr.update(index)
        self._loaded = True
        return len(index)

    async def resolve(self, name: str) -> str:
        await self._ensure_loaded()
        slug = slugify(name)

        # 1. direct slug / english match
        try:
            await self._api.raw_pokemon(slug)
            return slug
        except Exception:
            pass

        # 1b. natural-language regional form ("Alolan Ninetales")
        rslug = regional_slug(name)
        if rslug:
            try:
                await self._api.raw_pokemon(rslug)
                return rslug
            except Exception:
                pass

        # 2. locale index (French and any other built locale)
        if slug in self._fr:
            return self._fr[slug]

        # 3. fuzzy against the known slug universe
        try:
            slugs = await self._api.all_pokemon_slugs()
        except Exception:
            slugs = []
        match = difflib.get_close_matches(slug, slugs, n=1, cutoff=0.82)
        if match:
            return match[0]

        raise KeyError(
            f"Could not resolve Pokémon name {name!r}. If it is a non-English "
            f"name, run champions-mcp-prewarm to build the locale index."
        )
