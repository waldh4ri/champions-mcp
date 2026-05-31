from __future__ import annotations

# Champions format constants and alias resolution.
#
# This file re-exports from meta/formats.py — the single source of truth for
# all format metadata.  Existing import paths are preserved for compatibility.

from .formats import FORMAT_ALIASES, FORMAT_CONFIG, resolve_format  # noqa: F401

# Human-readable display names for each format key (derived from FORMAT_CONFIG).
CHAMPIONS_FORMAT_DISPLAY: dict[str, str] = {
    k: v["display"] for k, v in FORMAT_CONFIG.items()
}

