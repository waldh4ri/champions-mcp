"""Pokémon Champions stat math.

Champions stat model (verified 2026-05 from Centro leak + community guides):
  - Every Pokémon is Level 50 with fixed 31 IVs in every stat.
  - "Stat Points" (SP) replace EVs: 66 total, max 32 per stat, integers >= 0.
  - 1 SP = +1 to the *pre-nature* stat at Level 50 (so SP is added directly,
    not divided by 4 like EVs).
  - Natures still apply the usual +10% / -10% to one non-HP stat.

Non-HP:  floor( ( (2*Base + 31)//2 + 5 + SP ) * nature )
HP:      (2*Base + 31)//2 + 60 + SP        (nature never affects HP)

Cross-check: Garchomp (base Spe 102) -> 0 SP neutral = 122; 32 SP +Spe = 169
(identical to a 252+ Jolly Garchomp in the old EV system).
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

SPEED_PLUS_NATURES = sorted(
    n for n, (p, _) in NATURES.items() if p == "speed"
)
SPEED_MINUS_NATURES = sorted(
    n for n, (_, m) in NATURES.items() if m == "speed"
)


class StatError(ValueError):
    pass


def normalize_nature(nature: str | None) -> str:
    if not nature:
        return "serious"
    key = nature.strip().lower()
    if key not in NATURES:
        raise StatError(
            f"Unknown nature {nature!r}. Valid: {sorted(NATURES)}"
        )
    return key


def nature_effect(nature: str | None, stat: str) -> str:
    """Return '+', '-' or '=' for how `nature` affects `stat`."""
    plus, minus = NATURES[normalize_nature(nature)]
    if stat == plus:
        return "+"
    if stat == minus:
        return "-"
    return "="


def compute_stat(base: int, sp: int, stat: str, nature: str | None) -> int:
    """Final Champions stat value at Level 50.

    `stat` is one of ALL_STATS. `sp` is the Stat Points invested in it.
    """
    if sp < 0 or sp > MAX_PER_STAT_SP:
        raise StatError(f"SP for {stat} must be 0..{MAX_PER_STAT_SP}, got {sp}")
    floor_half = (2 * base + FIXED_IV) // 2
    if stat == "hp":
        if base == 1:  # Shedinja-style
            return 1
        return floor_half + 60 + sp
    pre = floor_half + 5 + sp
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
    """Validate a Champions SP spread: integer 0..32 per stat, total <= 66."""
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
    tailwind: bool = False,
    paralyzed: bool = False,
) -> int:
    """In-battle Speed: raw stat -> stage -> Scarf -> Tailwind -> paralysis.

    Each multiplier floors, matching modern-gen mechanics.
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

    Returns one result per requested nature (default: neutral + a +Spe nature),
    so the model can choose the cheapest investment / decide if +Spe is needed.
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
