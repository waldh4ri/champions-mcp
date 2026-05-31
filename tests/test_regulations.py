from __future__ import annotations

import json
from datetime import date

import pytest

from champions_mcp.config import Settings
from champions_mcp.regulations import Regulation, RegulationRegistry


def test_ma_loads_and_is_active_in_window():
    regs = RegulationRegistry(Settings.load())
    assert "M-A" in regs.list_ids()
    ma = regs.get("M-A")
    assert ma.mega.allowed is True
    assert ma.mega.max_per_battle == 1
    assert ma.ban_categories == []
    assert ma.roster_verified is True
    assert ma.allowed_species is not None
    assert len(ma.allowed_species) > 0
    assert ma.mega.eligible_species is not None
    assert len(ma.mega.eligible_species) > 0
    assert ma.is_active_on(date(2026, 5, 16))
    assert not ma.is_active_on(date(2026, 7, 1))


def test_active_selects_window():
    regs = RegulationRegistry(Settings.load())
    assert regs.active(date(2026, 5, 1)).id == "M-A"


def test_get_unknown_id_raises_key_error():
    regs = RegulationRegistry(Settings.load())
    with pytest.raises(KeyError, match="UNKNOWN"):
        regs.get("UNKNOWN")


def test_is_active_on_no_dates():
    # No start or end date -> always active.
    reg = Regulation.model_validate({"id": "X", "name": "X"})
    assert reg.is_active_on(date(2020, 1, 1))
    assert reg.is_active_on(date(2099, 12, 31))


def test_is_active_on_open_ended_start():
    reg = Regulation.model_validate({"id": "X", "name": "X", "start_date": "2026-01-01"})
    assert reg.is_active_on(date(2026, 6, 1))
    assert not reg.is_active_on(date(2025, 12, 31))


def test_is_active_on_open_ended_end():
    reg = Regulation.model_validate({"id": "X", "name": "X", "end_date": "2026-06-30"})
    assert reg.is_active_on(date(2026, 6, 30))
    assert not reg.is_active_on(date(2026, 7, 1))


def test_is_active_on_bad_date_string_returns_false():
    reg = Regulation.model_validate(
        {"id": "X", "name": "X", "start_date": "not-a-date"}
    )
    assert reg.is_active_on(date(2026, 5, 1)) is False


def test_active_raises_when_no_regulations(tmp_path, monkeypatch):
    # An empty regulations directory should make active() raise KeyError.
    reg_dir = tmp_path / "regulations"
    reg_dir.mkdir()
    monkeypatch.setenv("CHAMPIONS_MCP_DATA_DIR", str(tmp_path))
    regs = RegulationRegistry(Settings.load())
    with pytest.raises(KeyError, match="No regulation files found"):
        regs.active()


def test_active_fallback_to_most_recent_when_none_active(tmp_path, monkeypatch):
    # Create a regulation whose window is in the past.
    data = {
        "id": "OLD",
        "name": "Old Reg",
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
    }
    reg_dir = tmp_path / "regulations"
    reg_dir.mkdir()
    (reg_dir / "OLD.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("CHAMPIONS_MCP_DATA_DIR", str(tmp_path))
    regs = RegulationRegistry(Settings.load())
    # No regulation is active on today (far future); should return the most recent.
    result = regs.active(date(2099, 1, 1))
    assert result.id == "OLD"
