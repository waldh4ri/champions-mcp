# Workflow

### 0. Establish format
- If unspecified, ask **VGC** (doubles) or **BSS** (singles).
- Call `get_game_rules(format=…)`. Read `session_tool_guide`, the SP budget
  (66/32), Item/Species Clause, Mega cap, bring count, and the active data
  season.

### 1. Read the meta
- **`get_threat_profile(format)` first** → the top threats with the form actually
  played (Megas resolved), typing, ability, Speed tier, weaknesses, and the
  moves/items the field runs on them, plus which attacking types hit the most
  threats. This is the threat landscape in one call; never fill these facts in
  from memory.
- `get_usage_stats(format)` → the wider ranking (each row already carries the
  played form's `types`, `abilities` and `base_speed`).
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
- Also note which **modes** the field runs: how many top teams carry Tailwind,
  Trick Room, each weather and each terrain. Read it from `get_sample_teams` and
  `get_observed_sets` move/ability data, not from memory. The team needs a
  plan against each common mode, not just against each common Pokémon.

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
- **Read `shared_weaknesses` in `analyze_team`** (lens 4) and fix every
  `flagged` type, and **check every pair for anti-synergy** (lens 3) —
  `analyze_team` does not check anti-synergy for you.

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
