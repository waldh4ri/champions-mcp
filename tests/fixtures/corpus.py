"""Seed a database's observed-team corpus from the recorded CI corpus.

Runs the *real* ingest code on recorded upstream data, so CI exercises the
Limitless / pokedb ingest and the team-report scrapers and parsers without
the long scrapes:

  - Limitless standings  -> BuildIngest.add_limitless (format classification, sets)
  - pokedb exports        -> BuildIngest.add_pokedb (Japanese items, Z-Mega stones)
  - team-report pages     -> reports.pull_reports via a ReplayFetcher (listing
                             parser, pokesol / yakkun / blog article parsers,
                             Japanese -> English resolution)

Usage (CI, after `champions-mcp-pull --skip-builds`):
    python tests/fixtures/corpus.py [path/to/champions.sqlite]
"""

from __future__ import annotations

import lzma
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from champions_mcp.db import Db  # noqa: E402
from champions_mcp.pull import reports  # noqa: E402
from champions_mcp.pull.builds import (  # noqa: E402
    POKEDB_SEASON_REGULATION,
    BuildIngest,
    _season_number,
    classify_format,
)

CORPUS = Path(__file__).with_name("corpus.json.xz")
HALF_LIFE_DAYS = 21.0


def load() -> dict[str, Any]:
    return json.loads(lzma.decompress(CORPUS.read_bytes()))


def seed(db: Db, corpus: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replace the database's corpus tables with the recorded corpus."""
    corpus = corpus or load()
    db.ensure_schema()
    db.reset_observed_builds()
    ingest = BuildIngest(db, corpus["ja_item_names"])
    stats: dict[str, Any] = {}
    for rec in corpus["limitless"]:
        lists = [p["decklist"] for p in rec["standings"] if p.get("decklist")]
        fmt = classify_format(rec["event"].get("name") or "", lists)
        stats[f"limitless_{fmt}"] = stats.get(f"limitless_{fmt}", 0) + ingest.add_limitless(
            rec["event"], rec["standings"], fmt=fmt, half_life_days=HALF_LIFE_DAYS)
    for export in corpus["pokedb"]:
        reg = POKEDB_SEASON_REGULATION.get(_season_number(export.get("season", "")))
        rule = str(export.get("rule", ""))
        key = "pokedb_single" if rule in ("single", "シングル") else "pokedb_double"
        stats[key] = stats.get(key, 0) + ingest.add_pokedb(export, reg, half_life_days=HALF_LIFE_DAYS)
    stats.update(ingest.flush())
    db.set_meta("builds", stats)
    with tempfile.TemporaryDirectory() as tmp:
        fetcher = reports.ReplayFetcher(corpus["report_pages"], Path(tmp))
        full = reports.pull_reports(db, Path(tmp), ("M-C",), fetcher=fetcher)
    db.set_meta("full_teams", full)
    return {"builds": stats, "full_teams": full}


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "champions.sqlite"
    out = seed(Db(path))
    print(json.dumps(out, indent=1, default=str))
