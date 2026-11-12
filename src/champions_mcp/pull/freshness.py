"""Is the served database current enough, or does the container need a pull?

The container only pulls on first start (or CHAMPIONS_MCP_PULL=1), so after
an image upgrade it can keep serving data built by older code: tables the new
code reads simply aren't there (e.g. full_teams), and the observed-team corpus
stops at whatever season it was pulled in. This check catches both:

  - data format: the pull records db.DATA_FORMAT; an older value (or none)
    means the code needs data it doesn't have;
  - age: older than CHAMPIONS_MCP_MAX_DATA_AGE_DAYS (default 7; 0 disables),
    so a long-running stack still picks up new seasons and tournaments.

Usage (entrypoint.sh): python -m champions_mcp.pull.freshness <db_path>
Exit 0 = fresh, 10 = stale (the reason is printed), 11 = no usable database.

    python -m champions_mcp.pull.freshness --corpus <db_path>
Exit 0 = the observed-team corpus is there, 12 = it is missing (e.g. its
background fetch was interrupted by a restart).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..db import DATA_FORMAT


def check(db_path: Path, max_age_days: float) -> tuple[int, str]:
    """(exit code, reason) for the database at ``db_path``."""
    if not db_path.exists():
        return 11, "no database"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        con.close()
    except sqlite3.Error as exc:
        return 11, f"unreadable database ({exc})"

    def val(key):
        raw = meta.get(key)
        try:
            return json.loads(raw) if raw is not None else None
        except (TypeError, ValueError):
            return raw

    fmt = val("data_format") or 1
    if int(fmt) < DATA_FORMAT:
        return 10, f"data format {fmt} is older than this code's {DATA_FORMAT}"
    built = val("generated_at")
    if max_age_days > 0 and built:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(str(built))
        except ValueError:
            return 10, f"unparseable build date {built!r}"
        if age.total_seconds() > max_age_days * 86400:
            return 10, f"built {age.days} days ago (max {max_age_days:g})"
    return 0, f"data format {fmt}, built {built}"


def corpus_missing(db_path: Path) -> bool:
    """True when the database has no observed-team corpus (build_teams), which
    a first start fetches in the background: if that was interrupted, the
    next start must resume it rather than wait for the age check."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            n = con.execute("SELECT COUNT(*) FROM build_teams").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return True
    return n == 0


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--corpus":
        db_path = Path(args[1] if len(args) > 1 else os.environ.get("CHAMPIONS_MCP_DB", "/data/champions.sqlite"))
        missing = corpus_missing(db_path)
        print("missing" if missing else "present")
        raise SystemExit(12 if missing else 0)
    db_path = Path(args[0] if args else os.environ.get("CHAMPIONS_MCP_DB", "/data/champions.sqlite"))
    max_age = float(os.environ.get("CHAMPIONS_MCP_MAX_DATA_AGE_DAYS", "7") or 0)
    code, reason = check(db_path, max_age)
    print(reason)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
