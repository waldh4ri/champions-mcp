from __future__ import annotations

from pydantic import BaseModel, Field


class Stats(BaseModel):
    hp: int = 0
    attack: int = 0
    defense: int = 0
    special_attack: int = 0
    special_defense: int = 0
    speed: int = 0

    @property
    def total(self) -> int:
        return (
            self.hp
            + self.attack
            + self.defense
            + self.special_attack
            + self.special_defense
            + self.speed
        )


class Pokemon(BaseModel):
    slug: str
    name: str
    names_by_locale: dict[str, str] = Field(default_factory=dict)
    types: list[str] = Field(default_factory=list)
    abilities: list[str] = Field(default_factory=list)
    hidden_ability: str | None = None
    base_stats: Stats = Field(default_factory=Stats)
    is_legendary: bool = False
    is_mythical: bool = False
    is_baby: bool = False
    generation: str | None = None
    mega_forms: list[str] = Field(default_factory=list)


class Move(BaseModel):
    slug: str
    name: str
    type: str | None = None
    damage_class: str | None = None
    power: int | None = None
    accuracy: int | None = None
    pp: int | None = None
    priority: int = 0
    target: str | None = None
    effect: str | None = None


class Item(BaseModel):
    slug: str
    name: str
    category: str | None = None
    effect: str | None = None
    is_mega_stone: bool = False


class TeamMember(BaseModel):
    species: str
    ability: str | None = None
    item: str | None = None
    moves: list[str] = Field(default_factory=list)
    tera_type: str | None = None
    level: int = 50
    nickname: str | None = None
    nature: str | None = None
    # Champions Stat Points spread, e.g. {"speed": 32, "attack": 32, "hp": 2}.
    stat_points: dict[str, int] = Field(default_factory=dict)


class Team(BaseModel):
    members: list[TeamMember] = Field(default_factory=list)
    regulation_id: str | None = None


class Violation(BaseModel):
    member: str | None = None
    rule: str
    detail: str


class ValidationReport(BaseModel):
    legal: bool
    regulation_id: str
    roster_verified: bool
    violations: list[Violation] = Field(default_factory=list)
    warnings: list[Violation] = Field(default_factory=list)


class MetaEntry(BaseModel):
    species: str
    usage_percent: float | None = None
    common_items: list[str] = Field(default_factory=list)
    common_abilities: list[str] = Field(default_factory=list)
    common_moves: list[str] = Field(default_factory=list)
    common_tera: list[str] = Field(default_factory=list)
    common_spreads: list[str] = Field(default_factory=list)
    teammates: list[str] = Field(default_factory=list)
    checks_counters: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class UsageEntry(BaseModel):
    """A name + usage percentage pair from chaos stats."""
    name: str
    usage_percent: float


class ChaosSpread(BaseModel):
    """A nature+stat-spread entry from chaos stats.

    ``spread`` is the raw Smogon string (e.g. ``"Jolly:2/32/0/0/0/32"``).
    ``stat_points`` contains only the non-zero stats mapped to their SP value.
    In Champions, these are Stat Points (SP), not EVs.
    """
    spread: str
    nature: str
    stat_points: dict[str, int]
    usage_percent: float


class ChaosCounter(BaseModel):
    """A checks-and-counters entry from chaos stats."""
    species: str
    sample_size: int
    ko_rate: float
    switch_rate: float


class PokemonChaosData(BaseModel):
    """Full per-Pokémon data from Smogon monthly chaos stats.

    All usage values are percentages (0-100).  ``spreads`` are SP spreads
    (Stat Points, not EVs): each non-zero value is one SP, total budget ≤ 66.
    """
    species: str
    format: str
    month: str
    rating_cutoff: int
    total_battles: int
    usage_percent: float
    raw_count: int
    moves: list[UsageEntry]
    items: list[UsageEntry]
    abilities: list[UsageEntry]
    spreads: list[ChaosSpread]
    teammates: list[UsageEntry]
    checks_counters: list[ChaosCounter]


class SmogonSet(BaseModel):
    name: str
    description: str


class SmogonAnalysis(BaseModel):
    pokemon: str
    format: str
    overview: str
    sets: list[SmogonSet] = Field(default_factory=list)


class SourceHealth(BaseModel):
    source: str
    ok: bool
    detail: str = ""
    entries: int = 0


class MetaSnapshot(BaseModel):
    regulation_id: str
    entries: list[MetaEntry] = Field(default_factory=list)
    sample_teams: list[list[str]] = Field(default_factory=list)
    health: list[SourceHealth] = Field(default_factory=list)
    generated_at: float = 0.0


class TournamentSummary(BaseModel):
    id: str
    name: str
    game: str | None = None
    format: str | None = None
    date: str | None = None
    players: int | None = None
