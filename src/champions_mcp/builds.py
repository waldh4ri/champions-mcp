"""Observed-build read model — real teams, read as evidence rather than orders.

``usage.get_pokemon_sets`` answers *marginal* questions: how often does Garchomp
run Earthquake, how often does it hold a Choice Scarf. It cannot answer the
joint one — what a *Scarf* Garchomp runs — because each list is computed
independently. Concatenating the top of each list yields a build nobody plays.

This module reads ``build_teams``/``build_members``: complete lists somebody
actually brought. That makes the conditional questions answerable, and it makes
one more thing possible that a ranked list cannot express — **where the real
disagreement is**. Every response therefore separates:

* ``locked``      — choices nearly every build shares. Deviating is a real cost;
                    know what you are paying for.
* ``contested``   — slots where good players genuinely disagree. This is design
                    space, not an unsolved problem.
* ``fringe``      — rare but *attested* choices. Precedent for a sharp idea.

The point is not to hand back the most popular build. A team that copies the
modal set inherits the modal weaknesses, and the modal set is often a
compromise nobody's specific team actually wanted.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

from . import historical_sets
from .db import Db
from .formats import game_type
from .names import try_resolve
from .normalize import slugify

# Share thresholds separating the three bands above. Deliberately wide in the
# middle: most slots are genuinely contested and saying so is the useful signal.
LOCKED_SHARE = 0.85
FRINGE_SHARE = 0.15

# Sets below this weighted share are reported under `fringe_sets` rather than
# the main list — real, attested, and worth knowing about precisely because
# they are not what everyone else brings.
RARE_SET_SHARE = 0.05


def _pct(x: float) -> float:
    return round(100.0 * x, 1)


def _weighted_shares(pairs: list[tuple[Any, float]]) -> list[tuple[Any, float, float]]:
    """Collapse (value, weight) pairs into (value, weight, share), best first.

    Args:
        pairs: One entry per observation.

    Returns:
        Distinct values with their summed weight and share of the total.
    """
    totals: dict[Any, float] = defaultdict(float)
    for value, weight in pairs:
        totals[value] += weight
    grand = sum(totals.values()) or 1.0
    return sorted(
        ((v, w, w / grand) for v, w in totals.items()),
        key=lambda t: -t[1],
    )


def _effective_variety(shares: list[float]) -> float:
    """Perplexity of a share distribution — the *effective* number of options.

    Ten builds split 91/1/1/1/… is not variety; ten builds split evenly is.
    Shannon perplexity captures that in one number the model can reason with,
    where a raw "distinct sets" count cannot.

    Args:
        shares: Shares summing to ~1.

    Returns:
        The effective count, from 1.0 (fully settled) upward.
    """
    entropy = -sum(s * math.log(s) for s in shares if s > 0)
    return round(math.exp(entropy), 2)


def _band(share: float) -> str:
    if share >= LOCKED_SHARE:
        return "locked"
    if share < FRINGE_SHARE:
        return "fringe"
    return "contested"


def _slot_analysis(pairs: list[tuple[Any, float]], label: str) -> dict[str, Any]:
    """Split one decision (item / ability / nature) into the three bands.

    Args:
        pairs: (value, weight) observations for this decision.
        label: Field name, used in the returned payload.

    Returns:
        A dict with the ranked options and their band assignments.
    """
    ranked = _weighted_shares(pairs)
    return {
        "decision": label,
        "effective_options": _effective_variety([s for _, _, s in ranked]),
        "options": [
            {"value": v, "percent": _pct(s), "band": _band(s)}
            for v, _, s in ranked
        ],
    }


def _move_analysis(movesets: list[tuple[tuple[str, ...], float]]) -> dict[str, Any]:
    """Work out which move slots are settled and which are the player's to choose.

    Args:
        movesets: (sorted move tuple, weight) per observation.

    Returns:
        Locked/contested/fringe move lists plus how many slots are genuinely
        free once the locked moves are accounted for.
    """
    total = sum(w for _, w in movesets) or 1.0
    per_move: dict[str, float] = defaultdict(float)
    for moves, weight in movesets:
        for move in moves:
            per_move[move] += weight

    locked, contested, fringe = [], [], []
    for move, weight in sorted(per_move.items(), key=lambda kv: -kv[1]):
        share = weight / total
        entry = {"move": move, "percent": _pct(share)}
        (locked if share >= LOCKED_SHARE
         else fringe if share < FRINGE_SHARE
         else contested).append(entry)

    slot_count = max(len(m) for m, _ in movesets) if movesets else 4
    return {
        "locked_moves": locked,
        "contested_moves": contested,
        "fringe_moves": fringe,
        "free_slots": max(0, slot_count - len(locked)),
        "note": (
            f"{len(locked)} of {slot_count} move slots are effectively fixed; "
            f"the other {max(0, slot_count - len(locked))} are where real teams "
            f"differ. Fringe moves are rare but attested — precedent, not error."
        ),
    }


def _naive_trap(
    movesets: list[tuple[tuple[str, ...], float]], slot_count: int = 4,
) -> dict[str, Any] | None:
    """Measure how wrong the "four most popular moves" build actually is.

    This is the concrete cost of reading marginal usage as a set: take the
    top-N moves by independent popularity, then count how many *real* builds
    ran exactly that combination.

    Args:
        movesets: (sorted move tuple, weight) per observation.
        slot_count: Number of move slots.

    Returns:
        The naive set and the share of observations matching it, or None when
        there is nothing to compare against.
    """
    if not movesets:
        return None
    per_move: dict[str, float] = defaultdict(float)
    for moves, weight in movesets:
        for move in moves:
            per_move[move] += weight
    naive = tuple(sorted(m for m, _ in sorted(
        per_move.items(), key=lambda kv: -kv[1])[:slot_count]))
    total = sum(w for _, w in movesets) or 1.0
    hit = sum(w for moves, w in movesets if moves == naive)
    return {
        "naive_top_moves": list(naive),
        "actually_played_percent": _pct(hit / total),
        "note": (
            "Share of real builds running exactly the four most individually "
            "popular moves. When this is low, stacking marginal top-picks "
            "produces a set nobody plays."
        ),
    }


def _item_display(db: Db) -> dict[str, str]:
    """item slug -> English display name.

    The BSS corpus stores held items as the Japanese strings its upstream
    publishes (きあいのタスキ). Every one that resolved carries an ``item_slug``,
    so the English name the rest of the server uses is one lookup away —
    without it the same item would read differently per format.
    """
    return {r["slug"]: r["name"] for r in db.query("SELECT slug, name FROM items")}


def _item_of(row: dict[str, Any], display: dict[str, str]) -> str | None:
    """The English item name for a member row, falling back to the raw text."""
    return display.get(row["item_slug"] or "") or row["item"]


def _record(row: dict[str, Any]) -> str | None:
    if row.get("wins") is None:
        return None
    parts = [str(row["wins"]), str(row.get("losses") or 0)]
    if row.get("ties"):
        parts.append(str(row["ties"]))
    return "-".join(parts)


def _provenance(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"source": row["source"], "event": row["event"]}
    for key in ("player", "placing", "date", "regulation"):
        if row.get(key) is not None:
            out[key] = row[key]
    rec = _record(row)
    if rec:
        out["record"] = rec
    if row.get("rating") is not None:
        out["ladder_rating"] = row["rating"]
    return out


_THIN_CORPUS_SETS = 3  # fewer distinct real sets than this counts as "low"


def _with_historical_fallback(
    db: Db, out: dict[str, Any], slug: str, fmt: str,
) -> dict[str, Any]:
    """Attach Smogon-derived historical sets to a get_observed_sets payload
    that has little or no real evidence — the "defaulting pull" this exists
    for. Never overwrites a genuinely well-observed result.

    Args:
        db: Open Champions database handle.
        out: The response being built (mutated in place and returned).
        slug: Resolved Pokémon slug.
        fmt: Internal format key (``doubles``/``singles``).

    Returns:
        ``out``, with ``historical_sets`` (+ its usage note) attached if any
        exist.
    """
    sets = historical_sets._historical_sets_for(db, slug, fmt, 5)
    if sets:
        out["historical_sets"] = sets
        out["historical_sets_note"] = (
            "The real observed-team corpus is thin or empty here, so these "
            "cross-game Smogon/Showdown sets are included as a fallback — "
            "not Champions tournament data. " + historical_sets._NOTE
        )
    return out


def _no_corpus(db: Db, fmt: str, format_name: str) -> dict[str, Any]:
    return {
        "format": format_name,
        "observed_teams": 0,
        "error": (
            f"No observed-team corpus for {format_name}. Run `champions-mcp-pull` "
            f"to ingest it (Limitless tournament teamlists for VGC, the "
            f"champs.pokedb.tokyo ranked-ladder export for BSS). Until then use "
            f"get_pokemon_sets, and read it as marginal popularity, not as builds."
        ),
    }


def get_observed_sets(
    db: Db, name: str, format: str = "vgc", item: str | None = None,
    with_teammate: str | None = None, limit: int = 6,
) -> dict[str, Any]:
    """Complete builds this Pokémon was actually given, optionally conditioned.

    Args:
        db: Open Champions database handle.
        name: A Pokémon name or slug to resolve.
        format: A Champions format (``vgc``/``bss``).
        item: Restrict to builds holding this item.
        with_teammate: Restrict to teams also containing this Pokémon.
        limit: Maximum number of distinct sets to return.

    Returns:
        Observed sets with provenance, the locked/contested/fringe breakdown,
        and the marginal-stacking trap measurement.
    """
    fmt = game_type(format)
    slug = try_resolve(db, name)
    if not slug:
        return {"error": f"Could not resolve Pokémon {name!r}."}
    if not db.builds_available(fmt):
        return _with_historical_fallback(db, _no_corpus(db, fmt, format), slug, fmt)

    rows = db.observed_members(slug, fmt)
    if not rows:
        row = db.get_pokemon_row(slug)
        return _with_historical_fallback(db, {
            "pokemon": row["battle_name"], "format": format, "observations": 0,
            "note": (
                "This Pokémon does not appear in the observed-team corpus for "
                "this format. That is a statement about tournament/ladder "
                "sampling, not about viability — check get_pokemon_sets for "
                "ladder usage and judge the pick on its merits."
            ),
        }, slug, fmt)

    item_display = _item_display(db)

    # --- conditioning ------------------------------------------------- #
    conditions: dict[str, Any] = {}
    if item:
        # Match on the resolved slug so an English item name also selects the
        # Japanese-sourced BSS rows, then fall back to raw text for the
        # unresolved tail.
        want_slug, want_text = slugify(item), item.strip().lower()
        rows = [
            r for r in rows
            if (r["item_slug"] and r["item_slug"] == want_slug)
            or (r["item"] or "").lower() == want_text
        ]
        conditions["item"] = item_display.get(want_slug, item)
    if with_teammate:
        mate = try_resolve(db, with_teammate)
        if not mate:
            return {"error": f"Could not resolve teammate {with_teammate!r}."}
        rosters = db.observed_rosters(fmt)
        rows = [r for r in rows if mate in rosters.get(r["team_id"], ())]
        conditions["with_teammate"] = db.get_pokemon_row(mate)["battle_name"]
    if not rows:
        return {
            "pokemon": db.get_pokemon_row(slug)["battle_name"], "format": format,
            "conditions": conditions, "observations": 0,
            "note": "No observed build matches those conditions. That does not "
                    "make the combination bad — it makes it untested here.",
        }

    evidence = "full_set" if any(r["moves_json"] for r in rows) else "team_and_item"
    total_weight = sum(r["weight"] for r in rows) or 1.0

    # --- distinct joint sets ------------------------------------------ #
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = grouped.setdefault(r["set_hash"], {"rows": [], "weight": 0.0})
        entry["rows"].append(r)
        entry["weight"] += r["weight"]
    ordered = sorted(grouped.values(), key=lambda e: -e["weight"])

    rosters = db.observed_rosters(fmt)
    names = {
        p["slug"]: p["battle_name"]
        for p in db.query("SELECT slug, battle_name FROM pokemon")
    }

    def _set_payload(entry: dict[str, Any]) -> dict[str, Any]:
        sample = max(entry["rows"], key=lambda r: r["weight"])
        partners: dict[str, float] = defaultdict(float)
        for r in entry["rows"]:
            for other in rosters.get(r["team_id"], ()):
                if other != slug:
                    partners[other] += r["weight"]
        pw = sum(w for _, w in partners.items()) or 1.0
        payload: dict[str, Any] = {
            "item": _item_of(sample, item_display),
            "observations": len(entry["rows"]),
            "share_percent": _pct(entry["weight"] / total_weight),
            "common_partners": [
                {"name": names.get(s, s), "percent": _pct(w / pw)}
                for s, w in sorted(partners.items(), key=lambda kv: -kv[1])[:5]
            ],
            "seen_at": _provenance(sample),
        }
        if sample["moves_json"]:
            payload["ability"] = sample["ability"]
            payload["nature"] = sample["nature"]
            payload["moves"] = json.loads(sample["moves_json"])
        return payload

    main = [_set_payload(e) for e in ordered[:limit]]
    fringe_sets = [
        _set_payload(e) for e in ordered[limit:]
        if e["weight"] / total_weight >= RARE_SET_SHARE
    ][:3]

    out: dict[str, Any] = {
        "pokemon": db.get_pokemon_row(slug)["battle_name"],
        "slug": slug,
        "format": format,
        "evidence": evidence,
        "observations": len(rows),
        "distinct_sets": len(grouped),
        "effective_distinct_sets": _effective_variety(
            [e["weight"] / total_weight for e in ordered]),
        "sets": main,
    }
    if conditions:
        out["conditions"] = conditions
    if fringe_sets:
        out["fringe_sets"] = fringe_sets

    # --- where the disagreement lives --------------------------------- #
    decisions = [_slot_analysis(
        [(_item_of(r, item_display), r["weight"]) for r in rows if r["item"]], "item")]
    if evidence == "full_set":
        decisions.append(_slot_analysis(
            [(r["ability"], r["weight"]) for r in rows if r["ability"]], "ability"))
        decisions.append(_slot_analysis(
            [(r["nature"], r["weight"]) for r in rows if r["nature"]], "nature"))
        movesets = [
            (tuple(json.loads(r["moves_json"])), r["weight"])
            for r in rows if r["moves_json"]
        ]
        out["moves"] = _move_analysis(movesets)
        trap = _naive_trap(movesets)
        if trap:
            out["marginal_stacking_check"] = trap
    out["decisions"] = decisions

    if evidence == "team_and_item":
        out["evidence_note"] = (
            "BSS evidence is team-and-item only: the ranked-ladder export "
            "publishes each team's six Pokémon and their held items, but not "
            "moves, abilities, natures or Stat Points. Item and teammate "
            "correlations here are real; any moveset is your inference, so "
            "justify it with get_pokemon_moves + calc_damage rather than "
            "presenting it as observed."
        )

    out["how_to_use"] = (
        "These are builds real players brought, not a ranking to copy. `locked` "
        "choices are near-universal — deviate deliberately, not accidentally. "
        "`contested` slots are where strong players already disagree, so they "
        "are yours to decide from your team's needs. `fringe` entries are rare "
        "but attested: precedent for a sharp idea, not a mistake."
    )
    if len(grouped) < _THIN_CORPUS_SETS:
        out = _with_historical_fallback(db, out, slug, fmt)
    return out


def get_team_context(
    db: Db, team: list[str], format: str = "vgc", limit: int = 12,
) -> dict[str, Any]:
    """What real teams did next, given the Pokémon already on yours.

    Args:
        db: Open Champions database handle.
        team: Pokémon names/slugs already chosen.
        format: A Champions format (``vgc``/``bss``).
        limit: Maximum number of partner suggestions.

    Returns:
        Partners ranked by how often they joined this exact combination, split
        into well-trodden and under-explored, plus the matching sample teams.
    """
    fmt = game_type(format)
    if not db.builds_available(fmt):
        return _no_corpus(db, fmt, format)

    slugs, unresolved = [], []
    for entry in team:
        s = try_resolve(db, entry)
        (slugs.append(s) if s else unresolved.append(entry))
    if unresolved:
        return {"error": f"Could not resolve: {', '.join(unresolved)}."}
    if not slugs:
        return {"error": "Pass at least one Pokémon."}

    rosters = db.observed_rosters(fmt)
    weights = {
        r["team_id"]: r["weight"]
        for r in db.query(
            "SELECT team_id, weight FROM build_teams WHERE format = ?", (fmt,))
    }
    wanted = set(slugs)
    matching = [tid for tid, roster in rosters.items() if wanted <= set(roster)]
    baseline_total = sum(weights.get(t, 0.0) for t in rosters) or 1.0

    if not matching:
        return {
            "format": format, "core": [db.get_pokemon_row(s)["battle_name"] for s in slugs],
            "matching_teams": 0,
            "note": (
                "No observed team contains this exact combination. That is a "
                "genuinely untried core, not a refuted one — build it out with "
                "get_observed_sets on each member and validate with calc_damage. "
                "Drop a member to see what real teams did around the rest."
            ),
        }

    partners: dict[str, float] = defaultdict(float)
    for tid in matching:
        for slug in rosters[tid]:
            if slug not in wanted:
                partners[slug] += weights.get(tid, 0.0)
    matched_total = sum(weights.get(t, 0.0) for t in matching) or 1.0

    # How often each partner shows up in the format at large, so we can tell
    # "goes specifically with this core" apart from "is simply everywhere".
    overall: dict[str, float] = defaultdict(float)
    for tid, roster in rosters.items():
        for slug in roster:
            overall[slug] += weights.get(tid, 0.0)

    names = {
        p["slug"]: p["battle_name"]
        for p in db.query("SELECT slug, battle_name FROM pokemon")
    }
    ranked = []
    for slug, weight in sorted(partners.items(), key=lambda kv: -kv[1]):
        with_core = weight / matched_total
        at_large = (overall.get(slug, 0.0) / baseline_total) or 1e-9
        ranked.append({
            "name": names.get(slug, slug),
            "slug": slug,
            "with_this_core_percent": _pct(with_core),
            "format_wide_percent": _pct(at_large),
            # >1 means this partner is specifically drawn to your core rather
            # than just being a popular Pokémon.
            "affinity": round(with_core / at_large, 2),
        })

    conventional = ranked[:limit]
    # Real but uncommon answers to the same core: the interesting column.
    underexplored = sorted(
        (p for p in ranked[limit:] if p["affinity"] >= 1.5),
        key=lambda p: -p["affinity"],
    )[:6]

    return {
        "format": format,
        "core": [names.get(s, s) for s in slugs],
        "matching_teams": len(matching),
        "common_partners": conventional,
        "underexplored_partners": underexplored,
        "how_to_use": (
            "`with_this_core_percent` is how often a Pokémon joined this exact "
            "core; `affinity` divides that by its format-wide rate, so a high "
            "affinity means it is drawn to *your* core rather than merely "
            "popular. `underexplored_partners` are real but uncommon answers — "
            "the most promising place to differentiate. A low match count means "
            "weak evidence, not a bad core."
        ),
    }


def get_sample_teams(
    db: Db, format: str = "vgc", pokemon: str | None = None, limit: int = 3,
) -> dict[str, Any]:
    """Whole teams from the corpus, with provenance.

    Args:
        db: Open Champions database handle.
        format: A Champions format (``vgc``/``bss``).
        pokemon: Restrict to teams containing this Pokémon.
        limit: Maximum number of teams.

    Returns:
        Complete team lists with their event/record, deduplicated so the same
        list entered at several events appears once.
    """
    fmt = game_type(format)
    if not db.builds_available(fmt):
        return _no_corpus(db, fmt, format)

    slug = None
    if pokemon:
        slug = try_resolve(db, pokemon)
        if not slug:
            return {"error": f"Could not resolve Pokémon {pokemon!r}."}

    names = {
        p["slug"]: p["battle_name"]
        for p in db.query("SELECT slug, battle_name FROM pokemon")
    }
    item_display = _item_display(db)
    out = []
    for meta in db.observed_teams_for(fmt, slug, None, limit):
        members = []
        for m in db.observed_team(meta["team_id"]):
            entry: dict[str, Any] = {"name": names.get(m["slug"], m["slug"])}
            if m["item"]:
                entry["item"] = _item_of(m, item_display)
            if m["ability"]:
                entry["ability"] = m["ability"]
            if m["nature"]:
                entry["nature"] = m["nature"]
            if m["moves_json"]:
                entry["moves"] = json.loads(m["moves_json"])
            members.append(entry)
        out.append({
            **_provenance(meta),
            "evidence": meta["evidence"],
            "times_seen": meta.get("times_seen", 1),
            "team": members,
        })

    return {
        "format": format,
        "filter_pokemon": names.get(slug) if slug else None,
        "count": len(out),
        "teams": out,
        "how_to_use": (
            "Reference points, not templates. Read them for *structure* — how "
            "the speed control, the defensive backbone and the win condition "
            "fit together — then build your own around the matchups you expect. "
            "Copying a list also copies whatever it loses to."
        ),
    }
