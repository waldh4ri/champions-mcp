"""Static per-format battle rules (not present in the data source).

Replaces v1's curated regulation JSON: the "season" is whatever the data was
built from (see DB ``meta.season``); these are the immutable format rules
(team sizes, clauses, Mega cap) that apply on top of the season's roster.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_FORMATS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "formats.json"

# Accept a range of aliases the model might pass.
_ALIASES = {
    "vgc": "vgc", "doubles": "vgc", "double": "vgc", "vgc2026": "vgc",
    "bss": "bss", "singles": "bss", "single": "bss",
    "battle stadium singles": "bss", "bs": "bss",
}


@lru_cache(maxsize=1)
def _all() -> dict[str, Any]:
    return json.loads(_FORMATS_PATH.read_text(encoding="utf-8"))


def resolve_format(fmt: str | None) -> str:
    """Normalize a format argument to 'vgc' or 'bss' (defaults to 'vgc')."""
    if not fmt:
        return "vgc"
    return _ALIASES.get(fmt.strip().lower(), "vgc")


def rules(fmt: str | None) -> dict[str, Any]:
    """Look up the static rules block for a format.

    Args:
        fmt: A format name/alias (see `resolve_format`), or None for the default.

    Returns:
        The format's rules dict (team size, clauses, Mega cap, game_type, ...)
        from ``data/formats.json``.
    """
    return _all()[resolve_format(fmt)]


def game_type(fmt: str | None) -> str:
    """Internal usage-table key for a format.

    Args:
        fmt: A format name/alias, or None for the default.

    Returns:
        ``"doubles"`` for vgc, ``"singles"`` for bss.
    """
    return rules(fmt)["game_type"]
