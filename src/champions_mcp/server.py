"""champions-mcp v2 — MCP server.

Every tool reads from the local ``champions.sqlite`` mirror built by
``champions-mcp-pull`` (pokechamp.gg + PokeAPI for the type chart/localized
names, plus the observed-team corpus). The only runtime outbound calls are
the optional damage-calc sidecar (``calc_damage``) and ``create_pokepaste``.

Built on ``mcp.server.mcpserver.MCPServer`` (MCP SDK 2.x), which replaced the
1.x ``FastMCP`` class. The decorator surface is the same — ``@mcp.tool()``,
``@mcp.prompt()``, ``@mcp.resource(uri)`` — but the server no longer takes
host/port at construction; those are transport arguments to ``run()``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer  # pyright: ignore[reportMissingImports]

from . import legality as legality_mod
from . import (
    builds,
    historical_sets,
    pokedex,
    pokepaste,
    profiles,
    reference,
    roster,
    teambuilder,
    usage,
)
from . import roles as roles_mod
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
    normalize_spread,
    validate_spread,
)
from .config import Settings
from .damage import DamageCalculator
from .db import Db
from .formats import game_type, resolve_format, rules
from .names import NameError, resolve


class App:
    """Lazily-constructed singleton holding the DB + the damage calc."""

    _instance: App | None = None

    def __init__(self) -> None:
        """Load settings and open the DB + damage-calculator handles.

        Raises:
            FileNotFoundError: If ``champions.sqlite`` does not exist yet at
                the configured path (run ``champions-mcp-pull`` first).
        """
        self.settings = Settings.load()
        self.db = Db(self.settings.db_path)
        self.damage = DamageCalculator(self.settings, self.db)

    @classmethod
    def get(cls) -> App:
        """Return the process-wide `App` instance, constructing it on first call.

        Returns:
            The singleton `App`.
        """
        if cls._instance is None:
            cls._instance = App()
        return cls._instance


def _version() -> str:
    """This package's installed version, for the MCP handshake's serverInfo."""
    try:
        from importlib.metadata import version

        return version("champions-mcp")
    except Exception:  # not installed (e.g. running straight from a checkout)
        return "0.0.0"


mcp = MCPServer("champions-mcp", version=_version())


def _db() -> Db:
    """Shorthand for the singleton App's database handle."""
    return App.get().db


# ----------------------- Skill prompt -------------------------

# Packaged install (site-packages/champions_mcp/SKILL.md) vs. running from a
# repo checkout (skills/champions-team-building/SKILL.md) — try both so the
# prompt works either way without duplicating the file.
_SKILL_MD_CANDIDATES = (
    Path(__file__).parent / "SKILL.md",
    Path(__file__).resolve().parents[2] / "skills" / "champions-team-building" / "SKILL.md",
)


def _load_skill_md() -> str:
    """Read the team-building SKILL.md, stripping its YAML frontmatter if present.

    Returns:
        The skill guide body, or a short fallback message if the file is
        missing from both known locations.
    """
    for path in _SKILL_MD_CANDIDATES:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    text = text[text.find("\n", end + 1) + 1:]
            return text.lstrip()
    return ("Champions team-building skill not found. Call get_game_rules first, "
            "then follow each tool's guidance.")


@mcp.prompt()
def champions_team_building() -> str:
    """Champions team-building skill: rules, workflow and tool guide for building
    competitive VGC (doubles) or BSS (singles) teams. Load once per session."""
    return _load_skill_md()


# ----------------------- Game-rules baseline -------------------------

_STAT_POINTS_RULES: dict[str, Any] = {
    "total_budget": MAX_TOTAL_SP,
    "per_stat_maximum": MAX_PER_STAT_SP,
    "minimum_per_stat": 0,
    "stats": list(ALL_STATS),
    "effect": "1 SP = +1 to the pre-nature stat value at Level 50",
    "formula_non_hp": "floor( (2*base + 31)//2 + 5 + sp ) * nature_modifier",
    "formula_hp": "(2*base + 31)//2 + 60 + sp  (nature never affects HP)",
    "speed_plus_natures": SPEED_PLUS_NATURES,
    "speed_minus_natures": SPEED_MINUS_NATURES,
    "note": "SP are NOT EVs — 1 SP = +1 stat, not +¼ like EVs. Budget is 66, not 510.",
}

_SPEED_MODIFIERS = {
    "order": "raw stat → stat stage → Choice Scarf (×1.5) → Tailwind (×2) → paralysis (×0.5)",
    "flooring": "integer floor applied at each step",
}

_MEGA_RULES = {
    "max_active_per_battle": 1,
    "requirement": "Pokémon must hold its specific Mega Stone",
    "note": "Mega Evolves during the turn declared; acts at its Mega stat/ability. "
            "calc_damage auto-Mega-evolves from the held stone. Use list_megas to "
            "see every available Mega, its Stone and its stat changes.",
}

_TOOL_GUIDE = {
    "session_start": "get_game_rules(format) — call first.",
    "pokedex": "get_pokemon, search_pokemon, get_random_pokemon, list_megas, get_move, "
               "search_moves, list_items (the whole item pool — call before "
               "assigning items), get_item, search_items, get_ability, "
               "get_type_matchups, get_pokemon_weaknesses",
    "build": "list_pokemon, search_pokemon_by_type/ability/move, get_pokemon_moves "
             "(legal movepool), is_legal_move, is_legal_pokemon",
    "profiles": "list_type_profiles (rank every typing offensively/defensively), "
                "list_profile_tags (the tag vocabulary), search_pokemon_by_profile "
                "(fill a slot from the whole legal pool by tag). get_pokemon "
                "returns each Pokémon's own profile tags.",
    "roles": "list_roles(format) — the competitive role vocabulary for this "
             "format; get_role_description(role, format) — what a slot is for, "
             "its Champions-verified move/item signature and SP guidance. Roles "
             "differ sharply between vgc (doubles) and bss (singles).",
    "meta": "get_usage_stats, get_pokemon_sets, get_top_teammates, suggest_cores, "
            "get_speed_tiers — pass format=vgc|bss",
    "observed_builds": "get_observed_sets (complete builds real players brought, "
                       "with which moves go with which item), get_team_context "
                       "(partners conditioned on your whole core, with an "
                       "affinity score), get_sample_teams (whole teams with "
                       "provenance). Prefer get_observed_sets over "
                       "get_pokemon_sets when committing to a set: the latter "
                       "is per-slot popularity and stacking its top picks "
                       "produces a build nobody plays. get_historical_sets is "
                       "a cross-game (Smogon/Showdown) fallback for when that "
                       "corpus is thin — get_observed_sets surfaces it "
                       "automatically in that case too.",
    "using_the_meta": "Usage data tells you what the field brings and therefore "
                      "what you must beat — it is not a shopping list. Copy the "
                      "modal team and you inherit its losses while everyone "
                      "already knows the answers to it. Take the locked choices "
                      "seriously, decide the contested ones from your own "
                      "matchups, and check any idea with calc_damage / "
                      "compute_speed_threshold rather than by its usage %.",
    "stats": "calc_stats, compute_speed, compute_speed_threshold",
    "finish": "validate_team → analyze_team → calc_damage → create_pokepaste",
}


@mcp.tool()
async def get_game_rules(format: str = "vgc") -> dict[str, Any]:
    """**Call first in any session.** Pass format='vgc' (doubles, bring 4) or
    'bss' (singles, bring 3). Returns the format rules, the Champions Stat-Point
    system, Mega rules, the active data season, and a tool guide. All later tools
    take this same format argument."""
    db = _db()
    fmt = resolve_format(format)
    r = rules(fmt)
    return {
        "game": "Pokémon Champions",
        "format": fmt,
        "format_rules": r,
        "stat_points_system": _STAT_POINTS_RULES,
        "speed_modifiers": _SPEED_MODIFIERS,
        "mega_evolution": _MEGA_RULES,
        "data": {
            "source": "pokechamp.gg",
            "season": db.meta("season", "Current"),
            "data_version": db.meta("data_version", ""),
            "generated_at": db.meta("generated_at", ""),
            "counts": db.meta("counts", {}),
            "observed_builds": _observed_builds_summary(db, fmt),
        },
        "session_tool_guide": _TOOL_GUIDE,
    }


def _observed_builds_summary(db: Db, fmt: str) -> dict[str, Any]:
    """Describe the observed-team corpus backing this format, if any.

    Args:
        db: Open database handle.
        fmt: Resolved format key (``vgc``/``bss``).

    Returns:
        The corpus summary with its evidence level, or a note explaining that
        only marginal usage data is available.
    """
    summary = db.builds_available(game_type(fmt))
    if not summary:
        return {
            "available": False,
            "note": "No observed-team corpus — run champions-mcp-pull to ingest it. "
                    "Until then only marginal usage data is available.",
        }
    evidence = summary["evidence"]
    return {
        "available": True,
        "teams": summary["teams"],
        "regulations": summary["regulations"],
        "sources": summary["sources"],
        "evidence": evidence,
        "means": (
            "full_set: species + item + ability + nature + moves per member "
            "(tournament open team sheets)."
            if "full_set" in evidence else
            "team_and_item: species + held item per member. The ranked-ladder "
            "export publishes no moves, abilities or natures, so any moveset "
            "you give is inference and must be justified as such."
        ),
    }


# ----------------------------- Pokédex -------------------------------

@mcp.tool()
async def get_pokemon(name: str) -> dict[str, Any]:
    """Champions types, abilities, base stats and Mega forms for a Pokémon.
    Accepts localized and form names. Base stats are Champions Lv-50 base values —
    use calc_stats for final stats from an SP spread."""
    return pokedex.get_pokemon(_db(), name)


@mcp.tool()
async def search_pokemon(query: str, limit: int = 25) -> list[dict[str, str]]:
    """Substring search over the Champions roster. Returns {slug, name}."""
    return pokedex.search_pokemon(_db(), query, limit)


@mcp.tool()
async def get_random_pokemon() -> dict[str, Any]:
    """A uniformly random Pokémon from the current Champions roster — same
    shape as get_pokemon. Useful for inspiration, random-team challenges, or
    breaking a tie between candidates."""
    return pokedex.get_random_pokemon(_db())


@mcp.tool()
async def list_megas(query: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Species with a Mega Evolution in Champions: the Mega Stone that unlocks
    it, typing/ability change, and stat deltas vs. the base form.

    Champions has 80+ Megas, so omitting `query` returns a **compact index**
    (stone, typing, ability, BST change) — enough to see what exists. Pass a
    species/Mega name substring to get the **full** base stats and per-stat
    changes for the matches. `count` vs `matched` tells you if `limit` cut the
    list."""
    return pokedex.list_megas(_db(), query, limit)


@mcp.tool()
async def get_move(name: str) -> dict[str, Any]:
    """Type, category, power, accuracy, priority and effect text for a move."""
    return reference.get_move(_db(), name)


@mcp.tool()
async def search_moves(
    query: str | None = None, type: str | None = None,
    category: str | None = None, legal_only: bool = True, limit: int = 50,
) -> dict[str, Any]:
    """Search moves by name substring, type and/or category (physical/special/
    status). legal_only restricts to moves learnable by some Champions Pokémon."""
    return reference.search_moves(_db(), query, type, category, legal_only, limit)


@mcp.tool()
async def get_item(name: str) -> dict[str, Any]:
    """Item category, effect text and Mega-Stone flag."""
    return reference.get_item(_db(), name)


@mcp.tool()
async def list_items(
    category: str | None = None, query: str | None = None,
    include_mega_stones: bool = False, limit: int = 200,
) -> dict[str, Any]:
    """**The full Champions item pool**, grouped by category with effect text —
    call this before assigning items rather than reaching for an item you
    remember from another game.

    Champions' pool is much smaller than other games': Choice Scarf is the only
    Choice item, and there is no Assault Vest, Heavy-Duty Boots, Covert Cloak or
    Safety Goggles. **If an item is not in this list, it does not exist here.**

    Mega Stones are hidden by default (around half of the item pool, and the
    stone is decided by which Mega you want — see list_megas); pass
    include_mega_stones=True for them. Filter with `category` (held-items,
    type-enhancement, type-protection, medicine, choice, bad-held-items,
    species-specific) or a `query` substring."""
    return reference.list_items(_db(), category, query, include_mega_stones, limit)


@mcp.tool()
async def search_items(query: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Substring search of items by name — use when you know roughly what you
    want. To browse everything that exists, use list_items instead."""
    return reference.search_items(_db(), query, limit)


@mcp.tool()
async def get_ability(name: str) -> dict[str, Any]:
    """Effect text for an ability."""
    return reference.get_ability(_db(), name)


@mcp.tool()
async def get_type_matchups(type1: str, type2: str | None = None) -> dict[str, Any]:
    """Offensive and defensive effectiveness for a type or dual-type combo.

    Offensive lists the best-case STAB coverage (super-effective / resisted /
    immune) the typing reaches. Defensive stacks incoming multipliers across both
    types, grouping all 18 attacking types by multiplier (4×/2×/1×/½×/¼×/0×)."""
    return pokedex.get_type_matchups(_db(), type1, type2)


@mcp.tool()
async def get_pokemon_weaknesses(
    name: str | None = None, type1: str | None = None, type2: str | None = None,
) -> dict[str, Any]:
    """Full defensive type chart for a Pokémon (Champions typing) or an explicit
    type pair. Groups all 18 attacking types by multiplier (4×/2×/1×/½×/¼×/0×).

    **Call this instead of working the matchup out yourself.** Dual-type
    multipliers stack into a product and an immunity on either half wins
    outright, which is easy to get wrong from memory — and a wrong multiplier
    silently corrupts every threat assessment and switch-in built on it. Pass a
    Pokémon name to also pick up its Champions typing (regional forms share a
    name with the base; Megas often change type)."""
    return pokedex.get_pokemon_weaknesses(_db(), name, type1, type2)


# ----------------------- Roster & movepool ---------------------------

@mcp.tool()
async def list_pokemon(
    query: str | None = None, type: str | None = None,
    format: str | None = None, limit: int = 60,
) -> dict[str, Any]:
    """Roster pick list (every entry is legal this season). Filter by name
    substring and/or a single type; pass a format to sort by usage rank.

    The roster is 235 Pokémon, so this returns the first `limit` (default 60)
    matches. `count` is always the true match total and a `truncated` note
    appears when there are more — narrow with `query`/`type` rather than raising
    `limit` blindly. To rank by usage use get_usage_stats (`order='bottom'` for
    off-meta picks), not this tool."""
    return roster.list_pokemon(_db(), query, type, format, limit)


@mcp.tool()
async def search_pokemon_by_type(type1: str, type2: str | None = None) -> dict[str, Any]:
    """Champions Pokémon of a given type or exact type pair (Champions typing)."""
    return roster.pokemons_by_type(_db(), type1, type2)


@mcp.tool()
async def search_pokemon_by_ability(ability: str) -> dict[str, Any]:
    """Champions Pokémon with a given ability (checks Mega-form abilities too)."""
    return roster.pokemons_by_ability(_db(), ability)


@mcp.tool()
async def search_pokemon_by_move(move: str) -> dict[str, Any]:
    """Champions Pokémon that can learn a given move (Champions movepools)."""
    return roster.pokemons_by_move(_db(), move)


@mcp.tool()
async def get_pokemon_moves(name: str) -> dict[str, Any]:
    """**Call before assigning moves.** Full Champions-legal movepool for a
    Pokémon (pokechamp.gg learnsets; SV/PokeAPI learnsets are wrong).

    Also returns `coverage_for_weaknesses`: for each type this Pokémon is weak
    to, the legal moves it owns that hit that type back super-effectively (e.g.
    an Ice-weak Pokémon running a Rock or Fighting move). **Treat this as a
    consideration, not a rule.** The Pokémon that threaten you are most often of
    the type you are weak to, so hitting back turns their free switch-in into a
    trade — but never drop a move the gameplan needs just to satisfy it, and
    weigh it against raw power, STAB and what teammates already cover. Two
    caveats: the threat may instead be another type carrying that move (a
    Water-type with an Ice move), so confirm against real threats with
    get_usage_stats + calc_damage; and suggestions are ordered to prefer this
    Pokémon's stronger offensive side, which is where coverage actually lands."""
    return roster.get_pokemon_moves(_db(), name)


@mcp.tool()
async def is_legal_move(name: str, move: str) -> dict[str, Any]:
    """Quick check: can this Pokémon use this move in Champions?"""
    return roster.is_legal_move(_db(), name, move)


@mcp.tool()
async def is_legal_pokemon(name: str) -> dict[str, Any]:
    """Quick check: is this Pokémon in the current Champions roster?"""
    return roster.is_legal_pokemon(_db(), name)


# ----------------------------- Profiles ------------------------------

@mcp.tool()
async def list_type_profiles(
    sort: str = "defensive", type: str | None = None,
    dual_only: bool = False, limit: int = 30,
) -> dict[str, Any]:
    """Rank every typing — all 18 single types and 153 pairs — by how good it is
    defensively and offensively, each graded as a percentile against all the
    others.

    The two axes are independent and often opposed: Ice/Rock has one of the best
    offensive spreads on the board and the single worst defensive one. Use this
    to pick a typing for a slot, or to understand why a Pokémon struggles.

    `sort`: defensive | offensive | worst_defensive | worst_offensive | balanced.
    `type` filters to typings containing that type; `dual_only` drops the 18
    single types."""
    return profiles.list_type_profiles(_db(), sort, type, dual_only, limit)


@mcp.tool()
async def list_profile_tags(namespace: str | None = None) -> dict[str, Any]:
    """The profile-tag vocabulary, with each tag's meaning and how many roster
    Pokémon carry it — read this before searching so you use real tags.

    Four namespaces: `def:` and `off:` (typing quality), `stat:` (one base stat's
    percentile), `build:` (archetype derived from the whole spread, e.g.
    build:glass-cannon, build:trick-room, build:bulky-attacker).

    **All percentiles are relative to the Champions roster**, which rescaled base
    stats — "fast" here means fast in this game, not in another one."""
    return profiles.list_profile_tags(_db(), namespace)


@mcp.tool()
async def search_pokemon_by_profile(
    tags: list[str], match: str = "all", format: str | None = None,
    limit: int = 40, include_megas: bool = True,
) -> dict[str, Any]:
    """Find roster Pokémon by profile tag — the fastest way to fill a role slot
    from the whole legal pool instead of from memory.

    e.g. tags=["build:physical-attacker","build:very-fast"] for a fast physical
    breaker, or ["build:trick-room","stat:atk-elite"] for a Trick Room abuser.
    `match="all"` (default) requires every tag; `"any"` returns the union. Pass a
    format to sort results by usage rank. Call list_profile_tags first for the
    valid tags — unknown tags are rejected rather than silently ignored.

    Mega forms are profiled and searched as their own Pokémon (their typing,
    stats and ability all differ), and come back flagged `is_mega` with the
    `mega_stone` to hold; their `usage_rank` is the base species'. Set
    include_megas=False to search base forms only."""
    return profiles.search_pokemon_by_profile(
        _db(), tags, match, format, limit, include_megas)


# ------------------------------- Roles -------------------------------

@mcp.tool()
async def list_roles(
    category: str | None = None, format: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """The competitive role vocabulary — what each slot on a team is *for*
    (offensive pivot, physical wallbreaker, redirection support, Trick Room
    setter…), grouped by category with a viability grade per format.

    **Pass format='vgc' or 'bss'.** Roles are not transferable between the two:
    redirection (Follow Me) and spread attackers are core in VGC doubles and do
    literally nothing in BSS singles, while revenge killers and defensive pivots
    are core in singles and marginal in doubles. With a format set, unviable
    roles are moved to `excluded_for_this_format` with the reason.

    Optional `category` (offense, defense, pivot, speed_control, support,
    hazards, lead) and `query` (name/alias substring) narrow the list. Then call
    get_role_description for the full brief on one role."""
    return roles_mod.list_roles(category, format, query)


@mcp.tool()
async def get_role_description(role: str, format: str | None = None) -> dict[str, Any]:
    """Full brief on one competitive role: what it does and why, the stat and
    typing signature to look for, its signature moves/items/abilities **checked
    against the Champions data**, Stat-Point guidance for the 66-SP budget, the
    roles it pairs with, and the roles that answer it.

    Anything under `not_in_champions` does not exist in this game — Champions'
    item pool is much smaller than other games': Choice Scarf is the only Choice
    item, and there is no Assault Vest, Heavy-Duty Boots, Covert Cloak or Safety
    Goggles. Bulk and power come from Stat Points and typing.

    Accepts an id, name or alias ('offensive pivot', 'scarfer', 'follow me
    user'); ambiguous input returns candidates. Pass format='vgc'|'bss' for an
    explicit verdict, including a warning when the role doesn't work there."""
    return roles_mod.get_role_description(_db(), role, format)


# ------------------------------- Meta --------------------------------

@mcp.tool()
async def get_usage_stats(
    format: str = "vgc", top: int = 30, order: str = "top", megas_only: bool = False,
) -> dict[str, Any]:
    """Usage ranking for VGC (doubles) or BSS (singles). order='top' (default)
    returns the most-used Pokémon first. order='bottom' returns the
    least-used Pokémon first — every legal Pokémon is ranked (no cutoff), so
    this is the correct way to find real off-meta/underdog picks instead of
    guessing from general knowledge. Set megas_only=True to restrict the
    ranking to species with a Mega Evolution (e.g. to find the best or most
    slept-on Mega)."""
    return usage.get_usage_stats(_db(), format, top, order, megas_only)


@mcp.tool()
async def get_pokemon_sets(name: str, format: str = "vgc") -> dict[str, Any]:
    """Evidence for how a Pokémon is played: most-common moves, items, abilities,
    natures, SP spreads and teammates with usage %, for the format.

    **These are per-slot popularity distributions, not ready-made sets.** Each
    list is computed independently across every build, so the top four moves may
    never have occurred together and the top spread may belong to a different
    build than the top item — pasting the top of each list yields a set nobody
    plays. Use it to see what is proven viable and which Speed benchmarks players
    tune for, then build the set your team actually needs. A set that is not in
    this list is often the right answer, as long as you justify it with
    calc_damage/compute_speed_threshold and confirm legality with get_pokemon_moves."""
    return usage.get_pokemon_sets(_db(), name, format)


@mcp.tool()
async def get_observed_sets(
    name: str, format: str = "vgc", item: str | None = None,
    with_teammate: str | None = None, limit: int = 6,
) -> dict[str, Any]:
    """**Complete builds real players actually brought** — the joint data
    `get_pokemon_sets` cannot give you. Use this before committing a set.

    Where get_pokemon_sets says "51% Life Orb" and "80% Earthquake" separately,
    this says what a *Life Orb* one runs versus what a *Choice Scarf* one runs —
    they are usually different sets with different teammates. Filter with `item`
    or `with_teammate` to condition further.

    Read the three bands rather than the top row. `locked` choices are near
    universal, so deviating is a real cost you should be able to name.
    `contested` slots are where strong players already disagree — that is your
    design space, decided by the matchups *your* team expects, not by which
    option is 3% more popular. `fringe` entries are rare but attested: a
    precedent for a sharp idea, not a mistake. `marginal_stacking_check` shows
    how few real builds run the "four most popular moves" set, which is the
    trap this tool exists to prevent.

    Evidence differs by format: VGC gives `full_set` (item+ability+nature+moves,
    from tournament open team sheets), BSS gives `team_and_item` (the ranked
    ladder publishes teams and held items but no moves) — so in BSS any moveset
    is your inference and must be justified, not presented as observed.

    When the real observed-team corpus is thin or empty for this Pokémon/format
    (e.g. right after a regulation rollover, before tournaments exist yet), the
    response also carries `historical_sets` — curated cross-game Smogon/
    Showdown sets, each flagged with whether its move/item/ability is even
    legal in Champions today. That is a fallback, not Champions evidence; see
    get_historical_sets to pull the same thing directly."""
    return builds.get_observed_sets(_db(), name, format, item, with_teammate, limit)


@mcp.tool()
async def get_historical_sets(
    name: str, format: str = "vgc", limit: int = 8,
) -> dict[str, Any]:
    """Curated cross-game sets for a Pokémon from Smogon/Pokémon Showdown —
    every generation and tier Smogon has ever published, filtered only to this
    format's battle type (doubles for vgc, singles for bss; the specific tier/
    generation inside that doesn't matter).

    **Not Champions tournament data** — Champions predates all of it, has a
    much smaller item pool, and a different movepool per species. Every set
    carries `legal_in_champions` (overall) and per-move/item/ability flags, so
    a build that doesn't translate is visible rather than silently offered as
    fact. `invested_stats` names which stats the original EV spread favoured —
    turn that into a real Stat Point spread with calc_stats/
    compute_speed_threshold rather than converting the EV numbers directly;
    Champions' 66-SP budget doesn't map 1:1 onto 510 EVs.

    Use this as a last resort when get_observed_sets/get_pokemon_sets have
    little or nothing — it's also surfaced automatically inside
    get_observed_sets when its real corpus is thin."""
    return historical_sets.get_historical_sets(_db(), name, format, limit)


@mcp.tool()
async def get_team_context(
    team: list[str], format: str = "vgc", limit: int = 12,
) -> dict[str, Any]:
    """Given the Pokémon already on your team, what real teams put next to that
    exact combination — conditioned on your whole core, not on one Pokémon.

    `affinity` is the number to read: it divides how often a partner joined
    *this* core by how often it appears format-wide, so a high value means it is
    genuinely drawn to your structure rather than merely popular.
    `underexplored_partners` are real but uncommon answers to the same core —
    the most promising place to differentiate from everyone else's version.

    A core with no matches is untried, not refuted: build it out with
    get_observed_sets per member and validate with calc_damage."""
    return builds.get_team_context(_db(), team, format, limit)


@mcp.tool()
async def get_sample_teams(
    format: str = "vgc", pokemon: str | None = None, limit: int = 3,
) -> dict[str, Any]:
    """Whole teams from the observed corpus with their event, record and source.

    Read these for *structure* — how speed control, the defensive backbone and
    the win condition fit together — then build your own around the matchups you
    expect. Copying a list also copies whatever it loses to, and everyone else
    already has the answers to it."""
    return builds.get_sample_teams(_db(), format, pokemon, limit)


@mcp.tool()
async def get_top_teammates(name: str, format: str = "vgc") -> dict[str, Any]:
    """Pokémon most frequently brought alongside this one, by usage. For the
    stronger version — partners conditioned on your *whole* core, with an
    affinity score and under-explored options — use get_team_context."""
    return usage.get_top_teammates(_db(), name, format)


@mcp.tool()
async def suggest_cores(format: str = "vgc", limit: int = 15) -> dict[str, Any]:
    """Most frequent Pokémon pairs. Uses real observed teams when the corpus is
    available (with the share of teams each pair appears on), otherwise falls
    back to co-occurrence across the usage teammate lists.

    A popular core is a known quantity to your opponents too — treat these as
    the field you are building against as much as options to join."""
    return usage.suggest_cores(_db(), format, limit)


@mcp.tool()
async def get_speed_tiers(format: str = "vgc", limit: int = 40) -> dict[str, Any]:
    """Speed tiers (base + neutral-max + positive-nature-max Lv-50 Speed) for the
    most-used Pokémon in the format. Anchor Speed investment against these.

    Species that are predominantly played as their Mega are tiered on the
    **Mega's** Speed and named accordingly (flagged with `as_mega`), because the
    base-form tier is a benchmark you never actually face — Mega Metagross is
    base 130 Speed against the base form's 90."""
    return usage.get_speed_tiers(_db(), format, limit)


# ----------------------- Stat Points & Speed -------------------------

def _base_stats(species: str) -> tuple[str, dict[str, int]]:
    """Resolve a species name to its display name and Champions Lv-50 base stats.

    Args:
        species: A Pokémon name, localized name, or slug.

    Returns:
        A tuple of (battle_name, base_stats_by_key).

    Raises:
        NameError: If `species` does not resolve to a roster entry.
    """
    slug = resolve(_db(), species)
    row = _db().get_pokemon_row(slug)
    if row is None:
        raise NameError(f"Unknown Pokemon: {species}")
    bs = row["base_stats"]
    return row["battle_name"], {
        "hp": bs["hp"], "attack": bs["attack"], "defense": bs["defense"],
        "special-attack": bs["special_attack"],
        "special-defense": bs["special_defense"], "speed": bs["speed"],
    }


@mcp.tool()
async def calc_stats(
    name: str, stat_points: dict[str, int] | None = None, nature: str = "serious",
) -> dict[str, Any]:
    """Compute all six final Champions stats (Lv 50, 31 IVs) for an SP spread +
    nature; validates the 66-total / 32-per-stat budget. Accepts localized names."""
    sp = normalize_spread(stat_points or {})
    chk = validate_spread(sp)
    try:
        name, base = _base_stats(name)
        nat = normalize_nature(nature)
    except (StatError, NameError) as exc:
        return {"error": str(exc)}
    final = {s: compute_stat(base[s], int(sp.get(s, 0)), s, nat) for s in ALL_STATS}
    return {
        "species": name, "nature": nat, "base_stats": base,
        "stat_points": {s: int(sp.get(s, 0)) for s in ALL_STATS},
        "final_stats": final,
        "nature_effect": {s: nature_effect(nat, s) for s in ALL_STATS},
        "spread_valid": chk.ok, "spread_violations": chk.violations,
    }


@mcp.tool()
async def compute_speed(
    name: str, speed_sp: int = 0, nature: str = "serious", stage: int = 0,
    choice_scarf: bool = False, tailwind: bool = False, paralyzed: bool = False,
) -> dict[str, Any]:
    """Effective in-battle Speed with modifiers: raw → stage → Choice Scarf (×1.5)
    → Tailwind (×2) → paralysis (×0.5), flooring each step."""
    try:
        name, base = _base_stats(name)
        nat = normalize_nature(nature)
    except (StatError, NameError) as exc:
        return {"error": str(exc)}
    raw = compute_stat(base["speed"], speed_sp, "speed", nat)
    eff = effective_speed(base["speed"], speed_sp, nat, stage=stage,
                          choice_scarf=choice_scarf, tailwind=tailwind, paralyzed=paralyzed)
    return {
        "species": name, "base_speed": base["speed"], "speed_sp": speed_sp,
        "nature": nat, "raw_speed": raw, "effective_speed": eff,
        "modifiers": {"stage": stage, "choice_scarf": choice_scarf,
                      "tailwind": tailwind, "paralyzed": paralyzed},
    }


@mcp.tool()
async def compute_speed_threshold(
    attacker: str, defender: str, defender_speed_sp: int = 0,
    defender_nature: str = "serious", attacker_natures: list[str] | None = None,
    tie_is_enough: bool = False, attacker_stage: int = 0,
    attacker_choice_scarf: bool = False, attacker_tailwind: bool = False,
    attacker_paralyzed: bool = False, defender_stage: int = 0,
    defender_choice_scarf: bool = False, defender_tailwind: bool = False,
    defender_paralyzed: bool = False,
) -> dict[str, Any]:
    """Minimum Speed SP for `attacker` to outspeed `defender` (per nature), with
    SP left from the 66 budget. Default natures: neutral + Jolly."""
    natures = tuple(attacker_natures or ["serious", "jolly"])
    try:
        a_name, a_base = _base_stats(attacker)
        d_name, d_base = _base_stats(defender)
        for n in natures:
            normalize_nature(n)
        normalize_nature(defender_nature)
    except (StatError, NameError) as exc:
        return {"error": str(exc)}
    target = effective_speed(
        d_base["speed"], defender_speed_sp, defender_nature, stage=defender_stage,
        choice_scarf=defender_choice_scarf, tailwind=defender_tailwind, paralyzed=defender_paralyzed)
    results = min_sp_to_outspeed(
        a_base["speed"], target, natures=natures, tie_is_enough=tie_is_enough,
        attacker_mods={"stage": attacker_stage, "choice_scarf": attacker_choice_scarf,
                       "tailwind": attacker_tailwind, "paralyzed": attacker_paralyzed})
    return {
        "attacker": a_name, "attacker_base_speed": a_base["speed"],
        "defender": d_name, "defender_base_speed": d_base["speed"],
        "defender_effective_speed": target,
        "goal": "tie-or-faster" if tie_is_enough else "strictly-faster",
        "speed_plus_natures": SPEED_PLUS_NATURES,
        "options": [
            {"nature": r.nature, "min_speed_sp": r.min_sp,
             "achievable": r.min_sp is not None, "resulting_speed": r.resulting_speed,
             "sp_left_for_other_stats": r.sp_remaining_budget}
            for r in results
        ],
    }


# ------------------------- Team analysis -----------------------------

@mcp.tool()
async def validate_team(team: list[dict[str, Any]], format: str = "vgc") -> dict[str, Any]:
    """**Call last.** Full legality report for a team: roster membership, legal
    movepool, Item/Species Clause, SP budget and natures. Each member:
    {"species", "item"?, "ability"?, "moves"?, "nature"?, "stat_points"?}."""
    return legality_mod.validate_team(_db(), team, format)


@mcp.tool()
async def analyze_team(team: list[dict[str, Any]], format: str = "vgc") -> dict[str, Any]:
    """Legality + meta context for a team: per-member types/BST/usage rank and the
    top meta threats not on the team.

    Also reports `dependency_gaps` — members whose plan needs a field effect
    nobody on the team provides (slow attackers with no Trick Room/Tailwind/Speed
    control, a weather-only ability with no weather setter, a terrain-boosted
    move with no terrain). **Fix these before presenting the team**: a mode does
    not exist until a teammate sets it, so an unmet dependency is a broken build,
    not a caveat to note. Singles teams also get `protect_in_singles`."""
    return teambuilder.analyze_team(_db(), team, format)


# --------------------------- Damage calc -----------------------------

@mcp.tool()
async def calc_damage(
    attacker: str, move: str, defender: str,
    attacker_ability: str | None = None, attacker_item: str | None = None,
    attacker_nature: str = "serious", attacker_stat_points: dict[str, int] | None = None,
    attacker_boosts: dict[str, int] | None = None, attacker_status: str | None = None,
    defender_ability: str | None = None, defender_item: str | None = None,
    defender_nature: str = "serious", defender_stat_points: dict[str, int] | None = None,
    defender_boosts: dict[str, int] | None = None, defender_status: str | None = None,
    weather: str | None = None, terrain: str | None = None,
    game_type: str = "doubles", helping_hand: bool = False,
    attacker_tailwind: bool = False, crit: bool = False, move_hits: int | None = None,
) -> dict[str, Any]:
    """Champions damage calc via @smogon/calc (gen-0 native mode), Lv 50 / 31 IVs.
    Pass Stat Points per side e.g. {"attack": 32, "speed": 32}. For Mega, pass the
    Mega Stone as the item. ALWAYS set game_type ("doubles"=VGC | "singles"=BSS).
    Returns rolls, %HP, KO chance and a description. Requires the champions-calc
    Docker image (or CHAMPIONS_MCP_CALC_HTTP)."""
    req = {
        "attacker": attacker, "move": move, "defender": defender,
        "attacker_ability": attacker_ability, "attacker_item": attacker_item,
        "attacker_nature": attacker_nature, "attacker_stat_points": attacker_stat_points,
        "attacker_boosts": attacker_boosts, "attacker_status": attacker_status,
        "defender_ability": defender_ability, "defender_item": defender_item,
        "defender_nature": defender_nature, "defender_stat_points": defender_stat_points,
        "defender_boosts": defender_boosts, "defender_status": defender_status,
        "weather": weather, "terrain": terrain, "game_type": game_type,
        "helping_hand": helping_hand, "attacker_tailwind": attacker_tailwind,
        "crit": crit, "move_hits": move_hits,
    }
    return await App.get().damage.calculate(req)


# ----------------------------- Export --------------------------------

@mcp.tool()
async def create_pokepaste(
    team: list[dict[str, Any]], title: str = "", author: str = "",
    notes: str = "", force: bool = False,
) -> dict[str, Any]:
    """Upload a finalised team to pokepast.es; returns {"url", "paste"}. Call after
    validate_team passes. SP appear on the EVs line (1 SP ≠ 4 EVs; budget 66).

    **Every member must be complete** — species, item, ability, nature, four
    moves and a full 66-SP spread. Incomplete teams are rejected with the
    per-member list of what is missing and nothing is published: a set with no
    ability or spread still renders as a valid-looking Showdown block, so the
    gap would be invisible to whoever imports it. Fill the gaps and call again
    rather than presenting a partial team as finished. `force=True` publishes
    anyway — only when the user explicitly asked for a partial paste."""
    return await pokepaste.create_pokepaste(_db(), team, title, author, notes, force)


# --------------------------- Resources -------------------------------

@mcp.resource("pokedex://champions")
def resource_roster() -> str:
    import json
    # Explicit high limit: this resource is the *whole* roster by contract, and
    # unlike a tool result it isn't squeezed through a client's per-call size
    # cap — so it must not inherit list_pokemon's conservative tool default.
    return json.dumps(roster.list_pokemon(_db(), limit=10_000))


def main() -> None:
    """Entry point for the `champions-mcp` console script.

    Reads `MCP_TRANSPORT` (`stdio` by default, or `streamable-http`/`sse`)
    and runs the server with that transport.

    Bind address comes from `FASTMCP_HOST`/`FASTMCP_PORT`. Those names outlived
    the 1.x FastMCP class they were named for, but they are the documented
    interface (docker-compose.yml, .env.example) so renaming them would break
    existing configs for nothing. They apply only to the HTTP transports —
    stdio has nothing to bind.
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    kwargs: dict[str, Any] = {}
    if transport in ("streamable-http", "sse"):
        kwargs = {
            "host": os.environ.get("FASTMCP_HOST", "127.0.0.1"),
            "port": int(os.environ.get("FASTMCP_PORT", "8000")),
        }
    mcp.run(transport=transport, **kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
