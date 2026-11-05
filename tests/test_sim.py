"""Battle simulator bridge (sim.py) and, when the image is available, the
champions-sim sidecar end to end.

The unit tests need only the local champions.sqlite. The integration tests
run the real simulator through `docker run` and skip when Docker or the
`champions-sim` image isn't there (build it with
`docker build -f docker/sim/Dockerfile -t champions-sim:latest .`).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from champions_mcp import pokepaste
from champions_mcp.config import Settings
from champions_mcp.sim import BattleSimulator, SimError

GARCHOMP = {
    "species": "Garchomp", "item": "Life Orb", "ability": "Rough Skin", "nature": "Jolly",
    "moves": ["Earthquake", "Dragon Claw", "Rock Slide", "Protect"],
    "stat_points": {"hp": 2, "attack": 32, "speed": 32},
}


@pytest.fixture(scope="module")
def sim(db) -> BattleSimulator:
    return BattleSimulator(Settings.load(), db)


# ------------------------------------------------------------------ names

def test_sim_paste_keeps_floette_eternal(db):
    """pokepast.es wants "Floette" (sprite), the simulator needs the real form:
    "Floette" doesn't exist in Gen 9 and can't hold Floettite."""
    m = {"species": "floette", "item": "Floettite", "ability": "Flower Veil",
         "nature": "Modest", "moves": ["Moonblast"]}
    assert pokepaste.member_to_showdown(db, m).startswith("Floette @")
    assert pokepaste.member_to_showdown(db, m, for_sim=True).startswith("Floette-Eternal @")


def test_sim_paste_still_collapses_battle_only_forms(db):
    m = {"species": "aegislash-blade", "item": "Leftovers", "moves": ["Shadow Ball"]}
    assert pokepaste.member_to_showdown(db, m, for_sim=True).startswith("Aegislash @")


# ------------------------------------------------------------ team inputs

def test_missing_spread_nature_ability_are_filled_and_reported(sim):
    bare = {"species": "Garchomp", "item": "Life Orb", "moves": ["Earthquake", "Dragon Claw"]}
    done, notes = sim.complete_member(bare, "doubles")
    assert sum(done["stat_points"].values()) <= 66 and done["stat_points"]
    assert done["nature"] and done["ability"]
    assert len(notes) == 3 and all(n.startswith("Garchomp:") for n in notes)


def test_complete_member_keeps_what_was_given(sim):
    done, notes = sim.complete_member(GARCHOMP, "doubles")
    assert done == GARCHOMP and notes == []


def test_member_without_moves_or_species_is_refused(sim):
    with pytest.raises(SimError, match="no moves"):
        sim.complete_member({"species": "Garchomp"}, "doubles")
    with pytest.raises(SimError, match="unknown species"):
        sim.complete_member({"species": "Agumon", "moves": ["Tackle"]}, "doubles")


def test_paste_text_passes_through(sim):
    paste, notes = sim.team_paste("Garchomp @ Life Orb\n- Earthquake", "vgc")
    assert paste.startswith("Garchomp") and notes == []
    with pytest.raises(SimError):
        sim.team_paste("   ", "vgc")
    with pytest.raises(SimError):
        sim.team_paste([], "vgc")


def test_battle_cap_is_enforced(sim):
    with pytest.raises(SimError, match="cap"):
        sim._cap(sim._s.sim_max_battles + 1)


def test_opponent_id_resolves_to_the_exact_team_the_pool_plays(sim):
    """A worst_matchups `id` replays that very team, not a reconstruction."""
    for fmt in ("bss", "vgc"):
        pool, _ = sim.meta_opponents(fmt, 3)
        for o in pool:
            paste, notes = sim.team_paste(o["id"], fmt)
            assert paste == o["paste"]
            assert notes and o["label"] in notes[0]


def test_a_string_that_is_not_a_team_is_refused(sim):
    """A model once passed 'opponents per tool not relevant to schema' as a
    variant; it was parsed as a one-Pokémon paste."""
    with pytest.raises(SimError, match="not a team"):
        sim.team_paste("opponents per tool not relevant to schema", "bss")
    with pytest.raises(SimError, match="not a team"):
        sim.team_paste("report:does-not-exist", "bss")


def test_errors_name_the_format_that_was_used(sim):
    out = sim._finish({"ok": False, "error": "team is not legal in gen9championsvgc2026regmc"}, [], format="vgc")
    assert out["format"] == "vgc" and "format='bss'" in out["error"]


# -------------------------------------------------------------- meta pool

def test_meta_opponents_are_complete_distinct_teams(sim):
    pool, notes = sim.meta_opponents("vgc", 12)
    assert len(pool) == 12
    assert len({o["id"] for o in pool}) == 12
    for o in pool:
        blocks = [b for b in o["paste"].replace("\r", "").split("\n\n") if b.strip()]
        assert len(blocks) == 6
        assert all("Ability:" in b and "EVs:" in b and b.count("\n- ") >= 1 for b in blocks)
        assert len(o["label"].split(" / ")) == 6
    assert any("spread" in n for n in notes)


def test_bss_pool_is_real_bss_teams(sim, db):
    """BSS opponents: full report teams first, then pokedb ladder teams — no
    VGC top-up while real BSS teams remain."""
    pool, notes = sim.meta_opponents("bss", 40)
    assert len(pool) == 40
    assert not any("VGC tournament teams" in n for n in notes)
    n_full = db.query_one("SELECT count(*) n FROM full_teams WHERE format='singles'")["n"]
    full = [o for o in pool if o["id"].startswith("report:")]
    assert len(full) == min(40, n_full)
    assert pool[:len(full)] == full  # report teams come first
    # Japanese ladder items arrive translated (no raw Japanese in a paste)
    assert all(ord(c) < 0x3000 for o in pool for c in o["paste"])


# ------------------------------------------------- end to end (docker run)

def _image_available() -> bool:
    """A reachable simulator: the HTTP sidecar if configured, else the docker image."""
    url = os.environ.get("CHAMPIONS_MCP_SIM_HTTP")
    if url:
        try:
            import httpx
            return httpx.get(url.rstrip("/") + "/health", timeout=5).json().get("ok") is True
        except Exception:
            return False
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", "champions-sim:latest"],
                       capture_output=True, text=True)
    return r.returncode == 0


needs_image = pytest.mark.skipif(not _image_available(), reason="champions-sim image not built")


def _team(sim, idx: int, fmt: str = "vgc") -> str:
    pool, _ = sim.meta_opponents(fmt, idx + 1)
    return pool[idx]["paste"]


@needs_image
def test_vs_meta_end_to_end(sim):
    r = asyncio.run(sim.vs_meta(_team(sim, 0), "vgc", 4, 4, "heuristic", None, 3))
    assert "error" not in r, r
    # an illegal corpus team (a typo in a scraped teamlist) is dropped and reported, not played
    dropped = r.get("opponents_dropped", [])
    assert r["opponents_used"] + len(dropped) == 4 and all(d["problem"] for d in dropped)
    assert r["games"] == 4 * r["opponents_used"] and r["errors"] == 0
    lo, hi = r["win_rate_ci95"]
    assert 0 <= lo <= r["win_rate"] <= hi <= 1
    assert len(r["members"]) == 6 and r["worst_matchups"][0]["source"]
    assert r["format_id"] == "gen9championsvgc2026regmc"


@needs_image
def test_matchup_is_reproducible(sim):
    a, b = _team(sim, 0), _team(sim, 1)
    r1 = asyncio.run(sim.matchup(a, b, "bss", 12, "heuristic", None, 9))
    r2 = asyncio.run(sim.matchup(a, b, "bss", 12, "heuristic", None, 9))
    assert "error" not in r1, r1
    assert (r1["wins"], r1["losses"], r1["members"]) == (r2["wins"], r2["losses"], r2["members"])


@needs_image
def test_compare_reports_paired_delta(sim):
    base = _team(sim, 0)
    r = asyncio.run(sim.compare(base, [base], "vgc", 3, 4, "heuristic", None, 5))
    assert "error" not in r, r
    delta = r["variants"][1]["delta_vs_base"]
    # identical teams with identical seeds: exactly no difference
    assert delta["win_rate"] == 0 and delta["significant"] is False


@needs_image
def test_illegal_team_returns_problems(sim):
    bad = dict(GARCHOMP, item="Choice Band")  # a "Past" item in Champions
    team = [bad] + [dict(GARCHOMP, species=s, item="") for s in
                    ("Kingambit", "Sneasler", "Whimsicott", "Incineroar", "Gholdengo")]
    r = asyncio.run(sim.matchup(team, _team(sim, 0), "vgc", 2, "heuristic", None, 1))
    assert "error" in r and r.get("problems")


@needs_image
@pytest.mark.parametrize("information", ["open_sheets", "closed", "omniscient"])
def test_every_information_mode_runs(sim, information):
    r = asyncio.run(sim.matchup(_team(sim, 0, "bss"), _team(sim, 1, "bss"), "bss", 8, "heuristic",
                                None, 2, information))
    assert "error" not in r, r
    assert r["information"] == information and r["games"] == 8 and r["errors"] == 0


@needs_image
def test_unknown_information_mode_is_refused(sim):
    with pytest.raises(SimError, match="information mode"):
        asyncio.run(sim.matchup(_team(sim, 0), _team(sim, 1), "vgc", 2, "heuristic", None, 1, "telepathy"))


@needs_image
def test_bss_vs_meta_plays_real_report_teams(sim, db):
    if not db.query_one("SELECT count(*) n FROM full_teams WHERE format='singles'")["n"]:
        pytest.skip("no team-report corpus in this database")
    r = asyncio.run(sim.vs_meta(_team(sim, 0, "bss"), "bss", 6, 4, "heuristic", None, 1))
    assert "error" not in r, r
    assert r["games"] == 24 and r["errors"] == 0 and r["format_id"] == "gen9championsbssregmc"
    assert any("published team reports" in a for a in r.get("assumptions", []))
    assert all(len(m["species"]) for m in r["members"])
    assert all(0 <= m["brought_pct"] <= 1 for m in r["members"])


@needs_image
def test_floette_eternal_team_simulates(sim):
    """The sim export must keep Floette-Eternal (paste export maps it to "Floette")."""
    picks = [("floette", "Floettite"), ("garchomp", "Life Orb"), ("kingambit", "Leftovers"),
             ("sneasler", "White Herb"), ("whimsicott", "Focus Sash"), ("basculegion-male", "Mystic Water")]
    team = [{"species": slug, "item": item, "moves": sim.usage_moves(slug, "doubles", item)}
            for slug, item in picks]
    paste, notes = sim.team_paste(team, "vgc")
    assert paste.startswith("Floette-Eternal @ Floettite")
    assert len(notes) >= 6  # spreads/natures/abilities filled from usage, and said so
    r = asyncio.run(sim.matchup(team, _team(sim, 1), "vgc", 4, "heuristic", None, 1))
    assert "error" not in r, r
    assert r["errors"] == 0 and any(m["species"] == "Floette-Eternal" for m in r["members"])


@needs_image
def test_worst_matchup_can_be_replayed_by_id(sim):
    team = _team(sim, 0, "bss")
    r = asyncio.run(sim.vs_meta(team, "bss", 4, 2, "heuristic", None, 1))
    assert "error" not in r, r
    worst = r["worst_matchups"][0]
    log = asyncio.run(sim.replay(team, worst["id"], "bss", "heuristic", 1))
    assert "error" not in log, log
    assert any(worst["label"] in a for a in log["assumptions"])
    assert log["team_b"]["brought"] and log["log"]


@needs_image
def test_reported_leads_are_who_led(sim):
    """Leads used to be read from the end-of-battle party order."""
    r = asyncio.run(sim.replay(_team(sim, 0, "bss"), _team(sim, 1, "bss"), "bss", "heuristic", 3))
    first_in = next(line for line in r["log"] if line.startswith("A:") and " enters" in line)
    assert r["team_a"]["leads"] == [first_in[2:first_in.index(" enters")]]


@needs_image
def test_battle_log_reads_as_a_battle(sim):
    r = asyncio.run(sim.replay(_team(sim, 0), _team(sim, 1), "vgc", "heuristic", 4))
    assert "error" not in r, r
    text = "\n".join(r["log"])
    assert "== Turn 1 ==" in text and "used" in text
    assert r["winner"] in ("A", "B", "tie") and len(r["team_a"]["brought"]) == 4
