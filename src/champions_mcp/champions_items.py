from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .http import make_http_client
from .normalize import normalize_key as item_key  # noqa: F401 — re-exported

class ItemCatalog:
    """The set of items that exist in Pokémon Champions.

    Champions ships a curated item list (Serebii enumerates it), which is a
    *subset* of PokeAPI's universe — e.g. Life Orb does not exist in Champions.
    When ``verified`` is true, legality is decided purely by catalog membership;
    when false, the validator only warns (the catalog may be incomplete).
    """

    def __init__(
        self,
        keys: set[str],
        verified: bool,
        source_url: str = "",
        notes=None,
        names: list[str] | None = None,
    ) -> None:
        self.keys = keys
        self.names: list[str] = sorted(names) if names else []
        self.verified = verified
        self.source_url = source_url
        self.notes = notes or []

    def __contains__(self, name: str) -> bool:
        return item_key(name) in self.keys

    @property
    def loaded(self) -> bool:
        return bool(self.keys)

    @classmethod
    def load(cls, settings: Settings) -> "ItemCatalog":
        path = settings.data_dir / "champions_items.json"
        if not path.is_file():
            return cls(set(), verified=False)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_names: list[str] = data.get("items", [])
        keys = {item_key(n) for n in raw_names}
        return cls(
            keys=keys,
            names=raw_names,
            verified=bool(data.get("verified", False)),
            source_url=data.get("source_url", ""),
            notes=data.get("notes", []),
        )


async def scrape_serebii_items(
    settings: Settings,
    url: str = "https://www.serebii.net/pokemonchampions/items.shtml",
) -> list[str]:
    """Best-effort scrape of the Serebii Champions item list.

    Returns a flat, de-duplicated list of item display names (hold items, Mega
    Stones, berries, misc). Serebii markup is unstable; returns [] on failure.
    """
    try:
        async with make_http_client(settings) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        names: dict[str, str] = {}

        # Primary signal: itemdex anchors.
        for a in soup.select("a[href*='itemdex/']"):
            text = a.get_text(" ", strip=True)
            if _plausible(text):
                names[item_key(text)] = text

        # Fallback: data cells inside Serebii dextables.
        if len(names) < 50:
            for cell in soup.select("table.dextable td"):
                text = cell.get_text(" ", strip=True)
                if _plausible(text):
                    names.setdefault(item_key(text), text)

        return sorted(names.values())
    except Exception:
        return []


def _plausible(text: str) -> bool:
    return bool(text) and 2 <= len(text) <= 30 and re.match(
        r"^[A-Za-z][A-Za-z0-9 '\-\.]+$", text
    ) is not None
