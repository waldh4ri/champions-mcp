"""pokedb ranked-usage parsing (pull/pokedb_usage.py), on recorded pages.

Network-free: the fixture is the M-8 singles ranking and Garchomp's singles
page as served on 2026-11-10, so a change in pokedb's markup shows up here
(and in the live check in test_upstreams.py) rather than as an empty table.
"""

from __future__ import annotations

import json
import lzma
from pathlib import Path

from champions_mcp.pull import pokedb_usage as pu

_PAGES = json.loads(lzma.decompress(
    (Path(__file__).parent / "fixtures" / "pokedb_usage.json.xz").read_bytes()))


def test_ranking_and_season_selector():
    page = _PAGES["list"]
    ranking = pu.parse_ranking(page, 8, 0)
    assert len(ranking) > 200
    assert ranking[0] == (445, 0)                 # Garchomp tops M-8 singles
    assert len(set(ranking)) == len(ranking)
    assert pu.parse_seasons(page)[8] == "M-C"
    assert pu.parse_seasons(page)[5] == "M-B"
    assert pu.parse_updated(page) == "2026/11/10 08:35"


def test_pokemon_page_is_read_by_game_id():
    d = pu.parse_pokemon(_PAGES["show"])
    assert d["moves"][0] == {"id": 89, "rate": 76.0}          # Earthquake
    assert d["abilities"][0] == {"id": 24, "rate": 99.3}      # Rough Skin
    assert d["natures"][0] == {"id": 13, "rate": 31.2}        # Jolly
    assert d["items"][0]["id"] == 2640                        # Garchompite Z
    assert d["items"][0]["rate"] == 32.9
    assert all(len(v) <= pu.TOP_N for v in d.values())


def test_spreads_are_exact_not_merged():
    """The raw view's spreads, not the merged "AS 46.3%" bucket."""
    spreads = pu.parse_pokemon(_PAGES["show"])["spreads"]
    assert spreads[0] == {"rate": 34.9, "sp": [2, 32, 0, 0, 0, 32]}
    assert all(sum(s["sp"]) <= 66 for s in spreads)


def test_teammates():
    mates = pu.parse_pokemon(_PAGES["show"])["teammates"]
    assert mates[0] == [730, 0]                   # Primarina
    assert [445, 0] not in mates


def test_natures_follow_game_order():
    assert pu.nature(13) == ("Jolly", "Spe", "SpA")
    assert pu.nature(3) == ("Adamant", "Atk", "SpA")
    assert pu.nature(23) == ("Careful", "SpD", "SpA")
    assert pu.nature(12) == ("Serious", None, None)


def test_season_falls_back_only_within_a_regulation():
    seasons = {"8": "M-C", "7": "M-C", "6": "M-C", "5": "M-B"}
    young = {"season": 8, "seasons": seasons, "ranking": [[1, 0]] * 10}
    prev = {"season": 7, "seasons": seasons, "ranking": [[1, 0]] * 250}
    assert pu._pick_season(young, prev) is prev
    grown = {**young, "ranking": [[1, 0]] * 200}
    assert pu._pick_season(grown, prev) is grown
    new_reg = {"season": 6, "seasons": seasons, "ranking": [[1, 0]] * 10}
    old_reg = {"season": 5, "seasons": seasons, "ranking": [[1, 0]] * 250}
    assert pu._pick_season(new_reg, old_reg) is new_reg


def test_usage_comes_from_the_ladder(db):
    src = db.meta("usage", {}).get("sources", {})
    assert set(src) == {"singles", "doubles"}
    assert all(s["season"].startswith("M-") for s in src.values())


def test_items_keep_their_name_for_stone_fallback():
    items = pu.parse_pokemon(_PAGES["show"])["items"]
    assert items[0]["name"] == "ガブリアスナイトＺ"


def test_renumbered_mega_stones_resolve_through_the_holder():
    """pokedb gives its newer stones ids pokechamp doesn't use (3001+)."""
    stones = {"raichu": {"X": "Raichunite X", "Y": "Raichunite Y"},
              "dragonite": {"": "Dragoninite"}}
    assert pu._stone_for("raichu", "ライチュウナイトＹ", stones) == ("Raichunite Y", "raichunite-y")
    assert pu._stone_for("dragonite", "カイリュナイト", stones) == ("Dragoninite", "dragoninite")
    assert pu._stone_for("raichu", "ライチュウナイト", stones) is None   # ambiguous X/Y
    assert pu._stone_for("dragonite", "いのちのたま", stones) is None


def test_no_mega_stone_usage_is_dropped(db):
    skipped = db.meta("usage", {})
    assert not skipped.get("skipped_unknown_held_item"), skipped
    dragonite = db.usage_rows("dragonite", "singles", "held_item")
    assert any(r["name"] == "Dragoninite" for r in dragonite)
