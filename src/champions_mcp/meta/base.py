from __future__ import annotations

import abc

import httpx

from ..config import Settings
from ..http import make_http_client
from ..models import MetaEntry, SourceHealth


class MetaSource(abc.ABC):
    """A single meta/usage data source.

    Contract: ``collect`` never raises. On any failure it returns an empty list
    and reports it via :meth:`health`, so one broken/blocked site degrades the
    snapshot instead of failing the whole tool. Scraped sites are ToS-grey and
    structurally unstable by nature — keep parsing defensive.
    """

    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._health = SourceHealth(source=self.name, ok=False, detail="not run")

    def health(self) -> SourceHealth:
        return self._health

    def _client(self) -> httpx.AsyncClient:
        return make_http_client(self._s)

    async def collect(self, regulation_id: str) -> list[MetaEntry]:
        try:
            entries = await self._collect(regulation_id)
            self._health = SourceHealth(
                source=self.name, ok=True, detail="ok", entries=len(entries)
            )
            return entries
        except Exception as exc:  # noqa: BLE001 - sources must never break the tool
            self._health = SourceHealth(
                source=self.name,
                ok=False,
                detail=f"{type(exc).__name__}: {exc}"[:300],
            )
            return []

    @abc.abstractmethod
    async def _collect(self, regulation_id: str) -> list[MetaEntry]: ...

    async def sample_teams(self, regulation_id: str) -> list[list[str]]:
        return []
