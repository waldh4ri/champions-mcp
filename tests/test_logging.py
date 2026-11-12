"""The log format and the server's request/tool-call summaries."""

from __future__ import annotations

import logging

from champions_mcp import log as logmod
from champions_mcp import refresh, server_log


def _record(msg: str, level: int = logging.INFO, **fields) -> logging.LogRecord:
    rec = logging.LogRecord("champions_mcp.tool", level, __file__, 1, msg, (), None)
    rec.fields = fields
    return rec


def test_formatter_plain_line_with_fields_and_request_id():
    token = logmod.request_id.set("ab12cd")
    try:
        line = logmod.Formatter(color=False).format(
            _record("✓ calc_damage", took="22ms", move="Heat Wave", skipped=None))
    finally:
        logmod.request_id.reset(token)
    assert " INFO  tool " in line
    assert "[ab12cd] ✓ calc_damage took=22ms" in line
    assert 'move="Heat Wave"' in line  # values with spaces are quoted
    assert "skipped" not in line  # None fields are dropped
    assert "\x1b[" not in line


def test_formatter_color_only_when_enabled():
    assert "\x1b[" in logmod.Formatter(color=True).format(_record("x"))


def test_color_setting(monkeypatch):
    monkeypatch.setenv("LOG_COLOR", "1")
    assert logmod.color_enabled()
    monkeypatch.setenv("LOG_COLOR", "0")
    assert not logmod.color_enabled()
    monkeypatch.setenv("LOG_COLOR", "auto")
    monkeypatch.setenv("NO_COLOR", "1")
    assert not logmod.color_enabled()


def test_fmt_duration():
    assert logmod.fmt_duration(0.0123) == "12ms"
    assert logmod.fmt_duration(3.21) == "3.2s"
    assert logmod.fmt_duration(125) == "2m05s"


def test_args_summarize_teams_and_truncate():
    team = [{"species": "Garchomp", "moves": []}, {"name": "Incineroar", "moves": []}]
    paste = "Garchomp @ Life Orb\n- Earthquake\n\nNick (Incineroar) @ Sitrus Berry\n- Fake Out"
    out = server_log.summarize_args({"team": team, "team_b": paste, "variants": [team, team],
                                     "query": "x" * 200, "limit": None})
    assert out["team"] == "Garchomp,Incineroar"
    assert out["team_b"] == "Garchomp,Incineroar"
    assert out["variants"] == "2 teams"
    assert len(out["query"]) <= 60
    assert "limit" not in out


def test_outcome_fields_never_collide_with_arguments():
    """An outcome echoing an argument (get_pokemon's `name`) once raised a
    TypeError and silently dropped the whole log line."""
    merged = server_log._merge({"name": "Garchomp", "format": "vgc"},
                               {"name": "Garchomp", "format": "bss", "types": "Dragon,Ground"}, took="5ms")
    assert merged["name"] == "Garchomp" and merged["format"] == "vgc"
    assert merged["→format"] == "bss" and merged["types"] == "Dragon,Ground"
    assert "→name" not in merged


def test_result_summaries():
    calc = server_log.summarize_result("calc_damage", {
        "attacker": "Garchomp", "move": "Earthquake", "defender": "Incineroar",
        "min_pct": 81.2, "max_pct": 96.5, "ko_chance": "guaranteed 2HKO"})
    assert calc["dmg"] == "81.2-96.5%" and calc["ko"] == "guaranteed 2HKO"
    err = server_log.summarize_result("get_pokemon", {"error": '"No Pokémon \'X\'"'})
    assert err == {"error": "No Pokémon 'X'"}
    generic = server_log.summarize_result("get_pokemon", {"name": "Garchomp", "types": ["Dragon", "Ground"]})
    assert generic["types"] == "Dragon,Ground"


def test_access_filter_keeps_only_failures():
    f = logmod.DropSuccessfulAccess()

    def rec(status):
        r = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", (), None)
        r.args = ("127.0.0.1:1", "POST", "/mcp", "1.1", status)
        return r

    assert not f.filter(rec(200))
    assert f.filter(rec(404))


def test_refresh_ignores_unknown_or_empty_mode():
    assert refresh.start("", lambda: None) is False
    assert refresh.start("bogus", lambda: None) is False
    assert not refresh.running()
