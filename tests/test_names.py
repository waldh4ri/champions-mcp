from __future__ import annotations

import pytest


async def test_resolves_english_slug(names):
    assert await names.resolve("araquanid") == "araquanid"


async def test_resolves_french_seed_name(names):
    # Tarenbulle is the French name for Araquanid (seed map / built index).
    assert await names.resolve("Tarenbulle") == "araquanid"


async def test_fuzzy_fallback(names):
    assert await names.resolve("incineroarr") == "incineroar"


async def test_unresolvable_raises(names):
    with pytest.raises(KeyError):
        await names.resolve("Definitely Not A Pokemon 9000")


def test_regional_slug_natural_language():
    from champions_mcp.names import regional_slug
    assert regional_slug("Alolan Ninetales") == "ninetales-alola"
    assert regional_slug("Galarian Slowking") == "slowking-galar"
    assert regional_slug("Ninetales (Alolan Form)") == "ninetales-alola"
    assert regional_slug("Hisuian Zoroark") == "zoroark-hisui"
    assert regional_slug("Garchomp") is None
    assert regional_slug("Carchacrok") is None


async def test_resolves_regional_slug_via_natural_language(names):
    # "Sandslash (Alolan Form)" -> regional_slug -> "sandslash-alola"
    # FakePokeAPI knows "sandslash-alola", so this should resolve.
    result = await names.resolve("Sandslash (Alolan Form)")
    assert result == "sandslash-alola"


async def test_ensure_loaded_idempotent(names):
    # Calling resolve twice should not raise; _ensure_loaded is guarded by _loaded.
    assert await names.resolve("araquanid") == "araquanid"
    assert await names.resolve("araquanid") == "araquanid"


async def test_locale_index_hit_after_injection(names):
    # Manually inject an entry into the locale cache and confirm resolve finds it.
    names._fr["carchacrok"] = "garchomp"
    names._loaded = True
    assert await names.resolve("Carchacrok") == "garchomp"


async def test_ensure_loaded_updates_from_cache(fake_api, cache):
    """_ensure_loaded merges a pre-existing cache entry into _fr."""
    from champions_mcp.names import NameIndex, _NORMALIZE_CACHE_KEY

    # Seed the cache with a locale index entry before the NameIndex is created.
    await cache.set(_NORMALIZE_CACHE_KEY, {"pikachuu": "pikachu"}, ttl=None)
    idx = NameIndex(fake_api, cache)
    # "pikachuu" is not in the seed dict, so resolve will need the cache hit.
    # After _ensure_loaded, _fr should include the cached entry.
    assert "pikachuu" not in idx._fr
    await idx._ensure_loaded()
    assert idx._fr.get("pikachuu") == "pikachu"


async def test_build_locale_index_skips_failing_species(names, fake_api, monkeypatch):
    """Species whose raw_species() raises are silently skipped (except/continue path)."""
    call_count = 0

    async def fake_raw_species_raises(name_or_id):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("network error")

    async def fake_get(path):
        return {"results": [{"name": "garchomp"}, {"name": "electabuzz"}]}

    monkeypatch.setattr(fake_api, "raw_species", fake_raw_species_raises)
    monkeypatch.setattr(fake_api, "_get", fake_get)

    count = await names.build_locale_index("fr")
    # All species raised, so nothing was added.
    assert count == 0
    assert call_count == 2  # both species were attempted


async def test_build_locale_index(names, fake_api, monkeypatch):
    # Patch raw_species to return one French name.
    async def fake_raw_species(name_or_id):
        return {"names": [{"language": {"name": "fr"}, "name": "Élektek"}]}

    async def fake_get(path):
        return {"results": [{"name": "electabuzz"}]}

    monkeypatch.setattr(fake_api, "raw_species", fake_raw_species)
    monkeypatch.setattr(fake_api, "_get", fake_get)

    count = await names.build_locale_index("fr")
    assert count == 1
    assert await names.resolve("Élektek") == "electabuzz"


async def test_resolve_regional_slug_not_in_api(names, fake_api, monkeypatch):
    """When regional_slug succeeds but raw_pokemon(rslug) raises, fall through."""
    # "Alolan Pikachu" -> rslug = "pikachu-alola"; FakePokeAPI doesn't know it.
    # After the except/pass, it should fall through (no slug in _fr → unresolvable).
    import pytest
    with pytest.raises(KeyError):
        await names.resolve("Alolan Pikachu")


async def test_resolve_all_pokemon_slugs_raises(names, fake_api, monkeypatch):
    """When all_pokemon_slugs() raises, fuzzy matching falls back to empty list."""
    async def broken_slugs():
        raise RuntimeError("network error")

    monkeypatch.setattr(fake_api, "all_pokemon_slugs", broken_slugs)
    import pytest
    # "Xyzbogus" won't match anything in an empty slug list → KeyError.
    with pytest.raises(KeyError):
        await names.resolve("Xyzbogus123")
