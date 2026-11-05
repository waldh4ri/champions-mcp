"""Live upstream checks: every endpoint the pull depends on is up and still
parses into what the ingest expects.

CI doesn't run the long scrapes (it replays a recorded corpus), so these are
the guard against an upstream moving or changing shape: one or two requests
per source, each fed through the *real* client or parser. Marked `live`
(deselected by default); run with `pytest -m live`.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from champions_mcp.cache import Cache
from champions_mcp.config import Settings
from champions_mcp.pull import reports
from champions_mcp.pull import reports_extract as extract
from champions_mcp.pull.builds_source import LimitlessClient, PokedbClient
from champions_mcp.pull.pokechamp_source import PokechampClient

pytestmark = pytest.mark.live


class FirstPageOnly(reports.Fetcher):
    """A real (uncached) fetcher that refuses pagination, so a listing source
    is checked on one page instead of being crawled."""

    def get(self, url, *, max_age=None, encoding="utf-8"):
        if re.search(r"[?&](page=(?!1\b)\d+|start=(?!0\b)\d+)", url):
            return None
        return super().get(url, max_age=0, encoding=encoding)


@pytest.fixture(scope="module")
def settings(tmp_path_factory) -> Settings:
    s = Settings.load()
    # a throwaway HTTP cache so every check really hits the network
    object.__setattr__(s, "cache_db", tmp_path_factory.mktemp("cache") / "pull-cache.sqlite")
    return s


@pytest.fixture(scope="module")
def fetcher(tmp_path_factory) -> FirstPageOnly:
    return FirstPageOnly(tmp_path_factory.mktemp("pages"))


@pytest.fixture(scope="module")
def dex(fetcher) -> reports.Dex:
    return reports.Dex(lambda t: fetcher.get(reports.POKEAPI_CSV.format(t)) or "")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- roster

def test_pokechamp_regulations(settings):
    async def go():
        cache = Cache(settings.cache_db)
        client = PokechampClient(settings, cache)
        try:
            return await client.regulations()
        finally:
            await client.aclose()
            cache.close()
    regs = _run(go())
    titles = [r.get("title", "") for r in regs]
    assert any("Regulation Set M-" in t for t in titles), titles
    assert any(r.get("endDate") is None for r in regs), "no current regulation"


def test_pokeapi_name_tables(fetcher):
    for table in ("pokemon_species_names", "move_names", "item_names"):
        text = fetcher.get(reports.POKEAPI_CSV.format(table))
        assert text and "local_language_id" in text.splitlines()[0], table


# ------------------------------------------------------- observed teams

def test_limitless_tournaments_and_standings(settings):
    async def go():
        cache = Cache(settings.cache_db)
        client = LimitlessClient(settings, cache)
        try:
            events = await client.tournaments("M-C", 5)
            big = max(events, key=lambda e: e.get("players") or 0)
            return events, await client.standings(big["id"])
        finally:
            await client.aclose()
            cache.close()
    events, standings = _run(go())
    assert events and all("id" in e for e in events)
    lists = [p["decklist"] for p in standings if p.get("decklist")]
    assert lists, "no open teamsheets in the biggest recent event"
    mon = lists[0][0]
    assert {"name", "item", "ability", "attacks"} <= set(mon) or {"species", "item"} <= set(mon), mon


def test_pokedb_ladder_export(settings):
    async def go():
        cache = Cache(settings.cache_db)
        client = PokedbClient(settings, cache)
        try:
            n = max(reports._seasons_for(("M-C",)))
            for season in range(n, 0, -1):  # newest published season
                data = await client.season(season, "single")
                if data:
                    return data
        finally:
            await client.aclose()
            cache.close()
    data = _run(go())
    assert data and data["teams"], "no pokedb singles export"
    team = data["teams"][0]
    assert {"rank", "rating_value", "team"} <= set(team)
    assert len(team["team"]) == 6 and {"pokemon", "item"} <= set(team["team"][0])


# --------------------------------------------------------- team reports

def _check_listing(teams, *, min_resolved=4):
    assert teams, "listing parsed to nothing"
    t = teams[0]
    assert t["url"].startswith("http") and t["title"]
    assert sum(1 for r, _ in t["mons"] if r) >= min_resolved, t


def test_pokedb_article_index(fetcher, dex):
    teams = reports.src_pokedb(fetcher, dex, reports._seasons_for(("M-C",)))
    _check_listing(teams)
    assert any(t["rank"] for t in teams) and any(t["rate"] for t in teams)
    assert any(t["season"] for t in teams)


def test_yakkun_board(fetcher, dex):
    _check_listing(reports.src_yakkun(fetcher, dex, reports._seasons_for(("M-C",))))


def test_damekei_digest(fetcher, dex):
    teams = reports.src_damekei(fetcher, dex, reports._seasons_for(("M-C",)))
    _check_listing(teams, min_resolved=3)


def test_pokemon_teams_collection(fetcher, dex):
    teams = reports.src_pteams(fetcher, dex, reports._seasons_for(("M-C",)))
    assert teams and teams[0]["url"].startswith("https://")


def _article(fetcher, dex, kind):
    for t in reports.src_pokedb(fetcher, dex, reports._seasons_for(("M-C",))):
        url, k = reports._detail_url(t["url"])
        if k == kind:
            page = fetcher.get(url, encoding="euc-jp" if k == "yakkun" else None)
            if page:
                return t, url, page
    pytest.fail(f"no {kind} article linked from the pokedb index")


def test_pokesol_article_has_structured_sets(fetcher, dex):
    _, _, page = _article(fetcher, dex, "pokesol")
    d, master = extract.parse_pokesol(page, None)
    assert master is not None, "pokesol page no longer embeds its master data"
    assert d["setsFrom"] == "structured" and len(d["sets"]) == 6
    s = d["sets"][0]
    assert s["name"] and s["nature"] and len(s["moves"]) == 4 and s["sp"] and len(s["sp"]) == 6


def test_note_article_api(fetcher, dex):
    _, url, page = _article(fetcher, dex, "note")
    text = extract.article_text(page, url)
    assert len(text) > 300, "note API returned no article body"


def test_scraper_writes_into_the_cache_dir(fetcher):
    assert any(Path(fetcher.dir).iterdir())
