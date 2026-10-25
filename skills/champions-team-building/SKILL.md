---
name: champions-team-building
description: >
  Build a competitive, meta-accurate Pokémon Champions team (VGC doubles or
  BSS singles) end-to-end using the champions-mcp MCP server (real roster,
  movepools, items, Stat Points, usage stats, speed/damage calc). Use this
  whenever the user wants to build, design, theorycraft, improve, fix, or get
  suggestions for a Pokémon Champions team, core, lead, or set — including
  "fun gimmick" teams, legality checks, Stat-Point/EV spreads, speed tiers, or
  damage calcs for Champions. Trigger even if the user doesn't say "team
  builder" (e.g. "make me a Champions doubles team", "build me a BSS team",
  "is this Champions team legal", "what beats Sneasler in Champions", "help me
  spread my SP"). Do NOT use for Scarlet/Violet or other-game VGC/BSS unless
  they ask about Champions specifically.
---

# Champions Team Building

This is the core: the rules and the order of work. Deeper guidance lives in
sections — fetch one with `get_team_building_guide(section)` **when you reach
the step that needs it**, not all up front (index at the bottom).

## Hard rules

1. **The tools are the only source of facts.** Champions is not Scarlet/Violet:
   rebalanced movepools, a smaller item pool, Stat Points (66 total, 32/stat,
   1 SP = +1 stat) instead of EVs, Champions-original Megas, regional forms
   sharing a base name. Every typing, stat, ability, move, item, usage %,
   multiplier, Speed number and damage number you state must come from a tool
   result in this conversation. If no tool gave it, you do not know it.
2. **No mental math, of any kind.** Type multipliers → `get_pokemon_weaknesses` /
   `get_type_matchups`. Speed → `compute_speed` / `compute_speed_threshold`.
   Damage and KOs → `calc_damage`. Weather/terrain/room mechanics →
   `get_field_effects`. The server's type chart is authoritative: when a result
   surprises you, the result is right and your memory is wrong — do not argue
   with it or re-derive it.
3. **Do not reason about a fact you can fetch.** If you notice yourself writing
   "X is probably Water/…, or maybe…", stop and call the tool. One call beats a
   paragraph of guessing, every time.
4. **Megas are different Pokémon.** Many top threats are almost always Megas;
   `get_threat_profile` / `get_usage_stats` already give the played form's
   typing, ability and Speed. Calc against them with the Mega Stone as item.
5. **Never mix formats.** VGC = doubles, bring 4 of 6. BSS = singles, bring 3 of 6.
   Pass the same `format` (and matching `game_type` to `calc_damage`) everywhere.
6. **A mode only exists if a teammate sets it.** Trick Room, Tailwind, weather,
   terrain: no setter on the team means the plan does not work. Fix it; never
   present it as a caveat.
7. **Do not truncate tool output yourself** (no `JSON.stringify(...).slice(...)`).
   Ask for less instead: `query=` / `limit=` / `top=` arguments, or map results
   to the few fields you need.

## Order of work — with commit points

Keep each research step short. The commit points are mandatory: when you reach
one, decide and move on.

**0. Format.** If not given, ask VGC or BSS (or pick and state it if you must
work without asking). `get_game_rules(format)`.

**1. Threat map (1–2 tool rounds).** `get_threat_profile(format)` — one call gives
the top threats' played form, typing, ability, Speed tier, weaknesses, common
moves/items, and which attacking types hit the most of them. Add
`get_usage_stats(format, top=60)` only if you need the wider ranking (off-meta =
roughly usage rank 40+). Write down the 5–6 threats that matter most.

**2. Plan — COMMIT POINT.** Propose **at most 3** candidate gameplans, each in one
sentence (VGC: the ideal first three turns; BSS: lead → how it gains the edge →
what closes). Score each against the threat map in a few lines, **pick one, and
state it**. Do not open new candidates after this point unless a calc proves
the chosen plan cannot work.
→ `get_team_building_guide('lenses')` for how a professional judges a plan.

**3. Roles and core (1–2 tool rounds).** `list_roles(format)`; assign each slot a
role. Fill them: `search_pokemon_by_profile(tags, format)`,
`get_team_context(core, format)`, `get_observed_sets(name, format)`.
Take candidates **from these results, not from memory** — a recalled name may not
be in the Champions roster at all (`get_pokemon` says so and suggests the
nearest roster names).
→ `get_team_building_guide('roles')` for the VGC / BSS skeletons and checks.

**4. Core — COMMIT POINT.** Name the six (BSS: as 2–3 overlapping 3-cores).
Before choosing the last slot, run `analyze_team(format)` on the first five and
pick slot six against its `shared_weaknesses` — don't build that table by hand.
**BSS: a battle allows one Mega, so each 3-core may depend on at most one Mega**
(a team can carry several stones; a single bring uses one).
After this, research only what a specific set needs.

**5. Sets (per member).** `get_observed_sets` → `get_pokemon_moves` → item from
`list_items` → ability. Respect the Item Clause.
→ `get_team_building_guide('workflow')` step 4 for how to read set data.

**6. Spreads + calcs (the loop).** `compute_speed_threshold` for Speed SP,
`calc_stats`, then `calc_damage` against each key threat until every benchmark
passes. Offensively aim for a guaranteed OHKO; a 3HKO never justifies a pick.
Defensively the answer wants to be 3HKO'd or worse. Report % ranges.
- **Calc budget:** per member, only the 2–4 calcs that decide its job (the
  threats it must remove or survive). One batch per member, at most ~8 calcs a
  call. Do not re-run a suite you already have.
- Pass Mega names ("Mega Lucario Z") or the stone as item, `weather`/`terrain`
  when the plan sets them, and `game_type='singles'` for BSS.
- **When a number surprises you, read the result's `explain` and
  `description`** (type effectiveness, the defender's form and ability — e.g.
  Aura Guard halves contact damage). Never re-derive the damage formula or the
  stat formula; the listed stats are Lv-50 values and the tools already apply
  SP correctly.
- A spread change after a failed benchmark is one re-calc, not a new suite.

**7. Validate.** `validate_team` → `analyze_team(format)`: fix every
`dependency_gaps` entry and every `flagged` type in `shared_weaknesses`.
Re-run after each change.

**8. Pre-flight, then present.** Run the checklist below, then
`create_pokepaste` and answer in the output format.

## Pre-flight: before presenting any team

Every item answered from tool results. Any "no" is a fix, not a caveat.

- [ ] The plan fits in one sentence, and every support piece serves its endgame.
- [ ] Speed control exists (VGC: with a backup, and not stopped by one Fake Out).
- [ ] `analyze_team` shows no `dependency_gaps` and no unaddressed `flagged` shared weakness.
- [ ] No anti-synergy between abilities, weather, terrain or spread moves.
- [ ] Each of the top 4–6 threats has a named answer, backed by a `calc_damage` that meets the benchmark rules.
- [ ] Each slot names its phase (lead / switch / endgame) and the matchup it is for.
- [ ] Every item and every bulk investment points at a calc or Speed tier.
- [ ] VGC: 3–4 bring-4 plans, each with its own speed control. BSS: 2–3 cores, each with lead + win condition + answer to its own weakness, and at most one Mega per core.
- [ ] The write-up's spreads, natures and moves match the Poképaste exactly.
- [ ] `validate_team` is clean and `create_pokepaste` accepted the team.

## Tool quick map

| Need | Tool(s) |
|---|---|
| Rules / format / SP system / data season | `get_game_rules(format)` |
| **Threat map in one call** | `get_threat_profile(format, top)` |
| Usage ranking (with played typing/ability/Speed) | `get_usage_stats(format, top, order)` |
| Complete builds real players brought | `get_observed_sets(name, format, item, with_teammate)` |
| Partners for your whole core | `get_team_context(team, format)` |
| Whole teams with provenance | `get_sample_teams(format, pokemon)` |
| Per-slot popularity (moves/items/spreads) | `get_pokemon_sets(name, format)` |
| Cores / teammates / Speed benchmarks | `suggest_cores`, `get_top_teammates`, `get_speed_tiers` |
| Roles for this format | `list_roles(format)`, `get_role_description(role, format)` |
| Candidates by typing/stat profile | `search_pokemon_by_profile(tags, format)`, `list_profile_tags()`, `list_type_profiles()` |
| Legal picks | `is_legal_pokemon`, `list_pokemon`, `search_pokemon_by_type/ability/move` |
| Species / Mega data | `get_pokemon(name)` (a Mega name returns the Mega), `list_megas(query)` |
| Moves / items / abilities | `get_pokemon_moves`, `is_legal_move`, `get_move`, `list_items`, `get_item`, `get_ability` |
| Type matchups | `get_pokemon_weaknesses`, `get_type_matchups` |
| Weather / terrain / Trick Room / Tailwind / screens | `get_field_effects(name)` |
| Stats & Speed | `calc_stats`, `compute_speed`, `compute_speed_threshold` |
| Damage | `calc_damage(game_type='doubles'|'singles')` |
| Validation & analysis | `validate_team`, `analyze_team(format)` |
| Export | `create_pokepaste` |
| Deeper guidance | `get_team_building_guide(section)` |

## Sections (`get_team_building_guide(section)`)

| Section | Fetch it when |
|---|---|
| `lenses` | Choosing the plan (step 2): synergy, anti-synergy, shared weaknesses, consistency, pacing, slot jobs |
| `roles` | Steps 3–4: VGC and BSS role skeletons, Fake Out / speed-mode / bring-plan checks, how BSS cores win |
| `workflow` | Any step in detail — especially reading set data (step 5) and the benchmark rules (step 6) |
| `principles` | The longer explanation of the hard rules |
| `creative` | The user wants an off-meta / creative team: where creativity belongs and the four gates every unusual pick must pass |
| `pitfalls` | Champions-specific traps (missing moves/items, Protect in BSS, Speed scale, regional forms) |
| `iterate` | The user reports results with a team and wants it improved |
| `setup` | The tools are missing and the server needs starting |

## Output

Present the final team as:

```
# <Team name / concept> — Champions <format>

<Gameplan: 1–3 sentences — the mode and how it wins>

## Team
### <Pokémon> @ <Item>  —  <Ability>  —  Mega: <if any>
- Nature: <nature>  |  Stat Points: <hp/atk/def/spa/spd/spe> (total/66)
- Moves: <m1> / <m2> / <m3> / <m4>
- Role: <role id from list_roles> — <one line on what it does here>
- Key calcs/speed: <e.g. out-speeds X after Tailwind; OHKOs Y; survives Z>
(repeat for all 6)

## Role coverage
<the skeleton, slot by slot: which Pokémon covers which role, and any role the
team deliberately skips + why>

## Meta coverage
<which top threats this beats and how; remaining risks + how to play them>

## How to bring it
VGC — 3–4 bring plans:
- <matchup / what you see at preview> → Lead: <A + B> | Back: <C + D> — <turn-1 plan>
BSS — the cores:
- <Core name>: <lead> / <win condition> / <answer> — bring into <what you see at preview>

## Weak points
<the honest list: matchups it struggles with and why, and what to watch for in play>

## Poképaste
<link from create_pokepaste>
```

Keep set rationale tied to the data you pulled (usage %, the calc result, the
speed benchmark) — that's what makes the team meta-accurate rather than a guess.
