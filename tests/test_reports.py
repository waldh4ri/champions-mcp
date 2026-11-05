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
