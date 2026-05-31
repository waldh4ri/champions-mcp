"""The global Pokémon Champions roster (which Pokémon exist in the game).

Derived from the active regulation's ``allowed_species`` list (committed to
``data/champions_pokemon.json`` by the prewarm script). Species are enriched
with types and names from PokeAPI so that regional forms can be matched by
(species line + typing) via ``ChampionsRoster.contains()``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .champions_items import item_key
from .config import Settings
from .normalize import REGION_MAP

_MEGA_RE = re.compile(r"(^mega\s|\(mega|\bprimal\s)", re.IGNORECASE)

TYPES = (
    "normal", "fire", "water", "electric", "grass", "ice", "fighting",
    "poison", "ground", "flying", "psychic", "bug", "rock", "ghost",
    "dragon", "dark", "steel", "fairy",
)
_REGION_SUFFIX_RE = re.compile(r"-(alola|galar|hisui|paldea)\b")


def is_regional_slug(slug: str) -> bool:
    return bool(_REGION_SUFFIX_RE.search(slug or ""))


def species_line_key(value: str) -> str:
    """Normalize any slug/name to its base species 'line' key.

    Strips Mega/Primal and regional suffixes so every form of a species maps
    to the same line, e.g. "ninetales-alola" / "Ninetales" -> "ninetales".
    """
    s = item_key(value)
    s = re.sub(r"-(mega|primal).*$", "", s)
    s = re.sub(r"-(alola|galar|hisui|paldea).*$", "", s)
    return s


def parse_entry(
    display: str,
    serebii_slug: str | None = None,
    types: list[str] | None = None,
) -> dict:
    """Classify a Serebii roster row into structured fields.

    ``serebii_slug`` comes from the /pokedex-champions/<slug>/ href and is the
    canonical key (e.g. "venusaur", "raichu-alola"). ``types`` is the row's
    Type column (the form's typing — Mega rows carry the Mega typing). ``keys``
    are the normalized lookup keys a *pickable* species answers to.
    """
    raw = display.strip()
    low = raw.lower()
    is_mega = bool(_MEGA_RE.search(low))

    region = None
    for word, code in REGION_MAP.items():
        if word in low:
            region = code
            break

    base = re.sub(r"\(.*?\)", "", raw)
    base = re.sub(r"^\s*(mega|primal)\s+", "", base, flags=re.IGNORECASE)
    base = re.sub(
        r"\b(alolan|galarian|hisuian|paldean)\b", "", base, flags=re.IGNORECASE
    )
    base = re.sub(r"\b(x|y)\s*form\b", "", base, flags=re.IGNORECASE)
    base = base.strip(" -")

    slug_key = item_key(serebii_slug) if serebii_slug else ""
    base_key = slug_key or item_key(base)

    keys: set[str] = set()
    if not is_mega:
        if slug_key:
            keys.add(slug_key)
        if base_key:
            keys.add(base_key)
        disp_key = item_key(base)
        if disp_key:
            keys.add(disp_key)
            if region:
                keys.add(f"{disp_key}-{region}")
    return {
        "name": raw,
        "slug": serebii_slug or "",
        "base": base_key,
        "region": region,
        "is_mega": is_mega,
        "types": [t.lower() for t in (types or []) if t.lower() in TYPES],
        "keys": sorted(keys),
    }


class ChampionsRoster:
    """Loaded global roster; answers 'is this species in Champions?'."""

    def __init__(
        self,
        entries: list[dict],
        verified: bool,
        source_url: str = "",
        notes=None,
    ) -> None:
        self.entries = entries
        self.verified = verified
        self.source_url = source_url
        self.notes = notes or []
        self._pick_keys: set[str] = set()
        # species line key -> list of typed variants (each a list of types)
        self._lines: dict[str, list[list[str]]] = {}
        self._flags: dict[str, dict] = {}
        self.pickable: list[dict] = []
        for e in entries:
            for k in e.get("keys", []):
                self._pick_keys.add(k)
            if not e.get("is_mega") and e.get("base"):
                self.pickable.append(e)
                lk = species_line_key(e.get("slug") or e["base"])
                etypes = [t.lower() for t in e.get("types", [])]
                self._lines.setdefault(lk, []).append(etypes)
                f = self._flags.setdefault(
                    lk,
                    {
                        "is_legendary": False,
                        "is_mythical": False,
                        "display": e.get("display") or e["name"],
                        "types": etypes,
                    },
                )
                f["is_legendary"] |= bool(e.get("is_legendary"))
                f["is_mythical"] |= bool(e.get("is_mythical"))

    @property
    def loaded(self) -> bool:
        return bool(self.entries)

    def _line_candidates(self, slug: str, base: str, name: str) -> set[str]:
        return {
            k
            for k in (
                species_line_key(slug),
                species_line_key(base),
                species_line_key(name),
            )
            if k
        }

    def contains(
        self, slug: str, base: str, name: str, types=None
    ) -> bool:
        """Is this Pokémon (incl. regional forms) in the Champions roster?

        Regional forms share name/slug with their base on Serebii and are
        distinguished only by typing, so a regional form is legal iff the
        species line has a Champions variant with its exact ``types``.
        """
        regional = is_regional_slug(slug)
        want = frozenset(t.lower() for t in (types or []))
        for lk in self._line_candidates(slug, base, name):
            variants = self._lines.get(lk)
            if not variants:
                continue
            if not regional:
                return True
            if want and any(frozenset(v) == want for v in variants):
                return True
            if not want and len(variants) > 1:
                return True
        return False

    def flags_for(self, slug: str, base: str, name: str) -> dict | None:
        for lk in self._line_candidates(slug, base, name):
            if lk in self._flags:
                return self._flags[lk]
        return None

    def types_for(
        self, slug: str, base: str, name: str, types=None
    ) -> list[str]:
        regional = is_regional_slug(slug)
        want = frozenset(t.lower() for t in (types or []))
        for lk in self._line_candidates(slug, base, name):
            variants = self._lines.get(lk)
            if not variants:
                continue
            for v in variants:
                if want and frozenset(v) == want:
                    return v
            if not regional:
                return variants[0]
        return []

    def by_type(
        self, type1: str, type2: str | None = None
    ) -> list[dict]:
        """Pickable (non-Mega) entries whose typing includes the given type(s).

        One type -> mono or part-typed matches; two types -> must have both.
        """
        t1 = type1.strip().lower()
        t2 = type2.strip().lower() if type2 else None
        out: list[dict] = []
        for e in self.pickable:
            ts = set(e.get("types", []))
            if t1 in ts and (t2 is None or t2 in ts):
                out.append(e)
        return out

    @classmethod
    def load(cls, settings: Settings) -> "ChampionsRoster":
        path = settings.data_dir / "champions_pokemon.json"
        if not path.is_file():
            return cls([], verified=False)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            entries=data.get("entries", []),
            verified=bool(data.get("verified", False)),
            source_url=data.get("source_url", ""),
            notes=data.get("notes", []),
        )


