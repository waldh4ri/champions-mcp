"""Export a finished team to pokepast.es (the only runtime outbound call).

Species are rendered with their Showdown name (``pokemon.showdown_name``);
item / ability / move names are canonicalized from the reference tables when
known. Champions Stat Points are written on the EVs line (1 SP ≠ 4 EVs).
"""

from __future__ import annotations

from typing import Any

from .champions_stats import (
    ALL_STATS, MAX_TOTAL_SP, looks_like_evs, member_stat_points,
)
from .db import Db
from .names import try_resolve

_SP_ABBREV = {
    "hp": "HP", "attack": "Atk", "defense": "Def",
    "special-attack": "SpA", "special-defense": "SpD", "speed": "Spe",
}


def _title(s: str) -> str:
    return " ".join(p.capitalize() for p in s.replace("_", " ").replace("-", " ").split())


def _species_name(db: Db, species: str) -> str:
    slug = try_resolve(db, species)
    if slug:
        row = db.get_pokemon_row(slug)
        if row:
            return row["showdown_name"]
    return _title(species)


def _item_name(db: Db, item: str) -> str:
    it = db.get_item(item)
    return it["name"] if it else _title(item)


def _move_name(db: Db, move: str) -> str:
    m = db.get_move(move)
    return m["name"] if m else _title(move)


def _ability_name(db: Db, ability: str) -> str:
    a = db.get_ability(ability)
    return a["name"] if a else _title(ability)


def member_to_showdown(db: Db, m: dict[str, Any]) -> str:
    """Render one team member as a Showdown-format text block.

    Args:
        db: Open Champions database handle.
        m: A team member dict (species, item, ability, moves, etc.).

    Returns:
        The member's Showdown-format lines joined with ``\\r\\n``.
    """
    lines: list[str] = []
    species = _species_name(db, m.get("species") or "Unknown")
    nickname = m.get("nickname")
    first = f"{nickname} ({species})" if nickname else species
    if m.get("item"):
        first += f" @ {_item_name(db, m['item'])}"
    lines.append(first)

    if m.get("ability"):
        lines.append(f"Ability: {_ability_name(db, m['ability'])}")
    if m.get("tera_type"):
        lines.append(f"Tera Type: {_title(m['tera_type'])}")
    level = m.get("level", 50)
    if level != 100:
        lines.append(f"Level: {level}")

    sp = member_stat_points(m)
    ev_parts = [f"{sp[s]} {_SP_ABBREV[s]}" for s in ALL_STATS if sp.get(s)]
    if ev_parts:
        lines.append("EVs: " + " / ".join(ev_parts))
    if m.get("nature"):
        lines.append(f"{_title(m['nature'])} Nature")
    for move in (m.get("moves") or [])[:4]:
        lines.append(f"- {_move_name(db, move)}")

    # pokepast.es splits members on \r\n\r\n and lines on \r\n
    return "\r\n".join(lines)


def team_to_showdown(db: Db, team: list[dict[str, Any]]) -> str:
    """Render a full team as Showdown-format text, one block per member.

    Args:
        db: Open Champions database handle.
        team: The full list of team members.

    Returns:
        The complete Showdown-format paste text.
    """
    return "\r\n\r\n".join(member_to_showdown(db, m) for m in team)


# An exported paste is the deliverable — someone imports it and plays it. A
# member missing its ability, nature or spread still renders as a valid-looking
# Showdown block, so the omission is invisible in the output and silently
# becomes a default in-game. Refuse to publish an incomplete team instead.
_REQUIRED_FIELDS = ("species", "ability", "item", "nature", "moves")


def check_team_complete(team: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-member list of what a paste-ready set is still missing.

    Args:
        team: The full list of team members.

    Returns:
        One ``{"member", "missing"}`` entry per member with gaps; empty if
        every member is paste-ready.
    """
    problems: list[dict[str, Any]] = []
    for i, m in enumerate(team or [], start=1):
        label = m.get("species") or f"member {i}"
        missing: list[str] = []
        for field in _REQUIRED_FIELDS:
            if not m.get(field):
                missing.append(field)
        moves = [x for x in (m.get("moves") or []) if x]
        if moves and len(moves) < 4:
            missing.append(f"moves ({len(moves)}/4 given)")
        sp = member_stat_points(m)
        if not sp:
            missing.append("stat_points")
        else:
            try:
                total = sum(int(v) for v in sp.values())
            except (TypeError, ValueError):
                total = 0
            if looks_like_evs(sp):
                missing.append(
                    "stat_points look like EVs (a value above the 32 per-stat "
                    f"cap) — Champions uses Stat Points: {MAX_TOTAL_SP} total, "
                    "32 per stat, 1 SP = +1 stat"
                )
            elif total < MAX_TOTAL_SP:
                missing.append(
                    f"stat_points only spend {total}/{MAX_TOTAL_SP} — the rest "
                    "is wasted, assign it"
                )
        if missing:
            problems.append({"member": label, "missing": missing})
    return problems


async def create_pokepaste(
    db: Db, team: list[dict[str, Any]],
    title: str = "", author: str = "", notes: str = "", force: bool = False,
) -> dict[str, Any]:
    """Publish a team to pokepast.es as a Showdown-format paste.

    Refuses to publish an incomplete team (see :func:`check_team_complete`)
    unless ``force`` is set, since a missing field still renders as a
    valid-looking set and would silently become an in-game default.

    Args:
        db: Open Champions database handle.
        team: The full list of team members.
        title: Optional paste title.
        author: Optional paste author.
        notes: Optional paste notes; a Champions SP note is appended.
        force: Publish even if the team is incomplete.

    Returns:
        ``{"url", "paste"}`` on success, or ``{"error": ...}`` (with
        ``incomplete_members`` when the team failed the completeness check).
    """
    import httpx

    if not team:
        return {"error": "No team given — pass the full list of members."}
    problems = [] if force else check_team_complete(team)
    if problems:
        return {
            "error": "Team is incomplete — nothing was published.",
            "incomplete_members": problems,
            "next_step": (
                "Fill in every missing field and call create_pokepaste again. A "
                "paste is the finished deliverable: a member with no ability, "
                "nature or spread still renders as a valid-looking set, so the "
                "gap would be invisible to whoever imports it and would silently "
                "become an in-game default. Do not hand over a partial team, and "
                "do not describe it as final. Use get_pokemon_sets / "
                "get_ability / list_items / calc_stats to fill the gaps, then "
                "re-run validate_team."
            ),
            "override": "Pass force=True only if the user explicitly asked for a "
                        "deliberately partial paste.",
        }

    paste_text = team_to_showdown(db, team)
    sp_note = "(Champions SP ≠ EVs: 1 SP = +1 stat at Lv 50, budget 66)"
    full_notes = f"{notes}\n{sp_note}".strip() if notes else sp_note
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
            resp = await client.post(
                "https://pokepast.es/create",
                data={"paste": paste_text, "title": title, "author": author, "notes": full_notes},
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
