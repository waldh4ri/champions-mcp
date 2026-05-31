from __future__ import annotations

from champions_mcp.champions_items import item_key
from champions_mcp.pokeapi import slugify, strip_accents


def test_strip_accents():
    assert strip_accents("Flabébé") == "Flabebe"
    assert strip_accents("Évoli") == "Evoli"
    assert strip_accents("Ho-Oh") == "Ho-Oh"


def test_slugify_handles_accents():
    # The bug: these used to become "flab-b-b" / "evoli" was "voli"-ish.
    assert slugify("Flabébé") == "flabebe"          # PokeAPI slug
    assert slugify("Évoli") == "evoli"              # FR Eevee
    assert slugify("Carchacrok") == "carchacrok"    # unaccented FR, unchanged
    assert slugify("Mr. Mime") == "mr-mime"


def test_item_key_handles_accents_and_regressions():
    assert item_key("Évoli") == "evoli"
    # existing behaviour must still hold
    assert item_key("King's Rock") == "kings-rock"
    assert item_key("Never-Melt Ice") == "never-melt-ice"
    assert item_key("Life Orb") == "life-orb"
