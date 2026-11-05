"""Container data freshness (pull/freshness.py): the entrypoint re-pulls when
the database was built by older code or is too old."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from champions_mcp.db import DATA_FORMAT
from champions_mcp.pull.freshness import check


def _db(tmp_path, **meta):
    path = tmp_path / "champions.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    con.commit()
    con.close()
    return path


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_current_and_recent_is_fresh(tmp_path):
    code, _ = check(_db(tmp_path, data_format=DATA_FORMAT, generated_at=_ago(1)), 7)
    assert code == 0


def test_data_from_older_code_is_stale(tmp_path):
    """The live-stack case: a database from before full_teams existed."""
    code, reason = check(_db(tmp_path, generated_at=_ago(1)), 7)  # no data_format at all
    assert code == 10 and "data format" in reason
    older = tmp_path / "older"
    older.mkdir()
    code, _ = check(_db(older, data_format=DATA_FORMAT - 1, generated_at=_ago(0)), 7)
    assert code == 10


def test_old_data_is_stale_unless_the_age_check_is_off(tmp_path):
    path = _db(tmp_path, data_format=DATA_FORMAT, generated_at=_ago(12))
    code, reason = check(path, 7)
    assert code == 10 and "12 days" in reason
    assert check(path, 0)[0] == 0


def test_missing_or_broken_database(tmp_path):
    assert check(tmp_path / "nope.sqlite", 7)[0] == 11
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database")
    assert check(bad, 7)[0] == 11


def test_the_real_pull_writes_the_current_format(db):
    """A freshly pulled database must pass, or every restart would re-pull."""
    if db.meta("data_format") is None:
        import pytest
        pytest.skip("local database predates data_format (re-run champions-mcp-pull)")
    assert int(db.meta("data_format")) == DATA_FORMAT
