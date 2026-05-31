from __future__ import annotations

import time

import pytest

from champions_mcp.cache import Cache


@pytest.fixture
def db(tmp_path) -> Cache:
    c = Cache(tmp_path / "test.sqlite")
    yield c
    c.close()


async def test_get_returns_none_for_missing_key(db):
    assert await db.get("no-such-key") is None


async def test_set_and_get_round_trip(db):
    await db.set("k", {"x": 1}, ttl=None)
    assert await db.get("k") == {"x": 1}


async def test_ttl_expiry_evicts_entry(db):
    await db.set("e", "value", ttl=0.001)  # 1 ms TTL
    time.sleep(0.02)
    assert await db.get("e") is None


async def test_permanent_entry_survives_time(db):
    await db.set("p", [1, 2, 3], ttl=None)
    time.sleep(0.01)
    assert await db.get("p") == [1, 2, 3]


async def test_overwrite_replaces_value(db):
    await db.set("k", "first", ttl=None)
    await db.set("k", "second", ttl=None)
    assert await db.get("k") == "second"


async def test_various_json_types_round_trip(db):
    for key, val in [("list", [1, 2, 3]), ("str", "hello"), ("num", 42.5), ("null", None)]:
        await db.set(key, val, ttl=None)
        assert await db.get(key) == val
