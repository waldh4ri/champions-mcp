"""Single source of truth for Champions format metadata.

All format-specific constants (Showdown IDs, display names, RPC labels) live
here.  Other modules derive their format dicts from FORMAT_CONFIG so that
a format rename only needs to be done once.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Core config
# ---------------------------------------------------------------------------

FORMAT_CONFIG: dict[str, dict] = {
    "vgc": {
        "display":     "[Champions] VGC 2026 Reg M-A (doubles, bring 4)",
        "showdown_id": "gen9championsvgc2026regma",   # shared by chaos + RPC
        "rpc_label":   "VGC 2026 Regulation M-A",    # value inside RPC JSON strategy objects
    },
    "bss": {
        "display":     "[Champions] BSS Reg M-A (singles, bring 3)",
        "showdown_id": "gen9championsbssregma",
        "rpc_label":   "Battle Stadium Singles",
    },
}

# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------

FORMAT_ALIASES: dict[str, str] = {
    # VGC — doubles
    "vgc": "vgc",
    "doubles": "vgc",
    "m-a": "vgc",
    "ma": "vgc",
    "regma": "vgc",
    "reg m-a": "vgc",
    "championsvgc2026regma": "vgc",
    "gen9championsvgc2026regma": "vgc",
    "[champions] vgc 2026 reg m-a": "vgc",
    "[gen 9 champions] vgc 2026 reg m-a": "vgc",
    "current": "vgc",
    # BSS — singles
    "bss": "bss",
    "singles": "bss",
    "battlestadiumsingles": "bss",
    "battle stadium singles": "bss",
    "championsbssregma": "bss",
    "gen9championsbssregma": "bss",
    "[champions] bss reg m-a": "bss",
    "[gen 9 champions] bss reg m-a": "bss",
}


def resolve_format(key: str) -> str:
    """Normalise *key* to one of ``"vgc"`` or ``"bss"``.

    Raises ``ValueError`` if the key cannot be resolved.
    """
    normalised = FORMAT_ALIASES.get(key.strip().lower())
    if normalised is None:
        raise ValueError(
            f"Unknown Champions format '{key}'. "
            f"Use 'vgc' (doubles) or 'bss' (singles)."
        )
    return normalised
