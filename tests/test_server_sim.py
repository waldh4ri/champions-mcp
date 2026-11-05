"""The simulation tools as MCP clients see them: registration, schemas and
descriptions, and real calls through server.py.

Needs MCP SDK 2.x (server.py's `MCPServer`), and the champions-sim image for
the calls; each part skips cleanly when its prerequisite is missing.
"""

from __future__ import annotations

import asyncio

import pytest

server = pytest.importorskip("champions_mcp.server", exc_type=ImportError)

from test_sim import needs_image  # noqa: E402

SIM_TOOLS = {"simulate_vs_meta", "compare_team_variants", "simulate_matchup", "simulate_battle_log"}


def _tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_simulation_tools_are_registered_with_full_descriptions(db):
    tools = _tools()
    assert SIM_TOOLS <= set(tools)
    for name in SIM_TOOLS:
        t = tools[name]
        props = t.input_schema["properties"]
        assert "information" in props and "format" in props and "seed" in props
        # the shared team-input notes must survive registration (decorator order)
        assert "Teams: a list of 6 member dicts" in t.description
        assert "open_sheets" in t.description


def _bss_team():
    sim = server.App.get().sim
    return sim.meta_opponents("bss", 1)[0][0]["paste"]


@needs_image
def test_simulate_vs_meta_tool(db):
    r = asyncio.run(server.simulate_vs_meta(team=_bss_team(), format="bss", opponents=3, games_per_opponent=2))
    assert "error" not in r, r
    assert r["games"] == 6 and r["errors"] == 0 and r["information"] == "open_sheets"


@needs_image
def test_compare_team_variants_tool(db):
    base = _bss_team()
    r = asyncio.run(server.compare_team_variants(base_team=base, variants=[base], format="bss",
                                                 opponents=2, games_per_opponent=2))
    assert "error" not in r, r
    assert r["variants"][1]["delta_vs_base"]["win_rate"] == 0


@needs_image
def test_battle_log_tool_and_bad_input(db):
    team = _bss_team()
    r = asyncio.run(server.simulate_battle_log(team_a=team, team_b=team, format="bss", seed=5))
    assert "error" not in r and r["log"], r
    # bad input comes back as an error payload, never an exception
    bad = asyncio.run(server.simulate_matchup(team_a=[{"species": "Agumon", "moves": ["Tackle"]}],
                                              team_b=team, format="bss", games=2))
    assert "error" in bad and "Agumon" in bad["error"]
    bad = asyncio.run(server.simulate_matchup(team_a=team, team_b=team, format="bss", games=2,
                                              information="telepathy"))
    assert "error" in bad
