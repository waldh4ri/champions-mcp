"""Record the CI test corpus from local pull caches (dev tool, not run in CI).

CI must not run the long scrapes (Limitless pacing, hundreds of team-report
articles). Instead it replays a small recorded slice through the *real*
ingest code (tests/fixtures/corpus.py). This script records that slice from
caches a local `champions-mcp-pull` already filled, so it needs no network
beyond cache misses:

  - Limitless: the largest recent M-C events' standings (full open teamsheets)
  - pokedb: the latest M-C ladder exports (singles trimmed to the top teams)
  - team reports: one pokedb article-index page trimmed to a mix of sources
    (pokesol, yakkun, note, blogs), every linked article, and the PokeAPI
    name tables the parsers need (trimmed to the languages they read)

Output: tests/fixtures/corpus.json.xz (one xz-compressed JSON document: ~0.5 MB
for ~18 MB of pages; pokesol pages embed the site's master data).

Usage: python tests/fixtures/record_corpus.py
"""

from __future__ import annotations

import asyncio
import csv
import lzma
import io
import json
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from champions_mcp.cache import Cache  # noqa: E402
from champions_mcp.config import Settings  # noqa: E402
from champions_mcp.db import Db  # noqa: E402
from champions_mcp.pull import reports  # noqa: E402
from champions_mcp.pull.builds import POKEDB_SEASON_REGULATION  # noqa: E402
from champions_mcp.pull.builds_source import LimitlessClient, PokedbClient  # noqa: E402
from champions_mcp.pull.pokeapi import PokeApiEnricher  # noqa: E402

OUT = Path(__file__).with_name("corpus.json.xz")
LIMITLESS_EVENTS = 3        # the biggest recent events
POKEDB_SINGLE_TEAMS = 60    # top of the ladder export
REPORT_CARDS = 36           # article-index cards kept
NAME_TABLES = ("pokemon_species_names", "pokemon", "move_names", "ability_names", "item_names", "nature_names")
LANGS = {"1", "9", "11"}    # ja-kana, en, ja-kanji


def _trim_csv(text: str) -> str:
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    if "local_language_id" not in head:
        return text
    li = head.index("local_language_id")
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(head)
    w.writerows(r for r in rows[1:] if r and r[li] in LANGS)
    return out.getvalue()


async def _builds(settings: Settings) -> dict:
    cache = Cache(settings.cache_db)
    lim, pdb = LimitlessClient(settings, cache), PokedbClient(settings, cache)
    api = PokeApiEnricher(settings, cache)
    db = Db(settings.db_path)
    try:
        events = await lim.tournaments("M-C", settings.builds_max_events)
        events = sorted((e for e in events if (e.get("players") or 0) >= settings.builds_min_players),
                        key=lambda e: e.get("players") or 0, reverse=True)
        limitless = []
        for ev in events:
            standings = await lim.standings(ev["id"])
            if sum(1 for p in standings if p.get("decklist")) >= 16:
                limitless.append({"event": ev, "standings": standings})
            if len(limitless) == LIMITLESS_EVENTS:
                break
        seasons = sorted(n for n, r in POKEDB_SEASON_REGULATION.items() if r == "M-C")
        pokedb = []
        for rule in ("single", "double"):
            for n in reversed(seasons):
                export = await pdb.season(n, rule)
                if export:
                    if rule == "single":
                        export = {**export, "teams": export["teams"][:POKEDB_SINGLE_TEAMS]}
                    pokedb.append(export)
                    break
        item_names = dict(await api.gather([api.item_ja_name(r["name"]) for r in db.query("SELECT name FROM items")]))
    finally:
        await lim.aclose()
        await pdb.aclose()
        await api.aclose()
        cache.close()
    return {"limitless": limitless, "pokedb": pokedb, "ja_item_names": item_names}


def _reports(settings: Settings) -> dict:
    f = reports.Fetcher(settings.data_dir / "reports-cache")
    seasons = reports._seasons_for(("M-C",))
    url = (f"https://champs.pokedb.tokyo/article/search?rule=0&season_start={seasons[0]}"
           f"&season_end={seasons[-1]}&per_page=100&page=1")
    page = f.get(url)
    marker = '<article class="card article-card">'
    head, *cards = page.split(marker)
    # a mix of sources, so every parser path is exercised
    by_kind: dict[str, list[str]] = {}
    for c in cards:
        m = reports.re.search(r'<footer.*?href="([^"]+)"', c, reports.re.S)
        kind = reports._detail_url(reports.html.unescape(m.group(1)))[1] if m else None
        if kind:
            by_kind.setdefault(kind, []).append(c)
    keep, i = [], 0
    while len(keep) < REPORT_CARDS and any(by_kind.values()):
        kind = list(by_kind)[i % len(by_kind)]
        if by_kind[kind]:
            keep.append(by_kind[kind].pop(0))
        i += 1
    listing = head + marker + marker.join(keep)
    pages = {url: listing}
    for c in keep:
        link = reports.html.unescape(reports.re.search(r'<footer.*?href="([^"]+)"', c, reports.re.S).group(1))
        durl, kind = reports._detail_url(link)
        text = f.get(durl, encoding="euc-jp" if kind == "yakkun" else None)
        if text:
            pages[durl] = text
    for t in NAME_TABLES:
        u = reports.POKEAPI_CSV.format(t)
        pages[u] = _trim_csv(f.get(u))
    kinds = Counter(reports._detail_url(u)[1] for u in pages if "pokeapi" not in u and u != url)
    print("report pages:", dict(kinds))
    return {"report_pages": pages}


def main() -> None:
    settings = Settings.load()
    corpus = {**asyncio.run(_builds(settings)), **_reports(settings)}
    raw = json.dumps(corpus, ensure_ascii=False, separators=(",", ":")).encode()
    OUT.write_bytes(lzma.compress(raw, preset=9 | lzma.PRESET_EXTREME))
    print(f"limitless events: {len(corpus['limitless'])}, pokedb exports: {len(corpus['pokedb'])}")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB xz, {len(raw) / 1e6:.1f} MB raw)")


if __name__ == "__main__":
    main()
