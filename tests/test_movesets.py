from __future__ import annotations

from champions_mcp.champions_movesets import (
    ChampionsMovesets,
    _extract_moves,
    move_key,
)


def test_move_key_normalization():
    assert move_key("Will-O-Wisp") == "will-o-wisp"
    assert move_key("U-turn") == "u-turn"
    assert move_key("Double-Edge") == "double-edge"


def test_extract_moves_filters_nav_pseudo_links():
    html = """
    <a href="/attackdex-champions/earthquake.shtml">Earthquake</a>
    <a href="/attackdex-champions/uturn.shtml">U-turn</a>
    <a href="/attackdex-champions/index.shtml">-Champions Attackdex</a>
    <a href="/pokedex-champions/garchomp/">Garchomp</a>
    """
    moves = _extract_moves(html)
    assert "Earthquake" in moves and "U-turn" in moves
    assert all(not m.startswith("-") for m in moves)
    assert "Garchomp" not in moves          # not an attackdex link


def test_movesets_lookup_and_legality(movesets):
    cand = {"araquanid"}
    legal = movesets.legal_moves(cand)
    assert legal is not None and "liquidation" in legal
    assert movesets.is_legal(cand, "Liquidation") == (True, True)
    assert movesets.is_legal(cand, "Knock Off") == (True, False)
    # unknown species -> known=False
    assert movesets.is_legal({"pikachu"}, "Thunderbolt") == (False, False)


def test_exact_regional_key_and_base_fallback():
    # Exact regional key (real case: PokeAPI slug == Serebii slug).
    regional = ChampionsMovesets(
        {"sandslash-alola": ["Icicle Spear", "Iron Head"]}, verified=True
    )
    assert regional.is_legal({"sandslash-alola"}, "Iron Head") == (True, True)
    # Fallback: catalog keyed by base, candidate carries a region suffix.
    base_keyed = ChampionsMovesets(
        {"sandslash": ["Earthquake", "Swords Dance"]}, verified=True
    )
    known, legal = base_keyed.is_legal({"sandslash-alola"}, "Earthquake")
    assert (known, legal) == (True, True)


def test_unloaded_is_safe():
    ms = ChampionsMovesets({}, verified=False)
    assert ms.loaded is False
    assert ms.legal_moves({"garchomp"}) is None
