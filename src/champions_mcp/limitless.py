from __future__ import annotations

from typing import Any

import httpx

from .cache import Cache
from .config import Settings
from .models import TournamentSummary


class LimitlessError(RuntimeError):
    pass


class LimitlessClient:
    """Client for the Limitless VGC tournament API.

    Base: https://play.limitlesstcg.com/api . Most endpoints are public; the
    ``/games/{game}/decks`` endpoint requires an API key (LIMITLESS_API_KEY).
    Responses are cached with a short TTL — tournament data changes slowly and
    we must stay within rate limits.
    """

    def __init__(self, settings: Settings, cache: Cache) -> None:
        self._s = settings
        self._cache = cache
        headers = {"User-Agent": settings.user_agent}
        if settings.limitless_api_key:
            headers["X-Access-Key"] = settings.limitless_api_key
        self._http = httpx.AsyncClient(
            base_url=settings.limitless_base,
            timeout=settings.http_timeout,
            headers=headers,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def has_api_key(self) -> bool:
        return bool(self._s.limitless_api_key)

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        cache_key = f"limitless:{path}:{sorted((params or {}).items())}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            resp = await self._http.get(path, params=params or {})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise LimitlessError(
                    f"Limitless {path} requires an API key "
                    f"(set LIMITLESS_API_KEY)."
                ) from exc
            raise LimitlessError(
                f"Limitless request failed ({exc.response.status_code}) for {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LimitlessError(
                f"Limitless request failed for {path}: {exc}"
            ) from exc
        data = resp.json()
        await self._cache.set(cache_key, data, ttl=self._s.tournament_ttl)
        return data

    async def list_tournaments(
        self,
        game: str = "VGC",
        format: str | None = None,
        organizer_id: int | None = None,
        limit: int = 50,
        page: int = 1,
    ) -> list[TournamentSummary]:
        params: dict[str, Any] = {"game": game, "limit": limit, "page": page}
        if format:
            params["format"] = format
        if organizer_id is not None:
            params["organizerId"] = organizer_id
        data = await self._get("/tournaments", params)
        rows = data if isinstance(data, list) else data.get("tournaments", [])
        out: list[TournamentSummary] = []
        for r in rows:
            out.append(
                TournamentSummary(
                    id=str(r.get("id")),
                    name=r.get("name", ""),
                    game=r.get("game"),
                    format=r.get("format"),
                    date=r.get("date"),
                    players=r.get("players") or r.get("playerCount"),
                )
            )
        return out

    async def tournament_details(self, tid: str) -> dict[str, Any]:
        return await self._get(f"/tournaments/{tid}/details")

    async def tournament_standings(self, tid: str) -> list[dict[str, Any]]:
        data = await self._get(f"/tournaments/{tid}/standings")
        return data if isinstance(data, list) else data.get("standings", [])

    async def tournament_pairings(self, tid: str) -> list[dict[str, Any]]:
        data = await self._get(f"/tournaments/{tid}/pairings")
        return data if isinstance(data, list) else data.get("pairings", [])

    async def winning_teams(
        self, game: str = "VGC", format: str | None = None, top: int = 8
    ) -> list[dict[str, Any]]:
        """Aggregate top-cut teams across recent tournaments.

        The per-player ``decklist`` shape in standings is game-specific and not
        documented for VGC; we surface it verbatim plus a normalized species
        guess so the model can still reason about it.
        """
        tours = await self.list_tournaments(game=game, format=format, limit=15)
        results: list[dict[str, Any]] = []
        for t in tours:
            try:
                standings = await self.tournament_standings(t.id)
            except LimitlessError:
                continue
            for s in standings[:top]:
                results.append(
                    {
                        "tournament": t.name,
                        "tournament_id": t.id,
                        "placement": s.get("placing") or s.get("placement"),
                        "player": s.get("name") or s.get("player"),
                        "team": _extract_species(s.get("decklist")),
                        "raw_decklist": s.get("decklist"),
                    }
                )
        return results


def _extract_species(decklist: Any) -> list[str]:
    if not decklist:
        return []
    if isinstance(decklist, list):
        out = []
        for entry in decklist:
            if isinstance(entry, dict):
                name = entry.get("pokemon") or entry.get("name") or entry.get("species")
                if name:
                    out.append(str(name))
            elif isinstance(entry, str):
                out.append(entry)
        return out
    if isinstance(decklist, dict):
        members = decklist.get("pokemon") or decklist.get("team") or []
        return _extract_species(members)
    return []
