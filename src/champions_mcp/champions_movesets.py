"""Per-species Pokémon Champions movesets (which moves a Pokémon can use).

PokeAPI has a `champions` version group but its learnsets are EMPTY, and the
`scarlet-violet` learnset is wrong for Champions (movepools were heavily
rebalanced: moves added/removed, PP standardised, blanket bans like Psybeam,
Incineroar losing Knock Off). The authoritative source is Serebii's per-Pokémon
Champions Pokédex "Standard Moves" table (+ Egg Moves page), scraped/verified
into ``data/champions_movesets.json``.

Keyed by the Serebii Champions slug (same slug used in champions_pokemon.json,
e.g. "garchomp", "sandslash-alola"); values are normalized move keys.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .http import make_http_client
from .normalize import normalize_key as item_key  # noqa: F401 — used in this module
from .normalize import normalize_key as move_key  # noqa: F401 — re-exported


class ChampionsMovesets:
    """Loaded per-species Champions movepools."""

    def __init__(
        self,
        movesets: dict[str, list[str]],
        verified: bool,
        source_url: str = "",
        notes=None,
    ) -> None:
        self._raw = movesets
        self._by_key: dict[str, set[str]] = {
            item_key(k): {move_key(m) for m in v} for k, v in movesets.items()
        }
        self.verified = verified
        self.source_url = source_url
        self.notes = notes or []

    @property
    def loaded(self) -> bool:
        return bool(self._by_key)

    def _lookup(self, candidate_keys: set[str]) -> set[str] | None:
        for c in candidate_keys:
            if c in self._by_key:
                return self._by_key[c]
        # region-stripped fallback (e.g. "raichu-alola" -> "raichu")
        for c in candidate_keys:
            base = re.sub(r"-(alola|galar|hisui|paldea).*$", "", c)
            if base in self._by_key:
                return self._by_key[base]
        return None

    def legal_moves(self, candidate_keys: set[str]) -> set[str] | None:
        """Return the species' legal Champions move keys, or None if unknown."""
        return self._lookup(candidate_keys)

    def is_legal(self, candidate_keys: set[str], move: str):
        """(known: bool, legal: bool) for one move on a species."""
        moves = self._lookup(candidate_keys)
        if moves is None:
            return (False, False)
        return (True, move_key(move) in moves)

    @classmethod
    def load(cls, settings: Settings) -> "ChampionsMovesets":
        path = settings.data_dir / "champions_movesets.json"
        if not path.is_file():
            return cls({}, verified=False)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            movesets=data.get("movesets", {}),
            verified=bool(data.get("verified", False)),
            source_url=data.get("source_url", ""),
            notes=data.get("notes", []),
        )


_BASE = "https://www.serebii.net/pokedex-champions"

# PokeAPI slugs that differ from Serebii's URL slugs.
_SEREBII_SLUG: dict[str, str] = {
    "mr-rime": "mr.rime",
    "mr-mime": "mr.mime",
}


def _extract_moves(html: str) -> list[str]:
    """Champions move display names from a Serebii Champions Pokédex page.

    Move names are the only /attackdex-champions/ anchors whose text is not a
    nav pseudo-link (those start with '-', e.g. '-Champions Attackdex').
    """
    soup = BeautifulSoup(html, "html.parser")
    names: dict[str, str] = {}
    for a in soup.select("a[href*='/attackdex-champions/']"):
        t = a.get_text(" ", strip=True)
        if (
            t
            and not t.startswith("-")
            and re.match(r"^[A-Za-z][A-Za-z0-9 '\-\.]+$", t)
        ):
            names[move_key(t)] = t
    return sorted(names.values())


async def scrape_species_moves(
    client: httpx.AsyncClient, slug: str, include_egg: bool = True
) -> list[str]:
    """Scrape one species' Champions movepool (Standard Moves [+ Egg Moves]).

    Returns [] on failure (Serebii markup is unstable).
    """
    moves: dict[str, str] = {}
    serebii_slug = _SEREBII_SLUG.get(slug, slug)
    try:
        r = await client.get(f"{_BASE}/{serebii_slug}/")
        r.raise_for_status()
        for m in _extract_moves(r.text):
            moves[move_key(m)] = m
    except Exception:
        return []
    if include_egg:
        try:
            r = await client.get(f"{_BASE}/{serebii_slug}/egg.shtml")
            if r.status_code == 200:
                for m in _extract_moves(r.text):
                    moves[move_key(m)] = m
        except Exception:
            pass
    return sorted(moves.values())


async def scrape_all_movesets(
    settings: Settings,
    slugs: list[str],
    concurrency: int = 6,
    include_egg: bool = True,
) -> dict[str, list[str]]:
    """Scrape Champions movepools for the given Serebii slugs (polite, bounded)."""
    out: dict[str, list[str]] = {}
    sem = asyncio.Semaphore(concurrency)
    async with make_http_client(settings) as client:
        async def one(slug: str) -> None:
            async with sem:
                mv = await scrape_species_moves(client, slug, include_egg)
                if mv:
                    out[slug] = mv

        await asyncio.gather(*(one(s) for s in slugs))
    return out
