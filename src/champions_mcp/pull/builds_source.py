"""Fetch *observed teams* — real six-Pokémon lists people actually brought.

championsbattledata gives marginal frequencies (how often Garchomp runs
Earthquake, how often it holds a Choice Scarf) but never the joint fact that a
*Scarf* Garchomp runs a different four moves than a Life Orb one. Two sources
close that gap, and they split cleanly by format:

* **Limitless** (`play.limitlesstcg.com/api`) — VGC/doubles. Tournament
  standings carry every player's full teamlist: species, item, ability, nature
  and all four moves. Open team sheets are enforced, so publish rates are high.
  No API key needed; unauthenticated callers get 50 requests per 5 minutes.

* **pokedb** (`champs.pokedb.tokyo`) — BSS/singles. Japan is where singles is
  actually played, and バトルデータベース チャンピオンズ exports the top ranked-ladder
  teams per season as JSON under a published open-data policy. Carries species
  and held item but **no moves** — hence the ``team_and_item`` evidence level.
  Western singles tournaments are not a usable substitute: across 916 Champions
  events on Limitless only 9 were singles.

Everything is cached permanently in ``pull-cache.sqlite``: a finished tournament
never changes, so a rebuild costs no network and only genuinely new events are
fetched.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..cache import Cache
from ..config import Settings

# Unauthenticated Limitless allows 50 requests / 5 min. Pace below that rather
# than sprinting into a 429 — a backfill is a background chore, not a race.
_UNAUTH_DELAY = 6.5
_AUTH_DELAY = 0.5

# The ladder export is regenerated as a season progresses, so unlike a finished
# tournament it must not be cached forever. A day is plenty.
_POKEDB_TTL = 86_400


class LimitlessClient:
    """Reads Champions tournament teamlists from the Limitless public API."""

    def __init__(self, settings: Settings, cache: Cache) -> None:
        """Open an HTTP client against the Limitless API.

        Args:
            settings: Runtime settings (base URL, optional key, timeout, UA).
            cache: Permanent pull cache to read/write through.
        """
        self._s = settings
        self._cache = cache
        headers = {"User-Agent": settings.user_agent}
        if settings.limitless_key:
            headers["X-Access-Key"] = settings.limitless_key
        self._http = httpx.AsyncClient(
            base_url=settings.limitless_base,
            timeout=settings.http_timeout,
            headers=headers,
            follow_redirects=True,
        )
        self._delay = _AUTH_DELAY if settings.limitless_key else _UNAUTH_DELAY
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _get(self, path: str, *, ttl: float | None) -> Any | None:
        """Cached, rate-paced GET against the Limitless API.

        Args:
            path: API path relative to the configured base.
            ttl: Cache lifetime in seconds, or None to cache permanently.

        Returns:
            The parsed JSON body, or None if the request 404s.
        """
        key = f"limitless:{path}"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached or None
        # Serialize *and* pace: concurrency here would only burn the quota
        # faster, and the whole job is bounded by the rate limit anyway.
        async with self._lock:
            try:
                resp = await self._http.get(path)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    await self._cache.set(key, {}, ttl=ttl)
                    return None
                raise
            finally:
                await asyncio.sleep(self._delay)
        data = resp.json()
        await self._cache.set(key, data, ttl=ttl)
        return data

    async def tournaments(self, regulation: str, limit: int) -> list[dict[str, Any]]:
        """List Champions VGC tournaments for one regulation, newest first.

        The index is deliberately given a short ttl — it grows as events finish,
        unlike the per-tournament standings it points at.

        Args:
            regulation: Limitless format id (e.g. ``"M-B"``).
            limit: Maximum number of tournaments to return.

        Returns:
            Tournament summary dicts (id, name, date, players, format).
        """
        out: list[dict[str, Any]] = []
        page = 1
        while len(out) < limit:
            batch = await self._get(
                f"/tournaments?game=VGC&format={regulation}&limit=100&page={page}",
                ttl=3600,
            )
            if not batch:
                break
            out += batch
            if len(batch) < 100:
                break
            page += 1
        return out[:limit]

    async def standings(self, tournament_id: str) -> list[dict[str, Any]]:
        """Fetch one tournament's standings, including every published teamlist.

        A finished tournament is immutable, so this is cached forever.

        Args:
            tournament_id: Limitless tournament id.

        Returns:
            One dict per player (name, placing, record, ``decklist``), or an
            empty list if the tournament has no published standings.
        """
        return await self._get(
            f"/tournaments/{tournament_id}/standings", ttl=None
        ) or []


class PokedbClient:
    """Reads Champions ranked-ladder team exports from champs.pokedb.tokyo.

    The site asks that applications download to their own server and cache
    rather than proxying end-user requests, which is exactly what the pull step
    does — this runs once at build time, never at serve time.
    """

    def __init__(self, settings: Settings, cache: Cache) -> None:
        """Open an HTTP client against the pokedb open-data endpoint.

        Args:
            settings: Runtime settings (base URL, timeout, user agent).
            cache: Pull cache to read/write through.
        """
        self._s = settings
        self._cache = cache
        self._http = httpx.AsyncClient(
            base_url=settings.pokedb_base,
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def season(self, number: int, rule: str = "single") -> dict[str, Any] | None:
        """Fetch one season's ranked-team export.

        Args:
            number: 1-based season number (1 = M-1, 2 = M-2, …).
            rule: ``"single"`` or ``"double"``.

        Returns:
            The parsed export (``season``, ``rule``, ``teams``), or None once
            the season number runs past what has been published.
        """
        path = f"/opendata/s{number}_{rule}_ranked_teams.json"
        key = f"pokedb:{path}"
        cached = await self._cache.get(key)
        if cached is not None:
            return cached or None
        try:
            resp = await self._http.get(path)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Future seasons 404 until they exist — remember it briefly so a
                # rebuild doesn't re-probe, but not forever, since it will exist.
                await self._cache.set(key, {}, ttl=_POKEDB_TTL)
                return None
            raise
        try:
            data = resp.json()
        except ValueError:
            # The 404 page is served as HTML with a 200 in some edge cases.
            return None
        if not isinstance(data, dict) or "teams" not in data:
            return None
        await self._cache.set(key, data, ttl=_POKEDB_TTL)
        await asyncio.sleep(0.5)
        return data

    async def all_seasons(self, rule: str = "single", *, max_season: int = 40) -> list[dict[str, Any]]:
        """Fetch every published season export for a rule, oldest first.

        Args:
            rule: ``"single"`` or ``"double"``.
            max_season: Hard stop, so an upstream change cannot loop forever.

        Returns:
            The season exports that exist, in ascending season order.
        """
        out: list[dict[str, Any]] = []
        misses = 0
        for n in range(1, max_season + 1):
            data = await self.season(n, rule)
            if data is None:
                misses += 1
                # Two consecutive gaps means we are past the end, not in a hole.
                if misses >= 2:
                    break
                continue
            misses = 0
            out.append(data)
        return out
