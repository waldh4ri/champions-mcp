from __future__ import annotations

import time

from ..cache import Cache
from ..config import Settings
from ..limitless import LimitlessClient, LimitlessError
from ..models import MetaEntry, MetaSnapshot
from .chaos import SmogonChaosClient, SmogonChaosSource
from .smogon import resolve_format


class MetaAggregator:
    """Thin wrapper around SmogonChaosSource + Limitless sample teams.

    The *format_key* accepted by :meth:`snapshot` can be one of the two
    Champions short keys (``"vgc"`` or ``"bss"``) **or** any alias
    accepted by :func:`~.smogon.resolve_format` (e.g. ``"doubles"``,
    ``"singles"``, ``"M-A"``).

    Stats are sourced directly from Smogon monthly chaos JSON:
    https://www.smogon.com/stats/YYYY-MM/chaos/gen9champions*-1760.json
    """

    def __init__(
        self,
        settings: Settings,
        cache: Cache,
        limitless: LimitlessClient,
        chaos_client: SmogonChaosClient,
    ) -> None:
        self._s = settings
        self._cache = cache
        self._limitless = limitless
        self._source = SmogonChaosSource(settings, chaos_client)

    async def snapshot(
        self, format_key: str = "vgc", refresh: bool = False
    ) -> MetaSnapshot:
        # Normalise to "vgc" / "bss" so cache keys are stable.
        try:
            fmt = resolve_format(format_key)
        except ValueError:
            fmt = "vgc"

        key = f"meta:snapshot:{fmt}"
        if not refresh:
            cached = await self._cache.get(key)
            if cached is not None:
                return MetaSnapshot.model_validate(cached)

        entries: list[MetaEntry] = await self._source.collect(fmt)
        health = [self._source.health()]

        # Sample teams are only available for VGC via Limitless.
        # BSS is not tracked by Limitless.
        sample_teams: list[list[str]] = []
        if fmt == "vgc":
            try:
                winners = await self._limitless.winning_teams(
                    game="VGC", format="M-A", top=8
                )
                sample_teams = [w["team"] for w in winners if w.get("team")]
            except LimitlessError:
                pass

        snap = MetaSnapshot(
            regulation_id=fmt,
            entries=entries,
            sample_teams=sample_teams,
            health=health,
            generated_at=time.time(),
        )
        await self._cache.set(key, snap.model_dump(), ttl=self._s.meta_ttl)
        return snap

