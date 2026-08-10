"""Tests for the observed-build layer.

Split in two: the pure normalisation logic (name/form/item resolution, format
classification, weighting) runs against a synthetic in-memory database and
always executes; the read-model tests need an ingested corpus and skip when the
local mirror has not been pulled with it.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from champions_mcp import builds as builds_read
from champions_mcp.db import Db
from champions_mcp.pull.builds import (
    BuildIngest,
    _roster_index,
    classify_format,
    observation_weight,
    resolve_ja,
)


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #

@pytest.fixture()
def mini_db(tmp_path) -> Db:
    """A minimal roster covering every form-resolution edge case."""
    db = Db(tmp_path / "mini.sqlite", create=True)
    roster = [
        # slug, battle_name, showdown_name, ja
        ("garchomp", "Garchomp", "Garchomp", "ガブリアス"),
        ("rotom", "Rotom", "Rotom", "ロトム"),
        ("rotom-wash", "Rotom Wash", "Rotom-Wash", "ロトム"),
        ("rotom-heat", "Rotom Heat", "Rotom-Heat", "ロトム"),
        ("basculegion-male", "Basculegion Male", "Basculegion", "イダイトウ"),
        ("basculegion-female", "Basculegion Female", "Basculegion-F", "イダイトウ"),
        ("samurott", "Samurott", "Samurott", "ダイケンキ"),
        ("hisuian-samurott", "Hisuian Samurott", "Samurott-Hisui", "ダイケンキ"),
        ("raichu", "Raichu", "Raichu", "ライチュウ"),
        ("alolan-raichu", "Alolan Raichu", "Raichu-Alola", "ライチュウ"),
        ("floette", "Floette", "Floette", "フラエッテ"),
        ("vivillon-fancy-pattern", "Vivillon Fancy Pattern", "Vivillon-Fancy", "ビビヨン"),
        ("mimikyu", "Mimikyu", "Mimikyu", "ミミッキュ"),
    ]
    db.executemany(
        "INSERT INTO pokemon VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (slug, battle, battle, showdown, None, "[]", "[]",
             '{"hp":100,"attack":100,"defense":100,"special_attack":100,'
             '"special_defense":100,"speed":100}', 600, "[]", None,
             json.dumps({"ja": ja}), None, None)
            for slug, battle, showdown, ja in roster
        ],
    )
    db.executemany(
        "INSERT INTO items VALUES (?,?,?,?,?)",
        [
            ("life-orb", "Life Orb", "held-items", None, 0),
            ("choice-scarf", "Choice Scarf", "held-items", None, 0),
            ("focus-sash", "Focus Sash", "held-items", None, 0),
            ("garchompite", "Garchompite", "mega-stones", None, 1),
            ("raichunite-x", "Raichunite X", "mega-stones", None, 1),
            ("raichunite-y", "Raichunite Y", "mega-stones", None, 1),
        ],
    )
    db.executemany(
        "INSERT INTO mega_forms VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("Mega Garchomp", "garchomp", "Garchomp", "Mega", "Garchompite",
             "[]", "Sand Force", "{}", 700, "{}", 100),
            ("Mega Raichu X", "raichu", "Raichu", "Mega X", "Raichunite X",
             "[]", "Static", "{}", 600, "{}", 100),
            ("Mega Raichu Y", "raichu", "Raichu", "Mega Y", "Raichunite Y",
             "[]", "Static", "{}", 600, "{}", 100),
        ],
    )
    return db


# --------------------------------------------------------------------- #
# Japanese species/form resolution (pokedb)
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("name", "form", "expected"),
    [
        ("ガブリアス", "", "garchomp"),
        # Appliance forms must not collapse to the base Rotom.
        ("ロトム", "ウォッシュロトム", "rotom-wash"),
        ("ロトム", "ヒートロトム", "rotom-heat"),
        ("ロトム", "", "rotom"),
        # Male is the base entry, female carries the -F suffix.
        ("イダイトウ", "オスのすがた", "basculegion-male"),
        ("イダイトウ", "メスのすがた", "basculegion-female"),
        # Regional forms resolve through the Showdown-name suffix.
        ("ダイケンキ", "ヒスイのすがた", "hisuian-samurott"),
        ("ダイケンキ", "", "samurott"),
        ("ライチュウ", "", "raichu"),
        # A form string with a single roster entry needs no disambiguation.
        ("ミミッキュ", "ばけたすがた", "mimikyu"),
        ("フラエッテ", "えいえんのはな", "floette"),
    ],
)
def test_resolve_ja(mini_db: Db, name: str, form: str, expected: str) -> None:
    by_ja, _ = _roster_index(mini_db)
    assert resolve_ja(by_ja, name, form) == expected


def test_resolve_ja_unknown_species(mini_db: Db) -> None:
    by_ja, _ = _roster_index(mini_db)
    assert resolve_ja(by_ja, "ピカチュウ", "") is None


# --------------------------------------------------------------------- #
# English species resolution (Limitless)
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Garchomp", "garchomp"),
        ("Rotom-Wash", "rotom-wash"),
        ("Wash Rotom", "rotom-wash"),          # upstream word order
        ("Samurott-Hisui", "hisuian-samurott"),
        ("Vivillon", "vivillon-fancy-pattern"),  # only pattern in Champions
        ("Floette-Eternal", "floette"),
        ("Eternal Flower Floette", "floette"),
        # Megas are the base species plus a held stone, not roster entries.
        ("Garchomp-Mega", "garchomp"),
        ("Raichu-Mega-Y", "raichu"),
    ],
)
def test_resolve_en(mini_db: Db, name: str, expected: str) -> None:
    assert BuildIngest(mini_db, {})._resolve_en(name) == expected


def test_gender_symbol_picks_the_right_form(mini_db: Db) -> None:
    """"Basculegion ♀" must not fall back to the male entry.

    Stripping the symbol leaves bare "Basculegion", which resolves to the
    *male* Pokémon — a different typing and movepool. Regression guard.
    """
    ingest = BuildIngest(mini_db, {})
    assert ingest._resolve_en("Basculegion ♀") == "basculegion-female"
    assert ingest._resolve_en("Basculegion") == "basculegion-male"


# --------------------------------------------------------------------- #
# Item resolution
# --------------------------------------------------------------------- #

def test_item_english(mini_db: Db) -> None:
    ingest = BuildIngest(mini_db, {})
    assert ingest._item("Life Orb", japanese=False) == ("Life Orb", "life-orb")
    # Unknown text is kept for visibility but never gets a slug.
    assert ingest._item("Leftover", japanese=False) == ("Leftover", None)


def test_empty_item_slots_are_not_items(mini_db: Db) -> None:
    """"No Item"/持ち物なし mean an empty slot, not an item of that name."""
    ingest = BuildIngest(mini_db, {"life-orb": "いのちのたま"})
    assert ingest._item("No Item", japanese=False) == (None, None)
    assert ingest._item("持ち物なし", japanese=True) == (None, None)
    assert ingest._item("", japanese=False) == (None, None)


def test_japanese_items_and_derived_mega_stones(mini_db: Db) -> None:
    """PokeAPI covers ordinary items; Champions-only stones are derived.

    Japanese stone names elide the species' final mora (ガブリアス ->
    ガブリアスナイト here, but マフォクシー -> マフォクシナイト upstream), and
    dual-Mega species disambiguate with a full-width X/Y.
    """
    ingest = BuildIngest(mini_db, {"life-orb": "いのちのたま"})
    assert ingest._item("いのちのたま", japanese=True) == ("いのちのたま", "life-orb")
    assert ingest._item("ガブリアスナイト", japanese=True)[1] == "garchompite"
    assert ingest._item("ライチュウナイトＹ", japanese=True)[1] == "raichunite-y"
    assert ingest._item("ライチュウナイトX", japanese=True)[1] == "raichunite-x"


# --------------------------------------------------------------------- #
# Format classification
# --------------------------------------------------------------------- #

def _team(*movesets: list[str]) -> list[dict]:
    return [{"attacks": m} for m in movesets]


def test_classify_format_by_name() -> None:
    assert classify_format("ChampionMads SINGLES Battle Arena #6", []) == "singles"
    assert classify_format("Champions League VGC August", []) == "doubles"


def test_classify_format_by_moves() -> None:
    """The event name is often silent, so the teams have to decide it."""
    doubles = [_team(
        ["Follow Me", "Protect", "Helping Hand", "Fake Out"],
        ["Protect", "Tailwind", "Moonblast", "Light Screen"],
        ["Protect", "Heat Wave", "Solar Beam", "Weather Ball"],
    )]
    singles = [_team(
        ["Stealth Rock", "Earthquake", "Dragon Tail", "Roar"],
        ["Spikes", "Toxic", "Roost", "Body Press"],
    )]
    assert classify_format("Weekly Cup", doubles) == "doubles"
    assert classify_format("Weekly Cup", singles) == "singles"


# --------------------------------------------------------------------- #
# Weighting
# --------------------------------------------------------------------- #

def test_weight_decays_with_age() -> None:
    today = date(2026, 8, 10)
    fresh = observation_weight(
        "2026-08-10", field_size=32, placing=None, half_life_days=21, today=today)
    old = observation_weight(
        "2026-06-15", field_size=32, placing=None, half_life_days=21, today=today)
    assert fresh > old
    # One half-life back is worth about half as much.
    half = observation_weight(
        "2026-07-20", field_size=32, placing=None, half_life_days=21, today=today)
    assert 0.45 < half / fresh < 0.55


def test_weight_rewards_bigger_fields_sublinearly() -> None:
    today = date(2026, 8, 10)
    small = observation_weight(
        "2026-08-10", field_size=8, placing=None, half_life_days=21, today=today)
    large = observation_weight(
        "2026-08-10", field_size=256, placing=None, half_life_days=21, today=today)
    assert large > small
    # A 32x bigger field must not be worth 32x more.
    assert large / small < 4


def test_weight_survives_a_missing_date() -> None:
    assert observation_weight(
        None, field_size=None, placing=None, half_life_days=21) == 1.0


# --------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------- #

def _limitless_member(name: str, item: str, moves: list[str]) -> dict:
    return {
        "id": name.lower(), "name": name, "item": item, "ability": "Rough Skin",
        "nature": "Jolly", "attacks": moves, "tera": None,
    }


def test_ingest_limitless_roundtrip(mini_db: Db) -> None:
    ingest = BuildIngest(mini_db, {})
    tournament = {
        "id": "t1", "name": "Test Cup", "date": "2026-08-01T00:00:00.000Z",
        "players": 32, "format": "M-B",
    }
    standings = [{
        "name": "Alice", "placing": 1, "record": {"wins": 7, "losses": 1, "ties": 0},
        "decklist": [
            _limitless_member("Garchomp", "Life Orb", ["Earthquake", "Protect"]),
            _limitless_member("Rotom-Wash", "Sitrus Berry", ["Hydro Pump"]),
        ],
    }]
    assert ingest.add_limitless(
        tournament, standings, fmt="doubles", half_life_days=21) == 1
    ingest.flush()
    rows = mini_db.observed_members("garchomp", "doubles")
    assert len(rows) == 1
    assert rows[0]["evidence"] == "full_set"
    assert json.loads(rows[0]["moves_json"]) == ["Earthquake", "Protect"]


def test_partial_teams_are_dropped(mini_db: Db) -> None:
    """A team with an unresolvable member must be discarded whole.

    Keeping the resolvable five would silently understate every co-occurrence
    that team takes part in, which is exactly what this corpus is for.
    """
    ingest = BuildIngest(mini_db, {})
    standings = [{
        "name": "Bob", "placing": 1, "record": {},
        "decklist": [
            _limitless_member("Garchomp", "Life Orb", ["Earthquake"]),
            _limitless_member("Notapokemon", "Life Orb", ["Earthquake"]),
        ],
    }]
    assert ingest.add_limitless(
        {"id": "t2", "name": "X", "date": None, "players": 32, "format": "M-B"},
        standings, fmt="doubles", half_life_days=21) == 0
    assert ingest.skipped.get("incomplete_team") == 1


def test_ingest_pokedb_has_no_moves(mini_db: Db) -> None:
    """The ladder export carries items but no movesets — evidence must say so."""
    ingest = BuildIngest(mini_db, {})
    export = {
        "season": "M-4", "rule": "シングル", "updated_at": "2026-08-10 00:00:00",
        "teams": [{
            "rank": 1, "rating_value": 2100.5,
            "team": [
                {"pokemon": "ガブリアス", "form": "", "item": "こだわりスカーフ"},
                {"pokemon": "ロトム", "form": "ウォッシュロトム", "item": "たべのこし"},
            ],
        }],
    }
    assert ingest.add_pokedb(export, "M-B", half_life_days=21) == 1
    ingest.flush()
    rows = mini_db.observed_members("garchomp", "singles")
    assert len(rows) == 1
    assert rows[0]["evidence"] == "team_and_item"
    assert rows[0]["moves_json"] is None


# --------------------------------------------------------------------- #
# Read model — degradation on an un-ingested database
# --------------------------------------------------------------------- #

def test_read_model_without_corpus_is_explicit(mini_db: Db) -> None:
    """No corpus must produce a clear explanation, never a crash or a guess."""
    out = builds_read.get_observed_sets(mini_db, "Garchomp", "vgc")
    assert out["observed_teams"] == 0
    assert "champions-mcp-pull" in out["error"]


def test_stale_database_without_build_tables(tmp_path) -> None:
    """A mirror from an older pull has no build_teams; the server must cope."""
    db = Db(tmp_path / "stale.sqlite", create=True)
    db.query("DROP TABLE build_teams")
    db.query("DROP TABLE build_members")
    assert db.has_builds_tables() is False
    assert db.builds_available("doubles") is None
    assert db.observed_members("garchomp", "doubles") == []
    assert db.observed_rosters("doubles") == {}


# --------------------------------------------------------------------- #
# Read model — analysis helpers
# --------------------------------------------------------------------- #

def test_effective_variety_distinguishes_settled_from_open() -> None:
    """Distinct-set counts lie; perplexity does not.

    Four options where one takes 97% is a settled decision, even though four
    options exist. An even split over four is a genuinely open one.
    """
    settled = builds_read._effective_variety([0.97, 0.01, 0.01, 0.01])
    open_ = builds_read._effective_variety([0.25, 0.25, 0.25, 0.25])
    assert settled < 1.3
    assert open_ == pytest.approx(4.0, abs=0.01)


def test_move_bands_split_locked_from_contested() -> None:
    movesets = [
        (("Earthquake", "Protect", "Rock Slide", "Dragon Claw"), 1.0),
        (("Earthquake", "Protect", "Rock Slide", "Stomping Tantrum"), 1.0),
        (("Earthquake", "Protect", "Swords Dance", "Scale Shot"), 1.0),
    ]
    out = builds_read._move_analysis(movesets)
    locked = {m["move"] for m in out["locked_moves"]}
    assert {"Earthquake", "Protect"} <= locked
    # Two slots fixed leaves two for the player to decide.
    assert out["free_slots"] == 2


def test_marginal_stacking_check_catches_the_frankenstein_set() -> None:
    """The four most popular moves need never have occurred together."""
    movesets = [
        (("A", "B", "C", "X"), 1.0),
        (("A", "B", "D", "Y"), 1.0),
        (("A", "C", "D", "Z"), 1.0),
    ]
    out = builds_read._naive_trap(movesets)
    assert out["naive_top_moves"] == ["A", "B", "C", "D"]
    assert out["actually_played_percent"] == 0.0
