"""Reads roster/reference/usage data from pokechamp.gg.

pokechamp.gg publishes the current Pokémon Champions legal roster, full move/
ability/item reference text, and per-Pokémon usage stats (both formats) well
ahead of championsbattledata.com and pokedb.tokyo — confirmed live with
Regulation M-C's additions (Golisopod, Baxcalibur, Rillaboom, the new Mega/
Z-Mega forms and their stones) days after rollout, while those two upstreams
were still on the previous regulation. Used here with the site's permission.

Two response shapes:

* ``/api/*.json`` — plain JSON (a REST-style index), read directly.
* ``/database/*/_payload.json`` — a Nuxt SSR payload; decoded via
  :mod:`.nuxt_payload` into a ``{cache_key: value}`` dict per page. The cache
  keys are Nuxt's own content-addressed ``useAsyncData`` hashes, not a stable
  public contract, so callers here pick the right value by *shape* (does it
  look like a list of rows? does it have a ``singles``/``doubles`` key?)
  rather than by key name.

Only the per-Pokémon page (``pokemon_page``) needs one request per legal
roster entry (~350); every listing endpoint covers the whole game in one
request each, cached permanently. Everything is cached through ``Cache`` so a
rebuild only re-fetches what a season change actually invalidates.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx

from ..cache import Cache
from ..config import Settings
from . import nuxt_payload

# The site's usage stats move as a season progresses; a finished season's
# reference tables (moves/abilities/items) don't. One conservative ttl for
# everything keeps this simple — a rebuild a day later just re-fetches.
_TTL = 86_400


def _pick(data: dict[str, Any], predicate: Callable[[Any], bool]) -> Any:
    """Return the one value in a decoded payload's ``data`` dict matching
    ``predicate``, keyed on shape rather than Nuxt's unstable cache-key names.

    Args:
        data: The ``data`` dict from a decoded ``_payload.json``.
        predicate: Selects the wanted value among the dict's entries.

    Returns:
        The matching value.

    Raises:
        ValueError: If no value (or more than one) matches.
    """
    matches = [v for v in data.values() if predicate(v)]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one matching payload value, found {len(matches)}"
        )
    return matches[0]


def _is_row_list(v: Any) -> bool:
    """Whether ``v`` looks like a listing endpoint's list of data rows."""
    return isinstance(v, list) and (not v or isinstance(v[0], dict))


class PokechampClient:
    """Reads Champions roster/reference/usage data from pokechamp.gg."""

    def __init__(self, settings: Settings, cache: Cache, *, concurrency: int = 6) -> None:
        """Open an HTTP client against pokechamp.gg, backed by ``cache``.

        Args:
            settings: Runtime settings (base URL, timeout, user agent).
            cache: Pull cache to read/write through.
            concurrency: Maximum in-flight per-Pokémon page requests.
        """
        self._http = httpx.AsyncClient(
            base_url=settings.pokechamp_base,
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
        self._cache = cache
        self._sem = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _get_json(self, path: str) -> Any:
        """Cached GET returning the parsed JSON body verbatim (no decoding)."""
        key = f"pokechamp:{path}"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached
        async with self._sem:
            resp = await self._http.get(path)
            resp.raise_for_status()
        data = resp.json()
        await self._cache.set(key, data, ttl=_TTL)
        return data

    async def _get_payload(self, path: str) -> dict[str, Any]:
        """Cached GET of a Nuxt ``_payload.json``, fully devalue-decoded.

        Returns:
            The decoded payload's ``data`` dict (cache-key -> value).
        """
        raw = await self._get_json(path)
        return nuxt_payload.decode(raw)["data"]

    # ---- roster + legality --------------------------------------------- #
    async def filter_index(self) -> list[dict[str, Any]]:
        """The current legal roster: one entry per buildable species/form.

        Returns:
            Rows with ``id`` (dex+form code), ``learnset`` (move ids) and
            ``abilityIds`` (``{"standard": [...], "hidden": id|None}``).
        """
        return await self._get_json("/api/pokemon-filter-index.json")

    async def pokemon_listing(self) -> list[dict[str, Any]]:
        """Identity/stats for every Pokémon entry the site has ever tracked
        (a superset of the current legal roster — filter to
        :meth:`filter_index`'s ids for "currently legal").

        Returns:
            Rows with ``id, no, formNo, slug, displayName, formName, types,
            baseStats, bst, weightKg``.
        """
        data = await self._get_payload("/database/pokemon/_payload.json")
        return _pick(data, _is_row_list)

    async def pokemon_page(self, slug: str) -> dict[str, Any]:
        """One Pokémon's full page: movepool, abilities and per-format usage.

        Args:
            slug: The entry's pokechamp slug (species or ``mega-<species>``
                form of it).

        Returns:
            A dict with the identity block spread in directly (``slug``,
            ``learnsetMoves`` — full move rows, ``abilityDetails``) plus a
            ``usage`` key holding the ``{"singles": {...}, "doubles": {...}}``
            per-format usage block.
        """
        data = await self._get_payload(f"/database/pokemon/{slug}/_payload.json")
        identity = _pick(data, lambda v: isinstance(v, dict) and "slug" in v)
        usage = _pick(data, lambda v: isinstance(v, dict) and "singles" in v)
        return {**identity, "usage": usage}

    async def moves_options(self) -> list[dict[str, Any]]:
        """id -> slug/name for every move (a superset of :meth:`moves_listing`
        by a handful of entries with no full row — used as a name-only
        fallback when an id turns up missing there).

        Returns:
            Rows with ``value`` (id), ``label`` (name) and ``slug``.
        """
        return await self._get_json("/api/moves-options.json")

    async def abilities_options(self) -> list[dict[str, Any]]:
        """id -> slug/name for every ability (a superset of
        :meth:`abilities_listing` by a handful of entries with no full row —
        used as a name-only fallback when an id turns up missing there).

        Returns:
            Rows with ``value`` (id), ``label`` (name) and ``slug``.
        """
        return await self._get_json("/api/abilities-options.json")

    # ---- reference pools (one request each, whole game) ----------------- #
    async def moves_listing(self) -> list[dict[str, Any]]:
        """Every move in the game with full battle data.

        Returns:
            Rows with ``id, slug, name, type, category, power, accuracy, pp,
            priority, description``.
        """
        data = await self._get_payload("/database/moves/_payload.json")
        return _pick(data, _is_row_list)

    async def abilities_listing(self) -> list[dict[str, Any]]:
        """Every ability in the game with its effect text.

        Returns:
            Rows with ``id, slug, name, description``.
        """
        data = await self._get_payload("/database/abilities/_payload.json")
        return _pick(data, _is_row_list)

    async def items_listing(self) -> list[dict[str, Any]]:
        """The full Champions item pool with effect text.

        Returns:
            Rows with ``id, slug, name, category, description`` — Mega Stones
            carry ``category == "Mega Stone"`` and a description naming the
            exact Mega form they unlock.
        """
        data = await self._get_payload("/database/items/_payload.json")
        return _pick(data, _is_row_list)

    async def regulations(self) -> list[dict[str, Any]]:
        """Every regulation×format entry (current and historical).

        Returns:
            Rows with ``title, format, teamSize, startDate, endDate,
            allowedPokemonCount``. Informational only — team-size rules stay
            hand-maintained in ``data/formats.json``.
        """
        data = await self._get_payload("/database/regulations/_payload.json")
        return _pick(data, _is_row_list)

    # ---- batch helper ---------------------------------------------------- #
    async def gather(self, coros: list) -> list:
        """Run a batch of coroutines concurrently (bounded by the client's
        semaphore) and return their results in order."""
        return await asyncio.gather(*coros)
