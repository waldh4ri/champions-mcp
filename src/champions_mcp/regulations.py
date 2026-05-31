from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Settings


class MegaRule(BaseModel):
    allowed: bool = False
    max_per_team: int | None = None
    max_per_battle: int = 1
    eligible_species: list[str] | None = None


class Regulation(BaseModel):
    id: str
    name: str
    game: str = "Pokémon Champions"
    start_date: str | None = None
    end_date: str | None = None
    team_size: int = 6
    level_cap: int = 50
    item_clause: bool = True
    species_clause: bool = True
    ban_categories: list[str] = Field(default_factory=list)
    restricted_species: list[str] = Field(default_factory=list)
    banned_species: list[str] = Field(default_factory=list)
    allowed_species: list[str] | None = None
    mega: MegaRule = Field(default_factory=MegaRule)
    banned_items: list[str] = Field(default_factory=list)
    banned_moves: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    roster_verified: bool = False
    notes: list[str] = Field(default_factory=list)

    def is_active_on(self, day: date) -> bool:
        try:
            start = date.fromisoformat(self.start_date) if self.start_date else None
            end = date.fromisoformat(self.end_date) if self.end_date else None
        except ValueError:
            return False
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True


class RegulationRegistry:
    """Loads curated regulation JSON files from the data directory."""

    def __init__(self, settings: Settings) -> None:
        self._dir = settings.regulations_dir
        self._cache: dict[str, Regulation] | None = None

    def _load_all(self) -> dict[str, Regulation]:
        if self._cache is not None:
            return self._cache
        regs: dict[str, Regulation] = {}
        if self._dir.is_dir():
            for path in sorted(self._dir.glob("*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                reg = Regulation.model_validate(data)
                regs[reg.id.upper()] = reg
        self._cache = regs
        return regs

    def list_ids(self) -> list[str]:
        return sorted(self._load_all().keys())

    def get(self, reg_id: str) -> Regulation:
        regs = self._load_all()
        try:
            return regs[reg_id.upper()]
        except KeyError as exc:
            raise KeyError(
                f"Unknown regulation {reg_id!r}. Known: {sorted(regs)}"
            ) from exc

    def active(self, day: date | None = None) -> Regulation:
        regs = self._load_all()
        if not regs:
            raise KeyError("No regulation files found in data/regulations/")
        day = day or date.today()
        for reg in regs.values():
            if reg.is_active_on(day):
                return reg
        # Fallback: most recent by start_date.
        return sorted(
            regs.values(), key=lambda r: r.start_date or "", reverse=True
        )[0]
