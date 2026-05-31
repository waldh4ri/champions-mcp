from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator

from mcp.server.fastmcp import FastMCP

from .cache import Cache
from .champions_items import ItemCatalog, item_key
from .champions_movesets import ChampionsMovesets
from .champions_roster import ChampionsRoster
from .champions_stats import (
    ALL_STATS,
    MAX_PER_STAT_SP,
    MAX_TOTAL_SP,
    SPEED_MINUS_NATURES,
    SPEED_PLUS_NATURES,
    StatError,
    compute_stat,
    effective_speed,
    min_sp_to_outspeed,
    nature_effect,
    normalize_nature,
    validate_spread,
)
from .config import Settings
from .damage import DamageCalculator
from .limitless import LimitlessClient, LimitlessError
from .meta.aggregator import MetaAggregator
from .meta.chaos import SmogonChaosClient
from .meta.smogon import resolve_format
from .meta.smogon_analysis import SmogonAnalysisClient
from .models import Team
from .names import NameIndex, regional_slug
from .normalize import base_species
from .pokeapi import PokeAPIClient
from .regulations import RegulationRegistry
from .services.legality import LegalityService
from .services.teambuilder import TeamBuilderService


class App:
    """Lazily-constructed singleton holding all clients/services."""

    _instance: "App | None" = None

    def __init__(self) -> None:
        self.settings = Settings.load()
        self.cache = Cache(self.settings.cache_db)
        self.pokeapi = PokeAPIClient(self.settings, self.cache)
        self.names = NameIndex(self.pokeapi, self.cache)
        self.regs = RegulationRegistry(self.settings)
        self.item_catalog = ItemCatalog.load(self.settings)
        self.roster = ChampionsRoster.load(self.settings)
        self.movesets = ChampionsMovesets.load(self.settings)
        self.limitless = LimitlessClient(self.settings, self.cache)
        self.chaos_client = SmogonChaosClient(self.settings, self.cache)
        self.meta = MetaAggregator(self.settings, self.cache, self.limitless, self.chaos_client)
        self.smogon_analyses = SmogonAnalysisClient(self.settings, self.cache)
        self.legality = LegalityService(
            self.pokeapi, self.names, self.item_catalog, self.roster,
            self.movesets,
        )
        self.builder = TeamBuilderService(
            self.pokeapi, self.names, self.legality, self.meta
        )
        self.damage = DamageCalculator(
            self.settings, self.names, self.pokeapi
        )

    @classmethod
    def get(cls) -> "App":
        if cls._instance is None:
            cls._instance = App()
        return cls._instance


import os

mcp = FastMCP(
    "champions-mcp",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8000")),
)


def _resolve_regulation(reg_id: str):
    app = App.get()
    if reg_id in ("", "current", "active"):
        return app.regs.active()
    return app.regs.get(reg_id)


# ----------------------- Skill prompt -------------------------

_SKILL_MD = Path(__file__).parent / "SKILL.md"


@mcp.prompt()
def champions_team_building() -> str:
    """Champions team-building skill: full rules, workflow, and tool guide
    for building competitive VGC (doubles) or BSS (singles) teams in
    Pok\u00e9mon Champions. Load this once at the start of any team-building
    session.
    """
    return _SKILL_MD.read_text(encoding="utf-8")


# ----------------------- Game-rules baseline -------------------------

_FORMAT_RULES: dict[str, Any] = {
    "vgc": {
        "display_name": "[Champions] VGC 2026 Reg M-A",
        "game_type": "doubles",
        "description": "Team of 6, bring 4 (two Pokémon active at once)",
        "team_size": 6,
        "bring": 4,
        "level_cap": 50,
        "ivs": "31 in every stat (fixed — no IV investment or choice)",
        "item_clause": "No two Pokémon on the same team may hold the same item",
        "species_clause": "No two Pokémon of the same species on the same team",
    },
    "bss": {
        "display_name": "[Champions] BSS Reg M-A",
        "game_type": "singles",
        "description": "Team of 6, bring 3",
        "team_size": 6,
        "bring": 3,
        "level_cap": 50,
        "ivs": "31 in every stat (fixed — no IV investment or choice)",
        "item_clause": "No two Pokémon on the same team may hold the same item",
        "species_clause": "No two Pokémon of the same species on the same team",
    },
}

_STAT_POINTS_RULES: dict[str, Any] = {
    "total_budget": MAX_TOTAL_SP,
    "per_stat_maximum": MAX_PER_STAT_SP,
    "minimum_per_stat": 0,
    "stats": list(ALL_STATS),
    "effect": "1 SP = +1 to the pre-nature stat value at Level 50",
    "formula_non_hp": "floor( (2*base + 31)//2 + 5 + sp ) * nature_modifier  "
                      "(nature_modifier = 1.1 for boosted, 0.9 for lowered, 1.0 neutral)",
    "formula_hp": "(2*base + 31)//2 + 60 + sp  (nature never affects HP)",
    "speed_plus_natures": SPEED_PLUS_NATURES,
    "speed_minus_natures": SPEED_MINUS_NATURES,
    "note": "SP are NOT EVs — 1 SP = +1 stat, not +¼ like EVs. Budget is 66, not 510.",
}

_STAT_STAGE_MULTIPLIERS: dict[str, str] = {
    "+1": "×1.5", "+2": "×2", "+3": "×2.5", "+4": "×3", "+5": "×3.5", "+6": "×4",
    "-1": "×0.67", "-2": "×0.5", "-3": "×0.4", "-4": "×0.33", "-5": "×0.29", "-6": "×0.25",
}

_SPEED_MODIFIERS: dict[str, Any] = {
    "order": "raw stat → stat stage → Choice Scarf (×1.5) → Tailwind (×2) → paralysis (×0.5)",
    "flooring": "integer floor applied at each step",
    "choice_scarf": "×1.5 (after stage)",
    "tailwind": "×2 (after scarf)",
    "paralysis": "×0.5 (after tailwind)",
}

_MEGA_RULES: dict[str, Any] = {
    "max_active_per_battle": 1,
    "requirement": "Pokémon must hold its specific Mega Stone",
    "timing": "Mega Evolves during the turn it is declared; acts at its Mega stat/ability",
    "note": "max_per_team has no hard cap by default; check the active regulation.",
}

_FORMAT_AGNOSTIC_TOOLS = (
    "get_pokemon, search_pokemon, get_move, search_moves, get_item, "
    "search_items, get_type_matchups, get_pokemon_weaknesses, "
    "get_pokemon_moves, is_legal_move, is_legal_pokemon, "
    "list_legal_pokemon, get_champions_roster, pokemons_by_type, "
    "pokemons_by_ability, pokemons_by_move, validate_team, "
    "calc_stats, validate_ev_spread, compute_speed, speed_threshold, "
    "create_pokepaste, get_regulation, list_regulations"
)


def _build_shared_rules() -> dict[str, Any]:
    """Parts of the game rules that are identical across VGC and BSS."""
    app = App.get()
    reg = app.regs.active()
    catalog = app.item_catalog
    return {
        "game": "Pokémon Champions",
        "stat_points_system": _STAT_POINTS_RULES,
        "stat_stage_multipliers": _STAT_STAGE_MULTIPLIERS,
        "speed_modifiers": _SPEED_MODIFIERS,
        "mega_evolution": _MEGA_RULES,
        "active_regulation": {
            "id": reg.id,
            "name": reg.name,
            "team_size": reg.team_size,
            "start_date": reg.start_date,
            "end_date": reg.end_date,
            "ban_categories": reg.ban_categories,
            "item_clause": reg.item_clause,
            "species_clause": reg.species_clause,
            "mega": reg.mega.model_dump(),
            "banned_items": reg.banned_items,
            "banned_moves": reg.banned_moves,
            "restricted_species_count": len(reg.restricted_species),
            "restricted_species": reg.restricted_species,
            "notes": reg.notes,
        },
        "item_catalog": {
            "total_items": len(catalog.keys),
            "verified": catalog.verified,
            "source_url": catalog.source_url,
            "important_absences": [
                "Life Orb", "Assault Vest", "Choice Specs", "Choice Band",
                "Rocky Helmet", "Heavy-Duty Boots", "Air Balloon",
                "Eviolite", "Flame Orb", "Toxic Orb",
            ],
            "notable_items_present": [
                "Choice Scarf", "Focus Sash", "Leftovers", "Shell Bell",
                "White Herb", "Mental Herb", "Lum Berry", "Sitrus Berry",
                "Scope Lens", "Focus Band", "King's Rock", "Bright Powder",
                "Light Ball",
            ],
            "items": sorted(catalog.keys),
            "catalog_notes": catalog.notes,
        },
    }


def _build_game_rules_vgc() -> dict[str, Any]:
    shared = _build_shared_rules()
    return {
        "game": shared["game"],
        "session_format": "vgc",
        "grounding_note": (
            "VGC session baseline. Pass format='vgc' and game_type='doubles' "
            "to every format-aware tool in this session."
        ),
        "format": _FORMAT_RULES["vgc"],
        "session_tool_guide": {
            "meta_format_arg": "Always pass format='vgc' to: "
                "get_usage_stats, get_smogon_analysis, analyze_team, get_pokemon_sets.",
            "calc_damage_game_type": "Always pass game_type='doubles'. "
                "Spread moves deal 75% damage to non-primary targets in doubles.",
            "team_building_workflow": [
                "1. ROSTER: list_legal_pokemon (or get_champions_roster) to pick legal Pok\u00e9mon.",
                "2. RESEARCH each Pok\u00e9mon: (a) get_smogon_analysis [written overview, skip if available=false]; "
                    "(b) get_pokemon_sets [real ladder data: moves/items/spreads/%]; "
                    "(c) get_pokemon_moves [full legal moveset — ALWAYS do this before assigning moves].",
                "3. SPREAD: calc_stats to verify final Lv50 stats; "
                    "speed_threshold to find min Speed SP vs targets.",
                "4. DAMAGE CHECKS: calc_damage to verify KO/damage ranges (game_type='doubles').",
                "5. LEGALITY: validate_team when the team is complete.",
                "6. META REVIEW: analyze_team for coverage/threat analysis.",
                "7. EXPORT: create_pokepaste for the shareable paste link.",
            ],
            "vgc_exclusive_tools": {
                "suggest_cores": "Co-occurrence patterns from Limitless top-cut VGC teams. Use early for inspiration.",
                "get_top_teams": "Top-cut team lists from Limitless VGC tournaments.",
                "search_tournaments": "Search Limitless VGC tournament history.",
                "get_tournament_standings": "Full standings for a specific tournament.",
            },
            "format_agnostic_tools": _FORMAT_AGNOSTIC_TOOLS,
        },
        "stat_points_system": shared["stat_points_system"],
        "stat_stage_multipliers": shared["stat_stage_multipliers"],
        "speed_modifiers": shared["speed_modifiers"],
        "mega_evolution": shared["mega_evolution"],
        "active_regulation": shared["active_regulation"],
        "item_catalog": shared["item_catalog"],
        "key_constraints_summary": [
            "Doubles: two Pokémon active per side simultaneously.",
            "Team exactly 6 Pokémon; bring 4 to battle.",
            "All Pokémon are Level 50 with 31 IVs in every stat.",
            "SP budget: 66 total, max 32 per stat, integers only.",
            "Item clause: each item may appear at most once across the 6-member team.",
            "Species clause: no two Pokémon of the same species.",
            "Only items in the Champions item catalog are legal (≠ PokeAPI universe).",
            "Max 1 active Mega Evolution per battle; requires the correct Mega Stone.",
            "Legendaries and Mythicals are banned in the current regulation (M-A).",
            "Spread moves (e.g. Earthquake, Discharge) hit both opponents but deal 75% damage.",
            "Tailwind doubles team speed for 4 turns — a key doubles mechanic.",
        ],
    }


def _build_game_rules_bss() -> dict[str, Any]:
    shared = _build_shared_rules()
    return {
        "game": shared["game"],
        "session_format": "bss",
        "grounding_note": (
            "BSS session baseline. Pass format='bss' and game_type='singles' "
            "to every format-aware tool in this session."
        ),
        "format": _FORMAT_RULES["bss"],
        "session_tool_guide": {
            "meta_format_arg": "Always pass format='bss' to: "
                "get_usage_stats, get_smogon_analysis, analyze_team, get_pokemon_sets.",
            "calc_damage_game_type": "Always pass game_type='singles'. "
                "No spread damage reduction; no partner interactions.",
            "team_building_workflow": [
                "1. ROSTER: list_legal_pokemon (or get_champions_roster) to pick legal Pok\u00e9mon.",
                "2. RESEARCH each Pok\u00e9mon: (a) get_smogon_analysis [written overview, skip if available=false]; "
                    "(b) get_pokemon_sets [real ladder data: moves/items/spreads/%]; "
                    "(c) get_pokemon_moves [full legal moveset — ALWAYS do this before assigning moves].",
                "3. SPREAD: calc_stats to verify final Lv50 stats; "
                    "speed_threshold to find min Speed SP vs targets.",
                "4. DAMAGE CHECKS: calc_damage to verify KO/damage ranges (game_type='singles').",
                "5. LEGALITY: validate_team when the team is complete.",
                "6. META REVIEW: analyze_team for coverage/threat analysis.",
                "7. EXPORT: create_pokepaste for the shareable paste link.",
            ],
            "format_agnostic_tools": _FORMAT_AGNOSTIC_TOOLS,
            "note": "Limitless tournament tools (suggest_cores, get_top_teams, "
                "search_tournaments, get_tournament_standings) cover VGC only "
                "and are not useful for BSS.",
        },
        "stat_points_system": shared["stat_points_system"],
        "stat_stage_multipliers": shared["stat_stage_multipliers"],
        "speed_modifiers": shared["speed_modifiers"],
        "mega_evolution": shared["mega_evolution"],
        "active_regulation": shared["active_regulation"],
        "item_catalog": shared["item_catalog"],
        "key_constraints_summary": [
            "Singles: one Pokémon active per side.",
            "Team exactly 6 Pokémon; bring 3 to battle.",
            "All Pokémon are Level 50 with 31 IVs in every stat.",
            "SP budget: 66 total, max 32 per stat, integers only.",
            "Item clause: each item may appear at most once across the 6-member team.",
            "Species clause: no two Pokémon of the same species.",
            "Only items in the Champions item catalog are legal (≠ PokeAPI universe).",
            "Max 1 active Mega Evolution per battle; requires the correct Mega Stone.",
            "Legendaries and Mythicals are banned in the current regulation (M-A).",
        ],
    }


@mcp.tool()
async def get_game_rules_vgc() -> dict[str, Any]:
    """**Call this first for any VGC (doubles) Champions session.**

    Returns the complete authoritative baseline for [Champions] VGC 2026 Reg M-A:
    doubles format rules (bring 4), Stat Points system, item catalog, Mega rules,
    active regulation, and a ``session_tool_guide`` listing exactly which tools to
    call and what arguments to pass throughout a VGC session.

    Do NOT call this for BSS (singles) work — use ``get_game_rules_bss`` instead.
    """
    return _build_game_rules_vgc()


@mcp.tool()
async def get_game_rules_bss() -> dict[str, Any]:
    """**Call this first for any BSS (singles) Champions session.**

    Returns the complete authoritative baseline for [Champions] BSS Reg M-A:
    singles format rules (bring 3), Stat Points system, item catalog, Mega rules,
    active regulation, and a ``session_tool_guide`` listing exactly which tools to
    call and what arguments to pass throughout a BSS session.

    Do NOT call this for VGC (doubles) work — use ``get_game_rules_vgc`` instead.
    """
    return _build_game_rules_bss()


# --------------------------- Pokédex tools ---------------------------


@mcp.tool()
async def search_pokemon(query: str, limit: int = 25) -> list[str]:
    """Search Pokémon by (partial) slug/name. Returns matching PokeAPI slugs.

    Searches the full PokeAPI universe (not Champions-filtered). When building
    a Champions team, use ``list_legal_pokemon`` (with a ``query`` argument)
    instead — it restricts results to regulation-legal Pokémon and is faster.
    Use ``search_pokemon`` only when you need to resolve a name to a PokeAPI
    slug before calling other PokeAPI tools.
    """
    return await App.get().pokeapi.search_pokemon(query, limit=limit)


@mcp.tool()
async def get_pokemon(name: str) -> dict[str, Any]:
    """Get a Pokémon's types, abilities, base stats and Mega forms.

    Accepts English or localized names (e.g. French "Tarenbulle" -> Araquanid).

    Note: returns *base stats*, not final Lv 50 stats. To compute final stats
    for a Champions spread, call ``calc_stats`` next. For the Pokémon's legal
    Champions moveset (different from PokeAPI learnsets!), call
    ``get_pokemon_moves``.
    """
    app = App.get()
    slug = await app.names.resolve(name)
    return (await app.pokeapi.get_pokemon(slug)).model_dump()


@mcp.tool()
async def get_move(name: str) -> dict[str, Any]:
    """Get a move's type, category, power, accuracy, priority and effect.

    Returns PokeAPI data. To check whether a specific Pokémon can use a move
    in Champions, call ``is_legal_move`` instead (Champions has rebalanced
    movepools that differ from PokeAPI learnsets).
    """
    return (await App.get().pokeapi.get_move(name)).model_dump()


@mcp.tool()
async def search_moves(
    query: str | None = None,
    type_filter: str | None = None,
    category: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Search Champions-known moves by name, type and/or damage category.

    `query`: case-insensitive substring of the move name.
    `type_filter`: Pokémon type (e.g. "fire", "fairy", "dragon").
    `category`: "physical", "special", or "status".

    Only moves present in at least one Champions Pokémon's moveset are
    searched — so results are Champions-accurate. When type_filter or
    category is given, PokeAPI details are fetched (cached; first call may
    be slower). Requires at least one filter.
    """
    app = App.get()
    if not query and not type_filter and not category:
        return {"error": "Provide at least one of: query, type_filter, category."}
    if not (app.movesets.loaded and app.movesets.verified):
        return {"error": "Champions moveset data unavailable; run "
                "champions-mcp-prewarm."}

    all_moves: list[str] = sorted(
        {m for moves in app.movesets._raw.values() for m in moves}
    )
    candidates = (
        [m for m in all_moves if query.strip().lower() in m.lower()]
        if query
        else all_moves
    )

    if not type_filter and not category:
        page = candidates[:limit]
        return {
            "query": query,
            "total_matching": len(candidates),
            "returned": len(page),
            "moves": [{"name": m} for m in page],
            "note": "Name-only search; use get_move for full details on each.",
        }

    # Fetch PokeAPI details for candidates (cap fetch set to avoid overload)
    fetch_set = candidates[:120]

    async def _fetch(name: str) -> dict | None:
        try:
            return (await app.pokeapi.get_move(name)).model_dump()
        except Exception:  # noqa: BLE001
            return None

    raw = await asyncio.gather(*(_fetch(m) for m in fetch_set))
    detailed: list[dict] = [r for r in raw if r is not None]

    if type_filter:
        t = type_filter.strip().lower()
        detailed = [m for m in detailed if m.get("type") == t]
    if category:
        c = category.strip().lower()
        detailed = [m for m in detailed if m.get("damage_class") == c]

    detailed.sort(key=lambda m: m.get("name", ""))
    total = len(detailed)
    return {
        "query": query,
        "type_filter": type_filter,
        "category_filter": category,
        "total_matching": total,
        "returned": min(limit, total),
        "moves": detailed[:limit],
    }


@mcp.tool()
async def get_item(name: str) -> dict[str, Any]:
    """Get an item's category, effect, and whether it is a Mega Stone."""
    return (await App.get().pokeapi.get_item(name)).model_dump()


@mcp.tool()
async def search_items(query: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Search the Champions item catalog by name.

    Returns Champions-verified display names matching the query substring
    (case-insensitive). Omit `query` to list the full catalog. Use `get_item`
    for full PokeAPI details (category, effect) on a specific item.
    """
    catalog = App.get().item_catalog
    if not catalog.names:
        return {"error": "Item catalog not loaded; run champions-mcp-prewarm."}
    matches = (
        [n for n in catalog.names if query.strip().lower() in n.lower()]
        if query
        else list(catalog.names)
    )
    total = len(matches)
    return {
        "query": query,
        "catalog_verified": catalog.verified,
        "total_matching": total,
        "returned": min(limit, total),
        "items": matches[:limit],
        "note": "Champions-only catalog; items absent here do not exist in Champions.",
    }


@mcp.tool()
async def get_type_matchups(type_name: str) -> dict[str, list[str]]:
    """Get offensive/defensive type effectiveness for a single type."""
    return await App.get().pokeapi.type_matchups(type_name)


@mcp.tool()
async def get_pokemon_weaknesses(
    name: str,
    type2: str | None = None,
) -> dict[str, Any]:
    """Defensive type chart for a Pokémon or a type combination.

    Pass a Pokémon name to auto-resolve its types, or pass a single type as
    `name` with an optional second type as `type2` to query a combination
    directly. Returns all 18 attacking types grouped by damage multiplier
    (4×, 2×, 1×, ½×, ¼×, 0×). For dual-type Pokémon the multipliers
    from both types are multiplied together.
    """
    _ALL_TYPES = (
        "normal", "fire", "water", "electric", "grass", "ice",
        "fighting", "poison", "ground", "flying", "psychic", "bug",
        "rock", "ghost", "dragon", "dark", "steel", "fairy",
    )
    app = App.get()

    # Resolve defending types
    if type2 is not None:
        # Explicit type pair
        def_types = [name.strip().lower(), type2.strip().lower()]
        display = "/".join(def_types)
        resolved = display
    elif name.strip().lower() in _ALL_TYPES:
        # Single type passed as name
        def_types = [name.strip().lower()]
        display = name
        resolved = name
    else:
        # Pokémon name
        try:
            slug = await app.names.resolve(name)
            mon = await app.pokeapi.get_pokemon(slug)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Could not resolve '{name}': {exc}"}
        def_types = mon.types
        display = mon.name
        resolved = f"{mon.name} ({'/'.join(mon.types)})"

    # Fetch type relations for each defending type concurrently
    try:
        relations = await asyncio.gather(
            *(app.pokeapi.type_matchups(t) for t in def_types)
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Type data fetch failed: {exc}"}

    # Compute combined multipliers
    mults: dict[str, float] = {t: 1.0 for t in _ALL_TYPES}
    for rel in relations:
        for atk in rel.get("double_damage_from", []):
            if atk in mults:
                mults[atk] *= 2.0
        for atk in rel.get("half_damage_from", []):
            if atk in mults:
                mults[atk] *= 0.5
        for atk in rel.get("no_damage_from", []):
            if atk in mults:
                mults[atk] = 0.0

    groups: dict[str, list[str]] = {
        "4x": [], "2x": [], "1x": [], "0.5x": [], "0.25x": [], "0x": []
    }
    label_map = {4.0: "4x", 2.0: "2x", 1.0: "1x", 0.5: "0.5x", 0.25: "0.25x", 0.0: "0x"}
    for atk_type, mult in sorted(mults.items()):
        label = label_map.get(mult)
        if label:
            groups[label].append(atk_type)

    return {
        "input": name if type2 is None else f"{name} / {type2}",
        "resolved": resolved,
        "defending_types": def_types,
        "weaknesses": groups,
    }


# --------------------------- Regulation tools ------------------------


@mcp.tool()
async def list_regulations() -> list[str]:
    """List all curated Pokémon Champions regulation set IDs."""
    return App.get().regs.list_ids()


@mcp.tool()
async def get_regulation(regulation_id: str = "current") -> dict[str, Any]:
    """Get a regulation set's rules (use 'current' for the active one)."""
    return _resolve_regulation(regulation_id).model_dump()


@mcp.tool()
async def validate_team(
    team: list[dict[str, Any]], regulation_id: str = "current"
) -> dict[str, Any]:
    """Final legality validation for a complete team.

    Call this LAST after choosing all Pokémon, moves, items and spreads.
    For quick checks during building, prefer ``is_legal_pokemon`` (single
    Pokémon) or ``is_legal_move`` (single move).

    Each team member is an object: {"species": str, "item"?: str,
    "ability"?: str, "moves"?: [str], "tera_type"?: str, "level"?: int,
    "nickname"?: str}. species/item/moves accept localized names. Returns a
    legality report (violations + warnings). Note: when a regulation's roster
    is unverified, category/ban checks are applied but the exact legal roster
    may differ — a warning flags this.
    """
    reg = _resolve_regulation(regulation_id)
    try:
        parsed = Team(members=team)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Invalid team payload: {exc}"}
    return (await App.get().legality.validate(parsed, reg)).model_dump()


# ----------------------- Roster (build-time) -------------------------


def _reg_excludes(reg) -> tuple[set[str], set[str]]:
    """Normalized {restricted+banned species keys} for a regulation."""
    restricted = {item_key(s) for s in reg.restricted_species}
    banned = {item_key(s) for s in reg.banned_species}
    return restricted, banned


def _iter_legal_entries(app: "App", reg) -> Iterator[dict]:
    """Yield pickable (non-Mega) roster entries passing the regulation's category/ban filters."""
    restricted, banned = _reg_excludes(reg)
    ban_legend = "legendary" in reg.ban_categories
    ban_myth = "mythical" in reg.ban_categories
    ban_restr = "restricted" in reg.ban_categories
    for e in app.roster.pickable:
        if ban_legend and e.get("is_legendary"):
            continue
        if ban_myth and e.get("is_mythical"):
            continue
        keyset = set(e.get("keys", [])) | {e.get("base", "")}
        if ban_restr and keyset & restricted:
            continue
        if keyset & banned:
            continue
        yield e


@mcp.tool()
async def get_champions_roster(
    query: str | None = None,
    include_megas: bool = False,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """The global Pokémon Champions roster (which Pokémon exist in the game).

    Source of truth for *existence* — use when exploring what Pokémon are
    in Champions or searching by name/type. For team building, prefer
    ``list_legal_pokemon`` which already filters out Legendaries/Mythicals
    and Restricted species for the active regulation.

    After finding a Pokémon: call ``get_pokemon_moves`` for its legal
    Champions moveset and ``get_pokemon_sets`` for how it's played on ladder.
    """
    r = App.get().roster
    rows = [e for e in r.entries if include_megas or not e.get("is_mega")]
    if query:
        q = query.strip().lower()
        rows = [e for e in rows if q in e["name"].lower()]
    total = len(rows)
    page = rows[offset : offset + max(1, limit)]
    return {
        "verified": r.verified,
        "source_url": r.source_url,
        "total_matching": total,
        "returned": len(page),
        "offset": offset,
        "entries": [
            {
                "name": e["name"],
                "slug": e.get("slug", ""),
                "types": e.get("types", []),
                "is_mega": bool(e.get("is_mega")),
                "is_legendary": bool(e.get("is_legendary")),
            }
            for e in page
        ],
        "note": "Some species appear twice with the SAME name — a base and a "
        "regional form (e.g. Ninetales Fire vs Alolan Ice/Fairy). They are "
        "distinguished only by 'types'. Mega rows carry the Mega typing.",
    }


@mcp.tool()
async def list_legal_pokemon(
    regulation_id: str = "current",
    query: str | None = None,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """Pokémon that are legal to PICK in a regulation (build-time allowlist).

    Primary filter during team building — already excludes Legendaries,
    Mythicals, Restricted species and banned Pokémon for the regulation.
    Supports ``query`` for substring search. No network; fast to call.

    Typical next steps per Pokémon:
    - ``get_pokemon_moves`` → legal Champions moveset (required before assigning moves)
    - ``get_pokemon_sets`` → how it's played on ladder (moves/items/spreads with %)
    - ``get_smogon_analysis`` → written strategy context (if an analysis exists)
    - ``is_legal_pokemon`` → quick single-Pokémon legality check with explanation

    Still run ``validate_team`` at the end for item/move/clause/spread checks.
    """
    app = App.get()
    reg = _resolve_regulation(regulation_id)
    picked: list[tuple[str, list[str]]] = [
        (e["name"], e.get("types", [])) for e in _iter_legal_entries(app, reg)
    ]
    # Disambiguate same-named regional pairs by appending the typing.
    name_counts: dict[str, int] = {}
    for n, _ in picked:
        name_counts[n] = name_counts.get(n, 0) + 1
    legal = [
        f"{n} ({'/'.join(ts)})" if name_counts[n] > 1 and ts else n
        for n, ts in picked
    ]
    if query:
        q = query.strip().lower()
        legal = [n for n in legal if q in n.lower()]
    legal = sorted(set(legal))
    total = len(legal)
    return {
        "regulation": reg.id,
        "roster_verified": app.roster.verified,
        "total_legal": total,
        "returned": min(max(1, limit), max(0, total - offset)),
        "offset": offset,
        "pokemon": legal[offset : offset + max(1, limit)],
        "note": "Build-time allowlist. Still run validate_team for "
        "item/move/clause/spread checks.",
    }


@mcp.tool()
async def is_legal_pokemon(
    name: str, regulation_id: str = "current"
) -> dict[str, Any]:
    """Quick single check: is this Pokémon legal to pick in the regulation?

    Resolves localized names, checks the global roster and the regulation's
    category/restricted/ban rules, and explains the result. Use during
    building to verify individual Pokémon before committing.

    For a batch overview of all legal Pokémon, use ``list_legal_pokemon``.
    For full team validation (items, moves, clauses), use ``validate_team``.
    """
    app = App.get()
    reg = _resolve_regulation(regulation_id)
    try:
        slug = await app.names.resolve(name)
        mon = await app.pokeapi.get_pokemon(slug)
    except Exception as exc:  # noqa: BLE001
        return {"input": name, "resolved": None,
                "legal": False, "reasons": [f"Unresolved species: {exc}"]}

    base = base_species(slug)
    in_roster = app.roster.contains(slug, base, mon.name, mon.types)
    is_regional = bool(regional_slug(name)) or any(
        f"-{r}" in slug for r in ("alola", "galar", "hisui", "paldea")
    )
    restricted, banned = _reg_excludes(reg)
    keys = {item_key(slug), item_key(base), item_key(mon.name)}

    reasons: list[str] = []
    if app.roster.loaded and app.roster.verified and not in_roster:
        reasons.append(
            f"This {'regional form' if is_regional else 'Pokémon'} is not in "
            "the Champions roster."
        )
    if "legendary" in reg.ban_categories and mon.is_legendary:
        reasons.append(f"{mon.name} is a Legendary (banned in {reg.id}).")
    if "mythical" in reg.ban_categories and mon.is_mythical:
        reasons.append(f"{mon.name} is a Mythical (banned in {reg.id}).")
    if "restricted" in reg.ban_categories and keys & restricted:
        reasons.append(f"{mon.name} is Restricted in {reg.id}.")
    if keys & banned:
        reasons.append(f"{mon.name} is banned in {reg.id}.")

    roster_types = app.roster.types_for(slug, base, mon.name, mon.types)
    return {
        "input": name,
        "resolved": mon.name,
        "slug": slug,
        "types": roster_types or mon.types,
        "is_regional_form": is_regional,
        "in_global_roster": in_roster,
        "regulation": reg.id,
        "legal": not reasons,
        "reasons": reasons or ["Legal to pick."],
        "form_note": "Several species have multiple Champions forms that "
        "share a name and are distinguished only by typing (e.g. Ninetales: "
        "Fire vs Alolan Ice/Fairy). 'types' is this form's Champions typing; "
        "request a regional form explicitly (e.g. 'Alolan Ninetales').",
    }


@mcp.tool()
async def pokemons_by_type(
    type1: str,
    type2: str | None = None,
    regulation_id: str = "current",
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """Legal Champions Pokémon of a given type (build-time, type-accurate).

    Uses the Serebii roster's per-form Type column so typing reflects the
    actual Champions entry (not inferred). Applies regulation category/ban
    filters. Mega rows are excluded.
    """
    app = App.get()
    reg = _resolve_regulation(regulation_id)
    t1 = type1.strip().lower()
    t2 = type2.strip().lower() if type2 else None
    legal_ids = {id(e) for e in _iter_legal_entries(app, reg)}
    result = [
        {"name": e["name"], "types": e.get("types", [])}
        for e in app.roster.by_type(t1, t2)
        if id(e) in legal_ids
    ]
    result.sort(key=lambda x: x["name"])
    total = len(result)
    return {
        "regulation": reg.id,
        "roster_verified": app.roster.verified,
        "query": [t for t in (type1, type2) if t],
        "match_rule": "both types" if type2 else "has type",
        "total": total,
        "offset": offset,
        "pokemon": result[offset : offset + max(1, limit)],
        "note": "Type-filtered from the verified Champions roster; still run "
        "validate_team for full legality.",
    }


@mcp.tool()
async def pokemons_by_ability(
    ability: str,
    include_hidden: bool = True,
    regulation_id: str = "current",
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """Legal Champions Pokémon that have a given ability (or hidden ability).

    Checks regular abilities and, unless ``include_hidden=False``, hidden
    abilities via PokeAPI. Applies the same regulation category/Restricted/ban
    filters as list_legal_pokemon. Results are cached; the first call may be
    slower if PokeAPI data has not been pre-warmed.
    """
    app = App.get()
    reg = _resolve_regulation(regulation_id)

    import re as _re
    ability_slug = _re.sub(r"[^a-z0-9]+", "-", ability.strip().lower()).strip("-")

    candidates = list(_iter_legal_entries(app, reg))

    def _ability_slugs(mon) -> tuple[list[str], str | None]:
        reg = [
            _re.sub(r"[^a-z0-9]+", "-", a.strip().lower()).strip("-")
            for a in mon.abilities
        ]
        hid = (
            _re.sub(r"[^a-z0-9]+", "-", mon.hidden_ability.strip().lower()).strip("-")
            if mon.hidden_ability
            else None
        )
        return reg, hid

    async def _check(entry: dict) -> dict | None:
        slug = entry.get("slug") or entry["base"]
        try:
            mon = await app.pokeapi.get_pokemon(slug)
        except Exception:  # noqa: BLE001
            return None

        reg_abilities, hidden_slug = _ability_slugs(mon)
        has_regular = ability_slug in reg_abilities
        has_hidden = include_hidden and hidden_slug == ability_slug

        if has_regular or has_hidden:
            return {
                "name": entry["name"],
                "types": entry.get("types") or mon.types,
                "ability_is_hidden": not has_regular and has_hidden,
                "via_mega": None,
            }

        # Check Mega-form abilities (Mega ability activates in-battle;
        # the base form is still the pickable species).
        for mega_slug in mon.mega_forms:
            try:
                mega = await app.pokeapi.get_pokemon(mega_slug)
            except Exception:  # noqa: BLE001
                continue
            mega_reg, mega_hid = _ability_slugs(mega)
            mega_has = ability_slug in mega_reg or (
                include_hidden and mega_hid == ability_slug
            )
            if mega_has:
                return {
                    "name": entry["name"],
                    "types": entry.get("types") or mon.types,
                    "ability_is_hidden": False,
                    "via_mega": mega_slug,
                }
        return None

    raw = await asyncio.gather(*(_check(e) for e in candidates))
    results: list[dict] = sorted(
        (r for r in raw if r is not None), key=lambda x: x["name"]
    )
    total = len(results)
    return {
        "regulation": reg.id,
        "roster_verified": app.roster.verified,
        "ability": ability,
        "ability_slug": ability_slug,
        "include_hidden": include_hidden,
        "total": total,
        "offset": offset,
        "returned": min(max(1, limit), max(0, total - offset)),
        "pokemon": results[offset : offset + max(1, limit)],
        "note": "Ability data from PokeAPI (Champions may have rebalanced abilities). "
        "Mega-form abilities are checked too; 'via_mega' names the Mega slug when the "
        "match is via a Mega. Still run validate_team for full legality.",
    }


# ----------------------- Movesets (build-time) -----------------------


async def _species_move_keys(name: str):
    """Resolve a (localized) name -> (display, candidate keys for movesets)."""
    app = App.get()
    slug = await app.names.resolve(name)
    mon = await app.pokeapi.get_pokemon(slug)
    base = base_species(slug)
    return mon.name, {item_key(slug), item_key(base), item_key(mon.name)}


@mcp.tool()
async def get_pokemon_moves(name: str) -> dict[str, Any]:
    """List the moves a Pokémon can legally use in Pokémon Champions.

    **Call this before assigning moves to any team slot.** Champions has
    rebalanced movepools (moves added/removed vs Scarlet/Violet); PokeAPI has
    NO Champions learnsets and will suggest illegal moves.

    To find all Pokémon that can learn a specific move, use ``pokemons_by_move``.
    To check a single move quickly, use ``is_legal_move``.
    """
    app = App.get()
    if not (app.movesets.loaded and app.movesets.verified):
        return {"error": "Champions moveset data unavailable; run "
                "champions-mcp-prewarm."}
    try:
        display, cand = await _species_move_keys(name)
    except Exception as exc:  # noqa: BLE001
        return {"input": name, "error": f"Unresolved species: {exc}"}
    moves = app.movesets.legal_moves(cand)
    if moves is None:
        return {"input": name, "resolved": display,
                "error": "No Champions moveset on file for this species."}
    return {
        "input": name,
        "resolved": display,
        "move_count": len(moves),
        "moves": sorted(moves),
        "source": app.movesets.source_url,
    }


@mcp.tool()
async def is_legal_move(name: str, move: str) -> dict[str, Any]:
    """Quick check: can `name` use `move` in Pokémon Champions? (+reason)."""
    app = App.get()
    if not (app.movesets.loaded and app.movesets.verified):
        return {"error": "Champions moveset data unavailable; run "
                "champions-mcp-prewarm."}
    try:
        display, cand = await _species_move_keys(name)
    except Exception as exc:  # noqa: BLE001
        return {"input": name, "legal": False,
                "reason": f"Unresolved species: {exc}"}
    known, legal = app.movesets.is_legal(cand, move)
    if not known:
        return {"species": display, "move": move, "legal": None,
                "reason": "No Champions moveset on file for this species."}
    return {
        "species": display,
        "move": move,
        "legal": legal,
        "reason": (
            f"{display} can use {move} in Champions." if legal
            else f"{display} cannot learn {move} in Pokémon Champions "
            "(rebalanced movepool)."
        ),
    }


@mcp.tool()
async def pokemons_by_move(
    move: str,
    regulation_id: str = "current",
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """Legal Champions Pokémon that can learn a given move.

    Uses the Champions-specific movepools from Serebii (not PokeAPI, which has
    no Champions learnsets). Applies the same regulation category/Restricted/ban
    filters as list_legal_pokemon. Pokémon whose moveset is not on file are
    omitted and counted separately. No network calls; fast even on cold start.
    """
    app = App.get()
    if not (app.movesets.loaded and app.movesets.verified):
        return {"error": "Champions moveset data unavailable; run "
                "champions-mcp-prewarm."}

    reg = _resolve_regulation(regulation_id)

    from .champions_movesets import move_key
    mk = move_key(move)

    results: list[dict] = []
    no_data: int = 0

    for e in _iter_legal_entries(app, reg):
        keyset = set(e.get("keys", [])) | {e.get("base", "")}
        moves = app.movesets.legal_moves(keyset)
        if moves is None:
            no_data += 1
            continue
        if mk in moves:
            results.append({"name": e["name"], "types": e.get("types", [])})

    results.sort(key=lambda x: x["name"])
    total = len(results)
    return {
        "regulation": reg.id,
        "movesets_verified": app.movesets.verified,
        "move": move,
        "move_key": mk,
        "total": total,
        "offset": offset,
        "returned": min(max(1, limit), max(0, total - offset)),
        "pokemon": results[offset : offset + max(1, limit)],
        "species_without_moveset_data": no_data,
        "note": "Champions-native learnsets (Serebii). Pokémon without moveset "
        "data on file are excluded from results.",
    }


# --------------------------- Meta tools ------------------------------

_FORMAT_CHOICES = '"vgc" ([Champions] VGC 2026 Reg M-A — doubles) or "bss" ([Champions] BSS Reg M-A — singles)'


@mcp.tool()
async def get_usage_stats(
    format: str = "vgc", top: int = 30, refresh: bool = False
) -> dict[str, Any]:
    """Smogon ladder usage rankings for a Champions format (format-wide overview).

    Returns the top-N most-used Pokémon by usage % from the Smogon monthly
    chaos JSON (rating cutoff 1760). Use this to understand the meta tier
    landscape and which threats are most common.

    **For per-Pokémon data (moves, items, spreads with %):** call
    ``get_pokemon_sets`` instead — it returns the full competitive breakdown
    for a single Pokémon.

    `format`: ``"vgc"`` (doubles, default) or ``"bss"`` (singles). Also
    accepts common aliases: ``"doubles"``, ``"singles"``, ``"M-A"``.
    **Always pass the format that matches the current session.**
    """
    try:
        fmt = resolve_format(format)
    except ValueError as exc:
        return {"error": str(exc), "valid_formats": _FORMAT_CHOICES}
    snap = await App.get().meta.snapshot(fmt, refresh=refresh)
    return {
        "format": fmt,
        "smogon_format": snap.regulation_id,
        "entries": [e.model_dump() for e in snap.entries[:top]],
        "health": [h.model_dump() for h in snap.health],
        "generated_at": snap.generated_at,
    }


@mcp.tool()
async def get_pokemon_sets(
    pokemon: str, format: str = "vgc", rating: int = 1760
) -> dict[str, Any]:
    """Full competitive breakdown for a single Pokémon from Smogon ladder stats.

    **Primary source for how a Pokémon is actually played** — returns the
    most common moves, items, abilities, SP spreads, teammates, and
    checks/counters with real usage percentages from rated ladder games.

    Recommended order for researching a Pokémon:
    1. ``get_smogon_analysis`` → written strategy overview + named sets
       (returns ``available: false`` if no analysis yet — most Pokémon won't
       have one yet since the metagame is new; that is normal)
    2. ``get_pokemon_sets`` (this tool) → actual ladder usage data: which
       moves, items and spreads top players use
    3. ``get_pokemon_moves`` → full legal moveset if you need options not
       in the top-usage list

    After picking a spread: call ``calc_stats`` to verify final Lv 50 stats.

    `pokemon`: English display name (e.g. ``"Feraligatr-Mega"``, ``"Incineroar"``).
    Partial names work (e.g. ``"Feraligatr"`` matches ``"Feraligatr-Mega"``).

    `format`: ``"vgc"`` (doubles) or ``"bss"`` (singles). Also accepts aliases.

    **SP Spreads**: values like ``"Jolly:2/32/0/0/0/32"`` are Stat Points, not
    EVs. Max 32 per stat, 66 total. Each SP = +1 to the base stat at Lv 50.
    """
    try:
        fmt = resolve_format(format)
    except ValueError as exc:
        return {"error": str(exc), "valid_formats": _FORMAT_CHOICES}
    result = await App.get().chaos_client.get_pokemon_chaos(pokemon, fmt, rating=rating)
    if result is None:
        return {
            "error": f"No chaos data found for '{pokemon}' in {fmt.upper()} (rating {rating}).",
            "hint": "Check the spelling — use the display name shown in game (e.g. 'Feraligatr-Mega').",
        }
    return result.model_dump()


@mcp.tool()
async def get_smogon_analysis(
    pokemon: str, format: str = "vgc"
) -> dict[str, Any]:
    """Get Smogon strategy analysis for a Pokémon in a Champions format.

    Returns a written overview and named competitive sets (with descriptions)
    from the Smogon strategy dex (https://www.smogon.com/dex/champions/).
    The Champions metagame is new — only a small number of high-profile Pokémon
    have published analyses so far. When no analysis exists, the tool returns
    ``available: false`` with a note; fall back to ``get_pokemon_sets`` and
    ``get_usage_stats`` for usage-based data.

    Supported formats:
    - ``gen9championsvgc2026regma`` (VGC 2026 Reg M-A, doubles)
    - ``gen9championsbssregma`` (Battle Stadium Singles)

    `pokemon`: English display name (e.g. "Incineroar", "Garchomp").
    `format`: "vgc" ([Champions] VGC 2026 Reg M-A — doubles) or "bss" ([Champions] BSS Reg M-A — singles).
    """
    try:
        fmt = resolve_format(format)
    except ValueError as exc:
        return {"error": str(exc), "valid_formats": _FORMAT_CHOICES}
    result = await App.get().smogon_analyses.get_analysis(pokemon, fmt)
    if result is None:
        return {
            "available": False,
            "pokemon": pokemon,
            "format": fmt,
            "note": (
                "No Smogon strategy analysis found for this Pokémon in the Champions format. "
                "The Champions metagame is new and only a small number of Pokémon have "
                "published analyses on the Smogon strategy dex so far. "
                "Use get_pokemon_sets and get_usage_stats for usage-based data instead."
            ),
        }
    return result.model_dump()


@mcp.tool()
async def get_top_teams(
    format: str | None = None, top: int = 8
) -> list[dict[str, Any]]:
    """Get top-cut teams from recent Limitless Champions VGC tournaments.

    **VGC (doubles) only** — Limitless does not track BSS.
    Use for team inspiration and meta context. For structured core patterns,
    call ``suggest_cores`` instead. Pass ``format="M-A"`` to filter to Reg M-A.

    For per-Pokémon usage data, use ``get_usage_stats`` or ``get_pokemon_sets``.
    """
    try:
        return await App.get().limitless.winning_teams(
            game="VGC", format=format, top=top
        )
    except LimitlessError as exc:
        return [{"error": str(exc)}]


# --------------------------- Tournament tools ------------------------


@mcp.tool()
async def search_tournaments(
    query: str | None = None,
    format: str | None = None,
    limit: int = 25,
    page: int = 1,
) -> list[dict[str, Any]]:
    """List Limitless Champions VGC tournaments, optionally filtered by name or format.

    Limitless only hosts VGC (doubles) tournaments. BSS is not available here.
    `query`: case-insensitive substring matched against the tournament name.
    `format`: exact format ID (e.g. "M-A").
    """
    try:
        rows = await App.get().limitless.list_tournaments(
            game="VGC", format=format, limit=limit, page=page
        )
        if query:
            q = query.strip().lower()
            rows = [r for r in rows if q in r.name.lower()]
        return [r.model_dump() for r in rows]
    except LimitlessError as exc:
        return [{"error": str(exc)}]


@mcp.tool()
async def get_tournament_standings(tournament_id: str) -> list[dict[str, Any]]:
    """Get full standings (placements, records, decklists) for a tournament."""
    try:
        return await App.get().limitless.tournament_standings(tournament_id)
    except LimitlessError as exc:
        return [{"error": str(exc)}]


# --------------------------- Reasoning tools -------------------------


@mcp.tool()
async def analyze_team(
    team: list[dict[str, Any]],
    regulation_id: str = "current",
    format: str = "vgc",
) -> dict[str, Any]:
    """Legality check + meta context for a complete built team.

    Call this after building a team to get:
    - Full legality report (violations, warnings).
    - Per-member typing and base-stat total.
    - Per-member usage % benchmark (low usage ≠ bad; just uncommon).
    - Top meta threats not on the team, to assess coverage gaps.

    `format` **must match the team's format** (vgc/bss): wrong format produces
    meaningless threat data. Use the same format as the current session.

    For single-Pokémon checks during building, use ``is_legal_pokemon`` and
    ``is_legal_move``. For a Pokepaste export, call ``create_pokepaste`` after.
    """
    reg = _resolve_regulation(regulation_id)
    try:
        fmt = resolve_format(format)
    except ValueError as exc:
        return {"error": str(exc), "valid_formats": _FORMAT_CHOICES}
    try:
        parsed = Team(members=team)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Invalid team payload: {exc}"}
    return await App.get().builder.analyze(parsed, reg, format_key=fmt)


@mcp.tool()
async def suggest_cores(
    regulation_id: str = "current", gimmick: bool = False, limit: int = 8
) -> dict[str, Any]:
    """Surface co-occurrence patterns from top-cut VGC teams as core inspiration.

    **VGC (doubles) only** — no BSS equivalent.
    Returns the most frequent Pokémon pairs found together in Limitless top-cut
    teams. Use early in team building to find proven synergistic pairs.

    With ``gimmick=True``, pairs including at least one lower-usage Pokémon
    are preferred — useful for off-meta theorycrafting.

    After picking a core: use ``get_pokemon_sets`` to research each member,
    then ``list_legal_pokemon`` to fill the remaining slots.
    """
    reg = _resolve_regulation(regulation_id)
    return await App.get().builder.suggest_cores(
        reg, gimmick=gimmick, limit=limit
    )


# --------------------- EV / Stat Points / speed ----------------------


async def _base_stats(species: str) -> tuple[str, dict[str, int]]:
    app = App.get()
    slug = await app.names.resolve(species)
    mon = await app.pokeapi.get_pokemon(slug)
    bs = mon.base_stats
    return mon.name, {
        "hp": bs.hp,
        "attack": bs.attack,
        "defense": bs.defense,
        "special-attack": bs.special_attack,
        "special-defense": bs.special_defense,
        "speed": bs.speed,
    }


@mcp.tool()
async def validate_ev_spread(stat_points: dict[str, int]) -> dict[str, Any]:
    """Validate a Champions Stat Points spread.

    Champions rule: integer SP >= 0 per stat, max 32 per stat, 66 total
    (1 SP = +1 to the pre-nature stat at Lv 50). Stats: hp, attack, defense,
    special-attack, special-defense, speed.

    Prefer ``calc_stats`` when you also need the resulting stat values — it
    validates the spread and returns all six final stats in a single call.
    """
    chk = validate_spread(stat_points)
    return {
        "ok": chk.ok,
        "total": chk.total,
        "budget": MAX_TOTAL_SP,
        "per_stat_cap": MAX_PER_STAT_SP,
        "remaining": MAX_TOTAL_SP - chk.total,
        "violations": chk.violations,
    }


@mcp.tool()
async def calc_stats(
    name: str,
    stat_points: dict[str, int] | None = None,
    nature: str = "serious",
) -> dict[str, Any]:
    """Compute all six final Champions stats (Lv 50, 31 IVs) for a spread.

    **Use after picking a spread from ``get_pokemon_sets``** to confirm exact
    stat values. Validates the SP budget and returns base + final stats.

    Accepts localized species names. Nature default is "serious" (neutral).
    For detailed in-battle Speed with modifiers, use ``compute_speed``.
    To find min SP to outspeed a target, use ``speed_threshold``.
    """
    sp = stat_points or {}
    chk = validate_spread(sp)
    try:
        name, base = await _base_stats(name)
        nat = normalize_nature(nature)
    except (StatError, KeyError) as exc:
        return {"error": str(exc)}
    final = {
        s: compute_stat(base[s], int(sp.get(s, 0)), s, nat) for s in ALL_STATS
    }
    return {
        "species": name,
        "nature": nat,
        "base_stats": base,
        "stat_points": {s: int(sp.get(s, 0)) for s in ALL_STATS},
        "final_stats": final,
        "nature_effect": {s: nature_effect(nat, s) for s in ALL_STATS},
        "spread_valid": chk.ok,
        "spread_violations": chk.violations,
    }


@mcp.tool()
async def compute_speed(
    name: str,
    speed_sp: int = 0,
    nature: str = "serious",
    stage: int = 0,
    choice_scarf: bool = False,
    tailwind: bool = False,
    paralyzed: bool = False,
) -> dict[str, Any]:
    """Effective in-battle Speed for a Champions set with modifiers applied.

    Order: raw stat -> stat stage -> Choice Scarf (x1.5) -> Tailwind (x2) ->
    paralysis (x0.5), flooring at each step.

    To find the minimum Speed SP required to outspeed a specific target,
    call ``speed_threshold`` instead.
    """
    try:
        name, base = await _base_stats(name)
        nat = normalize_nature(nature)
    except (StatError, KeyError) as exc:
        return {"error": str(exc)}
    raw = compute_stat(base["speed"], speed_sp, "speed", nat)
    eff = effective_speed(
        base["speed"], speed_sp, nat,
        stage=stage, choice_scarf=choice_scarf,
        tailwind=tailwind, paralyzed=paralyzed,
    )
    return {
        "species": name,
        "base_speed": base["speed"],
        "speed_sp": speed_sp,
        "nature": nat,
        "raw_speed": raw,
        "effective_speed": eff,
        "modifiers": {
            "stage": stage, "choice_scarf": choice_scarf,
            "tailwind": tailwind, "paralyzed": paralyzed,
        },
    }


@mcp.tool()
async def speed_threshold(
    attacker: str,
    defender: str,
    defender_speed_sp: int = 0,
    defender_nature: str = "serious",
    attacker_natures: list[str] | None = None,
    tie_is_enough: bool = False,
    attacker_stage: int = 0,
    attacker_choice_scarf: bool = False,
    attacker_tailwind: bool = False,
    attacker_paralyzed: bool = False,
    defender_stage: int = 0,
    defender_choice_scarf: bool = False,
    defender_tailwind: bool = False,
    defender_paralyzed: bool = False,
) -> dict[str, Any]:
    """Minimum Speed SP for `attacker` to outspeed `defender`.

    Use after identifying a speed target from ``get_usage_stats`` or
    ``get_pokemon_sets`` (check the top spreads for the defender's speed SP).

    Computes the defender's effective Speed from its assumed SP/nature/mods,
    then, for each requested attacker nature (default: neutral + Jolly),
    returns the least Speed SP that makes the attacker faster (or, with
    tie_is_enough, at least tied) and the SP left from the 66 budget. Any
    +Speed nature (timid/hasty/jolly/naive) is equivalent for Speed.
    """
    natures = tuple(attacker_natures or ["serious", "jolly"])
    try:
        a_name, a_base = await _base_stats(attacker)
        d_name, d_base = await _base_stats(defender)
        for n in natures:
            normalize_nature(n)
        normalize_nature(defender_nature)
    except (StatError, KeyError) as exc:
        return {"error": str(exc)}

    target = effective_speed(
        d_base["speed"], defender_speed_sp, defender_nature,
        stage=defender_stage, choice_scarf=defender_choice_scarf,
        tailwind=defender_tailwind, paralyzed=defender_paralyzed,
    )
    results = min_sp_to_outspeed(
        a_base["speed"], target,
        natures=natures, tie_is_enough=tie_is_enough,
        attacker_mods={
            "stage": attacker_stage,
            "choice_scarf": attacker_choice_scarf,
            "tailwind": attacker_tailwind,
            "paralyzed": attacker_paralyzed,
        },
    )
    return {
        "attacker": a_name,
        "attacker_base_speed": a_base["speed"],
        "defender": d_name,
        "defender_base_speed": d_base["speed"],
        "defender_effective_speed": target,
        "goal": "tie-or-faster" if tie_is_enough else "strictly-faster",
        "speed_plus_natures": SPEED_PLUS_NATURES,
        "options": [
            {
                "nature": r.nature,
                "min_speed_sp": r.min_sp,
                "achievable": r.min_sp is not None,
                "resulting_speed": r.resulting_speed,
                "sp_left_for_other_stats": r.sp_remaining_budget,
            }
            for r in results
        ],
    }


# --------------------------- Damage calc -----------------------------


@mcp.tool()
async def calc_damage(
    attacker: str,
    move: str,
    defender: str,
    attacker_ability: str | None = None,
    attacker_item: str | None = None,
    attacker_nature: str = "serious",
    attacker_stat_points: dict[str, int] | None = None,
    attacker_boosts: dict[str, int] | None = None,
    attacker_status: str | None = None,
    defender_ability: str | None = None,
    defender_item: str | None = None,
    defender_nature: str = "serious",
    defender_stat_points: dict[str, int] | None = None,
    defender_boosts: dict[str, int] | None = None,
    defender_status: str | None = None,
    weather: str | None = None,
    terrain: str | None = None,
    game_type: str = "doubles",
    helping_hand: bool = False,
    crit: bool = False,
    move_hits: int | None = None,
) -> dict[str, Any]:
    """Champions damage calc (via @smogon/calc): a move from one build vs another.

    **Before calling this:** use ``get_pokemon_sets`` to get realistic spreads
    for both sides; use ``get_move`` to confirm move details.

    Champions-native inputs: Lv 50, 31 IVs assumed. Pass **Stat Points** per
    side as e.g. {"attack": 32, "speed": 32, "hp": 2}. `*_nature`: nature name
    (default neutral). `*_boosts`: stat stages e.g. {"attack": 1}.
    `*_status`: brn/par/psn/tox/slp/frz. `weather`: sun/rain/sand/snow.

    **FORMAT RULE — always pass `game_type` explicitly:**
    - VGC session (doubles): ``game_type="doubles"`` (default)
    - BSS session (singles): ``game_type="singles"``
    Using the wrong game_type changes damage calculations (spread move
    reduction in doubles, etc.). Never use the default blindly.

    For Mega Evolution pass the Mega Stone as the item.
    Returns rolls, %HP, description and KO chance.
    Requires the `champions-calc` Docker image.
    """
    req = {
        "attacker": attacker,
        "move": move,
        "defender": defender,
        "attacker_ability": attacker_ability,
        "attacker_item": attacker_item,
        "attacker_nature": attacker_nature,
        "attacker_stat_points": attacker_stat_points,
        "attacker_boosts": attacker_boosts,
        "attacker_status": attacker_status,
        "defender_ability": defender_ability,
        "defender_item": defender_item,
        "defender_nature": defender_nature,
        "defender_stat_points": defender_stat_points,
        "defender_boosts": defender_boosts,
        "defender_status": defender_status,
        "weather": weather,
        "terrain": terrain,
        "game_type": game_type,
        "helping_hand": helping_hand,
        "crit": crit,
        "move_hits": move_hits,
    }
    return await App.get().damage.calculate(req)


# --------------------------- Pokepaste ----------------------------

_SP_STAT_ABBREV: dict[str, str] = {
    "hp": "HP",
    "attack": "Atk",
    "defense": "Def",
    "special-attack": "SpA",
    "special_attack": "SpA",
    "special-defense": "SpD",
    "special_defense": "SpD",
    "speed": "Spe",
}


_SPECIES_NAME_OVERRIDES: dict[str, str] = {
    "kommo-o": "Kommo-o",
    "jangmo-o": "Jangmo-o",
}


def _slug_to_species_name(slug: str) -> str:
    """Convert a PokeAPI species slug to a Showdown-compatible display name.

    Preserves hyphens (form separator) and title-cases each segment:
    'aegislash-shield' -> 'Aegislash-Shield', 'ninetales-alola' -> 'Ninetales-Alola'.
    Known exceptions (e.g. 'kommo-o') are returned from _SPECIES_NAME_OVERRIDES.
    If the value is already title-cased it is returned unchanged.
    """
    if slug in _SPECIES_NAME_OVERRIDES:
        return _SPECIES_NAME_OVERRIDES[slug]
    # Handle form suffixes: 'kommo-o-totem' -> 'Kommo-o-Totem'
    for base, display in _SPECIES_NAME_OVERRIDES.items():
        if slug.startswith(base + "-"):
            rest = slug[len(base) + 1 :]
            suffix = "-".join(p.capitalize() for p in rest.split("-"))
            return f"{display}-{suffix}"
    return "-".join(p.capitalize() for p in slug.split("-"))


def _slug_to_display_name(slug: str) -> str:
    """'trick-room' -> 'Trick Room', 'flash-fire' -> 'Flash Fire'."""
    return " ".join(p.capitalize() for p in slug.split("-"))


def _member_to_showdown(m: dict[str, Any], item_map: dict[str, str] | None = None) -> str:
    lines: list[str] = []
    species = _slug_to_species_name(m.get("species") or "Unknown")
    nickname = m.get("nickname")
    item_raw = (m.get("item") or "").strip()
    if item_raw:
        item: str | None = (item_map or {}).get(item_key(item_raw)) or _slug_to_display_name(item_raw)
    else:
        item = None

    first = f"{nickname} ({species})" if nickname else species
    if item:
        first += f" @ {item}"
    lines.append(first)

    if m.get("ability"):
        lines.append(f"Ability: {_slug_to_display_name(m['ability'])}")

    level = m.get("level", 50)
    if level != 100:
        lines.append(f"Level: {level}")

    sp: dict[str, int] = m.get("stat_points") or {}
    ev_parts = []
    for key in ("hp", "attack", "defense", "special-attack", "special-defense", "speed"):
        val = sp.get(key, sp.get(key.replace("-", "_"), 0))
        if val:
            ev_parts.append(f"{val} {_SP_STAT_ABBREV[key]}")
    if ev_parts:
        lines.append("EVs: " + " / ".join(ev_parts))

    if m.get("nature"):
        lines.append(f"{m['nature'].capitalize()} Nature")

    for move in (m.get("moves") or [])[:4]:
        lines.append(f"- {_slug_to_display_name(move)}")

    # pokepast.es splits members on \r\n\r\n and lines on \r\n
    return "\r\n".join(lines)


def _team_to_showdown(team: list[dict[str, Any]], item_map: dict[str, str] | None = None) -> str:
    return "\r\n\r\n".join(_member_to_showdown(m, item_map) for m in team)


@mcp.tool()
async def create_pokepaste(
    team: list[dict[str, Any]],
    title: str = "",
    author: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Upload a Champions team to pokepast.es and return the shareable link.

    **Call after ``validate_team`` confirms the team is fully legal** and all
    builds are finalised.

    Converts the team to Showdown paste format and posts it to pokepast.es.
    SP values appear in the EVs line; note that in Champions 1 SP ≠ 4 EVs
    (budget 66, not 510).

    Each team member: {"species": str, "item"?: str, "ability"?: str,
    "moves"?: [str], "nature"?: str, "stat_points"?: dict, "nickname"?: str,
    "level"?: int}. title, author, notes are optional paste metadata.
    Returns {"url": "https://pokepast.es/<id>", "paste": "<text>"}.
    """
    import httpx

    item_map = {item_key(n): n for n in App.get().item_catalog.names}
    paste_text = _team_to_showdown(team, item_map)
    sp_note = "(Champions SP \u2260 EVs: 1 SP = +1 stat at Lv 50, budget 66)"
    full_notes = f"{notes}\n{sp_note}".strip() if notes else sp_note

    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
            resp = await client.post(
                "https://pokepast.es/create",
                data={
                    "paste": paste_text,
                    "title": title,
                    "author": author,
                    "notes": full_notes,
                },
            )
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if location.startswith("/"):
                url = f"https://pokepast.es{location}"
            elif location.startswith("http"):
                url = location
            else:
                return {"error": f"Unexpected Location header: {location!r}"}
        elif resp.status_code == 200:
            url = str(resp.url)
        else:
            return {"error": f"Unexpected status {resp.status_code}: {resp.text[:200]}"}
        return {"url": url, "paste": paste_text}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------- Resources -------------------------------


@mcp.resource("regulation://current")
def resource_current_regulation() -> str:
    return _resolve_regulation("current").model_dump_json(indent=2)


@mcp.resource("pokedex://champions")
def resource_champions_pokedex() -> str:
    reg = _resolve_regulation("current")
    r = App.get().roster
    pickable = sum(1 for e in r.entries if not e.get("is_mega"))
    return (
        f"Pokémon Champions active regulation: {reg.id} ({reg.name}).\n"
        f"Global roster: {pickable} pickable species "
        f"(verified={r.verified}, source={r.source_url}).\n"
        "Before picking Pokémon, use list_legal_pokemon (full allowlist) or "
        "is_legal_pokemon (single check); then validate_team for "
        "item/move/clause/spread legality."
    )


def main() -> None:
    import logging
    import os
    from typing import Literal

    logging.getLogger("httpx").setLevel(logging.WARNING)
    transport: Literal["stdio", "sse", "streamable-http"] = os.environ.get(  # type: ignore[assignment]
        "MCP_TRANSPORT", "stdio"
    )
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
