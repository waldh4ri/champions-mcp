from __future__ import annotations

import json
import re

import httpx

from ..cache import Cache
from ..config import Settings
from ..http import make_http_client
from ..models import SmogonAnalysis, SmogonSet
from .formats import FORMAT_CONFIG, resolve_format

_RPC_URL = "https://www.smogon.com/dex/_rpc/dump-pokemon"

# Cache lifetime for analysis data: 24 h (analyses change rarely).
_ANALYSIS_TTL = 86_400

_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s{2,}")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse excess whitespace."""
    clean = _HTML_TAG.sub(" ", text or "")
    clean = _WHITESPACE.sub(" ", clean)
    return clean.strip()


def _to_alias(name: str) -> str:
    """Convert a Pokémon display name to a Smogon dex URL alias."""
    return name.strip().lower().replace(" ", "-").replace("'", "").replace(".", "")


class SmogonAnalysisClient:
    """Fetches and caches Smogon strategy analyses via the Smogon dex RPC.

    Data is fetched per-Pokémon from the ``champions`` gen on the Smogon
    strategy dex (https://www.smogon.com/dex/champions/) and cached per
    Pokémon alias.

    Usage::

        client = SmogonAnalysisClient(settings, cache)
        analysis = await client.get_analysis("Incineroar", "vgc")
    """

    def __init__(self, settings: Settings, cache: Cache) -> None:
        self._s = settings
        self._cache = cache

    def _http_client(self) -> httpx.AsyncClient:
        client = make_http_client(self._s)
        client.headers["content-type"] = "application/json"
        return client

    def _resolve_format(self, key: str) -> tuple[str, str, str]:
        """Return ``(fmt_key, showdown_id, display_name)`` for the given key."""
        fmt_key = resolve_format(key)
        cfg = FORMAT_CONFIG[fmt_key]
        return fmt_key, cfg["showdown_id"], cfg["rpc_label"]

    async def _fetch_pokemon_raw(self, alias: str) -> dict:
        """Fetch and cache the full ``dump-pokemon`` RPC response for *alias*.

        The complete payload (``strategies``, ``learnsets``, ``moves``,
        ``items``, etc.) is stored so nothing read from the wire is discarded.
        """
        cache_key = f"smogon:rpc2:champions:{alias}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        async with self._http_client() as client:
            resp = await client.post(
                _RPC_URL,
                content=json.dumps(
                    {"gen": "champions", "alias": alias, "language": "en"}
                ),
            )
            resp.raise_for_status()
            data: dict | None = resp.json()

        payload: dict = data or {}
        await self._cache.set(cache_key, payload, ttl=_ANALYSIS_TTL)
        return payload

    async def _fetch_pokemon(self, alias: str) -> list[dict]:
        """Return the list of strategies for *alias* from the Smogon dex RPC."""
        return (await self._fetch_pokemon_raw(alias)).get("strategies", [])

    async def get_analysis(
        self, pokemon: str, format_key: str = "vgc"
    ) -> SmogonAnalysis | None:
        """Return the Smogon analysis for *pokemon* in the given format.

        Returns ``None`` if no entry is found for the Pokémon or format.
        """
        _fmt_key, showdown_id, display_name = self._resolve_format(format_key)
        strategies = await self._fetch_pokemon(_to_alias(pokemon))

        strategy = next(
            (s for s in strategies if s.get("format") == display_name), None
        )
        if strategy is None:
            return None

        overview = _strip_html(strategy.get("overview") or strategy.get("comments") or "")
        sets: list[SmogonSet] = [
            SmogonSet(
                name=m.get("name") or "",
                description=_strip_html(m.get("description") or ""),
            )
            for m in (strategy.get("movesets") or [])
        ]

        return SmogonAnalysis(
            pokemon=pokemon,
            format=showdown_id,
            overview=overview,
            sets=sets,
        )
