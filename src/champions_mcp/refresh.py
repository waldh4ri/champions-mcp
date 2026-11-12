"""Background data refresh, so the server never waits on the slow upstreams.

The container's entrypoint decides *what* needs refreshing and passes it in
``CHAMPIONS_MCP_BACKGROUND_PULL``; the server starts serving at once and runs
the pull here, as a child process (its own memory, its log lines go to the
same stream). The pull builds into a copy and swaps it in atomically, then
the server reopens the database (see ``App.reload``).

Modes:
    corpus  the observed-team corpus + team reports into the existing
            database (``champions-mcp-pull --corpus-only``): what a first
            start defers, since Limitless rate-limits unauthenticated
            clients to 50 requests / 5 min (~15 min for a cold corpus).
    full    a complete pull (stale or forced refresh of existing data):
            the current data keeps being served until the new one is in.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import log as logmod

log = logmod.get("refresh")

#: What the background refresh is doing, for tools that depend on its data.
state: dict[str, Any] = {"mode": None, "running": False, "started": None, "result": None}

_FLAGS = {"corpus": ["--corpus-only"], "full": []}


def running() -> bool:
    return bool(state["running"])


def start(mode: str, on_done: Callable[[], None]) -> bool:
    """Run a background pull in ``mode``; call ``on_done`` once it succeeded.

    Returns:
        Whether a refresh was started (False for an unknown/empty mode).
    """
    mode = (mode or "").strip().lower()
    if mode not in _FLAGS:
        if mode:
            log.warning("unknown background refresh mode; ignoring", mode=mode, expected="corpus|full")
        return False
    flags = list(_FLAGS[mode])
    if mode == "full" and os.environ.get("CHAMPIONS_MCP_BUILDS_SKIP", "0") == "1":
        flags.append("--skip-builds")
    state.update(mode=mode, running=True, started=time.time(), result=None)

    def run() -> None:
        t0 = time.perf_counter()
        log.info(f"background {mode} refresh started; serving the current data meanwhile", bold=True,
                 note="observed teams / meta opponents fill in when it finishes" if mode == "corpus" else None)
        try:
            rc = subprocess.run([sys.executable, "-m", "champions_mcp.pull", *flags], check=False).returncode
        except Exception as exc:  # noqa: BLE001 - never take the server down
            rc = -1
            log.error("background refresh could not run", error=f"{type(exc).__name__}: {exc}")
        took = logmod.fmt_duration(time.perf_counter() - t0)
        state.update(running=False, result=rc)
        if rc != 0:
            log.warning("background refresh failed; still serving the previous data", mode=mode,
                        exit_code=rc, took=took)
            return
        try:
            on_done()
            log.info(f"background {mode} refresh finished; now serving the new data", bold=True, took=took)
        except Exception:  # noqa: BLE001
            log.error("could not reopen the refreshed data; restart the container", exc_info=True)

    threading.Thread(target=run, name=f"refresh-{mode}", daemon=True).start()
    return True
