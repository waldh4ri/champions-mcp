"""Ranked-ladder usage stats from champs.pokedb.tokyo (バトルデータベース チャンピオンズ).

pokedb mirrors the game's own ranked-battle statistics, per ladder season and
per rule (singles / doubles), refreshed daily during a season:

* ``/pokemon/list?season=N&rule=R`` — the usage ranking, one link per entry
  (``/pokemon/show/<dex>-<form>``), in rank order. Its season selector also
  lists every season with its regulation ("シーズンM-8 (M-C)").
* ``/pokemon/show/<dex>-<form>?season=N&rule=R`` — one Pokémon's page: move,
  ability, nature and held-item rates, exact Stat Point spreads, and the
  teammates it is most often brought with.

Everything that matters is keyed by the game's own numeric ids, embedded in
the page as JSON (``move_key``, ``ability_key``, ``item_key``,
``personality_key``) — the same ids pokechamp.gg uses for its move / ability /
item listings, and pokedb's ``<dex>-<form>`` is pokechamp's ``(no, formNo)``.
So nothing here translates Japanese names: every row joins by id against the
roster and reference tables the pull has already written. Only Stat Point
spreads and teammates are read from the rendered HTML (they carry no JSON).

Pages are fetched once per pull and only their parsed numbers are cached (in
``pull-cache.sqlite``, for a day), never the ~0.5 MB of HTML each.

Season choice: the newest season, unless it has only just started (fewer
than ``MIN_RANKED`` entries ranked yet) and the previous season ran under the
same regulation — then the previous one, which is still the right metagame.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import unicodedata
from typing import Any

import httpx

from ..cache import Cache
from ..config import Settings
from ..db import Db
from ..normalize import slugify

# pokedb's rule parameter -> the schema's format key.
RULES = {"singles": 0, "doubles": 1}

# The ranking refreshes daily; a day-old copy is what the site shows anyway.
_TTL = 86_400
# Bumped whenever parse_pokemon's output changes shape, so stale parses in
# the cache are never read back.
_CACHE_VERSION = "v2"
# Below this many ranked entries a season has only just started: its rates
# rest on a handful of battles and whole species are missing.
MIN_RANKED = 100
# Rows kept per category (pokedb shows the top 10 for every list but spreads).
TOP_N = 10

# The 25 natures in the game's internal order (``personality_key``). The
# raised stat is ``index // 5`` and the lowered one ``index % 5`` over
# Atk/Def/Spe/SpA/SpD — a property of the games, like the type list, not data.
NATURES = (
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
    "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive",
    "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky",
)
_NATURE_STATS = ("Atk", "Def", "Spe", "SpA", "SpD")

# Stat Point chip labels -> the schema's columns (H A B C D S, the Japanese
# community's stat letters).
_SP_COLS = {
    "H": "hp_points", "A": "attack_points", "B": "defense_points",
    "C": "sp_atk_points", "D": "sp_def_points", "S": "speed_points",
}
_SP_ORDER = tuple(_SP_COLS.values())

_SEASON_RE = re.compile(r"シーズンM-(\d+)\s*\((M-[A-Z])\)")
_SHOW_RE = re.compile(r'href="/pokemon/show/(\d{4})-(\d\d)\?season=(\d+)&(?:amp;)?rule=(\d)"')
_ATTR_JSON_RE = re.compile(r'(x-data|data-move-detail)="([^"]*?\{&quot;[^"]*)"')
_STONE_RE = re.compile(r"ナイト\s*([XYZ])?$")
_UPDATED_RE = re.compile(r"更新日\s*(?:<[^>]+>\s*)*(\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d\d)")


def nature(key: int) -> tuple[str, str | None, str | None]:
    """(name, raised stat, lowered stat) for a ``personality_key``; neutral
    natures raise and lower nothing."""
    up, down = _NATURE_STATS[key // 5], _NATURE_STATS[key % 5]
    return NATURES[key], (None if up == down else up), (None if up == down else down)


# ---------------------------------------------------------------- parsing

def parse_ranking(page: str, season: int, rule: int) -> list[tuple[int, int]]:
    """The usage ranking as ``(dex, form)`` keys, best first, de-duplicated."""
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for dex, form, s, r in _SHOW_RE.findall(page):
        key = (int(dex), int(form))
        if int(s) == season and int(r) == rule and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def parse_seasons(page: str) -> dict[int, str]:
    """Every season in the selector -> its regulation (``{8: "M-C", ...}``)."""
    return {int(n): reg for n, reg in _SEASON_RE.findall(page)}


def parse_updated(page: str) -> str | None:
    """The ranking's "更新日" (last refresh) stamp, if shown."""
    m = _UPDATED_RE.search(page)
    return m.group(1) if m else None


def _section(page: str, start: str, end: str) -> str:
    i = page.find(start)
    if i < 0:
        return ""
    j = page.find(end, i + len(start))
    return page[i: j if j > 0 else len(page)]


def _json_blobs(page: str) -> list[tuple[str, Any]]:
    """Every JSON value embedded in an Alpine ``x-data`` / ``data-move-detail``
    attribute, as (attribute, value). ``x-data`` wraps it in a call
    (``window.usagePieChart([...])``), which is stripped."""
    out = []
    for attr, raw in _ATTR_JSON_RE.findall(page):
        text = html.unescape(raw).strip()
        if attr == "x-data":
            i, j = text.find("("), text.rfind(")")
            if i < 0 or j < i:
                continue
            text = text[i + 1: j]
        try:
            out.append((attr, json.loads(text)))
        except ValueError:
            continue
    return out


def _rated(rows: list[dict], key: str, *, named: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda r: r.get("rank") or 0):
        if r.get(key) is None or r.get("rate") is None:
            continue
        if any(e["id"] == int(r[key]) for e in out):
            continue
        entry = {"id": int(r[key]), "rate": float(r["rate"])}
        if named:
            entry["name"] = r.get("name") or ""
        out.append(entry)
    return out[:TOP_N]


def parse_pokemon(page: str) -> dict[str, Any]:
    """One Pokémon page -> its usage numbers, keyed by game ids.

    Returns:
        ``{"moves", "abilities", "natures", "items": [{"id", "rate"}, ...],
        "spreads": [{"rate", "sp": [h, a, b, c, d, s]}, ...],
        "teammates": [[dex, form], ...]}``, each best first; items also
        carry their Japanese ``name``.
    """
    moves, pies = [], {}
    for attr, value in _json_blobs(page):
        if attr == "data-move-detail" and isinstance(value, dict):
            moves.append(value)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for key in ("ability_key", "personality_key", "item_key"):
                if key in value[0]:
                    pies.setdefault(key, value)

    # Stat Points: the "個別" (raw) view lists each exact spread; the default
    # "合算" view merges near-identical ones and is not a real spread.
    spreads = []
    raw = _section(page, "statViewMode === 'raw'\"", "</ul>")
    for item in re.split(r'<li class="usage-list-item usage-list-item--stats"', raw)[1:]:
        rate = re.search(r'usage-rate[^"]*">\s*([\d.]+)%', item)
        chips = re.findall(
            r'pokemon-stat-spread__label">\s*([HABCDS])\s*</span>\s*'
            r'<span class="pokemon-stat-spread__value[^"]*">\s*(\d+)', item)
        if not rate or not chips:
            continue
        pts = dict.fromkeys(_SP_ORDER, 0)
        for label, value in chips:
            pts[_SP_COLS[label]] = int(value)
        spreads.append({"rate": float(rate.group(1)), "sp": [pts[c] for c in _SP_ORDER]})
        if len(spreads) >= TOP_N:
            break

    teammates: list[list[int]] = []
    for dex, form, _s, _r in _SHOW_RE.findall(_section(page, "<h3>同じチーム</h3>", "</ul>")):
        key = [int(dex), int(form)]
        if key not in teammates:
            teammates.append(key)

    return {
        "moves": _rated(moves, "move_key"),
        "abilities": _rated(pies.get("ability_key", []), "ability_key"),
        "natures": _rated(pies.get("personality_key", []), "personality_key"),
        # names kept for items: pokedb numbers some Mega Stones differently
        # from pokechamp, and those resolve by name through the holder
        "items": _rated(pies.get("item_key", []), "item_key", named=True),
        "spreads": spreads,
        "teammates": teammates[:TOP_N],
    }


# ---------------------------------------------------------------- client

class PokedbUsageClient:
    """Reads ranked usage pages from champs.pokedb.tokyo, caching parsed data.

    The site asks applications to fetch to their own server and cache rather
    than proxy end-user requests; this runs once per pull, never at serve time.
    """

    def __init__(self, settings: Settings, cache: Cache) -> None:
        self._cache = cache
        self._http = httpx.AsyncClient(
            base_url=settings.pokedb_base,
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.user_agent, "Accept-Language": "ja"},
            follow_redirects=True,
        )
        self._sem = asyncio.Semaphore(max(1, settings.pokedb_concurrency))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _page(self, path: str) -> str:
        """GET one HTML page, retrying transient failures (429/5xx/timeouts)."""
        async with self._sem:
            for attempt in range(4):
                try:
                    resp = await self._http.get(path)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp)
                    resp.raise_for_status()
                    return resp.text
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if attempt == 3 or (status is not None and status < 500 and status != 429):
                        raise
                    await asyncio.sleep(2 ** attempt)
                finally:
                    await asyncio.sleep(0.25)  # stay polite across the batch
        raise AssertionError("unreachable")

    async def ranking(self, season: int | None, rule: int) -> dict[str, Any]:
        """One rule's ranking for a season (None = the site's current one).

        Returns:
            ``{"season", "seasons": {n: reg}, "updated", "ranking": [[dex, form], ...]}``.
        """
        key = f"pokedb-usage:{_CACHE_VERSION}:list:{season}:{rule}"
        cached = await self._cache.get(key)
        if cached:
            return cached
        query = f"rule={rule}" + (f"&season={season}" if season else "")
        page = await self._page(f"/pokemon/list?{query}")
        seasons = parse_seasons(page)
        if season is None:
            found = {int(s) for _d, _f, s, _r in _SHOW_RE.findall(page)}
            season = max(found) if found else max(seasons, default=0)
        data = {
            "season": season,
            "seasons": {str(k): v for k, v in seasons.items()},
            "updated": parse_updated(page),
            "ranking": [list(k) for k in parse_ranking(page, season, rule)],
        }
        await self._cache.set(key, data, ttl=_TTL)
        return data

    async def pokemon(self, season: int, rule: int, dex: int, form: int) -> dict[str, Any]:
        """One Pokémon's parsed usage for a season and rule."""
        key = f"pokedb-usage:{_CACHE_VERSION}:show:{season}:{rule}:{dex:04d}-{form:02d}"
        cached = await self._cache.get(key)
        if cached:
            return cached
        page = await self._page(f"/pokemon/show/{dex:04d}-{form:02d}?season={season}&rule={rule}")
        data = parse_pokemon(page)
        await self._cache.set(key, data, ttl=_TTL)
        return data


# ---------------------------------------------------------------- ingest

def _pick_season(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """The newest season, or the previous one while the newest is too young
    to be representative and the regulation hasn't changed."""
    if previous is None or len(current["ranking"]) >= MIN_RANKED:
        return current
    regs = current["seasons"]
    same_reg = regs.get(str(current["season"])) == regs.get(str(previous["season"]))
    return previous if same_reg and len(previous["ranking"]) > len(current["ranking"]) else current


def _stone_for(slug: str, ja_name: str, stones: dict[str, dict[str, str]]) -> tuple[str, str] | None:
    """A Mega Stone pokedb numbers differently from pokechamp (its newer
    stones: カイリュナイト, ライチュウナイトＸ...) -> (name, slug), via the
    holder's own Mega forms; the X/Y/Z suffix picks among several."""
    m = _STONE_RE.search(unicodedata.normalize("NFKC", ja_name or ""))
    if not m:
        return None
    by_suffix = stones.get(slug) or {}
    name = by_suffix.get(m.group(1) or "")
    if name is None and not m.group(1) and len(by_suffix) == 1:
        name = next(iter(by_suffix.values()))
    return (name, slugify(name)) if name else None


def _usage_rows(
    slug: str, fmt: str, data: dict[str, Any], refs: dict[str, dict[int, tuple[str, str]]],
    skipped: dict[str, int], stones: dict[str, dict[str, str]],
) -> list[tuple]:
    """One Pokémon's parsed page -> ``usage`` rows (schema column order)."""
    rows: list[tuple] = []

    def plain(category: str, entries: list[dict], ref: dict[int, tuple[str, str]]) -> None:
        rank = 0
        for e in entries:
            hit = ref.get(e["id"])
            if not hit and category == "held_item":
                hit = _stone_for(slug, e.get("name", ""), stones)
            if not hit:
                skipped[f"unknown_{category}"] = skipped.get(f"unknown_{category}", 0) + 1
                continue
            rank += 1
            name, name_slug = hit
            rows.append((slug, fmt, category, rank, name, name_slug, f"{e['rate']}%",
                         e["rate"], None, None, None, None, None, None, None, None))

    plain("move", data["moves"], refs["moves"])
    plain("ability", data["abilities"], refs["abilities"])
    plain("held_item", data["items"], refs["items"])
    for rank, e in enumerate((e for e in data["natures"] if 0 <= e["id"] < 25), start=1):
        name, up, down = nature(e["id"])
        rows.append((slug, fmt, "stat_alignment", rank, name, name.lower(), f"{e['rate']}%",
                     e["rate"], up, down, None, None, None, None, None, None))
    for rank, e in enumerate(data["spreads"], start=1):
        rows.append((slug, fmt, "stat_points", rank, None, None, f"{e['rate']}%", e["rate"],
                     None, None, *e["sp"]))
    return rows


async def ingest_usage(
    db: Db, settings: Settings, *,
    roster: dict[tuple[int, int], str],
    refs: dict[str, dict[int, tuple[str, str]]],
) -> dict[str, Any]:
    """Write ``usage`` / ``usage_rank`` / ``teammates`` (and the roster's rank
    columns) from pokedb's ranked-ladder stats, both formats.

    Args:
        db: Open database handle, with `pokemon` already populated.
        settings: Runtime settings (pokedb base URL, concurrency, cache path).
        roster: ``(dex, form)`` -> slug for every legal non-Mega roster entry.
        refs: ``{"moves"|"abilities"|"items": {game id: (name, slug)}}``.

    Returns:
        Row counts plus which season each format was read from.
    """
    # A species with a single roster entry takes every pokedb form of that
    # dex number (Vivillon's patterns, Maushold's family sizes are cosmetic).
    by_dex: dict[int, set[str]] = {}
    for (dex, _form), slug in roster.items():
        by_dex.setdefault(dex, set()).add(slug)

    def resolve(dex: int, form: int) -> str | None:
        slug = roster.get((dex, form))
        if slug:
            return slug
        only = by_dex.get(dex) or set()
        return next(iter(only)) if len(only) == 1 else None

    names = {r["slug"]: r["battle_name"] for r in db.query("SELECT slug, battle_name FROM pokemon")}
    # holder slug -> {"X"|"Y"|"Z"|"": stone name}, for stones matched by name
    stones: dict[str, dict[str, str]] = {}
    for r in db.query("SELECT species_slug, mega_kind, mega_stone FROM mega_forms "
                      "WHERE mega_stone IS NOT NULL"):
        stones.setdefault(r["species_slug"], {})[r["mega_kind"].replace("Mega", "").strip()] = r["mega_stone"]
    # a form without Megas of its own falls back to its dex siblings' stones
    # (Meowstic's Mega is filed under the male entry; the female holds it too)
    for (dex, _form), slug in roster.items():
        if slug not in stones:
            shared = {k: v for sib in sorted(by_dex.get(dex, ())) for k, v in stones.get(sib, {}).items()}
            if shared:
                stones[slug] = shared
    cache = Cache(settings.cache_db)
    client = PokedbUsageClient(settings, cache)
    rows_usage: list[tuple] = []
    rows_rank: list[tuple] = []
    rows_mates: list[tuple] = []
    ranks: dict[str, dict[str, int]] = {fmt: {} for fmt in RULES}
    skipped: dict[str, int] = {}
    sources: dict[str, Any] = {}
    try:
        for fmt, rule in RULES.items():
            current = await client.ranking(None, rule)
            previous = None
            if len(current["ranking"]) < MIN_RANKED and current["season"] > 1:
                previous = await client.ranking(current["season"] - 1, rule)
            chosen = _pick_season(current, previous)
            season = chosen["season"]
            sources[fmt] = {
                "season": f"M-{season}",
                "regulation": chosen["seasons"].get(str(season)),
                "updated": chosen["updated"], "ranked": len(chosen["ranking"]),
            }

            entries: list[tuple[str, tuple[int, int]]] = []
            for dex, form in chosen["ranking"]:
                slug = resolve(dex, form)
                if not slug:
                    skipped["unmapped_pokemon"] = skipped.get("unmapped_pokemon", 0) + 1
                    continue
                if slug in ranks[fmt]:
                    continue  # a cosmetic form of an entry already ranked higher
                ranks[fmt][slug] = len(ranks[fmt]) + 1
                entries.append((slug, (dex, form)))

            pages = await asyncio.gather(*(
                client.pokemon(season, rule, dex, form) for _slug, (dex, form) in entries))
            for (slug, _key), data in zip(entries, pages):
                rows_rank.append((fmt, ranks[fmt][slug], slug))
                rows_usage += _usage_rows(slug, fmt, data, refs, skipped, stones)
                mates = [m for m in (resolve(d, f) for d, f in data["teammates"]) if m and m != slug]
                for rank, mate in enumerate(dict.fromkeys(mates), start=1):
                    rows_mates.append((slug, fmt, rank, names.get(mate, mate), mate))
    finally:
        await client.aclose()
        cache.close()

    db.executemany("INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_usage)
    db.executemany("INSERT INTO usage_rank VALUES (?,?,?)", rows_rank)
    db.executemany("INSERT INTO teammates VALUES (?,?,?,?,?)", rows_mates)
    db.executemany(
        "UPDATE pokemon SET rank_doubles = ?, rank_singles = ? WHERE slug = ?",
        [(ranks["doubles"].get(s), ranks["singles"].get(s), s) for s in names],
    )
    return {
        "usage": len(rows_usage), "usage_rank": len(rows_rank), "teammates": len(rows_mates),
        "sources": sources, **{f"skipped_{k}": v for k, v in skipped.items()},
    }
