from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
_DEFAULT_DATA_DIR = _PKG_ROOT.parent.parent / "data"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_db: Path
    regulations_dir: Path
    pokeapi_base: str
    limitless_base: str
    limitless_api_key: str | None
    http_timeout: float
    meta_ttl: float
    tournament_ttl: float
    user_agent: str
    calc_docker: str
    calc_image: str
    calc_timeout: float
    calc_http: str | None  # if set, use HTTP sidecar instead of docker

    @staticmethod
    def load() -> "Settings":
        data_dir = Path(
            os.environ.get("CHAMPIONS_MCP_DATA_DIR", str(_DEFAULT_DATA_DIR))
        ).resolve()
        cache_db = Path(
            os.environ.get("CHAMPIONS_MCP_CACHE_DB", str(data_dir / "cache.sqlite"))
        ).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        cache_db.parent.mkdir(parents=True, exist_ok=True)
        return Settings(
            data_dir=data_dir,
            cache_db=cache_db,
            regulations_dir=data_dir / "regulations",
            pokeapi_base=os.environ.get(
                "CHAMPIONS_MCP_POKEAPI_BASE", "https://pokeapi.co/api/v2"
            ).rstrip("/"),
            limitless_base=os.environ.get(
                "CHAMPIONS_MCP_LIMITLESS_BASE", "https://play.limitlesstcg.com/api"
            ).rstrip("/"),
            limitless_api_key=os.environ.get("LIMITLESS_API_KEY") or None,
            http_timeout=float(os.environ.get("CHAMPIONS_MCP_HTTP_TIMEOUT", "20")),
            meta_ttl=float(os.environ.get("CHAMPIONS_MCP_META_TTL", "21600")),
            tournament_ttl=float(
                os.environ.get("CHAMPIONS_MCP_TOURNAMENT_TTL", "3600")
            ),
            user_agent=os.environ.get(
                "CHAMPIONS_MCP_USER_AGENT",
                "champions-mcp/0.1 (+https://github.com/; AI VGC team-building tool)",
            ),
            calc_docker=os.environ.get("CHAMPIONS_MCP_DOCKER", "docker"),
            calc_image=os.environ.get(
                "CHAMPIONS_MCP_CALC_IMAGE", "champions-calc:latest"
            ),
            calc_timeout=float(os.environ.get("CHAMPIONS_MCP_CALC_TIMEOUT", "30")),
            calc_http=os.environ.get("CHAMPIONS_MCP_CALC_HTTP") or None,
        )
