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

## Why this skill exists

Pokémon Champions is **not** Scarlet/Violet. Movepools were heavily rebalanced,
the item pool differs, stats use **Stat Points** (66 total, 32/stat, 1 SP = +1
stat) instead of EVs, there are Champions-original Megas, and regional forms
share names with their base. A model's pre-Champions intuition is unreliable.
The `champions-mcp` server is the source of truth. **Build from the tools, not
from memory.** "Meta-accurate" means the team is derived from real usage data,
not from generic Pokémon knowledge.

All of champions-mcp's data comes from a single source, **pokechamp.gg**,
pulled into a local database. Usage data (moves, items, abilities, natures, SP
spreads, teammates, rankings) exists for **both** VGC (doubles) and BSS
(singles).

## Prerequisite

This skill requires the `champions-mcp` MCP server. If its tools are not
available, tell the user the server isn't connected and stop — do not fall back
to memory/Scarlet-Violet assumptions.

### Connecting via docker-compose (recommended)

```bash
docker compose up -d --build
```

On first start the server builds its local database from pokechamp.gg
(takes ~30s), then prints the ready URL:

```
MCP URL : http://localhost:8000/mcp
```

Register it in VS Code `settings.json` or any MCP-HTTP client:

```json
"mcp": { "servers": { "champions-mcp": { "type": "http",
  "url": "http://localhost:8000/mcp" } } }
```

### Connecting via stdio (local dev)

```bash
pip install -e ".[dev]"
champions-mcp-pull     # build the local database once
champions-mcp          # stdio MCP server
```

## Core principles

- **Establish format first.** Ask whether the user wants **VGC** (doubles,
  bring 4) or **BSS** (singles, bring 3) before doing anything. Then call
  `get_game_rules` (pass `format='vgc'` or `'bss'`). The format governs every
  later tool call.
- **Never mix formats.** VGC meta/cores/damage calcs must not touch a BSS
  session and vice-versa. The `session_tool_guide` from the rules tool tells you
  what `format` / `game_type` to pass.
- **Use meta to map threats, not to pick Pokémon.** `get_usage_stats`,
  `suggest_cores`, `get_top_teammates`, and `get_speed_tiers` tell you what the
  team must answer — not which Pokémon to copy. Any coherent strategy is valid
  as long as it handles the real threat landscape.
- **Read builds jointly, never slot by slot.** `get_observed_sets(name, format)`
  returns whole builds real players brought, so you can see that a Choice Scarf
  Garchomp runs different moves *and* different teammates than a Life Orb one.
  Its three bands are the point: `locked` choices are near-universal (deviate
  deliberately, and be able to say what you gain), `contested` slots are where
  strong players already disagree (**that is your decision to make from your own
  matchups**, not a popularity contest), and `fringe` entries are rare but
  attested — precedent for a sharp idea. Its `marginal_stacking_check` tells you
  how few real builds run the "four most popular moves" set; when that number is
  low, assembling from `get_pokemon_sets` alone would have produced a build
  nobody plays.
- **Roles before Pokémon.** Decide what each of the 6 slots is *for*
  (`list_roles(format=…)`), then find species that fill those roles. A team
  assembled from six individually-strong Pokémon with no speed control, no way
  to break bulk, and no answer to setup is a bad team however good the parts
  are. Roles are format-specific — see the skeletons below.
- **Verify, don't assume.** Every species, move, item, and ability must be
  checked with the tools. Champions silently differs from SV.
- **Never work out a type matchup in your head — call the tool.** Use
  `get_pokemon_weaknesses(name)` for a Pokémon and `get_type_matchups(t1, t2)`
  for a typing. This is the single most error-prone thing to do from memory,
  and being wrong here silently poisons every threat assessment, switch-in and
  damage estimate downstream. Three traps: **(a)** dual-type multipliers
  *stack*, so the answer is a product (4x / 2x / 1x / ½x / ¼x / 0x) and one
  half's immunity beats the other half's weakness outright — Garchomp is 4x Ice
  *and* immune to Electric; **(b)** you must not assume a Champions Pokémon's
  typing, especially regional forms (which share a name with the base) and
  Megas (which frequently change type); **(c)** resisting a *type* is not the
  same as beating the *Pokémon* — confirm with `calc_damage`. State a
  multiplier only if a tool returned it this conversation.
- **Validate early and often.** `validate_team` is the gate; run it on a rough 6
  and after every change.
- **Champions-native math only.** Use `compute_speed_threshold` / `compute_speed` /
  `calc_stats` (Stat Points, not EVs) and `calc_damage` (Champions calc).
  `calc_stats` ↔ `calc_damage` form an **iteration loop**: a spread is not final
  until `calc_damage` confirms every KO/survival benchmark.

## Workflow

### 0. Establish format
- If unspecified, ask **VGC** (doubles) or **BSS** (singles).
- Call `get_game_rules(format=…)`. Read `session_tool_guide`, the SP budget
  (66/32), Item/Species Clause, Mega cap, bring count, and the active data
  season.

### 1. Read the meta
- `get_usage_stats(format)` → top Pokémon by usage = the **threat landscape**.
  Pass `order="bottom"` for the least-used Pokémon instead — every legal
  Pokémon is ranked (no cutoff), so this is the real way to find off-meta /
  underdog picks, not a guess from general knowledge.
- `suggest_cores(format)` and `get_top_teammates(name, format)` → common pairings
  (available for both VGC and BSS). Use for synergy awareness; off-meta is fine.
- `get_sample_teams(format, pokemon=…)` → whole teams from real events/ladder
  with their record and source. Read them for **structure** — how speed control,
  the defensive backbone and the win condition fit together — not as templates.
  A copied list also copies whatever it loses to, and the field already knows
  the answers to it.
- `get_speed_tiers(format)` → the speed benchmarks to plan investment around.
- **Check which threats are actually Megas.** Several top-usage species hold a
  Mega Stone in almost every game, so you never face the base form. The tools
  flag this — `played_as_mega` on `get_usage_stats`/`get_pokemon_sets`,
  `faced_as` on `analyze_team`, `as_mega` on `get_speed_tiers` (which is already
  tiered on the Mega's Speed). When a threat is flagged, the base stats, typing
  and ability from `get_pokemon` **do not apply to it**: calc against it with
  the Mega Stone as its item (`calc_damage` auto-Mega-evolves) and read its real
  stats from `list_megas(query=…)`. Ignoring this is the single easiest way to
  produce a team whose damage and Speed numbers are all wrong — e.g. tiering
  Speed against a base form that is 40 points slower than the Mega you'll meet.
- Summarize the 4–6 biggest threats; damage/speed-check against these later.

### 2. Decide the roles the team needs
- `list_roles(format=…)` → the role vocabulary **for this format**, with a
  viability grade per role. Do not carry roles across formats: redirection
  (Follow Me), spread attackers and Wide Guard are core in VGC and do literally
  nothing in BSS; revenge killers, phazers and defensive pivots are core in BSS
  and marginal-to-dead in VGC. The tool lists the excluded ones with reasons.
- Write down the 6 slots as roles first (e.g. VGC: Fake Out support / speed
  control / redirection / two attackers / bulky anchor), then fill them.
- `get_role_description(role, format=…)` for any slot you're unsure about → what
  it does, the stat and typing signature to look for, its signature moves and
  items **already checked against the Champions data**, and SP guidance.

### 3. Choose a legal core
- Start from the user's concept or a clear gameplan.
- Validate candidates with `is_legal_pokemon`.
- Fill role/type gaps from the legal pool: `search_pokemon_by_profile`,
  `list_pokemon`, `search_pokemon_by_type`, `search_pokemon_by_ability`, `search_pokemon_by_move`
  — the `find_candidates` field of each role brief tells you which to call.
- **`search_pokemon_by_profile` is the fastest way from a role to candidates.**
  Every Pokémon is tagged from its typing and its stat spread *relative to this
  roster*: `build:physical-attacker`, `build:glass-cannon`, `build:trick-room`,
  `build:bulky-attacker`, `def:fortress`, `off:elite-coverage`, `stat:spe-elite`…
  Translate the role's `look_for` into tags and search — e.g. a physical
  wallbreaker is `["build:physical-attacker","stat:atk-elite"]`, a Trick Room
  abuser is `["build:trick-room","stat:atk-elite"]`. Call `list_profile_tags`
  once for the vocabulary and how many Pokémon carry each tag; pass `format` to
  sort hits by usage rank. This searches the *whole legal pool*, so it surfaces
  off-meta picks you would never recall.
- `list_type_profiles` ranks all 171 typings defensively and offensively. The
  two axes are often opposed — Ice/Rock has near-elite coverage and the worst
  defensive profile on the board — so use it to decide what a slot's typing must
  do, and to understand why a candidate is fragile.
- **`get_team_context(team, format)` once you have 2–3 members.** It conditions
  partners on your *whole* core rather than on one Pokémon, and reports an
  `affinity` score: how often a partner joined this core divided by how often it
  appears format-wide. Affinity ≈ 1 means "merely popular"; a high affinity
  means genuinely drawn to your structure. Read
  `underexplored_partners` before the common list — those are real but uncommon
  answers to the same core, and the cheapest place to differentiate. A core with
  no matches is **untried, not refuted**: build it out and prove it with
  `calc_damage`.

### 4. Build each set from real data
- `get_pokemon` → Champions base stats, abilities, typing, Mega forms.
- `get_pokemon_moves` / `is_legal_move` → choose moves from the Champions
  movepool. Its `coverage_for_weaknesses` block lists, per weakness, the legal
  moves this Pokémon owns that hit that type back super-effectively — an
  Ice-weak Pokémon running a Rock or Fighting move punishes the switch-in that
  was meant to beat it. **Weigh it, don't obey it:** it competes with STAB, raw
  power and the gameplan's required moves, and a weakness a teammate already
  answers needs nothing here. It is most worth spending the 3rd or 4th
  moveslot on when the weakness is 4x, or when the threatening type is
  high-usage. Remember the move must run off the Pokémon's better offensive
  stat to matter (the suggestions are ordered that way), and that the threat may
  be another type carrying the move — confirm with `calc_damage`.
- **`get_observed_sets(name, format)` first.** This is the joint data: complete
  builds real players brought, with the moves that actually accompany each item
  and the teammates that came with them. Filter with `item=` or `with_teammate=`
  to condition further. Work from its bands — take the `locked` choices unless
  you can name what deviating buys, decide the `contested` ones from your team's
  needs, and treat `fringe`/`fringe_sets` as attested precedent when you want an
  angle nobody is prepared for.

  **Evidence differs by format.** VGC returns `full_set` (item + ability +
  nature + moves, from tournament open team sheets). BSS returns
  `team_and_item`: the ranked-ladder export publishes teams and held items but
  no moves, so item and teammate correlations are real while **any moveset is
  your inference** — build it from `get_pokemon_moves` and justify it with
  `calc_damage`/`compute_speed_threshold`, and say so rather than presenting it
  as observed. Neither source carries Stat Points; those still come from
  `get_pokemon_sets`.
- `get_pokemon_sets(name, format)` → **evidence, not a template.** Each list it
  returns (moves, items, abilities, natures, spreads) is an independent
  popularity distribution across every build of that Pokémon — *not* a set. The
  four commonest moves may never have appeared together, and the commonest
  spread may belong to a different build than the commonest item. Taking the top
  of each list produces a set nobody plays and that may be internally
  incoherent (a max-Speed spread on a Trick Room build, a physical spread with
  special moves).

  Use it in three steps: **(a)** read what is proven viable and which Speed
  numbers recur in `spreads` — those are real benchmarks; **(b)** infer the
  distinct builds hiding in the distribution and pick the one matching this
  slot's role; **(c)** adapt it to *your* team — its role, the threats it must
  answer, your speed mode, and the Item Clause once someone else took the
  popular item. The final set is the one that fits the team best, and it is
  frequently **not** one of the listed sets. Deviating is correct when
  `calc_damage`/`compute_speed_threshold` back it; only legality is non-negotiable.
- Pick an item (`list_items` to see the whole pool grouped by category — call it
  once rather than reaching for an item you remember; `get_item` for one;
  `search_items` when you know the name) and ability (`get_ability`).
  Respect the **Item Clause** (no duplicate items across the 6) — this alone
  guarantees at least some members cannot run their most-used item.

### 5. Spread + damage loop (iterate)
- `compute_speed_threshold` to set Speed SP + nature vs key targets (account for
  Tailwind / Choice Scarf).
- `calc_stats` for the candidate spread; `compute_speed` to sanity-check Speed.
- `calc_damage` with the correct `game_type` (doubles=VGC, singles=BSS), setting
  weather, ability, item, Mega (as the held stone), and SP on both sides.
- Adjust SP and repeat until every KO/survival target is met.

**What counts as a passing benchmark.** The two directions have opposite goals —
do not apply one rule to both.

*Offensively*, against a target this Pokémon is meant to remove:
- **Guaranteed OHKO** (the minimum roll KOs) — this is the bar to aim for.
- **A likely OHKO** (say ≥ 80%) is acceptable if you name the risk. A 50% OHKO
  is a coin flip, not a plan; do not present it as an answer to a threat.
- **2HKO** only with a stated reason — it is a wall you are pressuring rather
  than removing, or chip/spread damage/a partner covers the gap. On its own it
  gives the opponent a free turn.
- **3HKO or worse never justifies a pick.** Two free turns is a lost game, not a
  benchmark. If the best you can do is a 3HKO, that Pokémon is not the answer to
  that threat — find a different move, item, spread, or a different Pokémon.

*Defensively*, the goal inverts — a good defensive answer wants to be **3HKO'd
or worse** by the attack it is meant to switch into (survive two hits, act
twice). Being 2HKO'd is borderline; being OHKO'd means it is not an answer.
Never "fix" a wall by making it hit harder.

Report the actual numbers (`%HP` range, KO chance) rather than "it survives" or
"it kills".

### 6. Validate the full team
- `validate_team` on all 6. Fix **every** violation and re-validate.
- **A mode only exists if a teammate sets it.** If any member depends on Trick
  Room, Tailwind, weather or terrain, someone on the team must actually provide
  it — a Trick Room core with no Trick Room setter is not a team with a risk, it
  is a team that does not function. The same goes for a Swift Swim body with no
  rain, or Rising Voltage with no Electric Terrain. `analyze_team` reports these
  as `dependency_gaps`.
- **Treat an unmet dependency as a bug, not a caveat.** "Assumes Trick Room is
  already up", "relies on Trick Room (implicit)", or "consider adding a setter if
  needed" are not acceptable outputs — they describe a broken build in confident
  language. Add the enabler (or change the members), re-run `analyze_team`, and
  only then present the team. If you genuinely cannot close the gap, say the
  concept does not work and propose a different one; never hand over a team whose
  own gameplan is unavailable to it.
- Redundancy matters for the mode itself: speed control that lives on exactly one
  Pokémon dies with that Pokémon. In VGC especially, a second setter or a backup
  mode is normal.

### 7. Analyze, finalize, export
- `analyze_team(format)` → meta coverage + top unaddressed threats.
- `create_pokepaste` for an importable paste. **It rejects incomplete teams**:
  every member needs species, item, ability, nature, four moves and a full 66-SP
  spread. An incomplete set still renders as a valid-looking Showdown block, so
  the gap is invisible to whoever imports it and silently becomes an in-game
  default — fill the gaps and call again rather than presenting a partial team
  as finished.
- Spreads may be passed as `stat_points` or `evs` (the key is accepted either
  way), but the **values are always Stat Points** — 66 total, 32 per stat,
  1 SP = +1. An EV-scale spread (252/510) is rejected, not rescaled.

## Role skeletons

Start from one of these, adapt to the concept, and check every slot's brief with
`get_role_description(role, format=…)`. One Pokémon can cover two roles — six
slots do not mean six distinct roles — but every role in the skeleton must be
covered by *someone*.

**VGC (doubles, bring 4)** — the format is decided by speed control and by who
gets to move at all:

1. **Speed control** — `tailwind-setter` *or* `trick-room-setter` *or*
   `speed-drop-support`. Non-negotiable. Strong teams often carry two modes.
2. **Turn denial** — `fake-out-user` and/or `redirection-support`. This is how
   your attackers actually get their click off.
3. **Primary attacker** — `spread-attacker`, `physical-wallbreaker` or
   `special-wallbreaker`, matched to your speed mode (a Trick Room team wants
   `trick-room-abuser`s, not fast breakers).
4. **Secondary attacker** — different offensive type and different defensive
   profile from the first, so one wall can't answer both.
5. **Bulky anchor** — `bulky-attacker` or `intimidate-support`; survives the
   double-target and keeps the board stable.
6. **Flex** — the answer to whatever `analyze_team` flags: an opposing speed
   mode (`disruptor` with Taunt), spread offence (`field-protection-support`
   with Wide Guard), or a specific top-usage threat.

**BSS (singles, bring 6, pick 3)** — do **not** build one team of six. You only
ever field three, chosen at team preview against what you can see of their six,
so build **two or three overlapping 3-cores** and treat the six as the union of
them. A "balanced team of 6" that only works when all six are present is a team
that never actually shows up.

Each 3-core must independently satisfy:

1. **A real lead.** The Pokémon you are happy to send out blind on turn one.
2. **A win condition.** The thing that actually closes games in that core.
3. **An answer to the core's own weakness** — whatever beats slots 1 and 2.

Then check the union: overlapping members are good (they earn their slot in
several matchups), but if a Pokémon appears in no viable core, it is a wasted
slot. Aim for cores that answer *different* matchups — a fast-offence core and a
bulkier core, so team preview is a real choice rather than an illusion.

**What makes a real lead** (a strong lead is the most useful Pokémon on turn
one, not the strongest overall). It should do at least one of these on turn one,
without needing information it doesn't have yet:
- **Threaten immediately** — force a switch or a bad trade with strong neutral
  coverage against the format's common openers.
- **Generate momentum** — pivot out (`offensive-pivot`) so your real answer
  enters for free.
- **Establish the mode** — screens, weather, terrain or Trick Room, if your core
  is built on it.

It also needs to be **hard to lose to on turn one**: enough Speed to act first
(or priority instead), and either bulk or a Focus Sash so a bad matchup costs a
switch rather than a Pokémon. Note that bring-3 makes hazard leads much weaker
here than in 6v6 — with only two Pokémon behind, a turn spent on Stealth Rock is
usually better spent attacking. Never build a core whose only lead is something
you would rather not reveal.

Fill the remaining slots from the roles that fit the core's plan — typically
`physical-`/`special-wallbreaker`, a setup sweeper as the win condition,
`revenge-killer` or `priority-attacker` as insurance against opposing setup, and
a `defensive-pivot`/`tank` covering the side your core is soft to.

## Creative team building

The top-6 usage list is the most predictable team in the format — your opponent
has prepared for exactly those Pokémon. A distinctive team is usually a *better*
team, provided every pick is earned. Creativity belongs in the **concept and the
picks**; it never belongs in the **facts**.

Where to be creative:
- **The gameplan.** A clear, unusual mode (a specific weather, a Trick Room
  matchup flip, a two-Mega team with different win conditions per matchup) beats
  six individually-strong Pokémon with no plan.
- **Who fills a role.** Roles are fixed by the format; the *filler* is not.
  `get_usage_stats(format, order="bottom")` ranks every legal Pokémon with no
  cutoff — intersect it with `search_pokemon_by_move("<the role's signature move>")` to
  find real off-meta candidates rather than guessing which are underrated.
- **The angle on a known threat.** An unexpected answer to a top-usage Pokémon
  is worth more than a copied one, because it isn't played around.
- **The contested slots.** `get_observed_sets` marks every decision `locked`,
  `contested` or `fringe`. The contested ones are not unsolved problems — they
  are slots where the right answer genuinely depends on the rest of the team, so
  they are the intended place to express a plan. Its `free_slots` count says how
  many move slots are actually yours once the mandatory ones are spent, and
  `effective_distinct_sets` says how settled the Pokémon is overall: a low
  number means everyone brings the same thing and a deviation will surprise
  people; a high number means the species is already a toolbox.
- **The under-explored partner.** `get_team_context` returns
  `underexplored_partners` — Pokémon with high `affinity` for your exact core
  but low overall usage. These are the strongest creative leads available,
  because they are simultaneously *attested* (they really did appear with this
  core) and *unexpected*.

Creativity has to survive contact with the numbers, though. A deviation is
earned when `calc_damage` or `compute_speed_threshold` shows it hits a benchmark
the standard build misses — not merely because it is unusual. "Nobody plays
this" is a reason to check carefully, not a reason on its own. And an empty
result from the observed corpus means **untested**, never **refuted**: sampling
covers tournaments and the top of the ladder, not everything that works.

Every unconventional pick must clear the same four gates as an obvious one
(`get_role_description` returns them as `creative_pick_gate`):

1. **Legal** — it really learns the move / has the ability *in Champions*
   (`get_pokemon_moves`, `is_legal_move`).
2. **Signature** — its base stats and typing match the role's `look_for`
   (`get_pokemon`).
3. **Benchmark** — it hits the number the role lives on: the Speed tier
   (`compute_speed_threshold`) or the KO/survival roll (`calc_damage`). Most off-meta
   picks fail here — check it before falling in love with the idea.
4. **Relevance** — it does something concrete about a real top-usage threat.

A pick that clears all four is legitimate no matter how low its usage. A pick
that fails one is not creative, it is just worse than the obvious choice — say so
and move on. **Never bend a fact to rescue a fun idea**: if the tools don't
support it, the idea is dead, and reporting that honestly is the correct answer.

## Champions pitfalls (check, don't trust memory)

- **Moves**: SV learnsets are wrong here. Confirm via `get_pokemon_moves`.
  Notably absent: Teleport, Aromatherapy, Court Change, Spore, Tail Glow.
- **Items**: call `list_items` once — if an item is not in it, it does not exist
  here. The Champions item pool is much smaller than other games'.
  **Choice Scarf is the only Choice item** — there is no Choice Band or Specs —
  and there is no Assault Vest, Heavy-Duty Boots, Covert Cloak or Safety
  Goggles. Bulk and power must come from Stat Points, typing and abilities
  instead. Confirm with `search_items` before planning around any item.
- **Stats**: Stat Points (66/32, 1 SP = +1), not 252-EV math.
- **Protect is a doubles move.** In VGC it is near-mandatory (250 of 268 species
  run it, many above 90%). In BSS it is usually a wasted turn — only 48 of 268
  species run it at all, 15 of the top 60. With one Pokémon per side it buys no
  partner action, and a wasted turn in a 3-Pokémon game is free setup for the
  opponent. Put it on a BSS set only for a concrete reason: banking a Speed
  Boost-style accumulating turn, scaling passive damage behind reliable
  recovery, scouting a Choice lock, or triggering a turn-based item. `analyze_team`
  flags it on singles teams — justify each one or cut it.
- **Champions Speed is on its own scale** (roster 40-162, median ~98), so Speed
  numbers remembered from other games mean nothing. `get_pokemon` returns
  `speed_context` with the roster percentile: a species in the slowest ~10% is a
  Trick Room piece (setter or abuser) unless it is slow *on purpose* — a slow
  pivot, Baton Passer, Gyro Ball user, or a wall that never races. Slow with no
  plan just means moving second every turn; invest in bulk, not in Speed it
  cannot win with.
- **Regional forms**: share a name with the base and differ by typing — request
  them explicitly (e.g. "Alolan Ninetales") and trust the tool's typing.
- **Megas**: some are Champions-original; capped at one active per battle.
  `calc_damage` auto-Mega-evolves from the held stone.
- **Format mixing**: doubles has spread damage ×0.75 and Tailwind as a core
  mechanic; singles does not. Keep `game_type` / `format` matched to the session.

## Tool quick map

| Need | Tool(s) |
|---|---|
| Rules / format / SP system / data season | `get_game_rules(format=…)` |
| Meta: usage ranking | `get_usage_stats(format=…)` |
| **Complete builds real players brought** | `get_observed_sets(name, format=…, item=…, with_teammate=…)` |
| Partners for your whole core (+ affinity) | `get_team_context(team=[…], format=…)` |
| Whole teams with provenance | `get_sample_teams(format=…, pokemon=…)` |
| Meta: how a Pokémon is played (marginal) | `get_pokemon_sets(name, format=…)` |
| Meta: synergy / cores | `suggest_cores(format=…)`, `get_top_teammates(name, format=…)` |
| Meta: speed benchmarks | `get_speed_tiers(format=…)` |
| What a team slot is *for* (per format) | `list_roles(format=…)`, `get_role_description(role, format=…)` |
| Find candidates by typing/stat profile | `search_pokemon_by_profile(tags=[…])`, `list_profile_tags()`, `list_type_profiles()` |
| Legal picks | `is_legal_pokemon`, `list_pokemon`, `search_pokemon_by_type`, `search_pokemon_by_ability`, `search_pokemon_by_move` |
| Species data & moves | `get_pokemon`, `get_pokemon_moves`, `is_legal_move`, `get_move`, `get_type_matchups`, `get_pokemon_weaknesses` |
| Items / abilities | `get_item`, `search_items`, `get_ability` |
| Stat Points & speed | `calc_stats`, `compute_speed`, `compute_speed_threshold` |
| Damage (required before finalising a spread) | `calc_damage(game_type='doubles'|'singles')` |
| Validation & analysis | `validate_team`, `analyze_team(format=…)` |
| Export | `create_pokepaste` |

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

## Poképaste
<link from create_pokepaste>
```

Keep set rationale tied to the data you pulled (usage %, the calc result, the
speed benchmark) — that's what makes the team meta-accurate rather than a guess.
