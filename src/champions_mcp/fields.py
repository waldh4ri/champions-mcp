"""Field effects: what weather, terrain, rooms and screens actually do.

The move and ability text in the data says only "Summons snow for 5 turns" —
nothing about what snow *does*. Without a lookup a model reasons the mechanics
out from memory of older games (hail chip damage, the old Ice SpA boost), which
is exactly where its threat assessments went wrong. These are the current
(Gen 9) rules, the ones the Champions damage calc applies; durations and
extender items come from the Champions data itself.
"""

from __future__ import annotations

from typing import Any

from .db import Db
from .normalize import slugify

_FIELDS: dict[str, dict[str, Any]] = {
    "sun": {
        "kind": "weather", "calc_damage": "weather='sun'",
        "set_by": {"abilities": ["Drought", "Mega Sol (Mega Meganium)"], "moves": ["Sunny Day"]},
        "extender": "Heat Rock",
        "effects": [
            "Fire-type moves x1.5, Water-type moves x0.5.",
            "Solar Beam / Solar Blade need no charge turn.",
            "Thunder and Hurricane accuracy drops to 50%.",
            "Chlorophyll doubles Speed; Solar Power raises SpA x1.5 (holder loses 1/8 HP per turn).",
        ],
    },
    "rain": {
        "kind": "weather", "calc_damage": "weather='rain'",
        "set_by": {"abilities": ["Drizzle"], "moves": ["Rain Dance"]},
        "extender": "Damp Rock",
        "effects": [
            "Water-type moves x1.5, Fire-type moves x0.5.",
            "Thunder and Hurricane never miss.",
            "Swift Swim doubles Speed.",
        ],
    },
    "sand": {
        "kind": "weather", "calc_damage": "weather='sand'",
        "set_by": {"abilities": ["Sand Stream"], "moves": ["Sandstorm"]},
        "extender": "Smooth Rock",
        "effects": [
            "Rock-type Pokémon get SpD x1.5.",
            "Non-Rock/Ground/Steel Pokémon lose 1/16 max HP at end of each turn.",
            "Sand Rush doubles Speed; Sand Force boosts Rock/Ground/Steel moves x1.3.",
        ],
    },
    "snow": {
        "kind": "weather", "calc_damage": "weather='snow'",
        "set_by": {"abilities": ["Snow Warning"], "moves": ["Snowscape"]},
        "extender": "Icy Rock",
        "effects": [
            "Ice-type Pokémon get Defense x1.5.",
            "No end-of-turn damage (snow is not the old hail), and no Ice-move power boost.",
            "Blizzard never misses; Aurora Veil can be used.",
            "Slush Rush doubles Speed.",
        ],
    },
    "electric-terrain": {
        "kind": "terrain", "calc_damage": "terrain='electric'",
        "set_by": {"abilities": ["Electric Surge"], "moves": ["Electric Terrain"]},
        "extender": "Terrain Extender",
        "effects": [
            "Electric-type moves x1.3 when the user is grounded.",
            "Grounded Pokémon cannot fall asleep.",
            "Rising Voltage doubles in power against a grounded target.",
        ],
    },
    "grassy-terrain": {
        "kind": "terrain", "calc_damage": "terrain='grassy'",
        "set_by": {"abilities": ["Grassy Surge"], "moves": ["Grassy Terrain"]},
        "extender": "Terrain Extender",
        "effects": [
            "Grass-type moves x1.3 when the user is grounded.",
            "Grounded Pokémon recover 1/16 max HP each turn.",
            "Earthquake and Bulldoze deal x0.5 to grounded targets.",
            "Grassy Glide gains +1 priority.",
        ],
    },
    "psychic-terrain": {
        "kind": "terrain", "calc_damage": "terrain='psychic'",
        "set_by": {"abilities": ["Psychic Surge"], "moves": ["Psychic Terrain"]},
        "extender": "Terrain Extender",
        "effects": [
            "Psychic-type moves x1.3 when the user is grounded.",
            "Grounded Pokémon cannot be hit by priority moves — including your own team's Fake Out / Sucker Punch / Prankster on grounded targets.",
            "Expanding Force x1.5 (and hits both foes in doubles).",
        ],
    },
    "misty-terrain": {
        "kind": "terrain", "calc_damage": "terrain='misty'",
        "set_by": {"abilities": ["Misty Surge"], "moves": ["Misty Terrain"]},
        "extender": "Terrain Extender",
        "effects": [
            "Dragon-type moves deal x0.5 to grounded targets.",
            "Grounded Pokémon cannot be statused or confused — including by your own team's sleep/paralysis moves.",
        ],
    },
    "trick-room": {
        "kind": "room", "calc_damage": "n/a (turn order only)",
        "set_by": {"moves": ["Trick Room"]},
        "effects": [
            "5 turns (the turn it is set counts). Within a priority bracket the slower Pokémon moves first.",
            "Trick Room itself has -7 priority, so the setter moves last on the turn it sets it.",
            "Priority moves still go first; using Trick Room again while it is up ends it.",
        ],
    },
    "tailwind": {
        "kind": "side", "calc_damage": "attacker_tailwind / compute_speed(tailwind=True)",
        "set_by": {"moves": ["Tailwind"]},
        "effects": [
            "Doubles the Speed of the user's side for 4 turns (the turn it is set counts).",
        ],
    },
    "screens": {
        "kind": "side", "calc_damage": "not modelled — calc_damage reports damage without screens",
        "set_by": {"moves": ["Reflect", "Light Screen", "Aurora Veil (only in snow)"]},
        "extender": "Light Clay",
        "effects": [
            "Reflect halves physical damage, Light Screen halves special damage, Aurora Veil does both — in singles. In doubles each is about x0.67 instead.",
            "5 turns (8 with Light Clay). Critical hits ignore screens.",
        ],
    },
}

_ALIASES = {
    "harsh-sunlight": "sun", "sunny-day": "sun", "drought": "sun",
    "rain-dance": "rain", "drizzle": "rain",
    "sandstorm": "sand", "sand-stream": "sand",
    "hail": "snow", "snowscape": "snow", "snow-warning": "snow",
    "electric": "electric-terrain", "grassy": "grassy-terrain", "grass": "grassy-terrain",
    "psychic": "psychic-terrain", "misty": "misty-terrain",
    "reflect": "screens", "light-screen": "screens", "aurora-veil": "screens",
    "weather": None, "terrain": None,
}


def get_field_effects(db: Db, name: str | None = None) -> dict[str, Any]:
    """Mechanics of a weather, terrain, Trick Room, Tailwind or screens — or
    all of them when ``name`` is omitted.

    Args:
        db: Open Champions database handle (extender item text comes from it).
        name: A field effect, its setting move or ability (e.g. "snow",
            "Snowscape", "Snow Warning", "grassy", "Trick Room").

    Returns:
        ``{"effects": {...}}`` keyed by field effect, or ``{"error": ...}``.
    """
    keys = list(_FIELDS)
    if name:
        key = slugify(name)
        key = _ALIASES.get(key, key)
        kind = slugify(name) if slugify(name) in ("weather", "terrain") else None
        if kind:
            keys = [k for k in _FIELDS if _FIELDS[k]["kind"] == kind]
        elif key in _FIELDS:
            keys = [key]
        else:
            return {"error": f"Unknown field effect {name!r}. Known: {list(_FIELDS)}"}
    out = {}
    for k in keys:
        e = dict(_FIELDS[k])
        if e.get("extender"):
            item = db.query_one("SELECT effect FROM items WHERE name = ?", (e["extender"],))
            e["extender"] = {"item": e["extender"], "effect": item["effect"] if item else None}
        out[k] = e
    return {
        "effects": out,
        "source": ("Current-generation rules as applied by the Champions damage calc; "
                   "durations and extender items from the Champions data. Quote these "
                   "rather than mechanics remembered from older games."),
    }
