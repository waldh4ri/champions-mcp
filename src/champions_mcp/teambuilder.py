"""Team analysis: legality + meta context (usage benchmarks, top threats)."""

from __future__ import annotations

from typing import Any

from . import profiles
from .champions_stats import (
    SPEED_MINUS_NATURES, SPEED_PLUS_NATURES, member_stat_points,
)
from .db import Db
from .formats import game_type, resolve_format
from .legality import validate_team
from .names import try_resolve
from .normalize import slugify
from .usage import mega_usage_index


# Protect is close to mandatory in doubles and a trap in singles, and the
# Champions usage data says so plainly: in doubles 224 of 235 species run it
# (60% average among top-60 users, many above 90%); in singles only 42 species
# run it at all, 13 of the top 60, averaging 16%. The singles outliers are the
# real exceptions — a Speed Boost attacker banking a turn, or a recovery staller
# scaling passive damage — not a default fourth move.
_PROTECT_MOVES = {"protect", "detect", "spiky-shield", "baneful-bunker", "burning-bulwark"}

_PROTECT_BSS_WARNING = (
    "In BSS (singles) Protect is usually a wasted turn: with one Pokémon per "
    "side it buys no partner action, and spending a turn of a 3-Pokémon game to "
    "do nothing hands the opponent free setup or free damage. Champions usage "
    "agrees — only 42 of 235 species run it in singles (13 of the top 60), "
    "versus 224 of 235 in doubles. Keep it ONLY for a concrete reason: banking "
    "a Speed Boost/accumulating-ability turn, scaling passive damage behind "
    "reliable recovery, safely scouting a Choice lock, or triggering a "
    "turn-based item. Otherwise spend the slot on coverage, utility or recovery."
)


def _protect_warnings(db: Db, team: list[dict[str, Any]], gt: str) -> list[dict[str, Any]]:
    """Flag Protect-style moves on singles teams (see _PROTECT_BSS_WARNING)."""
    if gt != "singles":
        return []
    flagged = []
    for m in team:
        moves = m.get("moves") or []
        hits = [mv for mv in moves if slugify(str(mv)) in _PROTECT_MOVES]
        if hits:
            flagged.append({"member": m.get("species"), "moves": hits})
    return flagged


# A "mode" (Trick Room, Tailwind, weather, terrain) is not a property a team can
# assume — it exists only while a teammate is putting it there. A slow attacker
# on a team with no speed control simply moves second every turn; a Swift Swim
# body with no rain is just slow. These checks catch the gap mechanically,
# because a plan that depends on an effect nobody provides reads as coherent
# prose right up until the battle starts.
_TRICK_ROOM = "trick-room"
_TAILWIND = "tailwind"
_SPEED_DROP_MOVES = {"icy-wind", "electroweb", "thunder-wave", "sticky-web",
                     "bulldoze", "rock-tomb", "glare", "string-shot", "scary-face"}
_WEATHER_ABILITIES = {"drizzle", "drought", "sand-stream", "snow-warning"}
_WEATHER_MOVES = {"rain-dance", "sunny-day", "sandstorm", "snowscape", "hail"}
_WEATHER_ABUSER_ABILITIES = {"swift-swim", "chlorophyll", "sand-rush",
                             "slush-rush", "solar-power", "rain-dish", "dry-skin"}
_TERRAIN_ABILITIES = {"electric-surge", "grassy-surge", "psychic-surge", "misty-surge"}
_TERRAIN_MOVES = {"electric-terrain", "grassy-terrain", "psychic-terrain",
                  "misty-terrain"}
_TERRAIN_ABUSER_MOVES = {"rising-voltage", "expanding-force", "grassy-glide",
                         "misty-explosion", "psyblade"}


def _built_slow(member: dict[str, Any]) -> bool | None:
    """Is this member actually *built* slow? True/False, or None when the set
    doesn't say yet (no nature and no spread given)."""
    nature = slugify(str(member.get("nature") or ""))
    if nature:
        if nature in {slugify(n) for n in SPEED_MINUS_NATURES}:
            return True
        if nature in {slugify(n) for n in SPEED_PLUS_NATURES}:
            return False
    sp = member_stat_points(member)
    if sp:
        try:
            return int(sp.get("speed", 0) or 0) == 0
        except (TypeError, ValueError):
            return None
    return None if not nature else False


def _mode_dependency_gaps(
    db: Db, team: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find team members whose plan needs a field effect nobody on the team sets."""
    moves: set[str] = set()
    abilities: set[str] = set()
    for m in team:
        moves |= {slugify(str(x)) for x in (m.get("moves") or [])}
        if m.get("ability"):
            abilities.add(slugify(str(m["ability"])))

    has_tr = _TRICK_ROOM in moves
    has_tailwind = _TAILWIND in moves
    has_speed_drop = bool(moves & _SPEED_DROP_MOVES)
    has_weather = bool(abilities & _WEATHER_ABILITIES or moves & _WEATHER_MOVES)
    has_terrain = bool(abilities & _TERRAIN_ABILITIES or moves & _TERRAIN_MOVES)

    gaps: list[dict[str, Any]] = []

    # Slow attackers with no speed control of any kind. The `build:trick-room`
    # band is the roster's bottom quartile, which includes Pokémon that can
    # still be built fast — so only flag a member when its *actual build* is
    # slow (minus-Speed nature or no Speed SP), or when it is so slow it could
    # never win a race however it is built.
    stranded = []
    for m in team:
        slug = try_resolve(db, m.get("species") or "")
        if not slug:
            continue
        prof = profiles.pokemon_profile(db, slug)
        tags = prof.get("tags", [])
        if "build:trick-room" not in tags:
            continue
        if not any(t in tags for t in (
                "build:physical-attacker", "build:special-attacker",
                "build:mixed-attacker", "stat:atk-elite", "stat:spa-elite")):
            continue                      # a slow wall is not stranded
        slow_build = _built_slow(m)
        never_races = prof.get("stat_percentiles", {}).get("spe", 100) <= 10
        if slow_build or (slow_build is None and never_races):
            stranded.append(m.get("species"))
    if stranded and not (has_tr or has_tailwind or has_speed_drop):
        gaps.append({
            "gap": "speed_control",
            "members": stranded,
            "detail": (
                f"{', '.join(str(s) for s in stranded)} sit in the roster's "
                "slowest 10% but are built to attack, and no team member has "
                "Trick Room, Tailwind or a Speed-lowering move. They will move "
                "second every turn. A mode is only real if a teammate sets it — "
                "'assume Trick Room is up' is not a plan."
            ),
            "fix": "Add a Trick Room setter (search_pokemons_by_move('Trick Room')), or "
                   "another speed-control option, or replace these members with "
                   "faster ones.",
        })

    if abilities & _WEATHER_ABUSER_ABILITIES and not has_weather:
        gaps.append({
            "gap": "weather",
            "detail": (
                f"An ability that only works in weather "
                f"({', '.join(sorted(abilities & _WEATHER_ABUSER_ABILITIES))}) "
                "is on the team, but nobody sets weather. It does nothing."
            ),
            "fix": "Add a weather setter (search_pokemons_by_ability('Drizzle') etc.), "
                   "or pick an ability that works unsupported.",
        })

    if moves & _TERRAIN_ABUSER_MOVES and not has_terrain:
        gaps.append({
            "gap": "terrain",
            "detail": (
                f"{', '.join(sorted(moves & _TERRAIN_ABUSER_MOVES))} needs terrain "
                "to reach full power, but nobody on the team sets one."
            ),
            "fix": "Add a terrain setter, or accept the move's unboosted power.",
        })

    return gaps


def analyze_team(db: Db, team: list[dict[str, Any]], format: str = "vgc") -> dict[str, Any]:
    """Legality plus meta context: per-member usage/typing, top absent threats,
    a singles Protect check, and field-effect dependency gaps.

    Args:
        db: Open database handle.
        team: One dict per member, in the same shape `legality.validate_team` accepts.
        format: A format name/alias (see `formats.resolve_format`).

    Returns:
        A dict with `legal`, `legality` (the full `validate_team` report),
        `members`, `top_meta_threats_not_on_team`, and, when applicable,
        `protect_in_singles` and `dependency_gaps`.
    """
    fmt = resolve_format(format)
    gt = game_type(format)
    report = validate_team(db, team, fmt)

    members: list[dict[str, Any]] = []
    on_team: set[str] = set()
    for m in team:
        slug = try_resolve(db, m.get("species") or "")
        if not slug:
            members.append({"species": m.get("species"), "resolved": False})
            continue
        on_team.add(slug)
        row = db.get_pokemon_row(slug)
        rank = row.get("rank_doubles") if gt == "doubles" else row.get("rank_singles")
        members.append({
            "name": row["battle_name"], "types": row.get("types", []),
            "base_stat_total": row.get("bst"), "usage_rank": rank,
        })

    megas = mega_usage_index(db, gt)
    threats = []
    for r in db.usage_ranking(gt, 12):
        if r["slug"] in on_team:
            continue
        t = {"rank": r["rank"], "name": r["battle_name"], "slug": r["slug"]}
        m = megas.get(r["slug"])
        if m:
            # Checking the matchup against the base form is checking the wrong
            # Pokémon: it will be on the field as its Mega, with different
            # stats, typing and ability.
            t["faced_as"] = m["dominant"]["mega"]
            t["mega_usage_percent"] = m["mega_usage_percent"]
            t["mega_stone"] = m["dominant"]["stone"]
        threats.append(t)
        if len(threats) == 8:
            break

    note = ("Threats are the highest-usage Pokémon in the format absent from "
            "your team — check your matchup against each.")
    if any("faced_as" in t for t in threats):
        note += (
            " Threats with `faced_as` are met as that Mega, not the base form — "
            "run their calcs with `mega_stone` as the item (calc_damage "
            "auto-Mega-evolves) or your damage and speed numbers will be wrong."
        )

    out = {
        "format": fmt,
        "legal": report["legal"],
        "legality": report,
        "members": members,
        "top_meta_threats_not_on_team": threats,
        "note": note,
    }
    protect = _protect_warnings(db, team, gt)
    if protect:
        out["protect_in_singles"] = {
            "members": protect,
            "warning": _PROTECT_BSS_WARNING,
        }
    gaps = _mode_dependency_gaps(db, team)
    if gaps:
        out["dependency_gaps"] = {
            "gaps": gaps,
            "warning": (
                "This team depends on a field effect nobody provides. Treat each "
                "as a BUG TO FIX, not a caveat to report: a mode does not exist "
                "until a teammate sets it, so 'assume Trick Room is up' or "
                "'relies on Trick Room (implicit)' describes a team that does "
                "not work. Add the enabler or change the members, then re-run "
                "analyze_team — do not hand over a build with a gap still open."
            ),
        }
    return out
