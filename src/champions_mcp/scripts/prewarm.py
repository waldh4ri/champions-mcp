from __future__ import annotations

import asyncio
import json
import re

from ..cache import Cache
from ..champions_items import scrape_serebii_items
from ..champions_movesets import move_key, scrape_all_movesets
from ..champions_roster import species_line_key
from ..config import Settings
from ..names import NameIndex
from ..pokeapi import PokeAPIClient
from ..regulations import RegulationRegistry

_REGION_SLUG_RE = re.compile(r"-(alola|galar|hisui|paldea)\b")


def _region_from_slug(slug: str) -> str | None:
    m = _REGION_SLUG_RE.search(slug)
    return m.group(1) if m else None


async def _run() -> None:
    settings = Settings.load()
    cache = Cache(settings.cache_db)
    api = PokeAPIClient(settings, cache)
    try:
        print("Caching Pokémon slug index ...")
        slugs = await api.all_pokemon_slugs()
        print(f"  {len(slugs)} Pokémon slugs cached.")

        print("Building French locale name index (one request per species) ...")
        names = NameIndex(api, cache)
        count = await names.build_locale_index("fr")
        print(f"  {count} French names indexed.")

        print("Refreshing Champions item catalog from Serebii ...")
        items = await scrape_serebii_items(settings)
        if len(items) >= 100:
            doc = {
                "source": "Pokémon Champions item list",
                "source_url": "https://www.serebii.net/pokemonchampions/"
                "items.shtml",
                "verified": True,
                "scraped_count": len(items),
                "notes": [
                    "Items absent here do NOT exist in Champions.",
                ],
                "items": items,
            }
            (settings.data_dir / "champions_items.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  {len(items)} Champions items written (verified).")
        else:
            print(
                f"  scrape returned only {len(items)} items; keeping the "
                "existing catalog (Serebii layout may have changed)."
            )

        print("Building Champions roster from active regulation ...")
        regs = RegulationRegistry(settings)
        reg = regs.active()
        entries = []
        for slug in reg.allowed_species or []:
            base = species_line_key(slug)
            try:
                mon = await api.get_pokemon(slug)
                name = mon.name
                types = mon.types
                is_legendary = mon.is_legendary
                is_mythical = mon.is_mythical
            except Exception:
                name = slug
                types = []
                is_legendary = False
                is_mythical = False
            entries.append({
                "name": name,
                "slug": slug,
                "base": base,
                "region": _region_from_slug(slug),
                "is_mega": False,
                "types": types,
                "keys": sorted({slug, base}),
                "is_legendary": is_legendary,
                "is_mythical": is_mythical,
                "display": name,
            })
        doc = {
            "source": f"Pokémon Champions roster (regulation {reg.id})",
            "source_url": next(iter(reg.source_urls), ""),
            "verified": reg.roster_verified,
            "pickable_count": len(entries),
            "notes": [
                f"Derived from regulation {reg.id} allowed_species list.",
                "Re-run prewarm when the active regulation changes.",
            ],
            "entries": entries,
        }
        (settings.data_dir / "champions_pokemon.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  {len(entries)} species written from {reg.id} allowlist.")

        print("Scraping per-species Champions movesets from Serebii ...")
        moveset_slugs = sorted({species_line_key(s) for s in reg.allowed_species or []})
        movesets = await scrape_all_movesets(settings, moveset_slugs, concurrency=8)
        if len(movesets) >= 150:
            uniq = {move_key(m) for v in movesets.values() for m in v}
            (settings.data_dir / "champions_movesets.json").write_text(
                json.dumps(
                    {
                        "source": "Serebii Pokémon Champions Pokédex "
                        "(Standard + Egg moves)",
                        "source_url": "https://www.serebii.net/"
                        "pokedex-champions/",
                        "verified": True,
                        "species_count": len(movesets),
                        "total_unique_moves": len(uniq),
                        "notes": [
                            "Per-species Champions movepool. PokeAPI "
                            "champions learnsets are empty and SV learnsets "
                            "are wrong for Champions.",
                        ],
                        "movesets": movesets,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                f"  {len(movesets)} species movesets written "
                f"({len(uniq)} unique moves, verified)."
            )
        else:
            print(
                f"  only {len(movesets)} species scraped; keeping the "
                "existing moveset catalog."
            )
    finally:
        await api.aclose()
        cache.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
