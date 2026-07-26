"""`champions-mcp-pull` — build/refresh the local champions.sqlite mirror.

Usage:
    champions-mcp-pull                 # download index + build + enrich
    champions-mcp-pull --offline       # rebuild from the saved index copy
    python -m champions_mcp.pull
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..config import Settings
from ..db import Db
from . import build as build_mod
from . import source as source_mod

# `usage` is a floor, not exact: championsbattledata ranks every legal Pokémon
# in both formats with ~5 usage categories each, so a healthy pull lands in
# the tens of thousands of rows. A pull that parses zero/near-zero usage rows
# — e.g. the upstream "Current" ladder season having just reset with no
# battles logged yet, which drops the per-category `rows` breakdown from its
# battleSummary blocks entirely — has happened for real and is easy to miss
# since `pokemon`/`moves`/etc. still populate fine. Catch it here instead of
# silently shipping (or worse, clobbering a good existing mirror with) a
# database with no move/item/ability/spread usage % for anyone.
EXPECTED = {"pokemon": 235, "usage": 10_000}


async def _run(offline: bool) -> int:
    """Download (or load) the index, build into a temp DB, validate, then swap it in.

    Args:
        offline: If True, rebuild from the previously saved index copy
            instead of downloading a fresh one.

    Returns:
        Process exit code: 0 on success, 1 if the build failed validation,
        2 if `offline` was given but no saved index exists.
    """
    settings = Settings.load()
    index_copy = settings.data_dir / "pokemon-index.json"

    if offline:
        if not index_copy.exists():
            print(f"--offline given but no saved index at {index_copy}", file=sys.stderr)
            return 2
        print(f"Loading saved index from {index_copy} …")
        index = source_mod.load_index_file(index_copy)
        index_tmp: Path | None = None
    else:
        url = settings.source_base + source_mod.INDEX_PATH
        print(f"Downloading {url} …")
        # Download to a temp path first — don't clobber the last-known-good
        # saved index until the build below actually validates.
        index_tmp = index_copy.with_suffix(".json.tmp")
        index = await source_mod.download_index(settings, save_to=index_tmp)

    # Build into a temp DB alongside the real one (same dir, so the final
    # rename is an atomic same-filesystem move) rather than straight into
    # settings.db_path — a bad pull must never overwrite a good existing
    # mirror. Only swap it in after the sanity checks below pass.
    db_tmp = settings.db_path.with_suffix(".sqlite.tmp")
    db_tmp.unlink(missing_ok=True)
    print(f"Building {db_tmp} (enriching from PokeAPI; cached) …")
    db = Db(db_tmp, create=True)
    try:
        counts = await build_mod.build(db, settings, index)
    finally:
        db.close()

    season = index.get("defaultSeason", "?")
    print(f"\nDone. season={season} version={index.get('dataVersion','?')}")
    for table, n in counts.items():
        print(f"  {table:16} {n}")

    problems = [
        f"expected >= {expected} {table}, got {counts.get(table, 0)}"
        for table, expected in EXPECTED.items()
        if counts.get(table, 0) < expected
    ]
    if problems:
        print("\nWARNING: " + "; ".join(problems) + ".", file=sys.stderr)
        if settings.db_path.exists():
            print(
                f"Leaving the existing {settings.db_path} untouched (this build "
                "did not pass validation).",
                file=sys.stderr,
            )
        db_tmp.unlink(missing_ok=True)
        if index_tmp is not None:
            index_tmp.unlink(missing_ok=True)
        return 1

    db_tmp.replace(settings.db_path)
    if index_tmp is not None:
        index_tmp.replace(index_copy)
    return 0


def main() -> None:
    """Entry point for the `champions-mcp-pull` console script."""
    parser = argparse.ArgumentParser(description="Build champions.sqlite from championsbattledata.com")
    parser.add_argument(
        "--offline", action="store_true",
        help="rebuild from the saved pokemon-index.json instead of downloading",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.offline)))


if __name__ == "__main__":
    main()
