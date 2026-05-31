from __future__ import annotations

from champions_mcp.champions_roster import ChampionsRoster, parse_entry


def test_parse_plain_species():
    e = parse_entry("Garchomp", "garchomp")
    assert e["is_mega"] is False
    assert "garchomp" in e["keys"]


def test_parse_mega_is_flagged_and_not_pickable():
    e = parse_entry("Mega Venusaur", "venusaur")
    assert e["is_mega"] is True
    assert e["keys"] == []          # Megas are not a separate pick


def test_parse_meganium_is_not_a_mega():
    e = parse_entry("Meganium", "meganium")
    assert e["is_mega"] is False
    assert "meganium" in e["keys"]


def test_parse_regional_form():
    e = parse_entry("Sandslash (Alolan form)", "sandslash-alola")
    assert e["region"] == "alola"
    assert e["is_mega"] is False
    assert "sandslash-alola" in e["keys"]


def test_roster_contains_with_regional_typing():
    # Serebii lists regional forms under the SAME name+slug as the base,
    # differing only by the Type column (two "Sandslash" rows here).
    entries = [
        parse_entry("Garchomp", "garchomp", ["dragon", "ground"]),
        parse_entry("Sandslash", "sandslash", ["ground"]),          # Kanto
        parse_entry("Sandslash", "sandslash", ["ice", "steel"]),    # Alolan
        parse_entry("Mega Venusaur", "venusaur", ["grass", "poison"]),
    ]
    r = ChampionsRoster(entries, verified=True)
    assert r.contains("garchomp", "garchomp", "Garchomp", ["dragon", "ground"])
    # base Sandslash (Ground)
    assert r.contains("sandslash", "sandslash", "Sandslash", ["ground"])
    # Alolan Sandslash resolves to slug 'sandslash-alola' w/ Ice/Steel ->
    # matched to the Champions variant with that exact typing.
    assert r.contains(
        "sandslash-alola", "sandslash-alola", "Sandslash", ["ice", "steel"]
    )
    # A regional typing NOT present as a Champions variant -> not legal.
    assert not r.contains(
        "sandslash-galar", "sandslash-galar", "Sandslash", ["dark"]
    )
    assert not r.contains("mewtwo", "mewtwo", "Mewtwo", ["psychic"])
    # Venusaur only exists here as a Mega row -> not a pickable species.
    assert not r.contains("venusaur", "venusaur", "Venusaur", ["grass"])


def test_unloaded_roster_is_safe():
    r = ChampionsRoster([], verified=False)
    assert r.loaded is False
    assert r.contains("garchomp", "garchomp", "Garchomp") is False


def test_parse_entry_captures_types():
    e = parse_entry("Garchomp", "garchomp", ["Dragon", "Ground"])
    assert e["types"] == ["dragon", "ground"]      # lowercased
    # junk type values are dropped
    e2 = parse_entry("Pikachu", "pikachu", ["electric", "notatype"])
    assert e2["types"] == ["electric"]


def test_roster_by_type_and_types_for():
    entries = [
        parse_entry("Garchomp", "garchomp", ["dragon", "ground"]),
        parse_entry("Dragapult", "dragapult", ["dragon", "ghost"]),
        parse_entry("Incineroar", "incineroar", ["fire", "dark"]),
        parse_entry("Mega Charizard", "charizard", ["fire", "flying"]),
    ]
    r = ChampionsRoster(entries, verified=True)
    drag = sorted(e["name"] for e in r.by_type("dragon"))
    assert drag == ["Dragapult", "Garchomp"]
    # two-type AND match
    dg = [e["name"] for e in r.by_type("dragon", "ground")]
    assert dg == ["Garchomp"]
    # Mega rows are not pickable -> excluded from by_type
    assert r.by_type("fire", "flying") == []
    assert r.types_for("garchomp", "garchomp", "Garchomp") == [
        "dragon", "ground"]


def test_regional_form_requires_its_typing():
    # Only base Ninetales (Fire) is listed -> the Alolan form is NOT in this
    # roster, and a regional form must match by its own typing.
    base_only = ChampionsRoster(
        [parse_entry("Ninetales", "ninetales", ["fire"])], verified=True
    )
    assert base_only.contains(
        "ninetales", "ninetales", "Ninetales", ["fire"]
    ) is True
    assert base_only.contains(
        "ninetales-alola", "ninetales-alola", "Ninetales", ["ice", "fairy"]
    ) is False

    # With BOTH variants listed (the real Champions case), each resolves.
    both = ChampionsRoster(
        [
            parse_entry("Ninetales", "ninetales", ["fire"]),
            parse_entry("Ninetales", "ninetales", ["ice", "fairy"]),
        ],
        verified=True,
    )
    assert both.contains(
        "ninetales-alola", "ninetales-alola", "Ninetales", ["ice", "fairy"]
    ) is True
    assert both.types_for(
        "ninetales-alola", "ninetales-alola", "Ninetales", ["ice", "fairy"]
    ) == ["ice", "fairy"]
    assert both.contains(
        "ninetales", "ninetales", "Ninetales", ["fire"]
    ) is True
