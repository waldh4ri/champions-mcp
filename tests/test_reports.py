"""BSS team-report scrape (pull/reports.py): offline resolution logic.

Network-free: the scrape itself is exercised by `champions-mcp-pull`; these
cover how Japanese report data resolves against the Champions database, which
is where silent data loss would happen.
"""

from __future__ import annotations

import json

from champions_mcp.pull import reports
from champions_mcp.pull import reports_extract as extract


def test_species_labels_resolve_to_roster_slugs(db):
    assert reports._species_slug(db, "Mega Garchomp Z") == "garchomp"
    assert reports._species_slug(db, "Mega Charizard X") == "charizard"
    assert reports._species_slug(db, "Galarian Slowking") is not None
    assert reports._species_slug(db, "Rotom (Wash)") is not None
    assert reports._species_slug(db, "Agumon") is None


def test_champions_only_mega_stones_resolve_from_japanese(db):
    """Z stones aren't in PokeAPI's item names: resolved via the holder's Megas."""
    assert reports._stone(db, {"en": "Garchomp"}, "ガブリアスナイトＺ") == "Garchompite Z"
    assert reports._stone(db, {"en": "Mega Charizard Y"}, "リザードナイトY") == "Charizardite Y"
    assert reports._stone(db, {"en": "Garchomp"}, "いのちのたま") is None


def test_season_and_rank_parsing():
    assert reports._event_from_tags("シーズンM-7 ランクバトル", 8) == ("Ladder", 7)
    assert reports._rank_rate_from_title("【S7最終1位】ルカガブzzz R2548") == (1, 2548.0)
    assert reports._seasons_for(("M-C",)) == [6, 7, 8]


def test_spread_parsing_from_article_text():
    assert extract._sp_from_text("H32 A2 S32") == [32, 2, 0, 0, 0, 32]
    assert extract._sp_from_text("HP:24 / 防御:11 / 素早:19") == [24, 0, 11, 0, 0, 19]
    assert extract._sp_from_text("252-0-0-252-4-0") is None  # not labelled: not a spread


class _Lex:
    """Minimal stand-in for extract.Lexicon (normally built from PokeAPI CSVs)."""

    def __init__(self, table):
        self.index = {kind: {ja: (kind, ja) for ja in t} for kind, t in table.items()}
        self._en = {(kind, ja): en for kind, t in table.items() for ja, en in t.items()}
        self.nature_ja2en = table["nature"]

    def name(self, key, lang="ja"):
        return self._en[key] if lang == "en" else key[1]


def _report(members, **kw):
    t = dict(url="https://pokesol.app/u/x/articles/1", title="【M-7】test", trainer="x", season=7,
             rank=5, rate=2450.0, sources=["pokedb"], setsFrom="structured")
    t.update(kw)
    t["members"] = members
    return t


def test_recorded_corpus_replays_through_the_real_scrapers(db, tmp_path):
    """The whole pipeline (listing parser, pokesol + blog + note parsers,
    Japanese resolution, Limitless + pokedb ingest) on recorded pages."""
    import sys
    from pathlib import Path
    from shutil import copy2

    from champions_mcp.db import Db
    sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
    import corpus

    path = tmp_path / "seeded.sqlite"
    copy2(db.path, path)
    sdb = Db(path)
    out = corpus.seed(sdb)
    full = out["full_teams"]
    assert full["listed_pokedb"] >= 30 and full["reports_in_regulation"] >= 30
    assert full["sets_structured"] >= 10 and full["sets_parsed"] >= 1  # pokesol + free-form blogs both work
    assert full["full_teams"] >= 10
    rows = sdb.query("SELECT * FROM full_teams")
    assert {r["regulation"] for r in rows} == {"M-C"}
    assert any("pokesol" in r["url"] for r in rows)
    for r in rows:
        members = json.loads(r["members_json"])
        assert len(members) == 6 and len({m["species"] for m in members}) == 6
        for m in members:
            assert len(m["moves"]) == 4 and m["ability"] and m["nature"]
            assert sum(m["stat_points"].values()) <= 66
    builds = out["builds"]
    assert builds.get("limitless_doubles", 0) >= 100 and builds.get("pokedb_single", 0) >= 50
    # Z-Mega stones from the Japanese ladder export resolve (they aren't in PokeAPI)
    assert sdb.query_one("SELECT count(*) n FROM build_members WHERE item_slug LIKE '%-z'")["n"] > 0
    assert sdb.query_one("SELECT count(*) n FROM build_members bm JOIN build_teams bt USING(team_id) "
                         "WHERE bt.source='pokedb' AND bm.item IS NOT NULL AND bm.item_slug IS NULL")["n"] == 0


def test_complete_report_team_is_ingested_with_its_spreads(db, tmp_path):
    lex = _Lex({
        "move": {"じしん": "Earthquake", "げきりん": "Outrage", "がんせきふうじ": "Rock Tomb", "まもる": "Protect"},
        "ability": {"さめはだ": "Rough Skin"},
        "item": {"いのちのたま": "Life Orb"},
        "nature": {"ようき": "Jolly"},
    })
    rec = {"en": "Garchomp", "dex": 445, "mega": False, "ja": "ガブリアス"}
    good = {"item": "", "ability": "さめはだ", "nature": "ようき",
            "moves": ["じしん", "げきりん", "がんせきふうじ", "まもる"], "sp": [2, 32, 0, 0, 0, 32]}
    mega = dict(good, item="ガブリアスナイトＺ")
    team = _report([(rec, "ガブリアスナイトＺ", mega)] + [(rec, "いのちのたま", good)] * 5)
    bad_spread = _report([(rec, "", dict(good, sp=[32, 32, 32, 0, 0, 0]))] * 6, url="https://x/2")
    no_sets = _report([(rec, "", None)] * 6, url="https://x/3", setsFrom=None)

    from shutil import copy2

    from champions_mcp.db import Db
    path = tmp_path / "t.sqlite"
    copy2(db.path, path)
    tdb = Db(path)
    tdb.ensure_schema()
    stats = reports.ingest_reports(tdb, [team, bad_spread, no_sets], lex, ("M-C",))
    assert stats["full_teams"] == 1 and stats["skipped_bad_spread"] == 1 and stats["skipped_no_sets"] == 1
    row = tdb.query_one("SELECT * FROM full_teams")
    members = json.loads(row["members_json"])
    assert row["rank"] == 5 and row["regulation"] == "M-C" and row["url"].startswith("https://pokesol.app")
    assert members[0]["item"] == "Garchompite Z" and members[1]["item"] == "Life Orb"
    assert members[0]["moves"] == ["Earthquake", "Outrage", "Rock Tomb", "Protect"]
    assert members[0]["stat_points"] == {"hp": 2, "attack": 32, "speed": 32}
    assert members[0]["nature"] == "Jolly" and members[0]["ability"] == "Rough Skin"
