"""Download the single data source: championsbattledata.com pokemon-index.json.

The index JSON is strictly richer than the battle_data.zip the project README
points at: it embeds Champions-accurate stats/types/abilities, every form
(incl. Megas), full legal movepools (``learnableMoveNames``) and all parsed
usage rows. The zip only holds the usage CSVs, so the index is the real
single source. A raw copy is saved to the data dir for provenance/offline use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings

INDEX_PATH = "/data/pokemon-index.json"


async def download_index(settings: Settings, *, save_to: Path | None = None) -> dict[str, Any]:
    """Download and parse the championsbattledata pokemon-index.json.

    Args:
        settings: Runtime settings (source base URL, HTTP timeout, user agent).
        save_to: If given, also write the raw JSON here for provenance/offline use.

    Returns:
        The parsed index document.

    Raises:
        httpx.HTTPStatusError: The request did not succeed.
    """
    url = settings.source_base + INDEX_PATH
    async with httpx.AsyncClient(
        timeout=settings.http_timeout * 3,  # 15 MB payload
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if save_to is not None:
        save_to.write_text(json.dumps(data), encoding="utf-8")
    return data


def load_index_file(path: Path) -> dict[str, Any]:
    """Load a previously downloaded index (``--offline`` mode).

    Args:
        path: Path to a JSON file previously saved by `download_index`.

    Returns:
        The parsed index document.
    """
    return json.loads(path.read_text(encoding="utf-8"))
