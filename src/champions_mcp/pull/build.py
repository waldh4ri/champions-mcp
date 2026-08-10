"""Parse the championsbattledata index into champions.sqlite, then enrich.

Run order:
  1. parse index -> pokemon / types / abilities / learnable_moves / usage /
     usage_rank / teammates  (the single source, no network beyond the index)
  2. PokeAPI enrichment       -> moves / abilities / items / type_chart + names
  3. write meta markers, report counts
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..cache import Cache
from ..config import Settings
from ..db import Db
from ..normalize import slugify
from . import builds as builds_mod
from .known_items import KNOWN_HOLDABLE_ITEMS
from .pokeapi import TYPES, PokeApiEnricher

# championsbattledata battle name -> @smogon/calc + Showdown species name.
# Only the 33 non-trivial form entries need an override; base entries already
# carry a valid Showdown name in their battle_name. Doubles as the pokepaste
# export name and the damage-calc species name.
SHOWDOWN_OVERRIDES: dict[str, str] = {
    "aegislash-shield-forme": "Aegislash",
    "alolan-ninetales": "Ninetales-Alola",
    "alolan-raichu": "Raichu-Alola",
    "basculegion-female": "Basculegion-F",
    "basculegion-male": "Basculegion",
    "florges-red-flower": "Florges",
    "furfrou-natural-form": "Furfrou",
    "galarian-slowbro": "Slowbro-Galar",
    "galarian-slowking": "Slowking-Galar",
    "galarian-stunfisk": "Stunfisk-Galar",
    "gourgeist-jumbo-variety": "Gourgeist-Super",
    "gourgeist-large-variety": "Gourgeist-Large",
    "gourgeist-small-variety": "Gourgeist-Small",
    "hisuian-arcanine": "Arcanine-Hisui",
    "hisuian-avalugg": "Avalugg-Hisui",
    "hisuian-decidueye": "Decidueye-Hisui",
    "hisuian-goodra": "Goodra-Hisui",
    "hisuian-samurott": "Samurott-Hisui",
    "hisuian-typhlosion": "Typhlosion-Hisui",
    "hisuian-zoroark": "Zoroark-Hisui",
    "lycanroc-dusk-form": "Lycanroc-Dusk",
    "lycanroc-midnight-form": "Lycanroc-Midnight",
    "meowstic-female": "Meowstic-F",
    "palafin-zero-form": "Palafin",
    "paldean-tauros-aqua-breed": "Tauros-Paldea-Aqua",
    "paldean-tauros-blaze-breed": "Tauros-Paldea-Blaze",
    "paldean-tauros-combat-breed": "Tauros-Paldea-Combat",
    "rotom-fan": "Rotom-Fan",
    "rotom-frost": "Rotom-Frost",
    "rotom-heat": "Rotom-Heat",
    "rotom-mow": "Rotom-Mow",
    "rotom-wash": "Rotom-Wash",
    "vivillon-fancy-pattern": "Vivillon-Fancy",
}

# index format folder -> internal format key
FORMAT_KEY = {"Doubles": "doubles", "Singles": "singles"}

_STAT_COLS = (
    "hp_points", "attack_points", "defense_points",
    "sp_atk_points", "sp_def_points", "speed_points",
)


def _base_stats(summary: dict[str, Any]) -> dict[str, int]:
    """Extract the six base stats from a raw index ``summary`` block."""
    bs = summary.get("baseStats", {})
    return {
        "hp": bs.get("hp", 0),
        "attack": bs.get("attack", 0),
        "defense": bs.get("defense", 0),
        "special_attack": bs.get("sp_attack", 0),
        "special_defense": bs.get("sp_defense", 0),
        "speed": bs.get("speed", 0),
    }


def _split_abilities(raw: str) -> list[str]:
    """Split a ``"AbilityA|AbilityB"``-style raw ability string into a clean list."""
    return [a.strip() for a in (raw or "").split("|") if a.strip()]


def parse_index(db: Db, index: dict[str, Any]) -> dict[str, Any]:
    """Populate every source-derived table from the raw championsbattledata index.

    Args:
        db: Open database handle with `SCHEMA` already applied.
        index: The parsed pokemon-index.json document.

    Returns:
        Collected enrichment keys for the PokeAPI pass: sorted ``moves``,
        ``items``, ``abilities``, ``species`` names, plus a ``name_to_slug``
        lookup used while parsing teammate rows.
    """
    pokemon = index["pokemon"]
    name_to_slug = {p["battleName"]: p["slug"] for p in pokemon}

    rows_pokemon: list[tuple] = []
    rows_types: list[tuple] = []
    rows_abilities: list[tuple] = []
    rows_moves: list[tuple] = []
    rows_usage: list[tuple] = []
    rows_rank: list[tuple] = []
    rows_teammates: list[tuple] = []

    move_names: set[str] = set()
    item_names: set[str] = set()
    ability_names: set[str] = set()
    base_species: set[str] = set()

    for p in pokemon:
        slug = p["slug"]
        battle_name = p["battleName"]
        summary = p["summary"]
        primary = summary["primary"]
        base_name = primary.get("pokemon_name") or battle_name
        showdown = SHOWDOWN_OVERRIDES.get(slug, battle_name)
        types = summary.get("types", [])
        base_stats = _base_stats(summary)
        bst = summary.get("baseStatTotal", sum(base_stats.values()))
        forms = summary.get("forms", [])

        battle = summary.get("battleSummary", {})
        rank_by_fmt: dict[str, int | None] = {"doubles": None, "singles": None}
        season = index.get("defaultSeason", "Current")
        season_block = battle.get(season, {})
        for raw_fmt, fmt in FORMAT_KEY.items():
            block = season_block.get(raw_fmt)
            if not block:
                continue
            for row in block.get("rows", []):
                cat = row.get("category")
                rank = row.get("rank")
                cp = row.get("column_position")
                if rank_by_fmt[fmt] is None and cp is not None:
                    rank_by_fmt[fmt] = cp
                nm = row.get("name") or ""
                nm_slug = slugify(nm) if nm else None
                rows_usage.append((
                    slug, fmt, cat, rank, nm or None, nm_slug,
                    row.get("percentage") or None, row.get("percentage_value"),
                    row.get("stat_up") or None, row.get("stat_down") or None,
                    row.get("hp_points"), row.get("attack_points"),
                    row.get("defense_points"), row.get("sp_atk_points"),
                    row.get("sp_def_points"), row.get("speed_points"),
                ))
                if cat == "held_item" and nm:
                    item_names.add(nm)
                if cat == "teammate" and nm:
                    tslug = name_to_slug.get(nm, slugify(nm))
                    rows_teammates.append((slug, fmt, rank, nm, tslug))
            if rank_by_fmt[fmt] is not None:
                rows_rank.append((fmt, rank_by_fmt[fmt], slug))

        rows_pokemon.append((
            slug, battle_name, base_name, showdown, primary.get("form_kind"),
            json.dumps(types), json.dumps(_split_abilities(primary.get("abilities", ""))),
            json.dumps(base_stats), bst, json.dumps(forms),
            summary.get("sprite"), "{}", rank_by_fmt["doubles"], rank_by_fmt["singles"],
        ))
        for t in types:
            rows_types.append((slug, t))

        # abilities across primary + every form (captures Mega-only abilities)
        seen_ab: set[tuple[str, str]] = set()
        for f in [primary, *forms]:
            fname = f.get("form_name") or battle_name
            for ab in _split_abilities(f.get("abilities", "")):
                ability_names.add(ab)
                key = (slugify(ab), fname)
                if key in seen_ab:
                    continue
                seen_ab.add(key)
                rows_abilities.append((slug, slugify(ab), ab, fname))
            ha = f.get("hidden_ability")
            if ha:
                ability_names.add(ha)
                key = (slugify(ha), fname)
                if key not in seen_ab:
                    seen_ab.add(key)
                    rows_abilities.append((slug, slugify(ha), ha, fname))

        for mv in p.get("learnableMoveNames", []):
            move_names.add(mv)
            rows_moves.append((slug, slugify(mv), mv))

        base_species.add(slugify(base_name))

    db.executemany(
        "INSERT INTO pokemon VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_pokemon
    )
    db.executemany("INSERT INTO pokemon_types VALUES (?,?)", rows_types)
    db.executemany("INSERT INTO pokemon_abilities VALUES (?,?,?,?)", rows_abilities)
    db.executemany("INSERT INTO learnable_moves VALUES (?,?,?)", rows_moves)
    db.executemany(
        "INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_usage
    )
    db.executemany("INSERT INTO usage_rank VALUES (?,?,?)", rows_rank)
    db.executemany("INSERT INTO teammates VALUES (?,?,?,?,?)", rows_teammates)

    # championsbattledata only reports each Pokémon's top-10 held items per
    # format, so items that never crack a top-10 (e.g. status berries) are
    # missing from usage-derived item_names. Fill in the rest of the known
    # holdable item pool so search_items/get_item cover every real item.
    item_names.update(KNOWN_HOLDABLE_ITEMS)

    return {
        "moves": sorted(move_names),
        "items": sorted(item_names),
        "abilities": sorted(ability_names),
        "species": sorted(base_species),
        "name_to_slug": name_to_slug,
    }


async def enrich(db: Db, settings: Settings, keys: dict[str, Any]) -> None:
    """Fetch reference text + type chart from PokeAPI into the DB.

    Args:
        db: Open database handle, already populated by `parse_index`.
        settings: Runtime settings (PokeAPI base URL, HTTP cache path).
        keys: The enrichment keys returned by `parse_index` (moves, items,
            abilities, species).
    """
    cache = Cache(settings.cache_db)
    api = PokeApiEnricher(settings, cache)
    try:
        # moves
        moves = await api.gather([api.move(n) for n in keys["moves"]])
        db.executemany(
            "INSERT OR REPLACE INTO moves VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(m["slug"], m["name"], m.get("type"), m.get("damage_class"),
              m.get("power"), m.get("accuracy"), m.get("pp"), m.get("priority"),
              m.get("target"), m.get("effect")) for m in moves],
        )
        # abilities
        abils = await api.gather([api.ability(n) for n in keys["abilities"]])
        db.executemany(
            "INSERT OR REPLACE INTO abilities VALUES (?,?,?,?)",
            [(a["slug"], a["name"], a.get("effect"), a.get("short_effect")) for a in abils],
        )
        # items
        items = await api.gather([api.item(n) for n in keys["items"]])
        db.executemany(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)",
            [(i["slug"], i["name"], i.get("category"), i.get("effect"),
              i.get("is_mega_stone", 0)) for i in items],
        )
        # type chart (offensive multipliers, expanded to all 18 defenders)
        chart_rows: list[tuple] = []
        rels = await api.gather([api.type_relations(t) for t in TYPES])
        for atk, rel in zip(TYPES, rels):
            for dfn in TYPES:
                mult = 1.0
                if dfn in rel["no"]:
                    mult = 0.0
                elif dfn in rel["double"]:
                    mult = 2.0
                elif dfn in rel["half"]:
                    mult = 0.5
                chart_rows.append((atk.capitalize(), dfn.capitalize(), mult))
        db.executemany(
            "INSERT OR REPLACE INTO type_chart VALUES (?,?,?)", chart_rows
        )
        # localized species names -> attach to each pokemon sharing the base
        species_names = await api.gather(
            [api.species_names(s) for s in keys["species"]]
        )
        names_by_species = dict(zip(keys["species"], species_names))
        updates: list[tuple] = []
        for row in db.query("SELECT slug, base_name FROM pokemon"):
            names = names_by_species.get(slugify(row["base_name"]), {})
            updates.append((json.dumps(names), row["slug"]))
        db.executemany("UPDATE pokemon SET names_json = ? WHERE slug = ?", updates)
    finally:
        await api.aclose()
        cache.close()


def _stat_dict(f: dict[str, Any]) -> dict[str, int | None]:
    """Extract the six base stats from a raw ``forms_json`` form entry."""
    return {
        "hp": f.get("hp"), "attack": f.get("attack"), "defense": f.get("defense"),
        "special_attack": f.get("sp_attack"), "special_defense": f.get("sp_defense"),
        "speed": f.get("speed"),
    }


_MEGA_STONE_EFFECT_RE = re.compile(r"Mega Evolve into (Mega .+?)\.\s*$")

# Champions-exclusive Mega Stones with no PokeAPI entry, so there's no effect
# text to parse the target species from (see build_mega_forms). Verified by
# name against https://www.serebii.net/pokemonchampions/items.shtml.
CUSTOM_MEGA_STONES: dict[str, str] = {
    "barbaracite": "Mega Barbaracle",
    "chandelurite": "Mega Chandelure",
    "chesnaughtite": "Mega Chesnaught",
    "chimechite": "Mega Chimecho",
    "clefablite": "Mega Clefable",
    "crabominite": "Mega Crabominable",
    "delphoxite": "Mega Delphox",
    "dragalgite": "Mega Dragalge",
    "dragoninite": "Mega Dragonite",
    "drampanite": "Mega Drampa",
    "eelektrossite": "Mega Eelektross",
    "emboarite": "Mega Emboar",
    "excadrite": "Mega Excadrill",
    "falinksite": "Mega Falinks",
    "feraligite": "Mega Feraligatr",
    "floettite": "Mega Floette",
    "froslassite": "Mega Froslass",
    "glimmoranite": "Mega Glimmora",
    "golurkite": "Mega Golurk",
    "greninjite": "Mega Greninja",
    "hawluchanite": "Mega Hawlucha",
    "malamarite": "Mega Malamar",
    "meganiumite": "Mega Meganium",
    "meowsticite": "Mega Meowstic",
    "pyroarite": "Mega Pyroar",
    "raichunite-x": "Mega Raichu X",
    "raichunite-y": "Mega Raichu Y",
    "scolipite": "Mega Scolipede",
    "scovillainite": "Mega Scovillain",
    "scraftinite": "Mega Scrafty",
    "skarmorite": "Mega Skarmory",
    "staraptite": "Mega Staraptor",
    "starminite": "Mega Starmie",
    "victreebelite": "Mega Victreebel",
}


def build_mega_forms(db: Db) -> int:
    """Derive one row per Mega form (types/ability/stats + stat changes vs. the
    base form + the Mega Stone that unlocks it), so search-at-serve-time never
    has to walk ``pokemon.forms_json`` or cross-reference usage stats.

    A Mega form's ``forms_json`` blob is duplicated verbatim across every
    battle entry that shares its species family (e.g. both ``raichu`` and
    ``alolan-raichu`` carry "Mega Raichu X"/"Y"), so this groups by the exact
    Mega ``form_name`` first to dedupe.

    The Mega Stone is resolved by name, not by held-item usage stats — usage
    rows are noisy (a Pokémon's top-10 held items sometimes include another
    species' stone entirely, e.g. Charizard's usage rows list Raichunite Y),
    and can't disambiguate an X/Y pair anyway. Canonical stones carry a
    PokeAPI effect string ("Held: Allows Charizard to Mega Evolve into Mega
    Charizard X.") that names the exact target form; Champions-exclusive
    stones without a PokeAPI entry fall back to ``CUSTOM_MEGA_STONES``.
    Requires pokemon/items to already be populated (needs
    ``items.is_mega_stone`` + ``items.effect``, set during PokeAPI enrichment).

    Args:
        db: Open database handle, with `pokemon` and `items` already populated.

    Returns:
        The number of mega_forms rows written.
    """
    forms_by_name: dict[str, dict[str, Any]] = {}
    for row in db.query("SELECT slug, form_kind, forms_json FROM pokemon"):
        forms = json.loads(row["forms_json"])
        base = next((f for f in forms if f.get("form_kind") == "Base"), None)
        if not base:
            continue
        for f in forms:
            if not (f.get("form_kind") or "").startswith("Mega"):
                continue
            name = f.get("form_name")
            if name not in forms_by_name:
                forms_by_name[name] = {"form": f, "base": base, "species_slug": row["slug"]}
            elif row["form_kind"] == "Base":
                forms_by_name[name]["species_slug"] = row["slug"]

    stone_for_form: dict[str, str] = {}
    for r in db.query("SELECT slug, name, effect FROM items WHERE is_mega_stone = 1"):
        m = _MEGA_STONE_EFFECT_RE.search(r["effect"] or "")
        target = m.group(1) if m else CUSTOM_MEGA_STONES.get(r["slug"])
        if target:
            stone_for_form[target] = r["name"]

    rows_out: list[tuple] = []
    for name, entry in forms_by_name.items():
        f, base = entry["form"], entry["base"]
        base_stats = _stat_dict(base)
        mega_stats = _stat_dict(f)
        stat_changes = {
            k: mega_stats[k] - base_stats[k]
            for k in mega_stats
            if mega_stats.get(k) is not None and base_stats.get(k) is not None
        }
        bst = f.get("base_stat_total")
        base_bst = base.get("base_stat_total")
        bst_change = bst - base_bst if bst is not None and base_bst is not None else None
        rows_out.append((
            name, entry["species_slug"], base.get("form_name"), f.get("form_kind"),
            stone_for_form.get(name),
            json.dumps(f.get("types", [])),
            "|".join(a for a in (f.get("abilities", "") or "").split("|") if a),
            json.dumps(mega_stats), bst,
            json.dumps(stat_changes), bst_change,
        ))
    db.executemany(
        "INSERT OR REPLACE INTO mega_forms VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows_out
    )
    return len(rows_out)


async def build(
    db: Db, settings: Settings, index: dict[str, Any], *, with_builds: bool = True,
) -> dict[str, int]:
    """Run the full build: reset tables, parse the index, enrich, derive Megas,
    then ingest the observed-team corpus.

    Args:
        db: Open database handle.
        settings: Runtime settings, forwarded to `enrich`.
        index: The parsed pokemon-index.json document.
        with_builds: Fetch observed teams from Limitless/pokedb. Everything is
            cached, so a rebuild is cheap; pass False to skip the stage entirely.

    Returns:
        Row counts per table, also written to the `meta` table.
    """
    db.reset_build_tables()
    keys = parse_index(db, index)
    await enrich(db, settings, keys)
    build_mega_forms(db)
    if with_builds:
        # Depends on pokemon (Japanese names), items and mega_forms already
        # being populated — it resolves both upstreams against them.
        stats = await builds_mod.ingest_builds(db, settings)
        db.set_meta("builds", stats)
    db.set_meta("season", index.get("defaultSeason", "Current"))
    db.set_meta("data_version", index.get("dataVersion", ""))
    db.set_meta("generated_at", index.get("generatedAt", ""))
    db.set_meta("seasons", index.get("seasons", []))
    counts = {t: db.count(t) for t in (
        "pokemon", "learnable_moves", "usage", "moves", "abilities",
        "items", "mega_forms", "type_chart", "teammates",
        "build_teams", "build_members",
    )}
    db.set_meta("counts", counts)
    return counts
