"""Competitive role vocabulary (curated reference, not source data).

Every other module in champions-mcp reads the championsbattledata mirror. This
one does not: roles are *analytical* concepts from the wider competitive scene
(Smogon's Pokémon Dictionary and the OU Role Compendiums for singles, the VGC
teambuilding-framework literature for doubles), not a field in the data source.
They give a model a shared vocabulary for "what is this slot on the team *for*".

Two things keep the catalogue honest rather than hand-wavy:

* **Every role is scored per format.** Champions has two: VGC (doubles, bring 4)
  and BSS (singles, bring 3). A role that defines doubles can be dead weight in
  singles — Follow Me literally does nothing in a 1v1 — and vice versa. Each
  entry therefore carries a viability grade *and* a note for each format, so the
  model never carries singles instincts into a doubles team.
* **The move/item/ability signatures are checked against the DB at lookup time**
  (:func:`get_role_description`). Champions rebalanced movepools and the item
  pool, so a signature listed here is only reported as usable once the mirror
  confirms it exists and that some Champions Pokémon actually learns it.
"""

from __future__ import annotations

from typing import Any

from .db import Db
from .formats import resolve_format
from .normalize import slugify

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

VIABILITY = {
    "core": "A slot most good teams in this format want; build around it.",
    "common": "Frequently worth a slot; a normal, well-understood option.",
    "situational": "Viable but needs a specific gameplan or matchup to earn its slot.",
    "unviable": "Does not work in this format — the mechanic is absent, or the "
                "format is too short/too crowded for it to pay off.",
}

CATEGORIES = {
    "offense": "Slots whose job is to remove opposing Pokémon.",
    "defense": "Slots whose job is to absorb damage and deny the opponent progress.",
    "pivot": "Slots that trade positioning and momentum rather than raw damage.",
    "speed_control": "Slots that decide who moves first — the central axis of doubles.",
    "support": "Slots that make a teammate's job possible.",
    "hazards": "Slots that install or clear chip damage on switch-in.",
    "lead": "Slots defined by what they do on turn one.",
}

_VGC = "vgc"
_BSS = "bss"


def _f(vgc: tuple[str, str], bss: tuple[str, str]) -> dict[str, dict[str, str]]:
    """Build the per-format block from (viability, note) pairs."""
    return {
        _VGC: {"viability": vgc[0], "note": vgc[1]},
        _BSS: {"viability": bss[0], "note": bss[1]},
    }


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #

_ROLES: list[dict[str, Any]] = [
    # ----------------------------- Offense -------------------------------- #
    {
        "id": "physical-wallbreaker",
        "name": "Physical Wallbreaker",
        "category": "offense",
        "aliases": ["wallbreaker", "physical breaker", "breaker", "physical attacker"],
        "summary": "Immediate physical power meant to punch holes through bulky "
                   "Pokémon, not to sweep.",
        "description": (
            "A wallbreaker's value is damage on the turn it clicks a move — no "
            "setup, no boosting turn. It exists because a defensive core will "
            "otherwise wall everything else you own: you send it in, force a "
            "switch or take a KO, and something else finishes the job later. It "
            "does not need to outspeed anything (it usually doesn't), and it "
            "does not need to survive; it needs to make the opponent's walls "
            "stop being walls. Distinguish it from a sweeper: a sweeper wins the "
            "game itself, a wallbreaker makes the game winnable. Champions note: "
            "there is no Choice Band here — Choice Scarf is the only Choice item "
            "in the game — so breakers rely on Life Orb, Muscle Band, Expert Belt "
            "or a type-enhancement item rather than the locked 1.5× boost other "
            "formats assume."
        ),
        "formats": _f(
            vgc=("core", "Doubles is a short format — you rarely get more than "
                 "4-6 turns of real agency, so damage that lands immediately is "
                 "worth more than damage that needs a setup turn. Pair it with "
                 "redirection or Fake Out so it gets its click off."),
            bss=("core", "Bring-3 singles has no room for a long grind; the "
                 "breaker's job is to remove the one defensive Pokémon that "
                 "would otherwise stonewall your win condition."),
        ),
        "look_for": {
            "stats": "High base Attack; bulk and Speed are both secondary.",
            "typing": "STAB that hits the format's common walls neutrally or better.",
            "speed": "Usually below the fast tier — accept it and invest the SP "
                     "in Attack/bulk instead of chasing a Speed tier it can't reach.",
        },
        "signature_moves": [
            "Close Combat", "Knock Off", "Earthquake", "Flare Blitz",
            "Wood Hammer", "Play Rough", "Double-Edge", "Outrage",
        ],
        "signature_items": ["Life Orb", "Muscle Band", "Expert Belt", "Black Belt",
                            "Choice Scarf"],
        "signature_abilities": ["Adaptability", "Sheer Force", "Tough Claws", "Guts"],
        "sp_guidance": "Max Attack (32 SP) is close to mandatory. Spend what's "
                       "left on the bulk that lets it live one relevant hit, "
                       "rather than on a Speed tier it will miss anyway.",
        "pairs_with": ["redirection-support", "fake-out-user", "offensive-pivot",
                       "trick-room-setter"],
        "answered_by": ["physical-wall", "intimidate-support", "defensive-pivot",
                        "priority-attacker"],
        "find_candidates": [
            'get_usage_stats(format=…) then get_pokemon(name) — look for base Attack ≥ 120',
            'get_pokemon_sets(name, format=…) — check which item the meta actually runs',
            'search_items(query="Band")',
        ],
    },
    {
        "id": "special-wallbreaker",
        "name": "Special Wallbreaker",
        "category": "offense",
        "aliases": ["special breaker", "special attacker", "specs user"],
        "summary": "The special-side wallbreaker: immediate special power aimed at "
                   "the opponent's specially bulky Pokémon.",
        "description": (
            "Same logic as the physical wallbreaker, aimed at the other defensive "
            "stat. It matters as a separate role because defensive Pokémon are "
            "rarely bulky on both sides: a team's designated physical wall often "
            "folds to a strong special hit and vice versa. Champions note: there "
            "is no Assault Vest in this game, so a special wall has to buy its "
            "Special Defense with Stat Points and typing rather than with an "
            "item — which makes the 66-SP cap the real constraint on the role."
        ),
        "formats": _f(
            vgc=("core", "Also the natural home for spread special moves, which "
                 "makes it double as a spread attacker — see that role."),
            bss=("core", "Often the cleanest answer to physically defensive walls "
                 "that would otherwise blank your physical attackers."),
        ),
        "look_for": {
            "stats": "High base Special Attack.",
            "typing": "Coverage that is not walled by the format's most-used special sponge.",
            "speed": "Same as physical — Speed is a bonus, not the point.",
        },
        "signature_moves": [
            "Overheat", "Draco Meteor", "Hydro Pump", "Leaf Storm", "Thunderbolt",
            "Moonblast", "Sludge Bomb", "Focus Blast",
        ],
        "signature_items": ["Life Orb", "Wise Glasses", "Expert Belt", "Charcoal",
                            "Choice Scarf"],
        "signature_abilities": ["Adaptability", "Protean", "Solar Power", "Drought"],
        "sp_guidance": "Max Special Attack (32 SP). The remaining 34 SP usually "
                       "buy either a single Speed benchmark or enough HP/Def to "
                       "survive the format's common priority.",
        "pairs_with": ["redirection-support", "physical-wallbreaker", "weather-setter"],
        "answered_by": ["special-wall", "defensive-pivot", "priority-attacker"],
        "find_candidates": [
            'search_moves(category="special", type=…) for the coverage you need',
            'get_pokemon(name) — base Special Attack ≥ 120',
        ],
    },
    {
        "id": "mixed-attacker",
        "name": "Mixed Attacker",
        "category": "offense",
        "aliases": ["mixed sweeper", "mixed wallbreaker"],
        "summary": "Attacks off both Attack and Special Attack so no single "
                   "defensive stat can wall it.",
        "description": (
            "A mixed attacker splits its offence across both categories, "
            "specifically to beat the Pokémon that would hard-wall a one-sided "
            "attacker — the physically defensive wall eats a special hit, the "
            "specially defensive wall eats a physical one. The cost is real: "
            "you are splitting a finite stat budget across two offensive stats, "
            "so each hit is weaker than a dedicated breaker's. It earns its slot "
            "when a specific wall is the reason your team stalls out."
        ),
        "formats": _f(
            vgc=("situational", "The 66-SP budget punishes splitting harder in "
                 "doubles, where you also want bulk to survive two attackers. "
                 "Usually only worth it to break one specific problem Pokémon."),
            bss=("common", "A classic answer to defensive cores in singles: one "
                 "coverage move on the 'wrong' side turns a hard wall into a "
                 "2HKO."),
        ),
        "look_for": {
            "stats": "Both offensive stats respectable (neither dump-stat).",
            "typing": "Ideally STAB on one side and coverage on the other.",
            "speed": "Any — mixed attackers are usually breakers, not sweepers.",
        },
        "signature_moves": ["Draco Meteor", "Fire Blast", "Superpower", "Earthquake",
                            "Flip Turn"],
        "signature_items": ["Life Orb", "Expert Belt"],
        "signature_abilities": ["Protean", "Adaptability", "Mold Breaker"],
        "sp_guidance": "Rarely worth 32/32 on both offences — that is the whole "
                       "budget. Standard shape: max the primary stat, then buy "
                       "only enough of the secondary to hit the one benchmark "
                       "(confirm with calc_damage) that justifies going mixed.",
        "pairs_with": ["offensive-pivot", "disruptor"],
        "answered_by": ["tank", "priority-attacker", "revenge-killer"],
        "find_candidates": [
            'get_pokemon(name) — both offensive base stats ≥ 90',
            'get_pokemon_moves(name) — needs real moves on both sides',
        ],
    },
    {
        "id": "physical-setup-sweeper",
        "name": "Physical Setup Sweeper",
        "category": "offense",
        "aliases": ["setup sweeper", "physical sweeper", "swords dance user",
                    "dragon dance user", "win condition"],
        "summary": "Spends a turn boosting, then tries to KO the rest of the "
                   "opposing team by itself.",
        "description": (
            "The setup sweeper is a win condition: it invests one turn (Swords "
            "Dance, Dragon Dance, Bulk Up…) and converts that turn into a "
            "game-ending run. Two conditions must both hold before it goes: its "
            "checks must be gone or unable to switch in, and it must outspeed "
            "what is left — either natively, via its own boost, or via speed "
            "control. Setup that does not boost Speed (Swords Dance) needs a "
            "Pokémon that is already fast or a Tailwind/Trick Room mode behind "
            "it; setup that boosts both (Dragon Dance, Shell Smash) is more "
            "self-sufficient and correspondingly rarer."
        ),
        "formats": _f(
            vgc=("situational", "Doubles gives the opponent two Pokémon to "
                 "interrupt you — Fake Out, a spread move, Taunt, or simply "
                 "double-targeting. Setup only works behind redirection "
                 "(Follow Me/Rage Powder), a Fake Out of your own, or Protect "
                 "plus Tailwind. When it works it wins on the spot; treat it as "
                 "a plan you build the other five slots around."),
            bss=("core", "The standard singles win condition. Bring-3 means you "
                 "often see the opponent's full team at preview, so you can "
                 "judge before the match whether the sweep is live."),
        ),
        "look_for": {
            "stats": "High Attack and usable Speed; enough bulk to survive the setup turn.",
            "typing": "Few weaknesses to common priority; resistances that grant free setup turns.",
            "speed": "Either already in the top tier, or boosting Speed as part of setup.",
        },
        "signature_moves": [
            "Swords Dance", "Dragon Dance", "Bulk Up", "Belly Drum",
            "Shell Smash", "Curse", "Agility",
        ],
        "signature_items": ["Lum Berry", "Leftovers", "Life Orb", "Focus Sash",
                            "Sitrus Berry"],
        "signature_abilities": ["Moxie", "Intimidate", "Speed Boost", "Unburden"],
        "sp_guidance": "Attack is often the stat to *under*-invest: the boost "
                       "already doubles it. Buying the Speed tier that lets you "
                       "move first after one boost, plus enough HP/Def to "
                       "guarantee the setup turn, usually wins more games than "
                       "the last few points of Attack. Verify with calc_damage.",
        "pairs_with": ["redirection-support", "tailwind-setter", "fake-out-user",
                       "physical-wallbreaker", "screens-setter"],
        "answered_by": ["priority-attacker", "revenge-killer", "phazer",
                        "disruptor", "intimidate-support"],
        "find_candidates": [
            'pokemons_by_move("Swords Dance") / pokemons_by_move("Dragon Dance")',
            'get_pokemon_sets(name, format=…) — confirm the setup move is actually used',
        ],
    },
    {
        "id": "special-setup-sweeper",
        "name": "Special Setup Sweeper",
        "category": "offense",
        "aliases": ["nasty plot user", "calm mind user", "special sweeper",
                    "tail glow user"],
        "summary": "The special-side setup sweeper: boosts Special Attack, then "
                   "runs through the opposing team.",
        "description": (
            "Mechanically identical to the physical setup sweeper with two "
            "practical differences. First, common priority moves are mostly "
            "physical, so a special sweeper is somewhat less exposed to being "
            "revenge-killed by chip priority. Second, Calm Mind-style setup "
            "raises Special Defense as well, which lets a bulky special sweeper "
            "win one-on-one against special attackers outright — a slower, "
            "grindier win condition than a Swords Dance sweep."
        ),
        "formats": _f(
            vgc=("situational", "Same interruption problem as the physical "
                 "version — needs redirection, Protect, or Trick Room to buy "
                 "the turn. Calm Mind variants hold up better because the "
                 "Special Defense boost helps against double-targeting."),
            bss=("core", "Calm Mind and Nasty Plot are among the most reliable "
                 "singles win conditions, especially with recovery."),
        ),
        "look_for": {
            "stats": "High Special Attack; Calm Mind users also want real HP.",
            "typing": "Resistances that create free turns to click the boost.",
            "speed": "Fast already, or planning to use Trick Room / Tailwind.",
        },
        "signature_moves": ["Nasty Plot", "Calm Mind", "Quiver Dance",
                            "Growth", "Agility"],
        "signature_items": ["Leftovers", "Life Orb", "Lum Berry", "Focus Sash"],
        "signature_abilities": ["Competitive", "Magic Guard", "Speed Boost"],
        "sp_guidance": "As with the physical version, the boost multiplies "
                       "Special Attack — so bulk and Speed SP often out-earn "
                       "raw offence SP. Calm Mind sets lean HP-heavy.",
        "pairs_with": ["redirection-support", "screens-setter", "tailwind-setter",
                       "cleric"],
        "answered_by": ["special-wall", "disruptor", "priority-attacker", "phazer"],
        "find_candidates": [
            'pokemons_by_move("Nasty Plot") / pokemons_by_move("Calm Mind")',
            'get_speed_tiers(format=…) — check whether it outspeeds after boosting',
        ],
    },
    {
        "id": "cleaner",
        "name": "Cleaner",
        "category": "offense",
        "aliases": ["late-game cleaner", "sweeper"],
        "summary": "Fast attacker held back until the opposing team is weakened, "
                   "then sent in to KO everything left.",
        "description": (
            "A cleaner does not break anything — it finishes. Its job starts "
            "after the wallbreakers have done theirs and the opponent's team is "
            "sitting at half health: it is fast enough to outspeed what remains "
            "and strong enough to KO weakened targets, so it takes the last two "
            "or three Pokémon in a row. The skill is in *not* bringing it out "
            "early; a cleaner sent in against a healthy team is just a fragile "
            "attacker that trades one-for-one."
        ),
        "formats": _f(
            vgc=("situational", "Bring-4 doubles is short enough that a "
                 "dedicated 'late game' often never arrives. The idea survives "
                 "mostly as a fast attacker held in the back two."),
            bss=("common", "Fits bring-3 singles well: break with the first two, "
                 "clean with the third."),
        ),
        "look_for": {
            "stats": "High Speed with enough offence to KO weakened targets.",
            "typing": "Broad, hard-to-resist STAB coverage.",
            "speed": "Top of the format's Speed tiers — this is the defining stat.",
        },
        "signature_moves": ["U-turn", "Extreme Speed", "Aqua Jet", "Triple Axel"],
        "signature_items": ["Choice Scarf", "Life Orb", "Focus Sash"],
        "signature_abilities": ["Moxie", "Speed Boost", "Chlorophyll", "Swift Swim"],
        "sp_guidance": "Speed first — cleaning is a Speed-tier job. Use "
                       "get_speed_tiers and speed_threshold to buy the exact "
                       "tier, then dump the remainder into the offensive stat.",
        "pairs_with": ["physical-wallbreaker", "special-wallbreaker", "hazard-setter"],
        "answered_by": ["priority-attacker", "tank", "defensive-pivot"],
        "find_candidates": [
            'get_speed_tiers(format=…) — pick from the top of the list',
            'get_usage_stats(format=…) — see which fast attackers are actually played',
        ],
    },
    {
        "id": "revenge-killer",
        "name": "Revenge Killer",
        "category": "offense",
        "aliases": ["scarfer", "choice scarf user", "revenge kill"],
        "summary": "Comes in after a teammate faints to immediately KO the "
                   "Pokémon that just got the kill — usually an enemy sweeper.",
        "description": (
            "A revenge killer is insurance against setup. When the opponent's "
            "sweeper has boosted and taken one of your Pokémon down, the revenge "
            "killer switches in on the free turn and outspeeds and KOes it "
            "before it can continue. Speed is non-negotiable — normally bought "
            "with a Choice Scarf — and the trade-off is a locked move and no "
            "switching flexibility. Priority attackers do the same job from a "
            "different angle: they revenge-kill regardless of Speed, at the "
            "cost of much lower damage."
        ),
        "formats": _f(
            vgc=("situational", "Doubles has no 'free switch after a faint' — "
                 "the replacement comes in at end of turn and is exposed "
                 "immediately. The role is mostly absorbed by speed control "
                 "(Tailwind, Icy Wind) and priority instead."),
            bss=("core", "Singles gives you a genuinely free switch-in when a "
                 "Pokémon faints, which is exactly the turn this role is built "
                 "for. Nearly every balanced BSS team wants one."),
        ),
        "look_for": {
            "stats": "High Speed and enough Attack/Special Attack to OHKO a "
                     "weakened or frail sweeper.",
            "typing": "Resists or is immune to common boosted STAB.",
            "speed": "Must outspeed the format's boosted sweepers — Scarf tier.",
        },
        "signature_moves": ["U-turn", "Close Combat", "Trick", "Ice Shard"],
        "signature_items": ["Choice Scarf"],
        "signature_abilities": ["Intimidate", "Moxie"],
        "sp_guidance": "Compute the target with speed_threshold using "
                       "attacker_choice_scarf=True — a Scarf multiplies the "
                       "final stat by 1.5, so far fewer Speed SP are needed than "
                       "an unboosted attacker would want. Bank the difference "
                       "into the attacking stat.",
        "pairs_with": ["defensive-pivot", "physical-wall", "hazard-setter"],
        "answered_by": ["bulky-attacker", "priority-attacker", "trick-room-setter"],
        "find_candidates": [
            'speed_threshold(attacker=…, defender=…, attacker_choice_scarf=True)',
            'get_pokemon_sets(name, format="bss") — check Choice Scarf usage %',
        ],
    },
    {
        "id": "priority-attacker",
        "name": "Priority Attacker",
        "category": "offense",
        "aliases": ["priority user", "priority", "sucker punch user", "prankster user"],
        "summary": "Moves before the opponent regardless of Speed, to finish off "
                   "weakened threats or break a Focus Sash.",
        "description": (
            "Priority ignores the Speed stat entirely, which makes it the answer "
            "to everything that would otherwise outspeed you: boosted sweepers, "
            "Tailwind teams, faster attackers you cannot match. The damage is "
            "low — that is the price — so a priority attacker's real job is "
            "finishing something already in KO range, or removing a Focus Sash / "
            "Sturdy holder before it gets a turn. Status priority (Prankster "
            "Tailwind, Prankster Taunt) is the same mechanic used for support "
            "rather than damage, and is arguably more valuable."
        ),
        "formats": _f(
            vgc=("core", "Doubles rewards priority twice over: it beats Tailwind "
                 "and Trick Room speed swings, it picks off the second target "
                 "after a spread move, and Prankster status priority (Tailwind, "
                 "Taunt, Encore) is one of the format's defining support tools. "
                 "Note Fake Out is +3 priority — see the Fake Out role."),
            bss=("common", "Chiefly used to revenge-kill boosted sweepers that "
                 "have outrun the rest of your team."),
        ),
        "look_for": {
            "stats": "Enough Attack to make a low-power priority move actually KO.",
            "typing": "STAB on the priority move — unboosted priority rarely KOes otherwise.",
            "speed": "Irrelevant, except to break priority ties at the same bracket.",
        },
        "signature_moves": [
            "Extreme Speed", "Sucker Punch", "Aqua Jet", "Bullet Punch",
            "Ice Shard", "Shadow Sneak", "Grassy Glide", "Quick Attack",
            "First Impression", "Vacuum Wave",
        ],
        "signature_items": ["Life Orb", "Muscle Band", "Sitrus Berry", "Silk Scarf"],
        "signature_abilities": ["Prankster", "Gale Wings", "Technician"],
        "sp_guidance": "Priority moves have low base power, so the attacking "
                       "stat carries the whole KO — invest heavily there and "
                       "confirm the range with calc_damage. Speed SP are nearly "
                       "wasted on a pure priority attacker.",
        "pairs_with": ["spread-attacker", "physical-wallbreaker", "bulky-attacker"],
        "answered_by": ["physical-wall", "tank", "intimidate-support"],
        "find_candidates": [
            'get_move("Extreme Speed") — check priority field',
            'pokemons_by_ability("Prankster")',
            'pokemons_by_move("Sucker Punch")',
        ],
    },
    {
        "id": "spread-attacker",
        "name": "Spread Attacker",
        "category": "offense",
        "aliases": ["spread move user", "double-target attacker"],
        "summary": "Doubles-only: attacks both opposing Pokémon at once with "
                   "spread moves, trading per-target power for board-wide damage.",
        "description": (
            "In doubles, spread moves hit both opponents (some also hit your own "
            "ally) at 0.75× damage per target — so 1.5× total output if both "
            "targets are relevant. That maths is the engine of doubles offence: "
            "chipping both opponents into range simultaneously lets a partner or "
            "a priority move convert two KOs in one turn. The cost is that "
            "spread damage often fails to KO anything on its own, and that ally-"
            "hitting moves (Earthquake, Rock Slide-style board moves) constrain "
            "which partners you can bring — hence the pairing with Flying types, "
            "Levitate, Wide Guard and Protect."
        ),
        "formats": _f(
            vgc=("core", "The defining offensive pattern of the format. Build "
                 "around the ally-damage constraint: a spread-move user wants a "
                 "partner that is immune, resistant, or reliably Protecting."),
            bss=("unviable", "In singles there is only ever one opposing target, "
                 "so a spread move loses nothing but also gains nothing — it is "
                 "just a single-target move judged on its own power. Never pick "
                 "a Pokémon for this role in BSS."),
        ),
        "look_for": {
            "stats": "Strong offence on the relevant side; enough bulk to survive a turn.",
            "typing": "STAB on a spread move that the format's common leads don't resist.",
            "speed": "Moving first with a spread move is worth a lot — but Trick "
                     "Room spread attackers work on the same principle in reverse.",
        },
        "signature_moves": [
            "Earthquake", "Rock Slide", "Heat Wave", "Muddy Water", "Blizzard",
            "Dazzling Gleam", "Hyper Voice", "Surf", "Discharge", "Make It Rain",
        ],
        "signature_items": ["Life Orb", "Wise Glasses", "Sitrus Berry", "Expert Belt"],
        "signature_abilities": ["Drought", "Drizzle", "Levitate", "Sheer Force"],
        "sp_guidance": "Because spread damage is 0.75×, benchmarks must be "
                       "confirmed with calc_damage(game_type='doubles') — the "
                       "calc applies the reduction, single-target intuition does "
                       "not. It often takes noticeably more offensive SP than "
                       "expected to reach a spread OHKO.",
        "pairs_with": ["field-protection-support", "priority-attacker",
                       "weather-setter", "redirection-support"],
        "answered_by": ["field-protection-support", "bulky-attacker", "tank"],
        "find_candidates": [
            'search_moves(type=…, category=…) then get_move(name) to read the target field',
            'calc_damage(..., game_type="doubles") to see the 0.75× spread reduction applied',
        ],
    },
    {
        "id": "bulky-attacker",
        "name": "Bulky Attacker",
        "category": "offense",
        "aliases": ["bulky offense", "offensive anchor", "bulky sweeper"],
        "summary": "Invests in both bulk and damage so it can stay on the field "
                   "for several turns while still threatening KOs.",
        "description": (
            "Rather than maximising one axis, a bulky attacker buys enough "
            "survivability to take a hit from each opponent and enough offence "
            "to still demand answers. It is the workhorse slot of doubles: it "
            "survives the double-target, keeps applying pressure while support "
            "Pokémon do their work, and does not evaporate to chip damage. In "
            "singles the same idea shows up as a Pokémon that beats offensive "
            "threats one-on-one without needing setup."
        ),
        "formats": _f(
            vgc=("core", "Doubles means two attackers can focus one Pokémon per "
                 "turn, so frail attackers often never move twice. Bulky "
                 "attackers are why most VGC spreads look 'unoptimised' by "
                 "singles standards — the bulk is deliberate."),
            bss=("common", "Fills the same role as a tank with a real offensive "
                 "presence; strong into offence-heavy teams."),
        ),
        "look_for": {
            "stats": "Good HP plus one high offensive stat; neither defence a dump stat.",
            "typing": "Resistances to the format's most common attacking types.",
            "speed": "Mid-tier is fine; it wins by staying in, not by moving first.",
        },
        "signature_moves": ["Protect", "Knock Off", "Body Press", "Bulk Up", "U-turn"],
        "signature_items": ["Leftovers", "Sitrus Berry", "Shell Bell", "Occa Berry"],
        "signature_abilities": ["Intimidate", "Thick Fat", "Regenerator", "Multiscale"],
        "sp_guidance": "The 66-SP budget is well suited to this role: a split "
                       "like 24 HP / 24 offence / 18 defensive is legal and "
                       "coherent. Use calc_damage from *both* sides — pick the "
                       "HP/Def numbers that flip a specific 2HKO into a 3HKO.",
        "pairs_with": ["redirection-support", "tailwind-setter", "fake-out-user"],
        "answered_by": ["physical-wallbreaker", "special-wallbreaker", "disruptor"],
        "find_candidates": [
            'get_pokemon(name) — look for BST spread across HP/def and one offence',
            'get_pokemon_sets(name, format="vgc") — VGC spreads reveal this role clearly',
        ],
    },
    {
        "id": "glass-cannon",
        "name": "Glass Cannon",
        "category": "offense",
        "aliases": ["nuke", "frail attacker"],
        "summary": "Extreme offensive power on an extremely frail body — KOes "
                   "almost anything, dies to almost anything.",
        "description": (
            "A glass cannon converts the entire stat budget into damage and "
            "accepts that it will faint to any neutral hit, or to chip damage. "
            "It is a high-variance slot: correctly deployed it removes a key "
            "Pokémon for free, mispositioned it is a wasted team slot. It nearly "
            "always needs external protection — a Focus Sash, redirection, "
            "screens, or a Fake Out to buy the turn."
        ),
        "formats": _f(
            vgc=("situational", "Very exposed to double-targeting and to spread "
                 "chip. Only run one behind redirection or a Sash, and expect it "
                 "to get one meaningful turn."),
            bss=("common", "More forgiving in singles: only one Pokémon can "
                 "attack it per turn, so a Focus Sash reliably converts into a "
                 "guaranteed turn."),
        ),
        "look_for": {
            "stats": "Extreme offence, poor HP and defences.",
            "typing": "Ideally an immunity or two to create safe switch-ins.",
            "speed": "High — it must move first, since it will not survive to move second.",
        },
        "signature_moves": ["Draco Meteor", "Overheat", "Close Combat", "Hydro Pump"],
        "signature_items": ["Focus Sash", "Life Orb", "Wise Glasses"],
        "signature_abilities": ["Magic Guard", "Protean", "Sheer Force"],
        "sp_guidance": "Max the offensive stat and the Speed tier; defensive SP "
                       "are usually wasted because no realistic investment makes "
                       "it survive anyway. The exception: buying exactly enough "
                       "HP to survive a specific priority move — check with calc_damage.",
        "pairs_with": ["redirection-support", "screens-setter", "fake-out-user"],
        "answered_by": ["priority-attacker", "revenge-killer", "spread-attacker"],
        "find_candidates": [
            'get_pokemon(name) — very high offence, low HP/defences',
            'search_items(query="Sash")',
        ],
    },
    {
        "id": "trapper",
        "name": "Trapper",
        "category": "offense",
        "aliases": ["trapping", "shadow tag", "arena trap", "pursuit trapper"],
        "summary": "Prevents the opponent from switching, forcing a lose-lose "
                   "choice between staying in and taking damage.",
        "description": (
            "Trapping converts a normal matchup into a guaranteed removal: the "
            "target cannot switch out, so it either dies where it stands or "
            "wastes turns. Trapping is how you delete a specific wall or a "
            "specific check that your win condition cannot get past. It is "
            "narrow by design — you build it in to answer one named problem, "
            "not as general-purpose value. Champions note: Shadow Tag is the "
            "only trapping *ability* in this game (no Arena Trap, no Magnet "
            "Pull), so most trapping here has to come from moves."
        ),
        "formats": _f(
            vgc=("situational", "Weaker in doubles: trapping one of two active "
                 "opponents still leaves the other free, and bring-4 means "
                 "fewer switches to punish in the first place."),
            bss=("situational", "Genuinely strong when it removes the one "
                 "Pokémon blocking your sweeper — but bring-3 singles means you "
                 "might never see the target, making it a dead slot in those games."),
        ),
        "look_for": {
            "stats": "Enough offence to actually KO the trapped target.",
            "typing": "Should resist what the target throws while trapped.",
            "speed": "Matters only relative to the intended target.",
        },
        "signature_moves": ["Block", "Mean Look", "Spirit Shackle", "Whirlpool"],
        "signature_items": ["Leftovers", "Life Orb", "Muscle Band"],
        "signature_abilities": ["Shadow Tag"],
        "sp_guidance": "Tune SP against the one target you built it for: enough "
                       "bulk to survive that Pokémon's best move, enough offence "
                       "to KO it in the number of turns you can hold it.",
        "pairs_with": ["physical-setup-sweeper", "special-setup-sweeper"],
        "answered_by": ["disruptor", "priority-attacker"],
        "find_candidates": [
            'pokemons_by_ability("Shadow Tag") — the only trapping ability in Champions',
            'pokemons_by_move("Block")',
        ],
    },
    {
        "id": "trick-room-abuser",
        "name": "Trick Room Abuser",
        "category": "offense",
        "aliases": ["trick room attacker", "slow attacker", "tr abuser"],
        "summary": "Deliberately slow and strong — moves first while Trick Room "
                   "is up, because Trick Room reverses the Speed order.",
        "description": (
            "Trick Room inverts turn order for five turns, so the slowest "
            "Pokémon moves first. A TR abuser is built to exploit that: very low "
            "Speed, very high offence and usually high bulk, since it does not "
            "pay the usual price for being slow. Its whole clock is the five "
            "turns of Trick Room — everything about the set is tuned to score "
            "the maximum number of KOs inside that window. Note the corollary: "
            "a TR abuser is nearly helpless outside Trick Room, so the setter "
            "is not optional."
        ),
        "formats": _f(
            vgc=("core", "One of the format's two main modes (the other is "
                 "Tailwind). A Trick Room team is a real archetype, not a "
                 "gimmick — and TR abusers double as an answer to opposing "
                 "Tailwind teams."),
            bss=("situational", "Trick Room exists in singles but is much harder "
                 "to leverage: no partner to protect the setter, and the "
                 "opponent can simply switch and stall the five turns out."),
        ),
        "look_for": {
            "stats": "Very high Attack or Special Attack, very low base Speed, good HP.",
            "typing": "Bulk that lets it survive to use all five TR turns.",
            "speed": "The lower the better — base Speed under ~50 is the target.",
        },
        "signature_moves": ["Trick Room", "Protect", "High Horsepower", "Body Press"],
        "signature_items": ["Life Orb", "Sitrus Berry", "Muscle Band", "Leftovers"],
        "signature_abilities": ["Iron Fist", "Sheer Force", "Thick Fat"],
        "sp_guidance": "Put **zero** SP into Speed and prefer a Speed-lowering "
                       "nature (Brave/Quiet/Relaxed/Sassy) — inside Trick Room "
                       "lower Speed is strictly better. Check the nature lists "
                       "in get_game_rules, then spend the whole 66 on offence "
                       "and bulk.",
        "pairs_with": ["trick-room-setter", "redirection-support", "fake-out-user"],
        "answered_by": ["disruptor", "field-protection-support", "priority-attacker"],
        "find_candidates": [
            'get_speed_tiers(format="vgc") — then look at the *bottom* of the roster instead',
            'get_usage_stats(format="vgc") + get_pokemon(name) — base Speed ≤ 50 with high offence',
        ],
    },

    # ----------------------------- Defense -------------------------------- #
    {
        "id": "physical-wall",
        "name": "Physical Wall",
        "category": "defense",
        "aliases": ["physically defensive", "phys def wall", "physical tank"],
        "summary": "Built to absorb physical attacks repeatedly and reliably "
                   "across the whole game.",
        "description": (
            "A wall's contract is consistency: it should be able to switch into "
            "the format's main physical attacker again and again, not just once. "
            "That requires recovery (or Leftovers/Regenerator), a typing that "
            "resists what it is meant to answer, and enough HP that chip damage "
            "does not accumulate faster than it heals. A wall without recovery "
            "is a tank — it takes hits for a while, then stops being able to."
        ),
        "formats": _f(
            vgc=("situational", "Doubles games are short and two attackers can "
                 "focus one target, so pure walls without offensive presence "
                 "often just lose tempo. The bulk usually goes on a bulky "
                 "attacker or a support Pokémon instead."),
            bss=("common", "A staple of balance in bring-3 singles, though the "
                 "short game length means it must contribute (status, hazards, "
                 "pivoting) rather than just sit there."),
        ),
        "look_for": {
            "stats": "High HP and Defense; recovery in the movepool.",
            "typing": "Resists common physical STAB; ideally few weaknesses.",
            "speed": "Low is fine — walls do not race.",
        },
        "signature_moves": ["Recover", "Roost", "Body Press", "Will-O-Wisp",
                            "Toxic", "Protect"],
        "signature_items": ["Leftovers", "Sitrus Berry", "Shed Shell", "Occa Berry"],
        "signature_abilities": ["Intimidate", "Regenerator", "Fur Coat", "Sturdy"],
        "sp_guidance": "HP is the most efficient defensive SP because it "
                       "multiplies both defences — but the 66-SP cap means you "
                       "cannot max HP *and* both defences. Pick the side you are "
                       "walling and verify the survival benchmark with calc_damage.",
        "pairs_with": ["special-wall", "cleric", "hazard-setter", "defensive-pivot"],
        "answered_by": ["special-wallbreaker", "mixed-attacker", "disruptor",
                        "stallbreaker"],
        "find_candidates": [
            'get_pokemon(name) — high HP + Defense',
            'pokemons_by_move("Recover") / pokemons_by_move("Roost")',
            'get_pokemon_weaknesses(name)',
        ],
    },
    {
        "id": "special-wall",
        "name": "Special Wall",
        "category": "defense",
        "aliases": ["specially defensive", "spdef wall", "special tank"],
        "summary": "The special-side wall: absorbs special attacks consistently "
                   "over the course of a game.",
        "description": (
            "Same contract as the physical wall, aimed at special attackers. It "
            "matters as a separate slot because a team that only walls one side "
            "hands the opponent a free win condition on the other. Assault Vest "
            "is the common shortcut here — a large Special Defense boost in "
            "exchange for giving up status moves, which turns the Pokémon into "
            "an offensive special sponge rather than a true wall."
        ),
        "formats": _f(
            vgc=("situational", "A dedicated passive special wall rarely earns a "
                 "slot in a bring-4 format; the bulk is usually folded into a "
                 "bulky attacker instead. With no Assault Vest in Champions, "
                 "that trade-off is even sharper here."),
            bss=("common", "Standard on balance teams; pairs with a physical "
                 "wall to cover both sides."),
        ),
        "look_for": {
            "stats": "High HP and Special Defense; recovery preferred.",
            "typing": "Resists the format's dominant special STAB.",
            "speed": "Irrelevant.",
        },
        "signature_moves": ["Wish", "Recover", "Heal Bell", "Toxic", "Protect"],
        "signature_items": ["Leftovers", "Sitrus Berry", "Chesto Berry",
                            "Wacan Berry"],
        "signature_abilities": ["Regenerator", "Thick Fat", "Water Absorb",
                                "Volt Absorb", "Flash Fire"],
        "sp_guidance": "Same HP-first logic as the physical wall, but with no "
                       "Assault Vest available the SP have to do all the work: "
                       "split HP and Special Defense and confirm the exact "
                       "survival benchmark with calc_damage. A type-protection "
                       "berry (Wacan, Occa, Passho…) is the closest Champions "
                       "equivalent to an item-based bulk boost, for one hit.",
        "pairs_with": ["physical-wall", "cleric", "defensive-pivot"],
        "answered_by": ["physical-wallbreaker", "mixed-attacker", "stallbreaker"],
        "find_candidates": [
            'get_pokemon(name) — high HP + Special Defense',
            'search_items(query="Berry") — type-protection berries for a one-hit boost',
        ],
    },
    {
        "id": "tank",
        "name": "Tank",
        "category": "defense",
        "aliases": ["bulky attacker (defensive)", "semi-wall"],
        "summary": "Takes hits and hits back, but without a wall's reliable "
                   "recovery — it absorbs damage for a while, not forever.",
        "description": (
            "The distinction from a wall is durability over time. A wall has "
            "recovery and can perform its job all game; a tank has the bulk to "
            "eat several hits and the offence to punish, but its HP only goes "
            "down. That makes it a mid-game workhorse rather than a permanent "
            "answer, and means chip damage is genuinely dangerous to it."
        ),
        "formats": _f(
            vgc=("common", "Very close to the bulky attacker role in practice; "
                 "the two names describe the same doubles slot from the "
                 "defensive and offensive side respectively."),
            bss=("common", "A good fit for bring-3 singles, where games are "
                 "short enough that the lack of recovery rarely gets punished."),
        ),
        "look_for": {
            "stats": "Good HP and at least one defence, plus real offence.",
            "typing": "Useful resistances; ideally an immunity.",
            "speed": "Low to mid.",
        },
        "signature_moves": ["Knock Off", "Body Press", "Protect", "Earthquake"],
        "signature_items": ["Leftovers", "Sitrus Berry", "Shell Bell", "Expert Belt"],
        "signature_abilities": ["Intimidate", "Thick Fat", "Regenerator"],
        "sp_guidance": "Split the 66 across HP, the relevant defence, and the "
                       "attacking stat. Because there's no recovery, buying the "
                       "HP that turns a 2HKO into a 3HKO is usually worth more "
                       "than the last few points of Attack.",
        "pairs_with": ["cleric", "defensive-pivot", "hazard-remover"],
        "answered_by": ["physical-wallbreaker", "special-wallbreaker", "trapper"],
        "find_candidates": [
            'get_pokemon(name) — balanced BST with no dump stat',
            'get_pokemon_sets(name, format=…)',
        ],
    },
    {
        "id": "status-absorber",
        "name": "Status Absorber",
        "category": "defense",
        "aliases": ["status sponge", "cleric target", "guts user"],
        "summary": "Switches into status moves safely — it is immune to, cures, "
                   "or actively benefits from the status.",
        "description": (
            "Status (burn, paralysis, poison, sleep) is how defensive teams beat "
            "attackers they cannot out-damage. A status absorber turns that "
            "plan off: through a typing immunity, an ability (Guts, Poison Heal, "
            "Natural Cure, Magic Bounce), Rest, or a Lum Berry, it takes the "
            "status move for free and often gets stronger for it. Its presence "
            "changes how the opponent can play — a Will-O-Wisp thrown at a Guts "
            "attacker is a lost turn and a doubled Attack stat."
        ),
        "formats": _f(
            vgc=("situational", "Doubles games are short and status is less "
                 "central than in singles; usually a bonus property on a bulky "
                 "attacker rather than a slot of its own. Still relevant against "
                 "Thunder Wave-based speed control and Spore/Rage Powder cores."),
            bss=("common", "Singles leans on status heavily, so having one "
                 "Pokémon that ignores it prevents a whole class of loss."),
        ),
        "look_for": {
            "stats": "Enough bulk to switch in repeatedly.",
            "typing": "Steel/Poison (poison immunity), Electric (paralysis), Fire (burn).",
            "speed": "Irrelevant.",
        },
        "signature_moves": ["Rest", "Sleep Talk", "Facade", "Substitute"],
        "signature_items": ["Lum Berry", "Chesto Berry", "Persim Berry", "Leftovers"],
        "signature_abilities": ["Guts", "Natural Cure", "Magic Bounce", "Poison Heal",
                                "Limber", "Immunity"],
        "sp_guidance": "Standard defensive investment. Guts-style sets are the "
                       "exception: they want Attack SP, because the status is "
                       "the point rather than the problem.",
        "pairs_with": ["cleric", "physical-wall", "defensive-pivot"],
        "answered_by": ["physical-wallbreaker", "disruptor"],
        "find_candidates": [
            'pokemons_by_ability("Guts") / pokemons_by_ability("Natural Cure")',
            'get_ability("Magic Bounce")',
            'get_pokemon_weaknesses(name) — type immunities to status',
        ],
    },
    {
        "id": "stallbreaker",
        "name": "Stallbreaker",
        "category": "defense",
        "aliases": ["anti-stall", "taunt user"],
        "summary": "Beats defensive Pokémon by shutting off their non-damaging "
                   "moves rather than by out-damaging them.",
        "description": (
            "A wall's power comes from its status moves — recovery, Toxic, "
            "hazards, phazing. A stallbreaker removes access to those (Taunt, "
            "Encore, Substitute, Magic Bounce) and then wins the resulting "
            "damage race, even though it could never break the wall through raw "
            "power. This is a different answer to walls than a wallbreaker: the "
            "wallbreaker overpowers them, the stallbreaker disables them."
        ),
        "formats": _f(
            vgc=("unviable", "Stall as an archetype does not exist in bring-4 "
                 "doubles — there is no clock to stall against and no room for "
                 "purely passive Pokémon. Taunt is still useful in VGC, but as "
                 "general disruption (see the Disruptor role), not as anti-stall."),
            bss=("situational", "Bring-3 singles is far too short for true "
                 "stall, so this role is much less important here than in 6v6 "
                 "singles. It stays relevant as an answer to bulky setup "
                 "Pokémon and to Toxic-based defensive sets."),
        ),
        "look_for": {
            "stats": "Enough bulk to survive while a wall runs out of options.",
            "typing": "Poison/Steel or Magic Guard to ignore Toxic damage.",
            "speed": "Must outspeed the wall to Taunt before it heals.",
        },
        "signature_moves": ["Taunt", "Encore", "Substitute", "Roost", "Toxic"],
        "signature_items": ["Leftovers", "Mental Herb", "Sitrus Berry"],
        "signature_abilities": ["Magic Guard", "Magic Bounce", "Prankster"],
        "sp_guidance": "Speed matters more than usual — Taunt only works if it "
                       "lands before the recovery move. Use speed_threshold "
                       "against the specific defensive Pokémon you're targeting.",
        "pairs_with": ["physical-setup-sweeper", "special-setup-sweeper"],
        "answered_by": ["bulky-attacker", "physical-wallbreaker"],
        "find_candidates": [
            'pokemons_by_move("Taunt")',
            'pokemons_by_ability("Magic Guard")',
        ],
    },
    {
        "id": "phazer",
        "name": "Phazer",
        "category": "defense",
        "aliases": ["shuffler", "roar user", "whirlwind user", "pseudo-hazer"],
        "summary": "Forces the opponent out with Roar/Whirlwind/Dragon Tail, "
                   "erasing stat boosts and denying setup.",
        "description": (
            "Phazing is the structural answer to setup sweepers: no matter how "
            "many boosts they accumulate, being forced out discards all of them. "
            "It also drags an unwilling Pokémon into hazards, which is why "
            "phazing and hazard stacking are traditionally built together. The "
            "weakness is that phazing is reactive and gives up your own turn's "
            "damage."
        ),
        "formats": _f(
            vgc=("unviable", "Forcing a switch is close to meaningless in "
                 "bring-4 doubles: the opponent has few Pokémon in reserve, "
                 "hazards are rarely up to punish the switch, and you gave up a "
                 "turn of damage in a format that lasts a handful of turns. Use "
                 "Intimidate, Taunt or Haze to answer setup instead."),
            bss=("situational", "Works, but bring-3 singles limits it — with "
                 "only two Pokémon in the back, phazing frequently just shuffles "
                 "into something worse for you. Clear Smog/Haze is often the "
                 "better boost-removal tool."),
        ),
        "look_for": {
            "stats": "Enough bulk to survive the boosted attacker's hit first.",
            "typing": "Resists the sweeper's STAB.",
            "speed": "Roar/Whirlwind have negative priority, so Speed doesn't help.",
        },
        "signature_moves": ["Roar", "Whirlwind", "Dragon Tail", "Circle Throw",
                            "Haze", "Clear Smog", "Perish Song"],
        "signature_items": ["Leftovers", "Sitrus Berry"],
        "signature_abilities": ["Sturdy", "Intimidate"],
        "sp_guidance": "Pure defensive investment — the phaze move does no "
                       "damage (or trivial damage), so offensive SP are wasted.",
        "pairs_with": ["hazard-setter", "physical-wall"],
        "answered_by": ["disruptor", "physical-wallbreaker"],
        "find_candidates": [
            'pokemons_by_move("Roar") / pokemons_by_move("Haze")',
            'get_move("Dragon Tail") — check the priority field',
        ],
    },

    # ------------------------------ Pivot --------------------------------- #
    {
        "id": "offensive-pivot",
        "name": "Offensive Pivot",
        "category": "pivot",
        "aliases": ["u-turn user", "volt switch user", "momentum pivot", "pivot"],
        "summary": "Threatens damage, then switches out on its own terms with "
                   "U-turn/Volt Switch — bringing a teammate in for free.",
        "description": (
            "Momentum is the resource here. An offensive pivot forces the "
            "opponent to respect a KO threat, then leaves with a pivoting move "
            "so its replacement enters *after* seeing what the opponent did. "
            "That free switch-in is what lets a frail wallbreaker or setup "
            "sweeper get onto the field safely — which is the real payoff. It "
            "differs from a defensive pivot in how it forces the switch: by "
            "threatening damage, not by being unbreakable."
        ),
        "formats": _f(
            vgc=("common", "Pivoting exists in doubles (U-turn, Flip Turn, "
                 "Parting Shot, Volt Switch) and is good for repositioning a "
                 "damaged Pokémon or bringing in a Trick Room abuser safely — "
                 "but it is less central than in singles, because doubles "
                 "momentum is mostly decided by speed control and Protect."),
            bss=("core", "The main engine of singles teams: pivot moves are how "
                 "you generate the free switches that everything else depends on."),
        ),
        "look_for": {
            "stats": "Enough offence to make the threat real; mid-to-high Speed.",
            "typing": "Resistances that create safe entry points.",
            "speed": "Faster is better — pivoting after taking a hit is much worse.",
        },
        "signature_moves": ["U-turn", "Volt Switch", "Flip Turn", "Parting Shot",
                            "Baton Pass", "Chilly Reception"],
        "signature_items": ["Choice Scarf", "Life Orb", "Muscle Band", "Expert Belt"],
        "signature_abilities": ["Regenerator", "Intimidate"],
        "sp_guidance": "Speed carries this role — pivot before taking the hit. "
                       "Use speed_threshold against the Pokémon you expect to "
                       "pivot away from, then split the rest between offence and "
                       "enough bulk to survive a hit if you're wrong.",
        "pairs_with": ["physical-wallbreaker", "special-wallbreaker", "glass-cannon",
                       "physical-setup-sweeper"],
        "answered_by": ["defensive-pivot", "trapper", "physical-wall"],
        "find_candidates": [
            'pokemons_by_move("U-turn") / pokemons_by_move("Volt Switch")',
            'get_pokemon_sets(name, format=…) — is the pivot move in its top moves?',
        ],
    },
    {
        "id": "defensive-pivot",
        "name": "Defensive Pivot",
        "category": "pivot",
        "aliases": ["bulky pivot", "slow pivot", "slow u-turn"],
        "summary": "Absorbs a hit, then pivots slowly so its replacement comes in "
                   "without taking damage at all.",
        "description": (
            "Where an offensive pivot forces switches through threat, a "
            "defensive pivot forces them by being hard to break — and its "
            "*slowness* is the feature. A slow U-turn or Parting Shot means the "
            "incoming teammate arrives after the opponent has already moved, so "
            "it enters completely free. This is the safest way to deploy a "
            "fragile win condition, and Regenerator-style recovery makes the "
            "cycle repeatable all game. Champions note: Teleport does not exist "
            "here, so slow pivoting has to come from U-turn, Volt Switch, Flip "
            "Turn or Parting Shot — all of which deal damage or drop stats, and "
            "so cannot be used purely defensively."
        ),
        "formats": _f(
            vgc=("situational", "The slow-pivot trick is much less valuable in "
                 "doubles, where you can simply switch one slot while the other "
                 "attacks. Worth it mainly for repositioning into Trick Room."),
            bss=("core", "One of the most reliable singles structures: soak a "
                 "hit, slow-pivot, and your breaker is in for free."),
        ),
        "look_for": {
            "stats": "High HP/defences, low Speed (deliberately).",
            "typing": "Resistances that let it switch in repeatedly.",
            "speed": "Low is *better* — the pivot move should go last.",
        },
        "signature_moves": ["U-turn", "Parting Shot", "Flip Turn", "Wish",
                            "Volt Switch"],
        "signature_items": ["Leftovers", "Sitrus Berry", "Shed Shell"],
        "signature_abilities": ["Regenerator", "Intimidate", "Natural Cure"],
        "sp_guidance": "Leave Speed at 0 SP and consider a Speed-lowering nature "
                       "— slow pivoting is the point. Spend the full 66 on HP "
                       "and the defence you're covering.",
        "pairs_with": ["physical-setup-sweeper", "special-setup-sweeper",
                       "physical-wallbreaker", "cleric"],
        "answered_by": ["disruptor", "trapper", "stallbreaker"],
        "find_candidates": [
            'pokemons_by_move("Parting Shot") / pokemons_by_move("U-turn")',
            'pokemons_by_ability("Regenerator")',
        ],
    },

    # -------------------------- Speed control ----------------------------- #
    {
        "id": "tailwind-setter",
        "name": "Tailwind Setter",
        "category": "speed_control",
        "aliases": ["tailwind", "speed control", "tailwind support"],
        "summary": "Doubles the whole team's Speed for four turns, flipping the "
                   "Speed race in your favour.",
        "description": (
            "Tailwind is the fast half of doubles speed control: for four turns "
            "your entire side moves first, which converts mid-Speed attackers "
            "into the fastest Pokémon on the field. Because the window is short, "
            "a Tailwind team is built to win inside it — hard-hitting attackers "
            "that need only a few turns. The setter itself is usually fast or "
            "has Prankster (so Tailwind goes up before it can be stopped), and "
            "commonly carries a Mental Herb against Taunt."
        ),
        "formats": _f(
            vgc=("core", "One of the two dominant speed modes; most VGC teams "
                 "run Tailwind, Trick Room, or a speed-drop move, and many run "
                 "two modes so they can play fast into slow teams and slow into "
                 "fast ones. Redundancy matters: Tailwind expires, so a second "
                 "setter or a backup mode is normal."),
            bss=("situational", "Tailwind works in singles but costs a full turn "
                 "of a 1v1, and the four turns often expire before you cash "
                 "them in. A Choice Scarf usually buys the same Speed advantage "
                 "for free."),
        ),
        "look_for": {
            "stats": "Enough bulk to survive the setup turn; offence optional.",
            "typing": "Something that isn't KOed on turn one by the common leads.",
            "speed": "High, or Prankster — Tailwind must go up before it's disrupted.",
        },
        "signature_moves": ["Tailwind", "Protect", "Taunt", "Fake Out"],
        "signature_items": ["Focus Sash", "Mental Herb", "Sitrus Berry"],
        "signature_abilities": ["Prankster", "Defiant"],
        "sp_guidance": "Buy the Speed to get Tailwind up on turn one (or rely on "
                       "Prankster and skip Speed entirely), then put the rest "
                       "into the bulk that survives a Fake Out plus an attack. "
                       "Speed SP on the *rest* of the team can then be reduced — "
                       "compute_speed(..., tailwind=True) shows the real numbers.",
        "pairs_with": ["physical-wallbreaker", "spread-attacker", "bulky-attacker",
                       "fake-out-user"],
        "answered_by": ["disruptor", "trick-room-setter", "priority-attacker"],
        "find_candidates": [
            'pokemons_by_move("Tailwind")',
            'pokemons_by_ability("Prankster")',
            'compute_speed(name, ..., tailwind=True)',
        ],
    },
    {
        "id": "trick-room-setter",
        "name": "Trick Room Setter",
        "category": "speed_control",
        "aliases": ["trick room", "tr setter", "room setter"],
        "summary": "Reverses turn order for five turns so the slowest Pokémon "
                   "move first.",
        "description": (
            "The slow half of speed control, and the counter to Tailwind teams. "
            "The setter's only job is to get Trick Room up and survive long "
            "enough to be worth the slot — so it wants bulk, a Mental Herb "
            "against Taunt, and ideally support from Fake Out or redirection. "
            "Because the setter is usually slow itself, it tends to set Trick "
            "Room *after* the opponent moves, which is fine: the effect starts "
            "the following turn regardless. A Trick Room team lives and dies on "
            "having actual abusers behind it."
        ),
        "formats": _f(
            vgc=("core", "A full archetype in doubles. Also used defensively as "
                 "a single slot on an otherwise normal team, to flip the Speed "
                 "race against fast offence. Redundancy is normal — two setters, "
                 "or a Mental Herb, because losing the setter loses the game plan."),
            bss=("situational", "Possible in singles but fragile: no partner to "
                 "shield the setter, and the opponent can stall the five turns "
                 "by switching. Usually only on dedicated slow-offence teams."),
        ),
        "look_for": {
            "stats": "High HP/defences so it survives to set and stays useful.",
            "typing": "Resistances to the common leads.",
            "speed": "Low — the setter is usually a Trick Room abuser too.",
        },
        "signature_moves": ["Trick Room", "Protect", "Fake Out", "Follow Me",
                            "Imprison"],
        "signature_items": ["Mental Herb", "Sitrus Berry", "Focus Sash", "Leftovers"],
        "signature_abilities": ["Prankster", "Levitate", "Sturdy"],
        "sp_guidance": "No Speed SP, Speed-lowering nature. Invest entirely in "
                       "HP and defences — surviving to set Trick Room is the "
                       "whole contribution. Note that in the Champions SP system "
                       "the 66-point cap makes 'max HP + max one defence' "
                       "impossible; pick the side that matters.",
        "pairs_with": ["trick-room-abuser", "redirection-support", "fake-out-user"],
        "answered_by": ["disruptor", "tailwind-setter", "priority-attacker"],
        "find_candidates": [
            'pokemons_by_move("Trick Room")',
            'get_item("Mental Herb")',
            'get_usage_stats(format="vgc") + get_pokemon(name) — low Speed, high bulk',
        ],
    },
    {
        "id": "speed-drop-support",
        "name": "Speed-Drop Support",
        "category": "speed_control",
        "aliases": ["icy wind user", "thunder wave user", "speed control (drop)",
                    "electroweb user"],
        "summary": "Slows the opponent instead of speeding yourself up — Icy "
                   "Wind, Electroweb, Thunder Wave, Sticky Web.",
        "description": (
            "The third form of speed control, and often the most flexible: it "
            "does not expire like Tailwind, it does not need a team built around "
            "it like Trick Room, and spread versions (Icy Wind, Electroweb) hit "
            "both opponents while chipping them. Thunder Wave halves Speed and "
            "adds full-paralysis chances but is blocked by Electric types and "
            "several abilities. The cost is that it is incremental — one Icy "
            "Wind may not be enough to flip the Speed order on its own."
        ),
        "formats": _f(
            vgc=("core", "Spread Speed drops are excellent in doubles: one move "
                 "slows both opponents and chips them for a partner's follow-up. "
                 "Many teams run this *alongside* Tailwind or Trick Room as a "
                 "backup speed mode."),
            bss=("situational", "Thunder Wave is a real singles tool and Sticky "
                 "Web can work, but a single-target Icy Wind is usually a weak "
                 "use of a turn. Choice Scarf tends to solve the same problem "
                 "more cleanly."),
        ),
        "look_for": {
            "stats": "Bulk to survive; offence secondary.",
            "typing": "Something that can switch in on the fast threats you want to slow.",
            "speed": "Should outspeed the target — the drop only helps if it lands first.",
        },
        "signature_moves": ["Icy Wind", "Electroweb", "Thunder Wave", "Sticky Web",
                            "Bulldoze", "Rock Tomb", "Glare", "String Shot"],
        "signature_items": ["Focus Sash", "Sitrus Berry", "Mental Herb"],
        "signature_abilities": ["Prankster", "Levitate"],
        "sp_guidance": "Enough Speed to move before the target you're slowing "
                       "(speed_threshold), then bulk. After a drop, recompute "
                       "the race with compute_speed using stage=-1 or -2 on the "
                       "opponent — a single Icy Wind is one stage (×2/3).",
        "pairs_with": ["spread-attacker", "bulky-attacker", "physical-wallbreaker"],
        "answered_by": ["status-absorber", "field-protection-support", "disruptor"],
        "find_candidates": [
            'pokemons_by_move("Icy Wind") / pokemons_by_move("Thunder Wave")',
            'compute_speed(name, ..., stage=-1) to model the drop',
        ],
    },

    # ----------------------------- Support -------------------------------- #
    {
        "id": "redirection-support",
        "name": "Redirection Support",
        "category": "support",
        "aliases": ["follow me user", "rage powder user", "redirector", "redirection"],
        "summary": "Doubles-only: forces every single-target attack to hit it "
                   "instead of its partner.",
        "description": (
            "Follow Me and Rage Powder pull all single-target attacks onto the "
            "redirector, which is the cleanest way to guarantee a partner gets "
            "its turn — a setup move, a Trick Room, or a full-power attack from "
            "something that would otherwise be focused down. The redirector "
            "trades its own turn and its own HP for the partner's turn, so it "
            "wants bulk and often Sitrus Berry. Note the limits: redirection "
            "does not stop spread moves, and Rage Powder specifically is "
            "ignored by Grass types and by Overcoat. Champions note: there is "
            "no Safety Goggles here, so Overcoat and Grass typing are the only "
            "ways to ignore Rage Powder."
        ),
        "formats": _f(
            vgc=("core", "One of the format's defining support tools. If your "
                 "gameplan is 'this Pokémon must get one turn', redirection is "
                 "usually the answer."),
            bss=("unviable", "Follow Me and Rage Powder redirect attacks aimed "
                 "at an *ally* — in a 1v1 there is no ally, so they do nothing "
                 "of value. Never assign this role in BSS."),
        ),
        "look_for": {
            "stats": "High HP and mixed bulk — it is absorbing both attacks.",
            "typing": "Resistances to common attacking types.",
            "speed": "Follow Me/Rage Powder have +2 priority, so Speed barely matters.",
        },
        "signature_moves": ["Follow Me", "Rage Powder", "Ally Switch", "Protect",
                            "Sleep Powder", "Helping Hand"],
        "signature_items": ["Sitrus Berry", "Mental Herb", "Leftovers", "Occa Berry"],
        "signature_abilities": ["Regenerator", "Lightning Rod", "Overcoat",
                                "Friend Guard"],
        "sp_guidance": "Pure bulk. Because it eats both opposing attacks, split "
                       "the 66 across HP and *both* defences rather than "
                       "specialising — then verify with calc_damage from both "
                       "sides that it survives the turn it needs to redirect.",
        "pairs_with": ["trick-room-setter", "physical-setup-sweeper", "glass-cannon",
                       "trick-room-abuser", "physical-wallbreaker"],
        "answered_by": ["spread-attacker", "disruptor", "fake-out-user"],
        "find_candidates": [
            'pokemons_by_move("Follow Me") / pokemons_by_move("Rage Powder")',
            'pokemons_by_ability("Lightning Rod") / pokemons_by_ability("Friend Guard")',
        ],
    },
    {
        "id": "fake-out-user",
        "name": "Fake Out User",
        "category": "support",
        "aliases": ["fake out", "flinch support"],
        "summary": "Uses Fake Out (+3 priority, guaranteed flinch on the turn it "
                   "switches in) to delete an opposing turn.",
        "description": (
            "Fake Out removes one opponent from the turn entirely: it always "
            "moves first and always flinches (barring Inner Focus or a "
            "Substitute), but only on the turn the user enters the field. In "
            "doubles that is a full tempo turn — enough to "
            "set Trick Room safely, get a setup move off, or turn a 2v2 into an "
            "effective 2v1 for a turn. Its limits are what make it a skill "
            "check: it works once per switch-in, it fails against Ghost types "
            "(Normal-type move) and Inner Focus, and using it means not "
            "attacking. Champions note: there is no Covert Cloak in this game, "
            "so Ghost typing, Inner Focus and Substitute are the *only* outs — "
            "Fake Out is correspondingly stronger here than in games that have "
            "an item answer to it."
        ),
        "formats": _f(
            vgc=("core", "A near-mandatory tool. Fake Out support is how "
                 "fragile setup and Trick Room actually happen, and it is also "
                 "the main way to stop an opposing Trick Room or Tailwind from "
                 "going up on turn one."),
            bss=("situational", "Fake Out works in singles too, but spending a "
                 "turn on chip damage and a flinch is a much weaker trade when "
                 "there is only one opponent and the turn count is short. "
                 "Occasionally used to break a Focus Sash."),
        ),
        "look_for": {
            "stats": "Moderate Attack (Fake Out chip matters for Sash-breaking); bulk.",
            "typing": "Not walled by common Ghost-type leads.",
            "speed": "+3 priority, so Speed only matters versus another Fake Out user.",
        },
        "signature_moves": ["Fake Out", "Protect", "Parting Shot", "U-turn",
                            "Knock Off", "Taunt"],
        "signature_items": ["Sitrus Berry", "Leftovers", "Mental Herb", "Focus Sash"],
        "signature_abilities": ["Intimidate", "Inner Focus", "Technician"],
        "sp_guidance": "Bulk-first, with enough Attack to break a Focus Sash "
                       "(check with calc_damage). Speed SP are usually wasted — "
                       "the only Speed race that matters is against another Fake "
                       "Out user, and even then it rarely justifies the points.",
        "pairs_with": ["trick-room-setter", "physical-setup-sweeper",
                       "tailwind-setter", "glass-cannon"],
        "answered_by": ["field-protection-support", "status-absorber"],
        "find_candidates": [
            'pokemons_by_move("Fake Out")',
            'pokemons_by_ability("Inner Focus") — one of the few Fake Out answers in Champions',
        ],
    },
    {
        "id": "intimidate-support",
        "name": "Intimidate / Stat-Drop Support",
        "category": "support",
        "aliases": ["intimidate", "attack drop", "stat drop support"],
        "summary": "Lowers the opponent's offensive stats on switch-in or with a "
                   "move, softening the whole team's damage intake.",
        "description": (
            "Intimidate drops Attack the moment the Pokémon enters — free, "
            "repeatable, and in doubles it hits *both* opponents at once, which "
            "is why it is one of the most valuable abilities in the format. The "
            "same idea extends to moves (Charm, Snarl, Fake Tears in reverse) "
            "and to Parting Shot. The practical effect is defensive: your whole "
            "team's effective bulk goes up, which is often worth more than any "
            "single Pokémon's investment. Watch for the punish — Defiant and "
            "Competitive turn your Intimidate into a free boost for them."
        ),
        "formats": _f(
            vgc=("core", "Hits both opponents per switch-in and can be re-"
                 "triggered by pivoting. Snarl (spread Special Attack drop) is "
                 "the special-side equivalent and equally valued."),
            bss=("common", "Still strong in singles — a free Attack drop on "
                 "switch-in — but it only affects one opponent, so the payoff "
                 "is roughly half of the doubles version."),
        ),
        "look_for": {
            "stats": "Bulk, so it can switch in repeatedly to re-trigger the drop.",
            "typing": "Resistances that create free switch-in opportunities.",
            "speed": "Irrelevant for the ability; matters for the move versions.",
        },
        "signature_moves": ["Snarl", "Parting Shot", "Charm", "Baby-Doll Eyes",
                            "U-turn"],
        "signature_items": ["Sitrus Berry", "Leftovers", "Shell Bell"],
        "signature_abilities": ["Intimidate"],
        "sp_guidance": "Defensive investment. Remember Intimidate is applied "
                       "*outside* the SP system — pass attacker_boosts={'atk': "
                       "-1} to calc_damage to model it, rather than trying to "
                       "buy the equivalent bulk with SP.",
        "pairs_with": ["bulky-attacker", "redirection-support", "physical-wall"],
        "answered_by": ["special-wallbreaker", "special-setup-sweeper"],
        "find_candidates": [
            'pokemons_by_ability("Intimidate")',
            'pokemons_by_move("Snarl")',
            'calc_damage(..., defender_ability=…, attacker_boosts={"atk": -1})',
        ],
    },
    {
        "id": "field-protection-support",
        "name": "Field Protection Support",
        "category": "support",
        "aliases": ["wide guard user", "quick guard user", "ally switch",
                    "protect support"],
        "summary": "Doubles-only: blanks whole categories of incoming moves with "
                   "Wide Guard, Quick Guard or Ally Switch.",
        "description": (
            "These moves are the doubles counter-play layer. Wide Guard blocks "
            "every spread move aimed at your side for the turn, which single-"
            "handedly answers the format's core offensive pattern. Quick Guard "
            "blocks priority (including Fake Out). Ally Switch swaps your two "
            "Pokémon's positions, dodging a focused attack entirely. All of them "
            "trade your turn for negating the opponent's, and all become "
            "unreliable if spammed — repeated use has a failure chance."
        ),
        "formats": _f(
            vgc=("core", "Wide Guard in particular is a defining answer to "
                 "spread offence and to Trick Room teams built on board-wide "
                 "damage. Expect good opponents to play around it."),
            bss=("unviable", "Wide Guard, Quick Guard and Ally Switch all "
                 "require an ally on the field; in singles they do nothing. "
                 "Plain Protect remains useful in BSS, but that is not this role."),
        ),
        "look_for": {
            "stats": "Bulk — the Pokémon needs to still be alive to use these.",
            "typing": "Anything that fits the team; the moves do the work.",
            "speed": "Wide/Quick Guard are +3 priority; Ally Switch is +2.",
        },
        "signature_moves": ["Wide Guard", "Quick Guard", "Ally Switch", "Protect",
                            "Detect"],
        "signature_items": ["Sitrus Berry", "Mental Herb", "Leftovers"],
        "signature_abilities": ["Prankster", "Friend Guard"],
        "sp_guidance": "Defensive investment; these moves have no damage "
                       "component. Priority means Speed SP contribute nothing to "
                       "the role itself.",
        "pairs_with": ["trick-room-abuser", "bulky-attacker", "spread-attacker"],
        "answered_by": ["physical-wallbreaker", "disruptor"],
        "find_candidates": [
            'pokemons_by_move("Wide Guard") / pokemons_by_move("Ally Switch")',
            'get_move("Quick Guard") — check priority',
        ],
    },
    {
        "id": "cleric",
        "name": "Cleric / Wish Support",
        "category": "support",
        "aliases": ["healer", "heal bell user", "aromatherapy", "wish passer",
                    "wish support"],
        "summary": "Heals the team — cures status with Heal Bell/Aromatherapy, or "
                   "passes HP with Wish.",
        "description": (
            "A cleric restores team-wide resources. Heal Bell cures status "
            "across the whole party (Aromatherapy does not exist in Champions), "
            "which undoes a defensive team's "
            "main plan against you. Wish is the other half: it heals the "
            "Pokémon in the slot next turn, so a bulky Wish user can keep a "
            "recovery-less wallbreaker alive across an entire game. Both are "
            "slow, turn-consuming effects, which is why they belong on Pokémon "
            "that can afford to spend turns."
        ),
        "formats": _f(
            vgc=("situational", "Bring-4 doubles rarely lasts long enough for a "
                 "cleric to pay off, and the turn cost is high. Healing "
                 "generally comes from items (Sitrus Berry) instead. Life Dew "
                 "and Pollen Puff are the more VGC-appropriate expressions."),
            bss=("common", "Useful in singles balance teams, especially against "
                 "status-heavy opponents — though bring-3 means you must weigh "
                 "the turn cost carefully."),
        ),
        "look_for": {
            "stats": "High HP (Wish heals for half the *user's* max HP) and bulk.",
            "typing": "Something that gets safe turns to click a non-damaging move.",
            "speed": "Low is fine.",
        },
        "signature_moves": ["Wish", "Heal Bell", "Life Dew", "Pollen Puff",
                            "Healing Wish", "Protect"],
        "signature_items": ["Leftovers", "Sitrus Berry"],
        "signature_abilities": ["Natural Cure", "Regenerator", "Hydration"],
        "sp_guidance": "Wish scales with the user's max HP, so HP SP are worth "
                       "double here — they both make the cleric bulkier and make "
                       "each Wish heal more. Use calc_stats to see the actual HP "
                       "number your SP buy.",
        "pairs_with": ["physical-wall", "special-wall", "physical-wallbreaker",
                       "defensive-pivot"],
        "answered_by": ["stallbreaker", "disruptor", "physical-wallbreaker"],
        "find_candidates": [
            'pokemons_by_move("Wish") / pokemons_by_move("Heal Bell")',
            'pokemons_by_ability("Natural Cure")',
        ],
    },
    {
        "id": "screens-setter",
        "name": "Screens Setter",
        "category": "support",
        "aliases": ["dual screener", "reflect user", "light screen user",
                    "aurora veil"],
        "summary": "Sets Reflect / Light Screen (or Aurora Veil) to halve incoming "
                   "damage for the team.",
        "description": (
            "Screens buy survivability for the whole team rather than one "
            "Pokémon — five turns (eight with Light Clay) in which everything "
            "takes roughly half damage. That is usually spent enabling something "
            "that could not otherwise set up: a sweeper gets its boosting turn, "
            "a frail attacker survives the hit it shouldn't. The setter itself "
            "is expendable and typically carries a Focus Sash so it survives to "
            "put both screens up."
        ),
        "formats": _f(
            vgc=("common", "Screens offence is a real doubles archetype, and "
                 "Aurora Veil (in hail/snow) covers both sides in one move. "
                 "Weakness: doubles gives the opponent two attackers, so five "
                 "turns of half damage still adds up quickly."),
            bss=("common", "A standard enabler for setup sweepers in singles; "
                 "the shorter bring-3 game means the screen turns overlap well "
                 "with the sweep attempt."),
        ),
        "look_for": {
            "stats": "Enough bulk (or a Focus Sash) to set both screens.",
            "typing": "Survives the common leads.",
            "speed": "High, or Prankster — screens must land before you're KOed.",
        },
        "signature_moves": ["Reflect", "Light Screen", "Aurora Veil", "Taunt",
                            "Memento", "Fake Out"],
        "signature_items": ["Light Clay", "Focus Sash", "Mental Herb"],
        "signature_abilities": ["Prankster", "Snow Warning"],
        "sp_guidance": "Speed (to set screens before dying) or nothing at all if "
                       "it has Prankster; a Focus Sash substitutes for defensive "
                       "SP. Screens setters are one of the few roles where "
                       "leaving SP on cheap stats is genuinely fine.",
        "pairs_with": ["physical-setup-sweeper", "special-setup-sweeper",
                       "glass-cannon"],
        "answered_by": ["disruptor", "physical-wallbreaker"],
        "find_candidates": [
            'pokemons_by_move("Reflect") / pokemons_by_move("Aurora Veil")',
            'get_item("Light Clay")',
        ],
    },
    {
        "id": "disruptor",
        "name": "Disruptor",
        "category": "support",
        "aliases": ["taunt user", "encore user", "trick user", "knock off user",
                    "utility"],
        "summary": "Attacks the opponent's options rather than their HP — Taunt, "
                   "Encore, Trick, Knock Off, Haze.",
        "description": (
            "Disruption denies the opponent's plan. Taunt shuts off status "
            "moves (speed control, setup, recovery, hazards); Encore locks them "
            "into a move they just used; Trick sabotages a wall with a Choice "
            "item; Knock Off removes the item outright; Haze/Clear Smog erase "
            "stat boosts. This is how you beat a strategy you cannot beat "
            "through damage — and in doubles it is the standard answer to Trick "
            "Room and Tailwind, since Taunt from a fast or Prankster Pokémon "
            "stops the mode from ever going up."
        ),
        "formats": _f(
            vgc=("core", "Taunt is the primary counter-play to opposing speed "
                 "control, and Knock Off removes the Sitrus Berries and Mental "
                 "Herbs the format runs on. Prankster Taunt is especially "
                 "valuable because it beats the setter to the punch."),
            bss=("core", "Knock Off and Trick are among the highest-value "
                 "singles moves; Encore and Taunt punish setup and passive "
                 "Pokémon alike."),
        ),
        "look_for": {
            "stats": "Enough bulk to survive a turn; offence optional.",
            "typing": "Whatever fits — the moves carry the role.",
            "speed": "High or Prankster — disruption is worthless if it lands second.",
        },
        "signature_moves": ["Taunt", "Encore", "Trick", "Switcheroo", "Knock Off",
                            "Haze", "Clear Smog", "Disable", "Torment"],
        "signature_items": ["Choice Scarf", "Focus Sash", "Leftovers"],
        "signature_abilities": ["Prankster", "Magic Bounce", "Unnerve"],
        "sp_guidance": "Speed is the stat that decides whether disruption works. "
                       "Use speed_threshold against the setter or sweeper you're "
                       "aiming at; with Prankster, skip Speed and buy bulk instead.",
        "pairs_with": ["physical-setup-sweeper", "bulky-attacker", "offensive-pivot"],
        "answered_by": ["status-absorber", "bulky-attacker"],
        "find_candidates": [
            'pokemons_by_move("Taunt") / pokemons_by_move("Knock Off")',
            'pokemons_by_ability("Prankster")',
        ],
    },
    {
        "id": "weather-setter",
        "name": "Weather Setter",
        "category": "support",
        "aliases": ["rain setter", "sun setter", "sand setter", "snow setter",
                    "drizzle", "drought"],
        "summary": "Establishes rain / sun / sand / snow, changing damage, "
                   "accuracy and abilities for the whole field.",
        "description": (
            "Weather is a field-wide multiplier: it boosts one attacking type "
            "and weakens another, powers accuracy effects (Thunder, Hurricane, "
            "Blizzard), and switches on a whole class of abilities (Swift Swim, "
            "Chlorophyll, Sand Rush, Solar Power). Modern weather is nearly "
            "always set by an ability on switch-in rather than a move, which "
            "makes the setter cheap in turns but means weather wars are decided "
            "by who switched in last. A weather setter without abusers behind it "
            "is a wasted slot."
        ),
        "formats": _f(
            vgc=("core", "Sun and rain are two of the main doubles archetypes: "
                 "the boosted spread moves (Heat Wave in sun, Muddy Water in "
                 "rain) plus Swift Swim/Chlorophyll speed make weather a "
                 "complete gameplan by itself."),
            bss=("common", "Works in singles, but a full weather team asks a lot "
                 "of a bring-3 format — you may never draw the abuser. Often "
                 "used for a single boosted attacker instead."),
        ),
        "look_for": {
            "stats": "Enough bulk to switch back in and re-set the weather.",
            "typing": "Should not fold to opposing weather setters.",
            "speed": "Only matters for move-based weather setting.",
        },
        "signature_moves": ["Rain Dance", "Sunny Day", "Sandstorm", "Snowscape",
                            "Protect"],
        "signature_items": ["Heat Rock", "Damp Rock", "Smooth Rock", "Icy Rock",
                            "Focus Sash"],
        "signature_abilities": ["Drizzle", "Drought", "Sand Stream", "Snow Warning"],
        "sp_guidance": "Usually bulk — the setter's job is to come back and "
                       "re-set weather after a weather war. Pass weather=… to "
                       "calc_damage so the boost is actually reflected in your "
                       "benchmarks; a sun-boosted Fire move is 1.5×.",
        "pairs_with": ["field-abuser", "spread-attacker", "special-wallbreaker"],
        "answered_by": ["weather-setter", "disruptor"],
        "find_candidates": [
            'pokemons_by_ability("Drizzle") / pokemons_by_ability("Drought")',
            'search_items(query="Rock") — the weather-extending rocks',
            'calc_damage(..., weather="sun"|"rain"|"sand"|"snow")',
        ],
    },
    {
        "id": "terrain-setter",
        "name": "Terrain Setter",
        "category": "support",
        "aliases": ["electric terrain", "grassy terrain", "psychic terrain",
                    "misty terrain", "surge"],
        "summary": "Sets a terrain that boosts a move type and adds a protective "
                   "effect for grounded Pokémon.",
        "description": (
            "Terrain is the other field effect, and it is as much defensive as "
            "offensive. Electric Terrain boosts Electric moves and blocks sleep; "
            "Grassy Terrain boosts Grass, heals grounded Pokémon each turn and "
            "weakens Earthquake; Psychic Terrain boosts Psychic and — critically "
            "— blocks priority moves against grounded Pokémon; Misty Terrain "
            "blocks status and halves Dragon damage. Which terrain you pick is "
            "usually decided by the protective clause, not the damage boost. "
            "Champions note: Electric Surge is the only terrain-setting ability "
            "in this game and there is no Terrain Extender, so terrain here is "
            "almost always set by the move — meaning it costs a turn and lasts "
            "only five."
        ),
        "formats": _f(
            vgc=("common", "Psychic Terrain shutting off Fake Out and priority, "
                 "and Grassy Terrain weakening spread Earthquake, are both "
                 "strong structural effects in doubles."),
            bss=("common", "Same effects apply; Electric Terrain's sleep "
                 "immunity and Misty Terrain's status immunity are the usual "
                 "reasons to run it in singles."),
        ),
        "look_for": {
            "stats": "Bulk to re-set the terrain after a terrain war.",
            "typing": "Ideally matches the boosted type so it profits directly.",
            "speed": "Matters for move-based setting only.",
        },
        "signature_moves": ["Electric Terrain", "Grassy Terrain", "Psychic Terrain",
                            "Misty Terrain", "Expanding Force", "Rising Voltage"],
        "signature_items": ["Sitrus Berry", "Leftovers", "Focus Sash"],
        "signature_abilities": ["Electric Surge"],
        "sp_guidance": "Bulk-focused, like weather. Pass terrain=… to calc_damage "
                       "— the boost and the Earthquake reduction are both applied "
                       "by the calc, not by intuition.",
        "pairs_with": ["field-abuser", "priority-attacker", "special-wallbreaker"],
        "answered_by": ["terrain-setter", "disruptor"],
        "find_candidates": [
            'pokemons_by_move("Grassy Terrain") / pokemons_by_move("Psychic Terrain")',
            'pokemons_by_ability("Electric Surge") — the only surge ability in Champions',
            'calc_damage(..., terrain=…)',
        ],
    },
    {
        "id": "field-abuser",
        "name": "Weather / Terrain Abuser",
        "category": "support",
        "aliases": ["weather abuser", "swift swim user", "chlorophyll user",
                    "terrain abuser", "sun sweeper", "rain sweeper"],
        "summary": "The payoff Pokémon for a field effect — doubled Speed, "
                   "boosted STAB, or a terrain-only move.",
        "description": (
            "The setter is the cost; this is the payoff. Under the right field, "
            "an abuser becomes something it otherwise is not: Swift Swim and "
            "Chlorophyll double Speed (turning a mid-Speed attacker into the "
            "fastest thing on the field, which is a form of speed control), "
            "Solar Power and weather-boosted STAB turn a good attacker into a "
            "nuke, and terrain-locked moves (Rising Voltage, Expanding Force) "
            "only reach their real power with the terrain up. The catch is "
            "total dependence: if the field flips, the abuser is often below "
            "average."
        ),
        "formats": _f(
            vgc=("core", "Weather teams in doubles are built as setter + two "
                 "abusers; the doubled Speed doubles as the team's speed "
                 "control, replacing Tailwind entirely."),
            bss=("situational", "Strong when it works, but bring-3 means you may "
                 "draw the abuser without the setter. Consider whether the "
                 "Pokémon is still acceptable with no field up."),
        ),
        "look_for": {
            "stats": "High offence; Speed can be mediocre if the ability doubles it.",
            "typing": "STAB matching the boosted type.",
            "speed": "Judge it *after* the ability — a Swift Swim user's real "
                     "Speed is double the number on the sheet.",
        },
        "signature_moves": ["Weather Ball", "Hurricane", "Thunder", "Solar Beam",
                            "Rising Voltage", "Expanding Force", "Grassy Glide",
                            "Blizzard"],
        "signature_items": ["Life Orb", "Wise Glasses", "Muscle Band", "Expert Belt"],
        "signature_abilities": ["Swift Swim", "Chlorophyll", "Sand Rush",
                                "Slush Rush", "Solar Power"],
        "sp_guidance": "The Speed-doubling abilities make Speed SP far less "
                       "valuable — the ×2 does the work, so move those points "
                       "into offence or bulk. Model it with compute_speed and "
                       "sanity-check that you still outspeed what matters if the "
                       "weather is removed.",
        "pairs_with": ["weather-setter", "terrain-setter", "spread-attacker"],
        "answered_by": ["weather-setter", "priority-attacker", "disruptor"],
        "find_candidates": [
            'pokemons_by_ability("Swift Swim") / pokemons_by_ability("Chlorophyll")',
            'pokemons_by_move("Weather Ball")',
        ],
    },

    # ----------------------------- Hazards -------------------------------- #
    {
        "id": "hazard-setter",
        "name": "Entry Hazard Setter",
        "category": "hazards",
        "aliases": ["stealth rock user", "spikes user", "toxic spikes",
                    "sticky web setter", "hazards"],
        "summary": "Installs Stealth Rock / Spikes / Toxic Spikes / Sticky Web to "
                   "punish every opposing switch.",
        "description": (
            "Hazards convert the opponent's switching into damage they cannot "
            "prevent, and they compound: over a long game, Stealth Rock plus "
            "Spikes turns 'survives' into 'gets KOed'. Their value scales "
            "directly with how many switches the format produces and how long "
            "games run — which is exactly why hazards are a cornerstone of 6v6 "
            "singles and a much smaller factor in Champions' two formats."
        ),
        "formats": _f(
            vgc=("situational", "Bring-4 doubles produces few switches and short "
                 "games, so hazards usually cannot pay back the turn spent "
                 "setting them. Occasionally seen as a lead option, but do not "
                 "treat a hazard setter as a required VGC slot."),
            bss=("common", "Stealth Rock is worth a slot in bring-3 singles "
                 "because it chips the two Pokémon in the back on entry. "
                 "Multi-layer Spikes stacking is much weaker here than in 6v6 "
                 "singles — there simply aren't enough switches to lay three "
                 "layers and cash them in."),
        ),
        "look_for": {
            "stats": "Enough bulk to get the hazard down; often a lead.",
            "typing": "Should force switches, which is what makes hazards pay.",
            "speed": "High enough to set before being KOed, or a Focus Sash.",
        },
        "signature_moves": ["Stealth Rock", "Spikes", "Toxic Spikes", "Sticky Web",
                            "Taunt"],
        "signature_items": ["Focus Sash", "Leftovers", "Mental Herb"],
        "signature_abilities": ["Sturdy", "Prankster", "Magic Bounce"],
        "sp_guidance": "Enough bulk or Speed to guarantee the hazard lands. "
                       "Given the 66-SP budget and how few turns these formats "
                       "run, do not over-invest in a Pokémon whose only job is "
                       "one turn of setup.",
        "pairs_with": ["phazer", "offensive-pivot", "cleaner", "suicide-lead"],
        "answered_by": ["hazard-remover", "disruptor"],
        "find_candidates": [
            'pokemons_by_move("Stealth Rock") / pokemons_by_move("Sticky Web")',
            'get_move("Spikes")',
        ],
    },
    {
        "id": "hazard-remover",
        "name": "Hazard Remover",
        "category": "hazards",
        "aliases": ["defogger", "rapid spinner", "spinner", "defog user",
                    "hazard control"],
        "summary": "Clears entry hazards with Defog, Rapid Spin or Court Change "
                   "so your team can switch freely.",
        "description": (
            "The counterpart to the hazard setter. Rapid Spin removes hazards "
            "from your side only and is a damaging move; Defog clears both "
            "sides (including your own hazards, which is a real cost); Tidy Up "
            "clears hazards and boosts. Magic Bounce is the pre-emptive "
            "version — it reflects the hazard move back at the setter. How much "
            "this role is worth is entirely a function of how much the format "
            "uses hazards. Champions note: there is no Heavy-Duty Boots here, so "
            "a Rock-weak Pokémon cannot opt out of Stealth Rock with an item — "
            "actively removing hazards is the only answer."
        ),
        "formats": _f(
            vgc=("situational", "Since hazards are rare in bring-4 doubles, a "
                 "dedicated remover is usually unnecessary. Only reach for it if "
                 "the meta you're building against actually runs hazards."),
            bss=("common", "Worth a slot when Stealth Rock is common, especially "
                 "if your team leans on a Rock-weak attacker — and with no "
                 "Heavy-Duty Boots in Champions there is no item shortcut, so "
                 "the slot is the only answer."),
        ),
        "look_for": {
            "stats": "Bulk to survive coming in on the hazard setter.",
            "typing": "Ideally not weak to Stealth Rock itself.",
            "speed": "Helps to remove before more hazards go up.",
        },
        "signature_moves": ["Defog", "Rapid Spin", "Mortal Spin", "Tidy Up"],
        "signature_items": ["Leftovers", "Sitrus Berry", "Shed Shell"],
        "signature_abilities": ["Magic Bounce", "Regenerator"],
        "sp_guidance": "Defensive investment so it can come in on the setter and "
                       "clear repeatedly. Rapid Spin sets sometimes want Attack "
                       "since the move deals damage and boosts Speed.",
        "pairs_with": ["physical-setup-sweeper", "cleaner", "defensive-pivot"],
        "answered_by": ["disruptor", "hazard-setter"],
        "find_candidates": [
            'pokemons_by_move("Defog") / pokemons_by_move("Rapid Spin")',
            'pokemons_by_ability("Magic Bounce")',
        ],
    },

    # ------------------------------- Lead --------------------------------- #
    {
        "id": "suicide-lead",
        "name": "Suicide Lead",
        "category": "lead",
        "aliases": ["lead", "sash lead", "hazard lead"],
        "summary": "Leads, does as much as possible on turns one and two — "
                   "hazards, screens, a Taunt — and is expected to faint.",
        "description": (
            "A suicide lead spends its life for tempo: it sets hazards or "
            "screens, Taunts the opposing setter, and dies. Usually built around "
            "a Focus Sash so it is guaranteed two turns regardless of what hits "
            "it. The gamble is that the tempo is worth a sixth of your team — "
            "which is a much harder sell in a bring-3 or bring-4 format than in "
            "6v6, since the lead is a quarter or a third of what you actually "
            "bring."
        ),
        "formats": _f(
            vgc=("unviable", "You bring 4 of 6 — throwing one away for hazards "
                 "that barely function in doubles is a losing trade. A fast "
                 "Taunt or Fake Out lead that *survives* is the doubles version "
                 "of this idea."),
            bss=("situational", "Works occasionally with Sticky Web or Stealth "
                 "Rock plus Taunt, but you are spending a third of your brought "
                 "team. Team preview in bring-3 also lets the opponent decline "
                 "the matchup."),
        ),
        "look_for": {
            "stats": "High Speed; bulk is irrelevant because of the Sash.",
            "typing": "Not weak to common lead attacks in a way the Sash can't fix.",
            "speed": "As high as possible — it must act first.",
        },
        "signature_moves": ["Stealth Rock", "Sticky Web", "Taunt", "Explosion",
                            "Memento", "Spikes", "Reflect", "Light Screen"],
        "signature_items": ["Focus Sash", "Light Clay", "Mental Herb"],
        "signature_abilities": ["Sturdy", "Prankster"],
        "sp_guidance": "Max Speed, everything else is close to irrelevant behind "
                       "a Focus Sash. Use speed_threshold against the format's "
                       "fastest common leads.",
        "pairs_with": ["physical-setup-sweeper", "cleaner", "hazard-setter"],
        "answered_by": ["disruptor", "priority-attacker", "hazard-remover"],
        "find_candidates": [
            'get_speed_tiers(format=…) — pick from the very top',
            'pokemons_by_move("Taunt")',
            'get_item("Focus Sash")',
        ],
    },
    {
        "id": "baton-passer",
        "name": "Baton Passer",
        "category": "support",
        "aliases": ["baton pass", "boost passer"],
        "summary": "Accumulates stat boosts (or a Substitute) and passes them to "
                   "a teammate with Baton Pass.",
        "description": (
            "Baton Pass moves the boosts, not the Pokémon: a bulky user sets up "
            "Agility or Calm Mind behind a Substitute, then passes the whole "
            "package to a teammate that can actually use it. It converts a "
            "Pokémon that is safe-but-weak into a turn of setup for one that is "
            "strong-but-fragile. It is a fragile plan — one Haze, Taunt, or a "
            "well-timed KO ends it — and it consumes turns, so it needs a very "
            "favourable matchup."
        ),
        "formats": _f(
            vgc=("situational", "Doubles gives the opponent two ways to "
                 "interrupt the chain every turn. Occasionally used as a single "
                 "Speed pass into a Trick Room-free offensive turn, but it is "
                 "a gimmick more than a framework here."),
            bss=("situational", "Bring-3 makes long pass chains impossible; a "
                 "one-step pass (Speed or a Substitute) into a breaker is the "
                 "realistic version."),
        ),
        "look_for": {
            "stats": "Bulk to survive the setup turns.",
            "typing": "Resistances that grant free turns.",
            "speed": "High, or it will not get the boosting turns off.",
        },
        "signature_moves": ["Baton Pass", "Agility", "Substitute", "Swords Dance",
                            "Calm Mind", "Iron Defense"],
        "signature_items": ["Leftovers", "Sitrus Berry", "Mental Herb"],
        "signature_abilities": ["Speed Boost", "Magic Guard", "Prankster"],
        "sp_guidance": "Bulk-first — the passer's own offence is irrelevant, it "
                       "only needs to survive long enough to boost and pass.",
        "pairs_with": ["physical-wallbreaker", "special-setup-sweeper", "glass-cannon"],
        "answered_by": ["phazer", "disruptor", "priority-attacker"],
        "find_candidates": [
            'pokemons_by_move("Baton Pass")',
            'pokemons_by_ability("Speed Boost")',
        ],
    },
]

_BY_ID: dict[str, dict[str, Any]] = {r["id"]: r for r in _ROLES}

_LOOKUP: dict[str, str] = {}
for _role in _ROLES:
    _LOOKUP[_role["id"]] = _role["id"]
    _LOOKUP[slugify(_role["name"])] = _role["id"]
    for _alias in _role["aliases"]:
        _LOOKUP.setdefault(slugify(_alias), _role["id"])


_SOURCES = [
    "Smogon — The Pokémon Dictionary (role definitions)",
    "Smogon — OU Role Compendium threads (SS / SV): role taxonomy",
    "VGC teambuilding-framework literature (doubles: speed control, "
    "redirection, Fake Out, spread offence)",
]

# Creativity in team building is choosing an unexpected *filler* for a slot, not
# inventing facts about it. These four gates are what separate a genuinely
# slept-on pick from one that simply doesn't work: each is answerable with a
# tool, so an unconventional choice can be defended with data instead of vibes.
_CREATIVE_PICK_GATE = {
    "principle": "Usage rank is evidence about what people play, not a rule about "
                 "what works. A low-usage Pokémon that clears all four gates below "
                 "is a legitimate pick — and often a better one, because opponents "
                 "prepare for the common answer. A pick that fails any gate is not "
                 "creative, it is just worse than the obvious choice.",
    "gates": [
        "1. LEGAL — it actually learns this role's defining move / has the "
        "required ability in Champions. Check get_pokemon_moves or "
        "pokemons_by_move; never assume a movepool from another game.",
        "2. SIGNATURE — its base stats and typing match this role's `look_for`. "
        "Check get_pokemon. A 'creative wallbreaker' with mediocre offence is "
        "just a bad Pokémon.",
        "3. BENCHMARK — it hits the number the role lives or dies on: the Speed "
        "tier (speed_threshold / get_speed_tiers) or the survival/KO roll "
        "(calc_damage). This is the gate most off-meta picks fail.",
        "4. RELEVANCE — it does something about at least one real top-usage "
        "threat. Check get_usage_stats, then confirm with calc_damage.",
    ],
    "how_to_find_them": "get_usage_stats(format=…, order='bottom') ranks every "
                        "legal Pokémon with no cutoff — intersect that with "
                        "pokemons_by_move(<this role's signature move>) to get "
                        "real off-meta candidates for this slot, instead of "
                        "guessing which Pokémon are underrated.",
}

_DISCLAIMER = (
    "Roles are analytical vocabulary, not data from championsbattledata. They "
    "describe what a team slot is *for*. Confirm every concrete pick against "
    "the Champions tools — is_legal_pokemon, get_pokemon_moves, get_item and "
    "get_pokemon_sets — because Champions movepools and items differ from "
    "other games."
)


# --------------------------------------------------------------------------- #
# Public read-model
# --------------------------------------------------------------------------- #

def _first_sentence(text: str) -> str:
    """First sentence of a note — enough to justify an exclusion in the compact
    listing without carrying the whole paragraph."""
    for i, ch in enumerate(text):
        if ch == "." and (i + 1 == len(text) or text[i + 1] == " "):
            return text[: i + 1]
    return text


def resolve_role(name: str) -> str | None:
    """Resolve a role id, display name or alias to a canonical role id."""
    key = slugify(name or "")
    if not key:
        return None
    if key in _LOOKUP:
        return _LOOKUP[key]
    # Substring fallback for partial phrasing ("offensive pivot user"). Only
    # commit when every match points at the same role — "wall" hits three
    # different roles, and silently picking one of them would hand back a
    # confidently wrong brief. Ambiguity resolves to None so the caller can
    # show the candidates instead.
    hits = {rid for k, rid in _LOOKUP.items() if k in key or key in k}
    return hits.pop() if len(hits) == 1 else None


def match_roles(name: str) -> list[str]:
    """Every role id a query could plausibly mean — used to offer candidates
    when :func:`resolve_role` finds the query ambiguous."""
    key = slugify(name or "")
    if not key:
        return []
    return sorted({rid for k, rid in _LOOKUP.items() if k in key or key in k})


def list_roles(
    category: str | None = None,
    format: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Compact catalogue of every competitive role, grouped by category.

    ``format`` ('vgc' or 'bss') filters out roles that are unviable in that
    format and reports the format-specific viability inline.
    """
    fmt = resolve_format(format) if format else None
    cat = (category or "").strip().lower().replace(" ", "_") or None
    q = slugify(query or "") or None

    out: dict[str, list[dict[str, Any]]] = {}
    excluded: list[dict[str, str]] = []

    for role in _ROLES:
        if cat and role["category"] != cat:
            continue
        if q and q not in slugify(
            f"{role['id']} {role['name']} {' '.join(role['aliases'])} {role['summary']}"
        ):
            continue

        entry: dict[str, Any] = {
            "id": role["id"],
            "name": role["name"],
            "summary": role["summary"],
        }
        if fmt:
            block = role["formats"][fmt]
            if block["viability"] == "unviable":
                excluded.append({
                    "id": role["id"], "name": role["name"],
                    "reason": _first_sentence(block["note"]),
                })
                continue
            # Only the grade here — the per-format reasoning is a whole
            # paragraph per role, which would blow past the tool-result size
            # budget of a typical harness. It lives in get_role_description.
            entry["viability"] = block["viability"]
        else:
            entry["viability"] = {
                f: role["formats"][f]["viability"] for f in (_VGC, _BSS)
            }
        out.setdefault(role["category"], []).append(entry)

    # Deliberately compact: this is an index the model scans to pick ~6 slots,
    # so it carries grades and one-line summaries only. The reasoning, the
    # Champions-verified move/item signature and the SP guidance are a
    # paragraph per role and live in get_role_description. Keeping the whole
    # catalogue small enough to survive a harness's tool-result size cap is the
    # difference between the model seeing 38 roles and seeing the first 20.
    result: dict[str, Any] = {
        "format": fmt,
        "count": sum(len(v) for v in out.values()),
        "categories": {c: CATEGORIES[c] for c in out},
        "viability_scale": VIABILITY,
        "roles_by_category": out,
        # Paid once here, not on every get_role_description call.
        "picking_creatively": _CREATIVE_PICK_GATE,
        "sources": _SOURCES,
        "note": _DISCLAIMER,
    }
    if fmt and excluded:
        result["excluded_for_this_format"] = excluded
    if cat and cat not in CATEGORIES:
        result["warning"] = (
            f"Unknown category {category!r}. Valid categories: "
            f"{', '.join(CATEGORIES)}."
        )
    return result


def _check_moves(db: Db, names: list[str]) -> dict[str, Any]:
    """Split signature moves into those Champions actually has (with the number
    of roster Pokémon that learn them) and those it doesn't."""
    available, missing = [], []
    for name in names:
        row = db.get_move(name)
        if not row:
            missing.append(name)
            continue
        users = db.pokemons_by_move(name)
        if not users:
            missing.append(name)
            continue
        available.append({
            "name": row["name"], "type": row["type"],
            "category": row["damage_class"], "power": row["power"],
            "learned_by_count": len(users),
        })
    return {"in_champions": available, "not_in_champions": missing}


def _check_items(db: Db, names: list[str]) -> dict[str, Any]:
    """Split signature items into those Champions actually has and those it
    doesn't."""
    available, missing = [], []
    for name in names:
        row = db.get_item(name)
        if row:
            available.append({"name": row["name"], "category": row["category"]})
        else:
            missing.append(name)
    return {"in_champions": available, "not_in_champions": missing}


def _check_abilities(db: Db, names: list[str]) -> dict[str, Any]:
    """Split signature abilities into those Champions actually has (with the
    number of roster Pokémon that carry them) and those it doesn't."""
    available, missing = [], []
    for name in names:
        row = db.get_ability(name)
        if not row:
            missing.append(name)
            continue
        users = db.pokemons_by_ability(name)
        available.append({
            "name": row["name"],
            "short_effect": row["short_effect"] or row["effect"],
            "pokemon_count": len(users),
        })
    return {"in_champions": available, "not_in_champions": missing}


def get_role_description(
    db: Db, role: str, format: str | None = None,
) -> dict[str, Any]:
    """Full brief on one role, with its move/item/ability signature verified
    against the Champions mirror."""
    rid = resolve_role(role)
    if rid is None:
        candidates = match_roles(role)
        out: dict[str, Any] = {
            "error": (
                f"{role!r} matches several roles — pick one."
                if candidates else f"Unknown role {role!r}."
            ),
            "known_role_ids": [r["id"] for r in _ROLES],
            "next_step": "Call list_roles(format=…) for the catalogue with summaries.",
        }
        if candidates:
            out["did_you_mean"] = [
                {"id": i, "name": _BY_ID[i]["name"], "summary": _BY_ID[i]["summary"]}
                for i in candidates
            ]
        return out

    r = _BY_ID[rid]
    fmt = resolve_format(format) if format else None

    # `meaning` is dropped: it restates the VIABILITY scale, which list_roles
    # already returns once per session. When a format is requested we send that
    # format's note in full and reduce the other to its grade — previously both
    # were sent in full *and* the requested one was repeated verbatim under
    # verdict_for_requested_format.
    def fmt_block(f: str) -> dict[str, Any]:
        return {"viability": r["formats"][f]["viability"],
                "note": r["formats"][f]["note"]}

    if fmt:
        other = _BSS if fmt == _VGC else _VGC
        formats_block: dict[str, Any] = {
            fmt: fmt_block(fmt),
            other: {"viability": r["formats"][other]["viability"]},
        }
    else:
        formats_block = {f: fmt_block(f) for f in (_VGC, _BSS)}

    out: dict[str, Any] = {
        "id": r["id"],
        "name": r["name"],
        "category": r["category"],
        "category_meaning": CATEGORIES[r["category"]],
        "aliases": r["aliases"],
        "summary": r["summary"],
        "description": r["description"],
        "format_viability": formats_block,
        "look_for": r["look_for"],
        "champions_signature": {
            "moves": _check_moves(db, r["signature_moves"]),
            "items": _check_items(db, r["signature_items"]),
            "abilities": _check_abilities(db, r["signature_abilities"]),
            # Terse by design: this rides along with every role lookup. The
            # rationale (Champions' much smaller item pool) is in the tool
            # description, which is sent once in the cached prefix.
            "note": "Verified against the Champions mirror; 'not_in_champions' "
                    "entries do not exist here — do not build around them.",
        },
        "stat_point_guidance": r["sp_guidance"],
        "pairs_with": [
            {"id": i, "name": _BY_ID[i]["name"]} for i in r["pairs_with"] if i in _BY_ID
        ],
        "answered_by": [
            {"id": i, "name": _BY_ID[i]["name"]} for i in r["answered_by"] if i in _BY_ID
        ],
        "find_candidates": r["find_candidates"],
        # The four creative-pick gates, the source list and the "verify against
        # Champions data" disclaimer are identical on every role. They are
        # returned once by list_roles (and restated in both tools'
        # descriptions, which live in the cached prompt prefix) rather than
        # re-paid on each of the 4-6 role lookups a team build makes — those
        # tokens would stay in the conversation for the rest of the session.
        "picking_creatively": _CREATIVE_PICK_GATE["principle"],
    }

    if fmt:
        block = r["formats"][fmt]
        out["requested_format"] = fmt
        # The note itself is already in format_viability[fmt]; only the grade's
        # meaning is added here, since format_viability no longer carries it.
        out["verdict"] = f"{block['viability']} — {VIABILITY[block['viability']]}"
        if block["viability"] == "unviable":
            out["warning"] = (
                f"{r['name']} does not work in {fmt.upper()} — {block['note']} "
                f"Do not assign this role when building a {fmt.upper()} team."
            )
    return out
