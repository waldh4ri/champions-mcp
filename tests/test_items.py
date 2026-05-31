from __future__ import annotations

import json

import pytest

from champions_mcp.champions_items import ItemCatalog, _plausible, item_key


def test_catalog_contains_normalized_name():
    cat = ItemCatalog(keys={"focus-sash", "choice-scarf"}, verified=True)
    assert "Focus Sash" in cat
    assert "focus-sash" in cat
    assert "King's Rock" not in cat


def test_catalog_not_contains():
    cat = ItemCatalog(keys={"focus-sash"}, verified=True)
    assert "Life Orb" not in cat


def test_catalog_loaded_property():
    assert ItemCatalog(keys=set(), verified=False).loaded is False
    assert ItemCatalog(keys={"focus-sash"}, verified=True).loaded is True


def test_catalog_names_sorted():
    cat = ItemCatalog(
        keys={"choice-scarf", "focus-sash"},
        names=["Focus Sash", "Choice Scarf"],
        verified=True,
    )
    assert cat.names == ["Choice Scarf", "Focus Sash"]


def test_catalog_notes_default_empty():
    cat = ItemCatalog(keys=set(), verified=False)
    assert cat.notes == []


def test_catalog_load_from_json(tmp_path, monkeypatch):
    data = {
        "items": ["Focus Sash", "Choice Scarf", "Leftovers"],
        "verified": True,
        "source_url": "https://example.com",
        "notes": ["a note"],
    }
    (tmp_path / "champions_items.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("CHAMPIONS_MCP_DATA_DIR", str(tmp_path))

    from champions_mcp.config import Settings
    cat = ItemCatalog.load(Settings.load())

    assert cat.verified is True
    assert "Focus Sash" in cat
    assert "Leftovers" in cat
    assert cat.source_url == "https://example.com"
    assert "a note" in cat.notes
    assert sorted(cat.names) == ["Choice Scarf", "Focus Sash", "Leftovers"]


def test_catalog_load_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAMPIONS_MCP_DATA_DIR", str(tmp_path))

    from champions_mcp.config import Settings
    cat = ItemCatalog.load(Settings.load())

    assert cat.loaded is False
    assert cat.verified is False


def test_plausible_accepts_typical_item_names():
    assert _plausible("Focus Sash") is True
    assert _plausible("King's Rock") is True
    assert _plausible("Never-Melt Ice") is True
    assert _plausible("AB") is True  # minimum length (2 chars)


def test_plausible_rejects_bad_values():
    assert _plausible("") is False
    assert _plausible("A") is False            # too short (1 char)
    assert _plausible("x" * 31) is False       # too long (> 30)
    assert _plausible("123 Bomb") is False     # starts with digit
    assert _plausible("-Attackdex") is False   # starts with '-'


def test_item_key_apostrophe_variants():
    # Both Unicode right-apostrophe and ASCII apostrophe must produce same key.
    assert item_key("King\u2019s Rock") == item_key("King's Rock")
    assert item_key("King's Rock") == "kings-rock"
