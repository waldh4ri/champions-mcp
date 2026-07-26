"""Tests for the competitive role catalogue.

Two things matter here: the catalogue must stay internally consistent (ids,
cross-references, per-format grading), and every move/item/ability it names as
a signature must actually exist in Champions — otherwise the tool would teach a
model to build around things this game doesn't have.
"""

from __future__ import annotations

import pytest

from champions_mcp import roles


def test_catalogue_is_well_formed():
    ids = {r["id"] for r in roles._ROLES}
    assert len(ids) == len(roles._ROLES)  # no duplicate ids

    for r in roles._ROLES:
        assert r["category"] in roles.CATEGORIES, r["id"]
        assert set(r["formats"]) == {"vgc", "bss"}, r["id"]
        for fmt in ("vgc", "bss"):
            assert r["formats"][fmt]["viability"] in roles.VIABILITY, (r["id"], fmt)
            assert r["formats"][fmt]["note"], (r["id"], fmt)
        # every cross-reference resolves to a real role
        for key in ("pairs_with", "answered_by"):
            for ref in r[key]:
                assert ref in ids, (r["id"], key, ref)


@pytest.mark.parametrize("probe,expected", [
    ("offensive-pivot", "offensive-pivot"),
    ("offensive pivot", "offensive-pivot"),
    ("scarfer", "revenge-killer"),          # alias
    ("follow me user", "redirection-support"),
    ("Trick Room Setter", "trick-room-setter"),
    ("fake out", "fake-out-user"),
])
def test_role_resolution(probe, expected):
    assert roles.resolve_role(probe) == expected


def test_unknown_role_is_reported_not_guessed():
    assert roles.resolve_role("notarole-xyz") is None


def test_formats_are_graded_separately():
    """The whole point of the per-format grading: doubles-only mechanics must be
    unviable in singles and vice versa."""
    vgc_only = {"redirection-support", "spread-attacker", "field-protection-support"}
    for rid in vgc_only:
        assert roles._BY_ID[rid]["formats"]["bss"]["viability"] == "unviable"
        assert roles._BY_ID[rid]["formats"]["vgc"]["viability"] != "unviable"

    # ...and singles concepts that don't survive bring-4 doubles
    for rid in ("phazer", "stallbreaker", "suicide-lead"):
        assert roles._BY_ID[rid]["formats"]["vgc"]["viability"] == "unviable"


def test_list_roles_filters_by_format():
    vgc = roles.list_roles(format="vgc")
    bss = roles.list_roles(format="bss")

    vgc_ids = {e["id"] for v in vgc["roles_by_category"].values() for e in v}
    bss_ids = {e["id"] for v in bss["roles_by_category"].values() for e in v}

    assert "redirection-support" in vgc_ids and "redirection-support" not in bss_ids
    assert "phazer" in bss_ids and "phazer" not in vgc_ids
    # excluded roles are surfaced with a reason rather than silently dropped
    assert {e["id"] for e in bss["excluded_for_this_format"]} >= {"redirection-support"}
    assert all(e["reason"] for e in bss["excluded_for_this_format"])
    # unfiltered listing carries both grades
    both = roles.list_roles()
    entry = next(e for v in both["roles_by_category"].values() for e in v
                 if e["id"] == "redirection-support")
    assert entry["viability"] == {"vgc": "core", "bss": "unviable"}


def test_list_roles_filters_by_category_and_query():
    speed = roles.list_roles(category="speed_control")
    assert set(speed["roles_by_category"]) == {"speed_control"}

    hit = roles.list_roles(query="wallbreaker")
    ids = {e["id"] for v in hit["roles_by_category"].values() for e in v}
    assert {"physical-wallbreaker", "special-wallbreaker"} <= ids

    assert "warning" in roles.list_roles(category="nonsense")


def test_signatures_exist_in_champions(db):
    """Every signature move/item/ability must be real in Champions — this is the
    guard against the catalogue teaching Choice Band or Assault Vest, which this
    game does not have."""
    unknown = []
    for r in roles._ROLES:
        sig = roles.get_role_description(db, r["id"])["champions_signature"]
        for kind in ("moves", "items", "abilities"):
            assert sig[kind]["in_champions"], (r["id"], kind)  # never all-empty
            unknown += [(r["id"], kind, n) for n in sig[kind]["not_in_champions"]]
    assert unknown == []


def test_get_role_description_shape(db):
    d = roles.get_role_description(db, "redirection-support", format="vgc")
    assert d["id"] == "redirection-support"
    assert d["category"] == "support"
    assert d["format_viability"]["vgc"]["viability"] == "core"
    assert d["format_viability"]["bss"]["viability"] == "unviable"
    assert d["verdict"].startswith("core")
    assert "warning" not in d
    # the requested format keeps its full reasoning; the other is grade-only, so
    # the note isn't paid twice for a format the caller didn't ask about
    assert d["format_viability"]["vgc"]["note"]
    assert "note" not in d["format_viability"]["bss"]

    moves = {m["name"] for m in d["champions_signature"]["moves"]["in_champions"]}
    assert "Follow Me" in moves
    assert all(m["learned_by_count"] > 0
               for m in d["champions_signature"]["moves"]["in_champions"])


def test_unviable_role_warns_for_the_requested_format(db):
    d = roles.get_role_description(db, "redirection-support", format="bss")
    assert d["verdict"].startswith("unviable")
    assert "BSS" in d["warning"]


def test_unknown_role_returns_the_catalogue(db):
    d = roles.get_role_description(db, "definitely-not-a-role")
    assert "error" in d
    assert len(d["known_role_ids"]) == len(roles._ROLES)
    assert "did_you_mean" not in d  # nothing plausibly matches


def test_ambiguous_role_offers_candidates_instead_of_guessing(db):
    """'wall' could mean three different roles — answering with an arbitrary one
    would hand back a confidently wrong brief."""
    assert roles.resolve_role("wall") is None
    d = roles.get_role_description(db, "wall")
    ids = {c["id"] for c in d["did_you_mean"]}
    assert {"physical-wall", "special-wall", "physical-wallbreaker"} <= ids
    assert "matches several roles" in d["error"]


def test_per_call_payloads_dont_repeat_static_guidance(db):
    """Static guidance belongs in the tool description (sent once, in the cached
    prompt prefix) or in list_roles (called once per build) — not in every role
    lookup, where a 4-6 call build re-pays it and leaves it in context forever."""
    import json
    brief = roles.get_role_description(db, "cleaner")
    # the multi-paragraph gate, the source list and the disclaimer live on the
    # listing, not on each brief
    assert "creative_pick_gate" not in brief
    assert "sources" not in brief and "note" not in brief
    assert isinstance(brief["picking_creatively"], str)

    listing = roles.list_roles(format="vgc")
    assert isinstance(listing["picking_creatively"], dict)  # full gate, once
    assert listing["sources"] and listing["note"]

    # a realistic build (listing + 5 briefs) must stay affordable
    total = len(json.dumps(listing, ensure_ascii=False)) + sum(
        len(json.dumps(roles.get_role_description(db, r), ensure_ascii=False))
        for r in ("physical-wallbreaker", "tailwind-setter", "fake-out-user",
                  "bulky-attacker", "redirection-support")
    )
    assert total < 36000, f"listing + 5 briefs = {total} chars"


def test_listing_stays_small_enough_for_a_tool_result_budget():
    """list_roles is an index, not a set of briefs: harnesses cap tool results
    (docker/chat/main.py uses MAX_TOOL_RESULT_CHARS), and a silently halved
    catalogue would hide roles from the model."""
    import json
    for kwargs in ({}, {"format": "vgc"}, {"format": "bss"}):
        payload = json.dumps(roles.list_roles(**kwargs), ensure_ascii=False)
        assert len(payload) < 12000, (kwargs, len(payload))
        # the per-format reasoning belongs in get_role_description, not here
        assert "format_note" not in payload
