"""Shared slug/key normalisation utilities.

Single source of truth for all string normalisation used throughout the
champions-mcp codebase.  Import from here instead of duplicating logic.
"""

from __future__ import annotations

import re
import unicodedata

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# ---------------------------------------------------------------------------
# Basic normalisation
# ---------------------------------------------------------------------------


def strip_accents(value: str) -> str:
    """Transliterate accented Latin letters to ASCII (é->e, ñ->n, ü->u...).

    NFKD splits a letter into base + combining mark; dropping the combining
    marks leaves the bare ASCII letter. Without this, "Flabébé" slugifies to
    "flab-b-b" instead of PokeAPI's "flabebe".
    """
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(c)
    )


def slugify(value: str) -> str:
    """Convert a display name to a PokeAPI-style slug."""
    return _SLUG_RE.sub("-", strip_accents(value).strip().lower()).strip("-")


def normalize_key(name: str) -> str:
    """Normalize an item/move name to a comparison key.

    Strips accents and apostrophes *before* collapsing non-alphanumeric runs
    so Serebii display names, user input and PokeAPI slugs all converge:
    "King's Rock" / "kings-rock" -> "kings-rock";
    "Never-Melt Ice" -> "never-melt-ice"; "Évoli" -> "evoli".
    """
    s = strip_accents(name).strip().lower().replace("'", "").replace("\u2019", "")
    return _SLUG_RE.sub("-", s).strip("-")


# ---------------------------------------------------------------------------
# Mega / Primal
# ---------------------------------------------------------------------------

MEGA_SUFFIXES = ("-mega", "-mega-x", "-mega-y", "-primal")


def base_species(slug: str) -> str:
    """Strip Mega/Primal suffixes to get the base species slug."""
    for suffix in MEGA_SUFFIXES:
        if slug.endswith(suffix):
            return slug[: -len(suffix)]
    return slug


# ---------------------------------------------------------------------------
# Regional forms
# ---------------------------------------------------------------------------

# Adjective form  ->  PokeAPI slug suffix
REGION_MAP: dict[str, str] = {
    "alolan": "alola",
    "galarian": "galar",
    "hisuian": "hisui",
    "paldean": "paldea",
}

REGION_WORD_RE = re.compile(
    r"\b(alolan|galarian|hisuian|paldean)\b", re.IGNORECASE
)
