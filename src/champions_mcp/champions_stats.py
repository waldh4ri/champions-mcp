"""Pokémon Champions stat math.

Champions stat model (verified 2026-05 from Centro leak + community guides):
  - Every Pokémon is Level 50 with fixed 31 IVs in every stat.
  - "Stat Points" (SP) replace EVs: 66 total, max 32 per stat, integers >= 0.
  - 1 SP = +1 to the *pre-nature* stat at Level 50 (so SP is added directly,
    not divided by 4 like EVs).
  - Natures still apply the usual +10% / -10% to one non-HP stat.

The listed stats in this data are already the Level-50 values at 0 SP (31 IVs),
so the level formula is NOT applied again:

Non-HP:  floor( (Listed + SP) * nature )
HP:      Listed + SP                       (nature never affects HP)

Cross-check: Garchomp (listed Spe 122, i.e. classic base 102) -> 0 SP neutral =
122; 32 SP +Spe = 169 (identical to a 252+ Jolly Garchomp in the old EV system),
and identical to what calc_damage uses.
"""

from __future__ import annotations

from dataclasses import dataclass

LEVEL = 50
FIXED_IV = 31
MAX_TOTAL_SP = 66
MAX_PER_STAT_SP = 32

NON_HP_STATS = ("attack", "defense", "special-attack", "special-defense", "speed")
ALL_STATS = ("hp", *NON_HP_STATS)

# nature -> (boosted_stat, lowered_stat); neutral natures map to (None, None)
NATURES: dict[str, tuple[str | None, str | None]] = {
    "hardy": (None, None),
    "lonely": ("attack", "defense"),
    "brave": ("attack", "speed"),
    "adamant": ("attack", "special-attack"),
    "naughty": ("attack", "special-defense"),
    "bold": ("defense", "attack"),
    "docile": (None, None),
    "relaxed": ("defense", "speed"),
    "impish": ("defense", "special-attack"),
    "lax": ("defense", "special-defense"),
    "timid": ("speed", "attack"),
    "hasty": ("speed", "defense"),
    "serious": (None, None),
    "jolly": ("speed", "special-attack"),
    "naive": ("speed", "special-defense"),
    "modest": ("special-attack", "attack"),
    "mild": ("special-attack", "defense"),
    "quiet": ("special-attack", "speed"),
    "bashful": (None, None),
    "rash": ("special-attack", "special-defense"),
    "calm": ("special-defense", "attack"),
    "gentle": ("special-defense", "defense"),
    "sassy": ("special-defense", "speed"),
    "careful": ("special-defense", "special-attack"),
    "quirky": (None, None),
}

_STAT_ALIASES: dict[str, str] = {
    "hp": "hp", "health": "hp",
    "atk": "attack", "attack": "attack",
    "def": "defense", "defense": "defense", "defence": "defense",
    "spa": "special-attack", "sp_atk": "special-attack", "sp-atk": "special-attack",
    "spatk": "special-attack", "special-attack": "special-attack",
    "special_attack": "special-attack", "sp. atk": "special-attack",
    "spd": "special-defense", "sp_def": "special-defense", "sp-def": "special-defense",
    "spdef": "special-defense", "special-defense": "special-defense",
    "special_defense": "special-defense", "sp. def": "special-defense",
    "spe": "speed", "speed": "speed",
}


def canonical_stat(stat: str) -> str | None:
    """Map an arbitrary stat key ('spa', 'sp_atk', 'Speed') to ALL_STATS form."""
    return _STAT_ALIASES.get(str(stat).strip().lower())


def normalize_spread(spread: dict[str, int]) -> dict[str, int]:
    """Canonicalize a spread's keys; unknown keys are kept so validation flags them."""
    out: dict[str, int] = {}
    for k, v in (spread or {}).items():
        out[canonical_stat(k) or k] = v
    return out


# Champions calls them Stat Points, but "EVs" is the reflex from every other
# Pokémon game, so a team member may arrive with either key. Accept both — while
# still validating the VALUES as Stat Points, because the scales differ (SP: 66
# total, 32 per stat, 1 SP = +1; EVs: 510 total, 252 per stat, 4 EVs = +1). An
# EV-scale spread is therefore not silently converted; it fails validation with
# an explanation, which is the honest outcome.
_SPREAD_KEYS = ("stat_points", "evs", "ev", "sp", "spread")


def member_stat_points(member: dict) -> dict[str, int]:
    """Read a team member's spread under any of the accepted keys."""
    for key in _SPREAD_KEYS:
        value = (member or {}).get(key)
        if value:
            return normalize_spread(value)
    return {}


def looks_like_evs(spread: dict[str, int]) -> bool:
    """Does this spread look like it was written on the EV scale (0-252)?

    Used to turn a confusing "over budget" error into one that names the real
    mistake. Any single value above the 32-per-stat cap is impossible as SP.
    """
    try:
        values = [int(v) for v in (spread or {}).values()]
    except (TypeError, ValueError):
        return False
    return any(v > MAX_PER_STAT_SP for v in values)


SPEED_PLUS_NATURES = sorted(
    n for n, (p, _) in NATURES.items() if p == "speed"
)
SPEED_MINUS_NATURES = sorted(
    n for n, (_, m) in NATURES.items() if m == "speed"
)


class StatError(ValueError):
    """Raised for an invalid nature or an out-of-range/malformed SP spread."""


def normalize_nature(nature: str | None) -> str:
    """Lowercase and validate a nature name, defaulting to neutral.

    Args:
        nature: A nature name, or None/empty for the default.

    Returns:
        The lowercased nature key.

    Raises:
        StatError: `nature` is non-empty but not a recognized nature.
    """
    if not nature:
        return "serious"
    key = nature.strip().lower()
    if key not in NATURES:
        raise StatError(
            f"Unknown nature {nature!r}. Valid: {sorted(NATURES)}"
        )
    return key


def nature_effect(nature: str | None, stat: str) -> str:
    """Whether `nature` boosts, lowers, or leaves `stat` unaffected.

    Args:
        nature: A nature name (validated via `normalize_nature`).
        stat: One of `ALL_STATS`.

    Returns:
        ``"+"`` if boosted, ``"-"`` if lowered, ``"="`` otherwise.
    """
    plus, minus = NATURES[normalize_nature(nature)]
    if stat == plus:
        return "+"
    if stat == minus:
        return "-"
    return "="


def compute_stat(base: int, sp: int, stat: str, nature: str | None) -> int:
    """Final Champions stat value at Level 50.

    The "base stats" in this data (pokechamp.gg) are already the **Level-50
    stats at 0 SP** with the fixed 31 IVs — Garchomp's 183/150/115/100/105/122
    is exactly its classic base stats run through the Lv-50 formula. So the
    level formula must not be applied again: 1 SP adds +1 to that value, then
    the nature multiplies non-HP stats. (Applying the formula a second time
    inflated every stat — Jolly 32-SP Garchomp read 191 Speed instead of 169 —
    and disagreed with calc_damage, which uses the right numbers.)

    Args:
        base: The Pokémon's listed stat — its Lv-50 value at 0 SP.
        sp: Stat Points invested in this stat (0..`MAX_PER_STAT_SP`).
        stat: One of `ALL_STATS`.
        nature: A nature name, or None for neutral.

    Returns:
        The final stat value.

    Raises:
        StatError: `sp` is outside 0..`MAX_PER_STAT_SP`.
    """
    if sp < 0 or sp > MAX_PER_STAT_SP:
        raise StatError(f"SP for {stat} must be 0..{MAX_PER_STAT_SP}, got {sp}")
    if stat == "hp":
        if base == 1:  # Shedinja-style
            return 1
        return base + sp
    pre = base + sp
    effect = nature_effect(nature, stat)
    if effect == "+":
        return pre * 11 // 10
    if effect == "-":
        return pre * 9 // 10
    return pre


@dataclass(frozen=True)
class SpreadCheck:
    ok: bool
    total: int
    violations: list[str]


def validate_spread(spread: dict[str, int]) -> SpreadCheck:
    """Validate a Champions SP spread: integer 0..32 per stat, total <= 66.

    Args:
        spread: Mapping of stat name to Stat Points invested.

    Returns:
        A `SpreadCheck` with the total SP spent and any violation messages.
    """
    violations: list[str] = []
    total = 0
    for stat, val in spread.items():
        if stat not in ALL_STATS:
            violations.append(
                f"Unknown stat {stat!r} (use {', '.join(ALL_STATS)})."
            )
            continue
        if not isinstance(val, int) or isinstance(val, bool):
            violations.append(f"{stat}: SP must be an integer, got {val!r}.")
            continue
        if val < 0:
            violations.append(f"{stat}: SP cannot be negative ({val}).")
        if val > MAX_PER_STAT_SP:
            violations.append(
                f"{stat}: {val} SP exceeds the {MAX_PER_STAT_SP} per-stat cap."
            )
        total += max(val, 0)
    if total > MAX_TOTAL_SP:
        violations.append(
            f"Total {total} SP exceeds the {MAX_TOTAL_SP} budget "
            f"(over by {total - MAX_TOTAL_SP})."
        )
    return SpreadCheck(ok=not violations, total=total, violations=violations)


def effective_speed(
    base: int,
    sp: int,
    nature: str | None,
    *,
    stage: int = 0,
    choice_scarf: bool = False,
    speed_ability: bool = False,
    tailwind: bool = False,
    paralyzed: bool = False,
) -> int:
    """In-battle Speed: raw stat -> stage -> Scarf -> Speed-doubling ability ->
    Tailwind -> paralysis.

    Each multiplier floors, matching modern-gen mechanics.

    Args:
        base: Base Speed stat.
        sp: Speed Stat Points invested.
        nature: A nature name, or None for neutral.
        stage: Speed stat stage, clamped to -6..6.
        choice_scarf: Whether to apply the Choice Scarf ×1.5 multiplier.
        speed_ability: Whether a Speed-doubling ability is active (Swift Swim
            in rain, Chlorophyll in sun, Sand Rush in sand, Slush Rush in snow,
            Surge Surfer in Electric Terrain): ×2.
        tailwind: Whether to apply the Tailwind ×2 multiplier.
        paralyzed: Whether to apply the paralysis ×0.5 multiplier.

    Returns:
        The final in-battle Speed value.
    """
    spd = compute_stat(base, sp, "speed", nature)
    if stage:
        stage = max(-6, min(6, stage))
        if stage >= 0:
            spd = spd * (2 + stage) // 2
        else:
            spd = spd * 2 // (2 - stage)
    if choice_scarf:
        spd = spd * 3 // 2
    if speed_ability:
        spd = spd * 2
    if tailwind:
        spd = spd * 2
    if paralyzed:
        spd = spd // 2
    return spd


@dataclass(frozen=True)
class ThresholdResult:
    nature: str
    min_sp: int | None  # None => impossible even at 32 SP
    resulting_speed: int | None
    sp_remaining_budget: int | None  # 66 - min_sp


def min_sp_to_outspeed(
    attacker_base: int,
    target_speed: int,
    *,
    natures: tuple[str, ...] = ("serious", "jolly"),
    tie_is_enough: bool = False,
    attacker_mods: dict | None = None,
) -> list[ThresholdResult]:
    """Minimum Speed SP for the attacker to beat `target_speed`.

    Args:
        attacker_base: Attacker's base Speed stat.
        target_speed: The effective Speed value to beat.
        natures: Natures to try; one result is returned per nature (default:
            neutral + a +Spe nature) so the caller can choose the cheapest
            investment or decide if a +Spe nature is needed.
        tie_is_enough: If True, an exact tie with `target_speed` counts as a win.
        attacker_mods: Extra keyword args forwarded to `effective_speed` (stage,
            choice_scarf, speed_ability, tailwind, paralyzed).

    Returns:
        One `ThresholdResult` per requested nature.
    """
    mods = attacker_mods or {}
    results: list[ThresholdResult] = []
    for nat in natures:
        nat_key = normalize_nature(nat)
        found: int | None = None
        for sp in range(0, MAX_PER_STAT_SP + 1):
            spd = effective_speed(attacker_base, sp, nat_key, **mods)
            if spd > target_speed or (tie_is_enough and spd == target_speed):
                found = sp
                break
        if found is None:
            results.append(ThresholdResult(nat_key, None, None, None))
        else:
            results.append(
                ThresholdResult(
                    nature=nat_key,
                    min_sp=found,
                    resulting_speed=effective_speed(
                        attacker_base, found, nat_key, **mods
                    ),
                    sp_remaining_budget=MAX_TOTAL_SP - found,
                )
            )
    return results
