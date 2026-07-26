"""Pull-time HTTP response cache.

Used exclusively by ``champions-mcp-pull`` to avoid refetching immutable
PokeAPI data on repeated builds; the runtime MCP server never imports this
module, since it only ever reads the already-built ``champions.sqlite``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    ttl        REAL
);
"""


class Cache:
    """SQLite-backed JSON cache.

    A NULL ``ttl`` means the entry is permanent — this is how the PokeAPI mirror
    grows: immutable Pokédex data is fetched once and kept forever. Meta and
    tournament data use a finite ttl so it stays reasonably fresh.
    """

    def __init__(self, db_path: Path) -> None:
        """Open (creating if needed) the cache database at ``db_path``.

        Args:
            db_path: Path to the SQLite file backing the cache.
        """
        self._path = db_path
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def _get_sync(self, key: str) -> Any | None:
        """Synchronous, lock-free read; only call from within ``self._lock``."""
        row = self._conn.execute(
            "SELECT value, fetched_at, ttl FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value, fetched_at, ttl = row
        if ttl is not None and (time.time() - fetched_at) > ttl:
            self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._conn.commit()
            return None
        return json.loads(value)

    def _set_sync(self, key: str, value: Any, ttl: float | None) -> None:
        """Synchronous, lock-free write; only call from within ``self._lock``."""
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, fetched_at, ttl) "
            "VALUES (?, ?, ?, ?)",
            (key, json.dumps(value), time.time(), ttl),
        )
        self._conn.commit()

    async def get(self, key: str) -> Any | None:
        """Look up a cached value, dropping it first if it has expired.

        Args:
            key: Cache key.

        Returns:
            The decoded JSON value, or None if there is no entry (or it has
            expired past its ttl).
        """
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, key)

    async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Store a value under ``key``, replacing any existing entry.

        Args:
            key: Cache key.
            value: JSON-serializable value to store.
            ttl: Seconds until the entry expires, or None for a permanent entry.
        """
        async with self._lock:
            await asyncio.to_thread(self._set_sync, key, value, ttl)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
