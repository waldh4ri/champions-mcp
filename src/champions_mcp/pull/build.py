"""Parse pokechamp.gg into champions.sqlite, then enrich with PokeAPI.

Run order:
  1. parse pokechamp.gg -> pokemon / types / abilities / learnable_moves /
     moves / abilities / items (the current legal roster + the whole
     move/ability/item pool, from a handful of whole-game requests)
  2. one page fetch per non-Mega legal entry -> usage / usage_rank / teammates
     (the only pokechamp data that needs a per-entry request)
  3. build_mega_forms -> mega_forms, from the Mega/Z-Mega legal entries
     already parsed in step 1
  4. PokeAPI enrichment -> type_chart + localized species names (unrelated to
     which roster source is used; ``pull.builds``' pokedb ingestion resolves
     Japanese names against the latter)
  5. ingest_smogon_sets -> smogon_sets, a historical-builds fallback pool
     (independent of the observed-team corpus below)
  6. ingest_builds (Limitless + pokedb) -> build_teams / build_members
  7. write meta markers, report counts
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ..cache import Cache
from ..config import Settings
from ..db import Db
from ..normalize import slugify
from . import builds as builds_mod
from .pokeapi import TYPES, PokeApiEnricher
from .pokechamp_source import PokechampClient
from .smogon import ingest_smogon_sets

# pokechamp slug -> @smogon/calc + Showdown species name. Only entries whose
# Showdown name genuinely differs from pokechamp's own display name need one;
# a handful of the old championsbattledata overrides (Rotom's appliance
# formes, Aegislash "Shield Forme", Lycanroc "Dusk/Midnight Form", Florges
# "Red Flower", Furfrou "Natural Form", Palafin "Zero Form") don't apply here
# because pokechamp doesn't track those as separate legal slugs at all — the
# plain species slug already *is* the base/default form Showdown expects.
SHOWDOWN_OVERRIDES: dict[str, str] = {
    "alolan-ninetales": "Ninetales-Alola",
    "alolan-raichu": "Raichu-Alola",
    "basculegion-female": "Basculegion-F",
    "basculegion-male": "Basculegion",
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
    "meowstic-female": "Meowstic-F",
    "paldean-tauros-aqua-breed": "Tauros-Paldea-Aqua",
    "paldean-tauros-blaze-breed": "Tauros-Paldea-Blaze",
    "paldean-tauros-combat-breed": "Tauros-Paldea-Combat",
    # Without these the display name leaked through as the Showdown name
    # ("Wash Rotom" for Rotom-Wash): Poképastes would not import, and the
    # damage calc could not find the species at all.
    "aegislash-blade": "Aegislash-Blade",
    "alolan-persian": "Persian-Alola",
    "castform-rainy": "Castform-Rainy",
    "castform-snowy": "Castform-Snowy",
    "castform-sunny": "Castform-Sunny",
    "fan-rotom": "Rotom-Fan",
    "floette": "Floette-Eternal",
    "frost-rotom": "Rotom-Frost",
    "heat-rotom": "Rotom-Heat",
    "indeedee-female": "Indeedee-F",
    "indeedee-male": "Indeedee",
    "lycanroc-dusk": "Lycanroc-Dusk",
    "lycanroc-midnight": "Lycanroc-Midnight",
    "meowstic-male": "Meowstic",
    "morpeko-hangry-mode": "Morpeko-Hangry",
    "mow-rotom": "Rotom-Mow",
    "palafin-hero": "Palafin-Hero",
    "squawkabilly-yellow-plumage": "Squawkabilly-Yellow",
    "toxtricity-low-key": "Toxtricity-Low-Key",
    "wash-rotom": "Rotom-Wash",
}

# usage.py category strings each per-format usage list maps onto.
_USAGE_CATEGORY = {
    "moveUsage": "move", "itemUsage": "held_item", "abilityUsage": "ability",
}

_RAISED = {
    "atk": "Atk", "def": "Def", "spatk": "SpA", "spdef": "SpD", "spe": "Spe",
}

_STAT_COLS = (
    "hp_points", "attack_points", "defense_points",
    "sp_atk_points", "sp_def_points", "speed_points",
)


def _stat_dict(bs: dict[str, Any]) -> dict[str, int]:
    """Map pokechamp's ``{hp,atk,def,spatk,spdef,spe}`` to the schema's stat keys."""
    return {
        "hp": bs.get("hp") or 0, "attack": bs.get("atk") or 0,
        "defense": bs.get("def") or 0, "special_attack": bs.get("spatk") or 0,
        "special_defense": bs.get("spdef") or 0, "speed": bs.get("spe") or 0,
    }


def _mega_kind(form_name: str) -> str:
    """"Mega X"/"Mega Y"/"Mega Z" for a disambiguated Mega, else plain "Mega"."""
    for suffix in (" X", " Y", " Z"):
        if form_name.endswith(suffix):
            return f"Mega {suffix.strip()}"
    return "Mega"


_STONE_SPECIES_RE = re.compile(r"allows (.+?) to Mega Evolve")
_STONE_TARGET_RE = re.compile(r"Mega Evolve into (Mega [^.]+)")
_PAREN_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _resolve_mega_stones(
    items: list[dict[str, Any]], forms_by_species: dict[str, list[str]],
) -> dict[str, str]:
    """Match each Mega/Z-Mega form name to the item that unlocks it.

    pokechamp's Mega Stone descriptions name the base species always, and the
    exact target Mega form only when a species has more than one Mega option
    (e.g. Charizardite X/Y, Absolite vs. Absolite Z) — a single-Mega species'
    stone just says "...allows Absol to Mega Evolve.", generically.

    Args:
        items: Rows from :meth:`PokechampClient.items_listing`.
        forms_by_species: Base species display name -> its legal Mega form
            names (as in ``pokemon.formName``), grouped by shared dex number.

    Returns:
        Mega form name -> the item name that unlocks it. Omits any form left
        genuinely ambiguous (none observed as of Regulation M-C).
    """
    stones_by_species: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if it.get("category") != "Mega Stone":
            continue
        m = _STONE_SPECIES_RE.search(it.get("description") or "")
        if not m:
            continue
        species = _PAREN_SUFFIX_RE.sub("", m.group(1)).strip()
        stones_by_species.setdefault(species, []).append(it)

    stone_for_form: dict[str, str] = {}
    for species, forms in forms_by_species.items():
        species = _PAREN_SUFFIX_RE.sub("", species).strip()
        candidates = stones_by_species.get(species, [])
        if len(candidates) == 1:
            # One stone for the species: either a single Mega option, or (as
            # with Meowstic's Male/Female Megas) multiple forms that genuinely
            # share the one item.
            for form in forms:
                stone_for_form[form] = candidates[0]["name"]
            continue
        for it in candidates:
            m = _STONE_TARGET_RE.search(it.get("description") or "")
            if m:
                stone_for_form[m.group(1).strip()] = it["name"]
        unresolved = [f for f in forms if f not in stone_for_form]
        leftover = [it for it in candidates if it["name"] not in stone_for_form.values()]
        if len(unresolved) == 1 and len(leftover) == 1:
            stone_for_form[unresolved[0]] = leftover[0]["name"]
    return stone_for_form


async def parse_pokechamp(
    db: Db, client: PokechampClient,
) -> dict[str, Any]:
    """Populate every pokechamp-derived table: roster, reference pools, and
    (via one page fetch per legal non-Mega entry) usage/teammates.

    Args:
        db: Open database handle with ``SCHEMA`` already applied.
        client: Open :class:`PokechampClient`.

    Returns:
        Diagnostics: row counts written and any skip reasons encountered.
    """
    filter_index, listing, moves_listing, moves_options, abilities_listing, \
        abilities_options, items_listing, regulations = await client.gather([
            client.filter_index(), client.pokemon_listing(),
            client.moves_listing(), client.moves_options(),
            client.abilities_listing(), client.abilities_options(),
            client.items_listing(), client.regulations(),
        ])

    move_by_id: dict[int, dict[str, Any]] = {}
    for m in moves_listing:
        # Lowercase to match this table's existing (PokeAPI-derived)
        # convention — unlike pokemon_types/type_chart, which are Capitalized.
        move_by_id[m["id"]] = {
            "slug": slugify(m["name"]), "name": m["name"],
            "type": (m.get("type") or "").lower() or None,
            "damage_class": (m.get("category") or "").lower() or None,
            "power": m.get("power") or None,
            "accuracy": m.get("accuracy"), "pp": m.get("pp"),
            "priority": m.get("priority", 0), "target": None,
            "effect": m.get("description"),
        }
    for m in moves_options:
        move_by_id.setdefault(m["value"], {
            "slug": slugify(m["label"]), "name": m["label"], "type": None,
            "damage_class": None, "power": None, "accuracy": None, "pp": None,
            "priority": 0, "target": None, "effect": None,
        })

    ability_by_id: dict[int, dict[str, Any]] = {}
    for a in abilities_listing:
        ability_by_id[a["id"]] = {
            "slug": slugify(a["name"]), "name": a["name"],
            "effect": a.get("description"), "short_effect": a.get("description"),
        }
    for a in abilities_options:
        ability_by_id.setdefault(a["value"], {
            "slug": slugify(a["label"]), "name": a["label"],
            "effect": None, "short_effect": None,
        })

    items_by_slug: dict[str, tuple] = {}
    for it in items_listing:
        is_mega_stone = 1 if it.get("category") == "Mega Stone" else 0
        category = (it.get("category") or "other").strip().lower().replace(" ", "-")
        items_by_slug[slugify(it["name"])] = (
            slugify(it["name"]), it["name"], category, it.get("description"),
            is_mega_stone,
        )

    by_id = {p["id"]: p for p in listing}
    legal = [{**e, **by_id[e["id"]]} for e in filter_index if e["id"] in by_id]
    by_no: dict[int, list[dict[str, Any]]] = {}
    for e in legal:
        by_no.setdefault(e["no"], []).append(e)

    base_entries = [e for e in legal if not (e["formName"] or "").startswith("Mega")]
    mega_entries = [e for e in legal if (e["formName"] or "").startswith("Mega")]

    def _ability_names(ability_ids: dict[str, Any]) -> list[str]:
        names, seen = [], set()
        ids = list(ability_ids.get("standard") or [])
        if ability_ids.get("hidden") is not None:
            ids.append(ability_ids["hidden"])
        for aid in ids:
            a = ability_by_id.get(aid)
            if a and a["slug"] not in seen:
                seen.add(a["slug"])
                names.append(a["name"])
        return names

    # Mega sub-form entries per base species' `no`, in the exact shape
    # pokedex._form_view expects (mirrors the old forms_json convention).
    mega_forms_json: dict[int, list[dict[str, Any]]] = {}
    for e in mega_entries:
        base = _stat_dict(e["baseStats"])
        mega_forms_json.setdefault(e["no"], []).append({
            "form_name": e["displayName"], "form_kind": _mega_kind(e["displayName"]),
            "types": e.get("types", []),
            "abilities": "|".join(_ability_names(e["abilityIds"])),
            "hp": base["hp"], "attack": base["attack"], "defense": base["defense"],
            "sp_attack": base["special_attack"], "sp_defense": base["special_defense"],
            "speed": base["speed"], "base_stat_total": e.get("bst"),
        })

    rows_pokemon: list[tuple] = []
    rows_types: list[tuple] = []
    rows_abilities: list[tuple] = []
    rows_moves: list[tuple] = []
    skipped: dict[str, int] = {}

    def _skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for e in base_entries:
        slug = e["slug"]
        showdown = SHOWDOWN_OVERRIDES.get(slug, e["displayName"])
        # The species-level name (for PokeAPI's /pokemon-species lookup),
        # which for a regional/gendered alt form is its base sibling's name,
        # not this form's own display name (e.g. "Ninetales", not "Alolan
        # Ninetales" — PokeAPI groups regional forms under one species
        # entry). The base sibling is normally formNo==0, but a couple of
        # species (e.g. Floette, whose only tracked non-Mega form is "Eternal
        # Flower" at formNo 5) don't have one, so use whichever non-Mega
        # sibling has the lowest formNo instead of requiring exactly 0.
        non_mega_siblings = [
            s for s in by_no[e["no"]] if not (s["formName"] or "").startswith("Mega")
        ]
        species_sibling = min(
            non_mega_siblings or [e], key=lambda s: s["formNo"],
        )
        base_name = species_sibling["displayName"]
        rows_pokemon.append((
            slug, e["displayName"], base_name, showdown,
            e["formName"] or "Base",
            json.dumps(e.get("types", [])),
            json.dumps(_ability_names(e["abilityIds"])),
            json.dumps(_stat_dict(e["baseStats"])), e.get("bst"),
            json.dumps(mega_forms_json.get(e["no"], [])),
            e.get("spriteUrl"), "{}", None, None,
        ))
        for t in e.get("types", []):
            rows_types.append((slug, t))
        for name in _ability_names(e["abilityIds"]):
            ability_slug = slugify(name)
            rows_abilities.append((slug, ability_slug, name, None))
        for move_id in e.get("learnset", []):
            m = move_by_id.get(move_id)
            if not m:
                _skip("unresolved_move")
                continue
            rows_moves.append((slug, m["slug"], m["name"]))

    db.executemany(
        "INSERT INTO pokemon VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_pokemon
    )
    db.executemany("INSERT INTO pokemon_types VALUES (?,?)", rows_types)
    db.executemany("INSERT INTO pokemon_abilities VALUES (?,?,?,?)", rows_abilities)
    db.executemany("INSERT INTO learnable_moves VALUES (?,?,?)", rows_moves)

    move_rows = {m["slug"]: (
        m["slug"], m["name"], m["type"], m["damage_class"], m["power"],
        m["accuracy"], m["pp"], m["priority"], m["target"], m["effect"],
    ) for m in move_by_id.values()}
    db.executemany(
        "INSERT OR REPLACE INTO moves VALUES (?,?,?,?,?,?,?,?,?,?)",
        list(move_rows.values()),
    )
    ability_rows = {a["slug"]: (
        a["slug"], a["name"], a["effect"], a["short_effect"],
    ) for a in ability_by_id.values()}
    db.executemany(
        "INSERT OR REPLACE INTO abilities VALUES (?,?,?,?)",
        list(ability_rows.values()),
    )
    db.executemany(
        "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)",
        list(items_by_slug.values()),
    )

    usage_stats = await _ingest_usage(db, client, base_entries)
    return {
        "pokemon": len(rows_pokemon), "moves": len(move_rows),
        "abilities": len(ability_rows), "items": len(items_by_slug),
        **{f"skipped_{k}": v for k, v in skipped.items()},
        **usage_stats,
        # carried through to build_mega_forms / build
        "_mega_entries": mega_entries, "_by_no": by_no,
        "_items_listing": items_listing, "_ability_by_id": ability_by_id,
        "_regulations": regulations,
    }


def _spread_points(raw: str) -> dict[str, int]:
    """Decode an ``evSpreadUsage`` hex spread (``"20-20-00-00-02-00"``) into
    the schema's per-stat SP columns, in hp/atk/def/spatk/spdef/spe order."""
    parts = [int(tok, 16) for tok in raw.split("-")]
    return dict(zip(_STAT_COLS, parts))


async def _ingest_usage(
    db: Db, client: PokechampClient, base_entries: list[dict[str, Any]],
) -> dict[str, int]:
    """Fetch each legal non-Mega entry's page and write its per-format usage.

    This is the one part of the pokechamp pull that costs one request per
    roster entry — everything else (roster, movepool, reference pools) comes
    from whole-game listing endpoints.

    Args:
        db: Open database handle, already holding `pokemon` rows.
        client: Open `PokechampClient`.
        base_entries: The (non-Mega) legal entries just written to `pokemon`.

    Returns:
        Row counts for `usage`, `usage_rank` and `teammates`.
    """
    pages = await client.gather([client.pokemon_page(e["slug"]) for e in base_entries])

    rows_usage: list[tuple] = []
    rows_rank: list[tuple] = []
    rows_teammates: list[tuple] = []
    rank_updates: list[tuple] = []

    for entry, page in zip(base_entries, pages):
        slug = entry["slug"]
        ranks: dict[str, int | None] = {"doubles": None, "singles": None}
        for fmt, block in page["usage"].items():
            if fmt not in ("singles", "doubles"):
                continue
            ranks[fmt] = block.get("overallRank")
            if block.get("overallRank") is not None:
                rows_rank.append((fmt, block["overallRank"], slug))
            for key, category in _USAGE_CATEGORY.items():
                for rank, row in enumerate(block.get(key) or [], start=1):
                    rows_usage.append((
                        slug, fmt, category, rank, row["name"], row["slug"],
                        f"{row['rate']}%", row["rate"],
                        None, None, None, None, None, None, None, None,
                    ))
            for rank, row in enumerate(block.get("natureUsage") or [], start=1):
                rows_usage.append((
                    slug, fmt, "stat_alignment", rank, row["name"], row["slug"],
                    f"{row['rate']}%", row["rate"],
                    _RAISED.get(row.get("raisedStat"), row.get("raisedStat")),
                    _RAISED.get(row.get("loweredStat"), row.get("loweredStat")),
                    None, None, None, None, None, None,
                ))
            for rank, row in enumerate(block.get("evSpreadUsage") or [], start=1):
                pts = _spread_points(row["raw"])
                rows_usage.append((
                    slug, fmt, "stat_points", rank, None, None,
                    f"{row['rate']}%", row["rate"], None, None,
                    pts["hp_points"], pts["attack_points"], pts["defense_points"],
                    pts["sp_atk_points"], pts["sp_def_points"], pts["speed_points"],
                ))
            for rank, row in enumerate(block.get("teamTtog") or [], start=1):
                rows_teammates.append((slug, fmt, rank, row["name"], row["slug"]))
        rank_updates.append((ranks["doubles"], ranks["singles"], slug))

    db.executemany(
        "INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_usage
    )
    db.executemany("INSERT INTO usage_rank VALUES (?,?,?)", rows_rank)
    db.executemany("INSERT INTO teammates VALUES (?,?,?,?,?)", rows_teammates)
    db.executemany(
        "UPDATE pokemon SET rank_doubles = ?, rank_singles = ? WHERE slug = ?",
        [(d, s, slug) for d, s, slug in rank_updates],
    )
    return {"usage": len(rows_usage), "usage_rank": len(rows_rank),
            "teammates": len(rows_teammates)}


def build_mega_forms(
    db: Db,
    mega_entries: list[dict[str, Any]],
    items_listing: list[dict[str, Any]],
    ability_by_id: dict[int, dict[str, Any]],
) -> int:
    """Derive one row per Mega/Z-Mega form: stats, ability, typing, the Mega
    Stone that unlocks it, and its stat deltas vs. the base species.

    Args:
        db: Open database handle, with `pokemon` already populated (used for
            each base species' own stats/name).
        mega_entries: The Mega/Z-Mega legal entries collected by
            :func:`parse_pokechamp`, each already carrying a `_species_name`
            key (its base species' display name).
        items_listing: Raw item rows from :meth:`PokechampClient.items_listing`,
            used to resolve each form's Mega Stone by description text.
        ability_by_id: id -> {slug, name, ...}, from :func:`parse_pokechamp`.

    Returns:
        The number of `mega_forms` rows written.
    """
    forms_by_species: dict[str, list[str]] = {}
    for e in mega_entries:
        forms_by_species.setdefault(e["_species_name"], []).append(e["displayName"])
    stone_for_form = _resolve_mega_stones(items_listing, forms_by_species)

    base_by_name = {
        r["battle_name"]: r for r in
        db.query("SELECT slug, battle_name, base_stats_json FROM pokemon")
    }

    rows_out: list[tuple] = []
    for e in mega_entries:
        base_row = base_by_name.get(e["_species_name"])
        if not base_row:
            continue
        base_stats = json.loads(base_row["base_stats_json"])
        mega_stats = _stat_dict(e["baseStats"])
        stat_changes = {k: mega_stats[k] - base_stats.get(k, 0) for k in mega_stats}
        bst = e.get("bst")
        base_bst = sum(base_stats.values())
        bst_change = bst - base_bst if bst is not None else None
        name = e["displayName"]
        ability_ids = e["abilityIds"]
        ability_slugs_seen: set[str] = set()
        ability_names: list[str] = []
        for aid in [
            *(ability_ids.get("standard") or []),
            *([ability_ids["hidden"]] if ability_ids.get("hidden") is not None else []),
        ]:
            a = ability_by_id.get(aid)
            if a and a["slug"] not in ability_slugs_seen:
                ability_slugs_seen.add(a["slug"])
                ability_names.append(a["name"])
        ability = "|".join(ability_names)
        rows_out.append((
            name, base_row["slug"], e["_species_name"], _mega_kind(name),
            stone_for_form.get(name),
            json.dumps(e.get("types", [])), ability,
            json.dumps(mega_stats), bst,
            json.dumps(stat_changes), bst_change,
        ))
    db.executemany(
        "INSERT OR REPLACE INTO mega_forms VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows_out
    )
    return len(rows_out)


async def enrich(db: Db, settings: Settings, keys: dict[str, Any]) -> None:
    """Fetch the type chart + localized species names from PokeAPI.

    Unrelated to which roster source is used — kept as-is because it's cheap,
    the type chart is game-invariant, and ``pull.builds``' pokedb ingestion
    resolves Japanese species names against ``pokemon.names_json``.

    Args:
        db: Open database handle, already populated by `parse_pokechamp`.
        settings: Runtime settings (PokeAPI base URL, HTTP cache path).
        keys: ``{"species": [...]}`` — base species display names to look up.
    """
    cache = Cache(settings.cache_db)
    api = PokeApiEnricher(settings, cache)
    try:
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
        db.executemany("INSERT OR REPLACE INTO type_chart VALUES (?,?,?)", chart_rows)

        species_names = await api.gather(
            [api.species_names(s) for s in keys["species"]]
        )
        names_by_species = dict(zip(keys["species"], species_names))
        updates: list[tuple] = []
        for row in db.query("SELECT slug, base_name FROM pokemon"):
            key = slugify(_PAREN_SUFFIX_RE.sub("", row["base_name"]))
            names = names_by_species.get(key, {})
            updates.append((json.dumps(names), row["slug"]))
        db.executemany("UPDATE pokemon SET names_json = ? WHERE slug = ?", updates)
    finally:
        await api.aclose()
        cache.close()


async def build(db: Db, settings: Settings, *, with_builds: bool = True) -> dict[str, int]:
    """Run the full build: parse pokechamp.gg, derive Megas, enrich via
    PokeAPI, then ingest the observed-team corpus.

    Args:
        db: Open database handle.
        settings: Runtime settings.
        with_builds: Fetch observed teams from Limitless/pokedb. Everything is
            cached, so a rebuild is cheap; pass False to skip the stage entirely.

    Returns:
        Row counts per table, also written to the `meta` table.
    """
    db.reset_build_tables()
    cache = Cache(settings.cache_db)
    client = PokechampClient(settings, cache, concurrency=settings.pokechamp_concurrency)
    try:
        parsed = await parse_pokechamp(db, client)
    finally:
        await client.aclose()
        cache.close()

    mega_entries = parsed.pop("_mega_entries")
    by_no = parsed.pop("_by_no")
    items_listing = parsed.pop("_items_listing")
    ability_by_id = parsed.pop("_ability_by_id")
    regulations = parsed.pop("_regulations")
    for e in mega_entries:
        non_mega_siblings = [
            s for s in by_no[e["no"]] if not (s["formName"] or "").startswith("Mega")
        ]
        base = min(non_mega_siblings, key=lambda s: s["formNo"], default=None)
        e["_species_name"] = base["displayName"] if base else e["displayName"]
    build_mega_forms(db, mega_entries, items_listing, ability_by_id)

    species = sorted({
        slugify(_PAREN_SUFFIX_RE.sub("", r["base_name"]))
        for r in db.query("SELECT base_name FROM pokemon")
    })
    await enrich(db, settings, {"species": species})

    smogon_stats = await ingest_smogon_sets(db, settings)
    db.set_meta("smogon", smogon_stats)

    if with_builds:
        stats = await builds_mod.ingest_builds(db, settings)
        db.set_meta("builds", stats)

    current_reg = next((r for r in regulations if r.get("endDate") is None), None)
    db.set_meta("season", current_reg["title"] if current_reg else "Current")
    db.set_meta("data_version", (current_reg or {}).get("startDate", ""))
    db.set_meta("generated_at", datetime.now(timezone.utc).isoformat())
    db.set_meta("source", "pokechamp.gg")
    counts = {t: db.count(t) for t in (
        "pokemon", "learnable_moves", "usage", "moves", "abilities",
        "items", "mega_forms", "type_chart", "teammates",
        "build_teams", "build_members", "smogon_sets",
    )}
    db.set_meta("counts", counts)
    return counts
