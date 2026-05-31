from __future__ import annotations

from collections import Counter

from ..meta.aggregator import MetaAggregator
from ..models import Team
from ..names import NameIndex
from ..pokeapi import PokeAPIClient
from ..regulations import Regulation
from .legality import LegalityService


class TeamBuilderService:
    def __init__(
        self,
        pokeapi: PokeAPIClient,
        names: NameIndex,
        legality: LegalityService,
        meta: MetaAggregator,
    ) -> None:
        self._api = pokeapi
        self._names = names
        self._legality = legality
        self._meta = meta

    async def analyze(self, team: Team, regulation: Regulation, format_key: str = "vgc") -> dict:
        report = await self._legality.validate(team, regulation)
        snap = await self._meta.snapshot(format_key)
        usage = {e.species.strip().lower(): e for e in snap.entries}

        members_ctx = []
        team_types: Counter[str] = Counter()
        team_slugs: set[str] = set()
        for m in team.members:
            try:
                slug = await self._names.resolve(m.species)
                mon = await self._api.get_pokemon(slug)
            except Exception:  # noqa: BLE001
                members_ctx.append({"input": m.species, "resolved": None})
                continue
            team_slugs.add(slug)
            for t in mon.types:
                team_types[t] += 1
            me = usage.get(mon.name.lower()) or usage.get(slug.lower())
            members_ctx.append(
                {
                    "input": m.species,
                    "resolved": mon.name,
                    "types": mon.types,
                    "base_stat_total": mon.base_stats.total,
                    "meta_usage_percent": me.usage_percent if me else None,
                }
            )

        threats = [
            {"species": e.species, "usage_percent": e.usage_percent}
            for e in snap.entries
            if e.species.strip().lower() not in team_slugs
        ][:10]

        return {
            "regulation": regulation.id,
            "meta_format": format_key,
            "legality": report.model_dump(),
            "members": members_ctx,
            "team_type_spread": dict(team_types),
            "top_meta_threats_not_on_team": threats,
            "meta_health": [h.model_dump() for h in snap.health],
        }

    async def suggest_cores(
        self, regulation: Regulation, gimmick: bool = False, limit: int = 8
    ) -> dict:
        """Surface common cores from top-cut sample teams.

        With ``gimmick=True`` we bias toward pairs that include at least one
        lower-usage Pokémon — useful for off-meta theorycrafting rather than
        copying the most common shells.
        """
        snap = await self._meta.snapshot(regulation.id)
        usage = {e.species.strip().lower(): (e.usage_percent or 0.0)
                 for e in snap.entries}
        pair_counts: Counter[tuple[str, str]] = Counter()
        for team in snap.sample_teams:
            uniq = sorted(set(team))
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    pair_counts[(uniq[i], uniq[j])] += 1

        cores = []
        for (a, b), count in pair_counts.most_common(60):
            ua = usage.get(a.lower(), 0.0)
            ub = usage.get(b.lower(), 0.0)
            if gimmick and min(ua, ub) >= 15.0:
                continue  # skip pure-meta shells when hunting gimmicks
            cores.append(
                {
                    "core": [a, b],
                    "co_occurrences": count,
                    "usage_percent": [ua, ub],
                }
            )
            if len(cores) >= limit:
                break

        return {
            "regulation": regulation.id,
            "mode": "gimmick" if gimmick else "meta",
            "cores": cores,
            "sample_team_count": len(snap.sample_teams),
            "meta_health": [h.model_dump() for h in snap.health],
            "note": "Cores are co-occurrence patterns from top-cut teams — "
            "treat as inspiration, not prescriptions. Any strategy is valid as "
            "long as it can answer the real threat landscape. Validate with "
            "validate_team before finalising.",
        }
