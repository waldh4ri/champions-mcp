"""Runtime and pull-time configuration.

Centralizes every path and environment-driven setting the project needs:
where the served ``champions.sqlite`` lives, where the pull-time HTTP cache
lives, and the damage-calculator connection details. The MCP server only
ever reads ``db_path`` and the calc settings; the rest exists purely for
``champions-mcp-pull``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
_DEFAULT_DATA_DIR = _PKG_ROOT.parent.parent / "data"


@dataclass(frozen=True)
class Settings:
    """Runtime + pull settings.

    The server only needs ``db_path`` and the calc settings — it never touches
    the network. The remaining fields (``source_base``, ``pokeapi_base``,
    ``cache_db``, ``http_timeout``, ``user_agent``) are used solely by
    ``champions-mcp-pull`` when (re)building the local database.
    """

    data_dir: Path
    db_path: Path          # the served SQLite mirror (champions.sqlite)
    cache_db: Path         # pull-time HTTP cache (PokeAPI mirror)
    # --- pull-only ---
    source_base: str       # championsbattledata.com
    pokeapi_base: str      # PokeAPI, used once to enrich move/ability/item text
    http_timeout: float
    user_agent: str
    # --- pull-only: observed builds (see pull/builds.py) ---
    limitless_base: str      # play.limitlesstcg.com API — VGC tournament teamlists
    limitless_key: str | None  # optional; only raises the rate limit
    pokedb_base: str         # champs.pokedb.tokyo — BSS ranked-ladder team export
    builds_regulations: tuple[str, ...]  # which regulations to ingest
    builds_max_events: int   # cap on tournaments fetched per regulation
    builds_min_players: int  # skip tiny events — they are noise, not evidence
    builds_half_life_days: float  # recency decay applied to observation weights
    # --- damage calc (kept feature) ---
    calc_docker: str
    calc_image: str
    calc_timeout: float
    calc_http: str | None  # if set, use HTTP sidecar instead of docker

    @staticmethod
    def load() -> "Settings":
        """Build a Settings instance from environment variables and defaults.

        Returns:
            A populated, frozen Settings instance.
        """
        data_dir = Path(
            os.environ.get("CHAMPIONS_MCP_DATA_DIR", str(_DEFAULT_DATA_DIR))
        ).resolve()
        db_path = Path(
            os.environ.get("CHAMPIONS_MCP_DB", str(data_dir / "champions.sqlite"))
        ).resolve()
        cache_db = Path(
            os.environ.get("CHAMPIONS_MCP_CACHE_DB", str(data_dir / "pull-cache.sqlite"))
        ).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return Settings(
            data_dir=data_dir,
            db_path=db_path,
            cache_db=cache_db,
            source_base=os.environ.get(
                "CHAMPIONS_MCP_SOURCE_BASE", "https://championsbattledata.com"
            ).rstrip("/"),
            pokeapi_base=os.environ.get(
                "CHAMPIONS_MCP_POKEAPI_BASE", "https://pokeapi.co/api/v2"
            ).rstrip("/"),
            http_timeout=float(os.environ.get("CHAMPIONS_MCP_HTTP_TIMEOUT", "20")),
            user_agent=os.environ.get(
                "CHAMPIONS_MCP_USER_AGENT",
                "champions-mcp/2.0 (+https://github.com/; AI VGC team-building tool)",
            ),
            limitless_base=os.environ.get(
                "CHAMPIONS_MCP_LIMITLESS_BASE", "https://play.limitlesstcg.com/api"
            ).rstrip("/"),
            limitless_key=os.environ.get("CHAMPIONS_MCP_LIMITLESS_KEY") or None,
            pokedb_base=os.environ.get(
                "CHAMPIONS_MCP_POKEDB_BASE", "https://champs.pokedb.tokyo"
            ).rstrip("/"),
            builds_regulations=tuple(
                r.strip() for r in os.environ.get(
                    "CHAMPIONS_MCP_BUILDS_REGULATIONS", "M-C"
                ).split(",") if r.strip()
            ),
            builds_max_events=int(
                os.environ.get("CHAMPIONS_MCP_BUILDS_MAX_EVENTS", "150")
            ),
            builds_min_players=int(
                os.environ.get("CHAMPIONS_MCP_BUILDS_MIN_PLAYERS", "16")
            ),
            builds_half_life_days=float(
                os.environ.get("CHAMPIONS_MCP_BUILDS_HALF_LIFE_DAYS", "21")
            ),
            calc_docker=os.environ.get("CHAMPIONS_MCP_DOCKER", "docker"),
            calc_image=os.environ.get(
                "CHAMPIONS_MCP_CALC_IMAGE", "champions-calc:latest"
            ),
            calc_timeout=float(os.environ.get("CHAMPIONS_MCP_CALC_TIMEOUT", "30")),
            calc_http=os.environ.get("CHAMPIONS_MCP_CALC_HTTP") or None,
        )
