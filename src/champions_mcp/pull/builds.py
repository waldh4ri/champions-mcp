"""Normalise observed teams from Limitless + pokedb into build_teams/build_members.

Two upstreams with different shapes and different languages have to land in one
table, resolved against the same roster the rest of the server uses:

* Limitless sends English Showdown-style names, which ``Db.resolve_slug``
  already understands via ``pokemon.showdown_name``. A handful of word-order
  variants ("Wash Rotom") need aliasing, and free-text teamlist entry means a
  small tail of genuine player typos that are dropped rather than guessed at.

* pokedb sends Japanese names plus a separate form string. ``pokemon.names_json``
  already carries ``ja``, so base species resolve for free; the work is picking
  the right *form* — ロトム + ウォッシュロトム is ``rotom-wash``, not ``rotom``.
  That is done by mapping the Japanese form to the English form token the
  roster's Showdown name already ends in, so new forms need one line here
  rather than a new hardcoded pair.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from typing import Any

from ..db import Db
from ..normalize import normalize_key, slugify

# Limitless names Megas as their own species ("Charizard-Mega-Y"); the roster
# keeps them as the base species plus a held stone, which is also how the rest
# of the server models them (see db.mega_stone_usage).
_MEGA_SUFFIX_RE = re.compile(r"-mega(-[xy])?$")

# ---------------------------------------------------------------------- #
# Limitless: name fixups
# ---------------------------------------------------------------------- #

# Limitless spellings that resolve_slug cannot reach. Rotom forms are stored
# adjective-first upstream ("Wash Rotom") and noun-first here ("Rotom Wash");
# Champions' only Floette is the Eternal Flower one, which the roster carries
# as plain "Floette".
LIMITLESS_ALIASES: dict[str, str] = {
    "eternal-flower-floette": "floette",
    "wash-rotom": "rotom-wash",
    "heat-rotom": "rotom-heat",
    "frost-rotom": "rotom-frost",
    "mow-rotom": "rotom-mow",
    "fan-rotom": "rotom-fan",
    "floette-eternal": "floette",
    # Champions' only Vivillon is the Fancy pattern, sent unqualified upstream.
    "vivillon": "vivillon-fancy-pattern",
    # Gender is written as a symbol rather than the roster's "-F" suffix.
    "basculegion-f": "basculegion-female",
    "meowstic-f": "meowstic-female",
}

# "Basculegion ♀" must not normalise to bare "basculegion" — that resolves to
# the *male* entry, silently filing every female Basculegion under the wrong
# Pokémon (different typing, different movepool). Fold the symbols into the
# roster's own -F/-M suffix convention before normalising.
_GENDER_SYMBOLS = {"♀": " F", "♂": " M"}

# ---------------------------------------------------------------------- #
# pokedb: Japanese form -> the English token the roster's Showdown name ends in
# ---------------------------------------------------------------------- #

# An empty token means "this is the base form" — pick the candidate whose
# Showdown name carries no form suffix at all. Cosmetic/only-form entries
# (Mimikyu's ばけたすがた, Aegislash's シールドフォルム) never reach the token
# lookup because their species has a single roster entry.
JA_FORM_TOKENS: dict[str, str] = {
    "": "",
    "ヒスイのすがた": "Hisui",
    "アローラのすがた": "Alola",
    "ガラルのすがた": "Galar",
    "パルデアのすがた": "Paldea-Combat",
    "パルデアのすがた・コンバットしゅ": "Paldea-Combat",
    "パルデアのすがた・ブレイズしゅ": "Paldea-Blaze",
    "パルデアのすがた・ウォーターしゅ": "Paldea-Aqua",
    # Rotom appliances
    "ウォッシュロトム": "Wash",
    "ヒートロトム": "Heat",
    "フロストロトム": "Frost",
    "スピンロトム": "Fan",
    "カットロトム": "Mow",
    # Lycanroc
    "たそがれのすがた": "Dusk",
    "まよなかのすがた": "Midnight",
    "まひるのすがた": "",
    # Gender-split species: the male line is the base entry, the female one
    # carries the -F suffix (Basculegion, Meowstic).
    "オスのすがた": "",
    "メスのすがた": "F",
    # Gourgeist / Pumpkaboo sizes
    "おおだましゅ": "Super",
    "だいだましゅ": "Large",
    "こだましゅ": "Small",
    "ふつうのすがた": "",
}

# Japanese Mega Stone names are "<species>ナイト" (Floettite -> フラエッテナイト),
# with X/Y suffixes for dual-Mega species. PokeAPI has no entry for the
# Champions-exclusive stones, so they are resolved through the roster instead.
#
# The join is not a plain concatenation: the species' final mora is routinely
# dropped before ナイト — マフォクシー -> マフォクシナイト, ブリガロン ->
# ブリガロナイト, ドラミドロ -> ドラミドナイト. Rather than model which endings
# elide, both the full and the shortened stem are generated; only keys that do
# not already exist are added, so a wrong guess can never displace a real name.
_JA_XY_SUFFIXES = ("", "X", "Y", "Ｘ", "Ｙ")

# pokedb writes an empty item slot as a literal string rather than omitting it.
JA_NO_ITEM = {"持ち物なし", "なし", "-"}
EN_NO_ITEM = {"no item", "none", "-", ""}

# ---------------------------------------------------------------------- #
# Resolution helpers
# ---------------------------------------------------------------------- #

def _roster_index(db: Db) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """Build the lookup tables the two upstreams need.

    Args:
        db: Open database handle with `pokemon` populated.

    Returns:
        A tuple of (japanese name -> [(slug, showdown_name), …],
        lowercase english name/slug -> slug).
    """
    by_ja: dict[str, list[tuple[str, str]]] = {}
    by_en: dict[str, str] = {}
    for r in db.query("SELECT slug, battle_name, showdown_name, names_json FROM pokemon"):
        ja = json.loads(r["names_json"] or "{}").get("ja")
        if ja:
            by_ja.setdefault(ja, []).append((r["slug"], r["showdown_name"]))
        # Key on the normalised form so "Lycanroc Dusk", "Lycanroc-Dusk" and
        # "lycanroc-dusk-form" all converge without one alias each.
        for key in (r["slug"], r["battle_name"], r["showdown_name"]):
            by_en.setdefault(normalize_key(key), r["slug"])
    return by_ja, by_en


def resolve_ja(
    by_ja: dict[str, list[tuple[str, str]]], name: str, form: str,
) -> str | None:
    """Resolve a Japanese species + form pair to a roster slug.

    Args:
        by_ja: Japanese-name index from `_roster_index`.
        name: Japanese species name (e.g. ``"ロトム"``).
        form: Japanese form name, possibly empty (e.g. ``"ウォッシュロトム"``).

    Returns:
        The matching slug, or None if the species or its form is unknown.
    """
    cands = by_ja.get((name or "").strip())
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0][0]

    token = JA_FORM_TOKENS.get((form or "").strip())
    if token is None:
        return None
    if token:
        suffix = f"-{token}".lower()
        for slug, showdown in cands:
            if showdown.lower().endswith(suffix):
                return slug
        return None
    # Base form: the entry whose Showdown name carries no form suffix. Prefer an
    # exact hyphen-free name, then fall back to the shortest.
    plain = [c for c in cands if "-" not in c[1]]
    return (plain or sorted(cands, key=lambda c: len(c[1])))[0][0]


def _ja_item_index(db: Db, ja_names: dict[str, str]) -> dict[str, str]:
    """Map Japanese item names to roster item slugs.

    PokeAPI supplies localized names for canonical items. The Champions-exclusive
    Mega Stones have no PokeAPI entry at all, so those are derived from the
    ``<species>ナイト`` naming rule against the roster's own Japanese species
    names, which the pull already stores.

    Args:
        db: Open database handle with `items` and `pokemon` populated.
        ja_names: item slug -> Japanese name, from the PokeAPI enrichment pass.

    Returns:
        A mapping of Japanese item name to item slug.
    """
    out: dict[str, str] = {ja: slug for slug, ja in ja_names.items() if ja}

    stones = {
        r["slug"]: r["name"]
        for r in db.query("SELECT slug, name FROM items WHERE is_mega_stone = 1")
    }
    # A Japanese name maps to *several* roster slugs — localized names come from
    # the base species, so ライチュウ covers both raichu and alolan-raichu. Keep
    # every candidate: only one of them owns the Mega Stones.
    ja_species: dict[str, list[str]] = {}
    for r in db.query("SELECT slug, names_json FROM pokemon"):
        ja = json.loads(r["names_json"] or "{}").get("ja")
        if ja:
            ja_species.setdefault(ja, []).append(r["slug"])
    # slug of the stone that belongs to each species, via mega_forms
    stone_by_species: dict[str, list[str]] = {}
    for r in db.query(
        "SELECT species_slug, mega_stone, mega_kind FROM mega_forms "
        "WHERE mega_stone IS NOT NULL"
    ):
        stone_by_species.setdefault(r["species_slug"], []).append(r["mega_stone"])

    for ja_name, species_slugs in ja_species.items():
        names = [
            n for slug in species_slugs for n in (stone_by_species.get(slug) or [])
        ]
        if not names:
            continue
        stems = {ja_name, ja_name[:-1]} if len(ja_name) > 2 else {ja_name}
        for stem in stems:
            for xy in _JA_XY_SUFFIXES:
                key = f"{stem}ナイト{xy}"
                if key in out:
                    continue
                if xy:
                    want = xy.translate(str.maketrans("ＸＹ", "XY")).lower()
                    match = next(
                        (n for n in names if slugify(n).endswith(f"-{want}")), None)
                else:
                    # Only unambiguous when the species has a single stone;
                    # an X/Y pair must be disambiguated by the suffix.
                    match = names[0] if len(names) == 1 else None
                if match and slugify(match) in stones:
                    out[key] = slugify(match)
    return out


# ---------------------------------------------------------------------- #
# Format classification (Limitless has no doubles/singles flag)
# ---------------------------------------------------------------------- #

_SINGLES_NAME_RE = re.compile(
    r"single|singles|\bbss\b|battle stadium|1v1|solo battle", re.IGNORECASE
)

# Moves that only do something with an ally on the field. A team carrying
# several of these is a doubles team, whatever the event was called.
_DOUBLES_MOVES = {
    "follow me", "rage powder", "helping hand", "wide guard", "ally switch",
    "after you", "decorate", "coaching", "life dew", "heal pulse",
}
# Singles-defining structure: entry hazards and phazing have no place in a
# 4-slot doubles game but are the backbone of a singles team.
_SINGLES_MOVES = {
    "stealth rock", "spikes", "toxic spikes", "sticky web",
    "dragon tail", "roar", "whirlwind", "circle throw",
}


def classify_format(event_name: str, teams: list[list[dict[str, Any]]]) -> str:
    """Decide whether a Limitless event was doubles or singles.

    The API exposes no battle-format field — ``game`` is ``VGC`` for both — so
    this reads the event name first and falls back to what the teams actually
    run. Protect saturation is the strongest doubles tell (it protects against
    spread damage from a partner's move), hazards/phazing the strongest singles
    one.

    Args:
        event_name: The tournament name as published.
        teams: Parsed teamlists, each a list of member dicts with ``attacks``.

    Returns:
        ``"singles"`` or ``"doubles"``.
    """
    if _SINGLES_NAME_RE.search(event_name or ""):
        return "singles"
    if not teams:
        return "doubles"

    doubles_score = singles_score = 0
    for team in teams:
        moves = [m.lower() for member in team for m in (member.get("attacks") or [])]
        protects = sum(1 for m in moves if m in ("protect", "spiky shield", "detect"))
        doubles_score += len(set(moves) & _DOUBLES_MOVES) + (2 if protects >= 3 else 0)
        singles_score += len(set(moves) & _SINGLES_MOVES)
    return "singles" if singles_score > doubles_score else "doubles"


# ---------------------------------------------------------------------- #
# Weighting
# ---------------------------------------------------------------------- #

def observation_weight(
    when: str | None, *, field_size: int | None, placing: int | None,
    half_life_days: float, today: date | None = None,
) -> float:
    """Score how much one observed team should count.

    Three independent factors, deliberately gentle — this ranks evidence, it
    does not decide anything on its own:

    * **recency** — Champions regulations move fast; a half-life keeps last
      month's consensus from outvoting this week's.
    * **field size** — a 255-player event is stronger evidence than a 6-player
      one, but logarithmically: it is not 40x stronger.
    * **placing** — winning is signal, though a weak one, since most of a
      field's teams never make top cut and are still perfectly real builds.

    Args:
        when: ISO date string, or None if the upstream did not supply one.
        field_size: Number of players in the event, if known.
        placing: Final placing, if known.
        half_life_days: Days for the recency factor to halve.
        today: Reference date, injectable for tests.

    Returns:
        A positive weight, nominally around 1.0 for a recent mid-size event.
    """
    weight = 1.0
    if when:
        try:
            when_date = datetime.fromisoformat(when.replace("Z", "+00:00")).date()
            age = max(0, ((today or date.today()) - when_date).days)
            weight *= 0.5 ** (age / max(1.0, half_life_days))
        except ValueError:
            pass
    if field_size:
        weight *= min(1.5, math.log1p(field_size) / math.log1p(32))
    if placing and placing > 0:
        weight *= 1.0 + 0.5 / placing
    return round(weight, 6)


# ---------------------------------------------------------------------- #
# Row construction
# ---------------------------------------------------------------------- #

def _team_id(source: str, ref: str, player: str) -> str:
    return hashlib.sha1(f"{source}|{ref}|{player}".encode()).hexdigest()[:16]


def _set_hash(slug: str, item: str | None, ability: str | None,
              nature: str | None, moves: list[str] | None) -> str:
    payload = "|".join([
        slug, (item or "").lower(), (ability or "").lower(), (nature or "").lower(),
        ",".join(sorted(m.lower() for m in (moves or []))),
    ])
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _team_hash(members: list[dict[str, Any]]) -> str:
    return hashlib.sha1(
        "|".join(sorted(m["set_hash"] for m in members)).encode()
    ).hexdigest()[:16]


class BuildIngest:
    """Accumulates normalised observed teams, then writes them in one go."""

    def __init__(self, db: Db, ja_item_names: dict[str, str] | None = None) -> None:
        """Prepare roster/item lookups for ingestion.

        Args:
            db: Open database handle with `pokemon` and `items` populated.
            ja_item_names: item slug -> Japanese name from PokeAPI, used to
                resolve pokedb's Japanese item strings.
        """
        self._db = db
        self._by_ja, self._by_en = _roster_index(db)
        self._items = {
            r["slug"] for r in db.query("SELECT slug FROM items")
        }
        self._ja_items = _ja_item_index(db, ja_item_names or {})
        self.teams: list[tuple] = []
        self.members: list[tuple] = []
        self.skipped: dict[str, int] = {}

    # -- resolution ------------------------------------------------------ #
    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def _resolve_en(self, name: str) -> str | None:
        """Resolve an English/Showdown-style name, then an alias, then a
        Mega form ("Charizard-Mega-Y" -> charizard, since Megas are triggered
        by the held stone rather than being roster entries of their own)."""
        raw = name or ""
        for symbol, suffix in _GENDER_SYMBOLS.items():
            raw = raw.replace(symbol, suffix)
        key = normalize_key(raw)
        if not key:
            return None
        if key in self._by_en:
            return self._by_en[key]
        alias = LIMITLESS_ALIASES.get(key)
        if alias:
            return alias
        stripped = _MEGA_SUFFIX_RE.sub("", key)
        if stripped != key:
            return self._by_en.get(stripped) or LIMITLESS_ALIASES.get(stripped)
        return None

    def _item(self, item: str | None, *, japanese: bool) -> tuple[str | None, str | None]:
        """Normalise a raw held-item string to (display name, slug).

        "No Item"/持ち物なし mean an empty slot, not an item called that, and
        must not become a phantom entry in the item distribution. Names that
        do not resolve keep their display text but get a NULL slug, so a
        player's typo stays visible without polluting joins.
        """
        raw = (item or "").strip()
        if not raw:
            return None, None
        if raw in JA_NO_ITEM or raw.lower() in EN_NO_ITEM:
            return None, None
        if japanese:
            return raw, self._ja_items.get(raw)
        slug = slugify(raw)
        return raw, (slug if slug in self._items else None)

    # -- sources --------------------------------------------------------- #
    def add_limitless(
        self, tournament: dict[str, Any], standings: list[dict[str, Any]],
        *, fmt: str, half_life_days: float,
    ) -> int:
        """Ingest one Limitless tournament's standings.

        Args:
            tournament: Tournament summary (id, name, date, players, format).
            standings: The tournament's standings rows.
            fmt: ``"doubles"`` or ``"singles"``, from `classify_format`.
            half_life_days: Recency half-life for the weight.

        Returns:
            The number of teams ingested.
        """
        added = 0
        for entry in standings:
            decklist = entry.get("decklist")
            if not decklist:
                continue
            player = entry.get("name") or entry.get("player") or ""
            team_id = _team_id("limitless", tournament["id"], player)
            members: list[dict[str, Any]] = []
            for slot, mon in enumerate(decklist):
                slug = self._resolve_en(mon.get("name") or mon.get("id") or "")
                if not slug:
                    self._skip("unresolved_species")
                    continue
                moves = sorted({m.strip() for m in (mon.get("attacks") or []) if m.strip()})
                item, item_slug = self._item(mon.get("item"), japanese=False)
                members.append({
                    "slot": slot, "slug": slug,
                    "item": item, "item_slug": item_slug,
                    "ability": mon.get("ability") or None,
                    "nature": mon.get("nature") or None,
                    "moves": moves,
                    "set_hash": _set_hash(
                        slug, item, mon.get("ability"), mon.get("nature"), moves),
                })
            # A partial list is worse than no list: a five-Pokémon "team" would
            # silently understate every co-occurrence it takes part in.
            if len(members) != len(decklist) or not members:
                self._skip("incomplete_team")
                continue
            record = entry.get("record") or {}
            self.teams.append((
                team_id, "limitless", tournament["id"], tournament.get("name"),
                fmt, tournament.get("format"), (tournament.get("date") or "")[:10] or None,
                player, entry.get("placing"),
                record.get("wins"), record.get("losses"), record.get("ties"),
                None, tournament.get("players"), "full_set",
                observation_weight(
                    tournament.get("date"), field_size=tournament.get("players"),
                    placing=entry.get("placing"), half_life_days=half_life_days),
                _team_hash(members),
            ))
            for m in members:
                self.members.append((
                    team_id, m["slot"], m["slug"], m["item"], m["item_slug"],
                    m["ability"], m["nature"], json.dumps(m["moves"]), m["set_hash"],
                ))
            added += 1
        return added

    def add_pokedb(
        self, export: dict[str, Any], regulation: str | None, *, half_life_days: float,
    ) -> int:
        """Ingest one pokedb ranked-ladder season export.

        These records carry species + held item but no moves, ability or nature,
        so they land as ``team_and_item`` evidence and their ``moves_json`` stays
        NULL. That distinction is surfaced all the way out to the tools.

        Args:
            export: A parsed season file (``season``, ``rule``, ``teams``).
            regulation: The regulation this season was played under, if known.
            half_life_days: Recency half-life for the weight.

        Returns:
            The number of teams ingested.
        """
        season = export.get("season") or "?"
        fmt = "singles" if export.get("rule") == "シングル" else "doubles"
        when = (export.get("updated_at") or "")[:10] or None
        added = 0
        for entry in export.get("teams", []):
            rank = entry.get("rank")
            team_id = _team_id("pokedb", f"{season}|{fmt}", str(rank))
            members: list[dict[str, Any]] = []
            for slot, mon in enumerate(entry.get("team") or []):
                slug = resolve_ja(self._by_ja, mon.get("pokemon", ""), mon.get("form", ""))
                if not slug:
                    self._skip("unresolved_species")
                    continue
                item, item_slug = self._item(mon.get("item"), japanese=True)
                members.append({
                    "slot": slot, "slug": slug, "item": item,
                    "item_slug": item_slug,
                    "set_hash": _set_hash(slug, item, None, None, None),
                })
            if len(members) != len(entry.get("team") or []) or not members:
                self._skip("incomplete_team")
                continue
            self.teams.append((
                team_id, "pokedb", f"{season}|{fmt}", f"Ranked ladder {season}",
                fmt, regulation, when, None, rank, None, None, None,
                entry.get("rating_value"), None, "team_and_item",
                observation_weight(
                    when, field_size=None, placing=rank,
                    half_life_days=half_life_days),
                _team_hash(members),
            ))
            for m in members:
                self.members.append((
                    team_id, m["slot"], m["slug"], m["item"], m["item_slug"],
                    None, None, None, m["set_hash"],
                ))
            added += 1
        return added

    # -- write ----------------------------------------------------------- #
    def flush(self) -> dict[str, int]:
        """Write every accumulated row into the database.

        Returns:
            Counts of ``build_teams``/``build_members`` rows written, plus any
            skip reasons encountered.
        """
        self._db.executemany(
            "INSERT OR REPLACE INTO build_teams VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", self.teams,
        )
        self._db.executemany(
            "INSERT OR REPLACE INTO build_members VALUES (?,?,?,?,?,?,?,?,?)",
            self.members,
        )
        return {
            "build_teams": len(self.teams),
            "build_members": len(self.members),
            **{f"skipped_{k}": v for k, v in self.skipped.items()},
        }


# ---------------------------------------------------------------------- #
# Orchestration
# ---------------------------------------------------------------------- #

# pokedb numbers its exports by ladder *season* (M-1, M-2, …) while the legal
# roster is set by *regulation* (M-A, M-B). They are not the same axis and the
# site does not publish the mapping, so it is pinned here: season M-4 is
# confirmed Reg M-B (Champions team reports tagged "M-4" carry the M-B badge),
# and the earlier seasons predate M-B's 2026-06-20 rollout. Override with
# CHAMPIONS_MCP_POKEDB_SEASON_REGS="1:M-A,4:M-B" if a season straddles.
POKEDB_SEASON_REGULATION: dict[int, str] = {1: "M-A", 2: "M-A", 3: "M-A", 4: "M-B"}


def _season_number(season: str) -> int | None:
    """Extract the integer season number from a label like ``"M-4"``."""
    m = re.search(r"(\d+)", season or "")
    return int(m.group(1)) if m else None


async def ingest_builds(db: Db, settings: Any) -> dict[str, int]:
    """Fetch and ingest observed teams from both upstreams.

    VGC comes from Limitless tournament teamlists, BSS from the pokedb ranked
    ladder export. Everything is cached in ``pull-cache.sqlite``, so a rebuild
    re-reads without touching the network and only genuinely new events cost a
    request.

    Args:
        db: Open database handle, already populated by `parse_index` + `enrich`.
        settings: Runtime settings (source URLs, regulation filter, caps).

    Returns:
        Row counts and per-source/skip diagnostics.
    """
    from ..cache import Cache
    from .builds_source import LimitlessClient, PokedbClient
    from .pokeapi import PokeApiEnricher

    cache = Cache(settings.cache_db)
    api = PokeApiEnricher(settings, cache)
    limitless = LimitlessClient(settings, cache)
    pokedb = PokedbClient(settings, cache)
    stats: dict[str, int] = {}
    try:
        item_names = dict(await api.gather([
            api.item_ja_name(r["name"])
            for r in db.query("SELECT name FROM items")
        ]))
        ingest = BuildIngest(db, item_names)

        # --- VGC: Limitless tournament teamlists ---
        for regulation in settings.builds_regulations:
            events = await limitless.tournaments(
                regulation, settings.builds_max_events)
            for ev in events:
                if (ev.get("players") or 0) < settings.builds_min_players:
                    continue
                standings = await limitless.standings(ev["id"])
                lists = [
                    p["decklist"] for p in standings if p.get("decklist")
                ]
                if not lists:
                    continue
                fmt = classify_format(ev.get("name") or "", lists)
                n = ingest.add_limitless(
                    ev, standings, fmt=fmt,
                    half_life_days=settings.builds_half_life_days)
                stats[f"limitless_{fmt}"] = stats.get(f"limitless_{fmt}", 0) + n

        # --- BSS: pokedb ranked ladder export ---
        overrides = _season_regulation_overrides()
        for rule in ("single", "double"):
            for export in await pokedb.all_seasons(rule):
                num = _season_number(export.get("season", ""))
                reg = overrides.get(num) or POKEDB_SEASON_REGULATION.get(num)
                if settings.builds_regulations and reg not in settings.builds_regulations:
                    continue
                n = ingest.add_pokedb(
                    export, reg, half_life_days=settings.builds_half_life_days)
                stats[f"pokedb_{rule}"] = stats.get(f"pokedb_{rule}", 0) + n

        stats.update(ingest.flush())
    finally:
        await api.aclose()
        await limitless.aclose()
        await pokedb.aclose()
        cache.close()
    return stats


def _season_regulation_overrides() -> dict[int, str]:
    """Parse ``CHAMPIONS_MCP_POKEDB_SEASON_REGS`` (e.g. ``"3:M-A,4:M-B"``)."""
    import os

    raw = os.environ.get("CHAMPIONS_MCP_POKEDB_SEASON_REGS", "")
    out: dict[int, str] = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        num, _, reg = part.partition(":")
        if num.strip().isdigit() and reg.strip():
            out[int(num.strip())] = reg.strip()
    return out
