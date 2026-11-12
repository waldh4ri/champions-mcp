"""`champions-mcp-pull` — build/refresh the local champions.sqlite mirror.

Usage:
    champions-mcp-pull                 # build + enrich from pokechamp.gg
    champions-mcp-pull --skip-builds   # without the (slow) observed-team corpus
    champions-mcp-pull --corpus-only   # just the corpus, into the existing database
    python -m champions_mcp.pull
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import time

from .. import log as logmod
from ..config import Settings
from ..db import Db
from . import build as build_mod

# pokedb ranks ~260 Pokémon in both formats with several usage categories
# each, so a healthy pull lands in the tens of thousands of usage rows. A
# pull that comes back near-empty — e.g. mid-season-rollover, before
# the ladder has logged battles under the new regulation yet — has happened
# for real on the previous upstream and is easy to miss since `pokemon`/
# `moves`/etc. still populate fine. Catch it here instead of silently
# shipping (or worse, clobbering a good existing mirror with) a database
# with no move/item/ability/spread usage % for anyone.
EXPECTED = {"pokemon": 250, "usage": 10_000}

log = logmod.get("pull")


async def _refresh_builds_only(settings: Settings) -> dict[str, int] | None:
    """Refresh the observed-team corpus against the existing database, in place.

    Used when the primary pokechamp pull fails validation: that upstream
    being mid-reset has nothing to do with whether Limitless or pokedb are
    healthy, and blocking a fresh team corpus on an unrelated outage just
    means the LAN-facing tools sit on stale data for no reason.

    Never writes to ``settings.db_path`` directly — copies it to a temp path
    first (same atomic-swap discipline as a full rebuild), runs
    `ensure_schema` so a database from before the observed-build tables
    existed gets them added rather than erroring, ingests, and only then
    swaps the copy in. A failure here (network, a malformed upstream
    response) is caught and reported; it never corrupts or deletes the
    existing database.

    Args:
        settings: Runtime settings.

    Returns:
        The ingest stats on success, or None if no existing database is
        there to refresh, or the refresh itself failed.
    """
    if not settings.db_path.exists():
        return None
    tmp = settings.db_path.with_suffix(".sqlite.buildsrefresh.tmp")
    tmp.unlink(missing_ok=True)
    shutil.copy2(settings.db_path, tmp)
    db = Db(tmp)
    try:
        db.ensure_schema()
        stats = await build_mod.ingest_corpus(db, settings)
        build_mod.record_counts(db)
    except Exception as exc:  # pragma: no cover - defensive: never take the DB down with it
        log.error("observed-team corpus refresh failed; existing data unchanged",
                  error=f"{type(exc).__name__}: {exc}")
        tmp.unlink(missing_ok=True)
        return None
    finally:
        db.close()
    tmp.replace(settings.db_path)
    return stats


async def _run_corpus_only() -> int:
    """Refresh only the observed-team corpus (stages 6-7) in the existing
    database. The container runs this in the background after a first start,
    so the server is usable without waiting on Limitless's rate limit."""
    settings = Settings.load()
    if not settings.db_path.exists():
        log.error("--corpus-only needs an existing database; run a full pull first", db=settings.db_path)
        return 1
    t0 = time.perf_counter()
    log.info("corpus refresh started", bold=True, db=settings.db_path, regulations=settings.builds_regulations)
    stats = await _refresh_builds_only(settings)
    if stats is None:
        return 1
    log.info("corpus refresh complete; database swapped in", bold=True,
             build_teams=stats.get("build_teams"), took=logmod.fmt_duration(time.perf_counter() - t0))
    return 0


async def _run(with_builds: bool) -> int:
    """Build into a temp DB from pokechamp.gg, validate, then swap it in.

    Args:
        with_builds: If True, also ingest the observed-team corpus.

    Returns:
        Process exit code: 0 on success, 1 if the build failed validation.
    """
    settings = Settings.load()

    # Build into a temp DB alongside the real one (same dir, so the final
    # rename is an atomic same-filesystem move) rather than straight into
    # settings.db_path — a bad pull must never overwrite a good existing
    # mirror. Only swap it in after the sanity checks below pass.
    db_tmp = settings.db_path.with_suffix(".sqlite.tmp")
    db_tmp.unlink(missing_ok=True)
    t0 = time.perf_counter()
    log.info("pull started", bold=True, target=settings.db_path, source=settings.pokechamp_base,
             observed_teams="yes" if with_builds else "skipped", cache=settings.cache_db)
    db = Db(db_tmp, create=True)
    try:
        counts = await build_mod.build(db, settings, with_builds=with_builds)
        season = db.meta("season", "")
    except BaseException:
        log.error("pull crashed; existing data left untouched",
                  took=logmod.fmt_duration(time.perf_counter() - t0), exc_info=True)
        db.close()
        db_tmp.unlink(missing_ok=True)
        raise
    db.close()
    log.info("pull built", took=logmod.fmt_duration(time.perf_counter() - t0), season=season, **counts)

    problems = [
        f"expected >= {expected} {table}, got {counts.get(table, 0)}"
        for table, expected in EXPECTED.items()
        if counts.get(table, 0) < expected
    ]
    if problems:
        log.warning("pull failed validation; discarding it", problems="; ".join(problems),
                    kept=settings.db_path if settings.db_path.exists() else "nothing (no existing data)")
        db_tmp.unlink(missing_ok=True)

        # The observed-team corpus (Limitless/pokedb) is unrelated to
        # pokechamp's health, so a failure here shouldn't leave it stale too
        # — refresh it independently against the existing DB.
        if with_builds and settings.db_path.exists():
            with logmod.timed(log, "observed-team corpus refresh on the existing data") as out:
                refreshed = await _refresh_builds_only(settings)
                out.update(refreshed or {"result": "did not complete; existing data unchanged"})
        return 1

    db_tmp.replace(settings.db_path)
    log.info("pull complete; database swapped in", bold=True, db=settings.db_path,
             size_mb=round(settings.db_path.stat().st_size / 1e6, 1),
             took=logmod.fmt_duration(time.perf_counter() - t0))
    return 0


def main() -> None:
    """Entry point for the `champions-mcp-pull` console script."""
    parser = argparse.ArgumentParser(description="Build champions.sqlite from pokechamp.gg")
    parser.add_argument(
        "--skip-builds", action="store_true",
        help="skip the observed-team corpus (Limitless teamlists + pokedb "
             "ranked ladder). The first run fetches them over several minutes; "
             "later runs read from the pull cache and cost nothing.",
    )
    parser.add_argument(
        "--corpus-only", action="store_true",
        help="only refresh the observed-team corpus and team reports, in the "
             "existing database (what the container runs in the background).",
    )
    args = parser.parse_args()
    logmod.setup()
    if args.corpus_only:
        raise SystemExit(asyncio.run(_run_corpus_only()))
    raise SystemExit(asyncio.run(_run(not args.skip_builds)))


if __name__ == "__main__":
    main()
