---
name: champions-team-building
description: >
  Build a competitive, meta-accurate Pokémon Champions team (VGC doubles or
  BSS singles) end-to-end using the champions-mcp MCP server (real roster,
  movepools, items, Stat Points, usage stats, tournament data, speed/damage
  calc). Use this whenever the user wants to build, design, theorycraft,
  improve, fix, or get suggestions for a Pokémon Champions team, core, lead,
  or set — including "fun gimmick" teams, regulation-legal checks,
  Stat-Point/EV spreads, speed tiers, or damage calcs for Champions. Trigger
  even if the user doesn't say "team builder" (e.g. "make me a Champions
  doubles team", "build me a BSS team", "is this Champions team legal",
  "what beats Sneasler in Champions", "help me spread my SP"). Do NOT use for
  Scarlet/Violet or other-game VGC/BSS unless they ask about Champions
  specifically.
---

# Champions Team Building

## Why this skill exists

Pokémon Champions is **not** Scarlet/Violet. Movepools were heavily rebalanced
(moves added/removed, PP standardized, blanket bans), the item pool is a curated
subset (no Life Orb, Assault Vest, Choice Specs…), stats use **Stat Points**
(66 total, 32/stat, 1 SP = +1 stat) instead of EVs, there are
Champions-original Megas, and regional forms share names with their base. A
model's pre-Champions intuition is therefore unreliable. The `champions-mcp`
server is the source of truth. **Build from the tools, not from memory.**
"Meta-accurate" means the team is derived from real usage and tournament data,
not from generic Pokémon knowledge.

## Prerequisite

This skill requires the `champions-mcp` MCP server. Its tools are namespaced
`mcp__champions__*` (referred to below by short name, e.g. `get_game_rules_vgc`).
If those tools are not available, tell the user the server isn't connected and
stop — do not fall back to memory/Scarlet-Violet assumptions.

### Connecting via docker-compose (recommended)

Run the full stack from `champions-mcp/`:

```bash
docker compose up --build
```

The entrypoint prints the ready URL on startup:

```
MCP URL : http://localhost:8000/mcp
```

Register it in VS Code `settings.json`:

```json
"mcp": {
  "servers": {
    "champions-mcp": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Or in Claude Code / any MCP-HTTP client pointing at `http://localhost:8000/mcp`.

### Connecting via stdio (local dev)

```bash
pip install -e ".[dev]"
champions-mcp          # stdio, for MCP clients that spawn processes
```

## Core principles

- **Establish format first.** Ask the user whether they want **VGC** (doubles,
  bring 4) or **BSS** (singles, bring 3) before doing anything else. Then call
  `get_game_rules_vgc` OR `get_game_rules_bss` — never both, never the wrong
  one. The format you establish governs every tool call for the rest of the
  session.
- **Never mix formats.** VGC meta, VGC cores, and VGC damage calcs must not
  touch a BSS session, and vice-versa. The `session_tool_guide` returned by the
  rules tool tells you exactly what arguments to pass and which tools are
  unavailable for the chosen format.
- **Use meta to map threats, not to select Pokémon.** Meta data from
  `get_usage_stats`, `get_top_teams`, and `suggest_cores` (VGC only) tells you
  what the team must answer offensively and defensively — not which Pokémon
  to pick. Any coherent strategy is valid as long as it handles the real threat
  landscape.
- **Verify, don't assume.** Every species, move, and item must be checked with
  the tools. Champions silently differs from SV in ways memory will get wrong.
- **Validate early and often.** `validate_team` is the gate. Run it as soon as
  you have a rough 6 and after every change; fix every violation before moving
  on.
- **Champions-native math only.** Use `speed_threshold`/`compute_speed`/
  `calc_stats` (Stat Points, not EVs) and `calc_damage` (Champions calc). Don't
  hand-compute stats or reuse SV damage figures. `calc_stats` and `calc_damage`
  form an **iteration loop**: do not declare a spread final without running
  `calc_damage` to confirm every KO/survival benchmark.

## Workflow

Work through these phases in order. Loop back as needed, but never skip
format selection or final validation.

### 0. Establish format
- If the user has not specified **VGC** (doubles) or **BSS** (singles), ask
  before doing anything else.
- Call `get_game_rules_vgc` **or** `get_game_rules_bss` — never both.
- Read the `session_tool_guide` in the response. It lists:
  - Exactly which format string to pass to `get_usage_stats`,
    `get_smogon_analysis`, and `analyze_team`.
  - The correct `game_type` for `calc_damage` (`"doubles"` or `"singles"`).
  - Which tools are unavailable (BSS: `suggest_cores`, `get_top_teams`,
    `search_tournaments`, `get_tournament_standings`).
- Note bring count (4 for VGC, 3 for BSS), SP budget (66/32), item clause,
  species clause, Mega cap, and the legal item catalog.

### 1. Read the meta
- `get_usage_stats` (pass the session format) → top Pokémon by usage.
  These define the **threat landscape** — what the team must answer.
  Do **not** treat them as a template.
- **VGC only**: `get_top_teams` and/or `suggest_cores` → real tournament-winning
  cores. Use for inspiration and synergy awareness; off-meta strategies are
  equally valid.
- Summarize the 4–6 biggest threats; damage/speed-check against these later.

### 2. Choose a legal core
- Start from the user's concept or a clear gameplan (Trick Room, weather,
  hyper offense, stall, gimmick…).
- Validate every candidate with `is_legal_pokemon`.
- Fill role/type gaps from the **legal pool only**: `pokemons_by_type`,
  `list_legal_pokemon`. Aim for a coherent gameplan.

### 3. Build each set from real data
For every team member:
- `get_pokemon` → real base stats, abilities, typing.
- `get_pokemon_moves` / `is_legal_move` → choose moves from the Champions
  movepool. Never assume a move exists because it did in SV.
- Pick an item from the **legal catalog**. Respect the **item clause**.
- Choose an ability and Mega Stone if applicable (one active Mega per battle).

### 4. Spread + damage loop (iterate — spread is NOT final until benchmarks pass)
- Use `speed_threshold` to set Speed SP + nature vs key meta targets.
  For VGC, account for Tailwind / Choice Scarf. For BSS, account for Scarf.
- Use `calc_stats` to compute Lv 50 stats for the **candidate** spread;
  `compute_speed` to sanity-check Speed in-battle.
- Use `calc_damage` with the **correct `game_type`** from the session
  (doubles for VGC, singles for BSS). Set weather, ability, item, Mega,
  and SP spreads on both sides.
- **If benchmarks fail, adjust SP allocation and repeat `calc_stats` →
  `calc_damage` until every KO/survival target is met.**
  A spread is not final until `calc_damage` confirms the benchmarks.
- Verify the final spread with `validate_ev_spread` (66 total, ≤32/stat).

### 5. Validate the full team
- `validate_team` on all 6. Fix **every** violation and re-validate until clean.

### 6. Analyze, finalize, export
- `analyze_team` (pass the session format) → meta coverage and top unaddressed
  threats. Close real gaps; don't paper over them.
- `create_pokepaste` to give the user an importable paste.

## Strategic frameworks

These frameworks structure how to think about team construction for each format.
All item and move references below describe **Champions-legal** options only.
Always verify any specific move with `is_legal_move` or `get_pokemon_moves`
before committing to it — Champions movesets differ from Scarlet/Violet.

### VGC — Simultaneous Interaction Framework

VGC is played 2v2 on the field. Build around **Field States and Cores**, not
six independent slots.

```
[RULES] → [PRIMARY CORE] → [SPEED CONTROL] → [POSITIONING PIVOT] → [MITIGATION AUDIT] → [ROSTER]
```

**Step 1 — Primary Core (Rule of 2)**
Identify two Pokémon whose abilities or typings multiply each other's
effectiveness: weather setter + beneficiary, Trick Room setter + slow
powerhouse, Tailwind setter + fast sweeper, or a Mega + its enabler.
**The Mega Stone slot is always a team-building anchor** — decide which Pokémon
will Mega Evolve first, because only one may do so per battle. Build the lead
pair around unlocking that Mega safely.

**Step 2 — Speed Control Matrix (≥2 distinct options)**
Never rely on raw stats alone. The team must have dynamic speed control:
- *Aggressive*: Tailwind setter (doubles Speed for 4 turns) — verify with
  `is_legal_move`.
- *Reactive*: Trick Room option, a Choice Scarf holder, or a priority-move
  abuser to control the board against hyper-offense. Use `speed_threshold` to
  map the relevant speed tiers.

**Step 3 — Positioning Pivot (≥1 enabler)**
At least one Pokémon must manipulate the board without dealing damage:
- **Fake Out** — burns an opponent's turn; verify availability with
  `is_legal_move`.
- **Intimidate** — lowers physical output of both opponents on switch-in.
- **Follow Me / Rage Powder** — redirects lethal attacks to a bulky pivot;
  verify with `is_legal_move`.
- **Parting Shot / U-turn** — safe switches that preserve momentum.

**Step 4 — Mitigation Audit**
Cross-examine the 4 active slots (not just the 6 in hand):
- Non-Choice-item attackers should carry **Protect** or **Detect** to scout
  leads and burn enemy Tailwind turns.
- Distribute defensive items across the roster. Legal Champions options include:
  Focus Sash, Sitrus Berry, Lum Berry, Shell Bell, White Herb, Mental Herb,
  Focus Band, Scope Lens, King's Rock, Bright Powder, Choice Scarf, Leftovers.
  Respect the item clause — no duplicates across the 6.

---

### BSS — Autonomous 1v1 Pivot Framework

BSS is 6-in-hand, bring 3. Synergy matters less than **individual matchup
versatility**. Each Pokémon must be self-sufficient enough that any bring-3
combination is functional.

```
[RULES] → [LEAD / FIELD SETTER] → [DEFENSIVE PIVOT] → [DUAL WIN-CONDITIONS] → [REVENGE KILLER] → [ROSTER]
```

**Step 1 — Lead / Field-State Setter**
Slot 1 must be an anti-lead capable of taking an initial blow and dictating the
opening turn:
- Verify whether entry hazards (Stealth Rock, Spikes) are available with
  `is_legal_move` — Champions movesets differ from SV. If they are, a Focus
  Sash setter can force chip damage or deny setups with Taunt.
- If hazards are unavailable, the lead should still threaten disruption (Taunt,
  status, priority) rather than raw damage.

**Step 2 — Defensive Pivot Backbone**
A resilient anchor with type immunities (Ground, Ghost, or Fairy coverage) to
absorb unfavorable lead matchups and switch safely. Champion-legal defensive
items: Sitrus Berry, Lum Berry, Leftovers, Shell Bell, Focus Band. This slot
is a natural candidate for the team's **Mega** if the Mega has high defensive
utility.

**Step 3 — Dual Win-Conditions (1 Physical + 1 Special)**
The two sweeping threats must be **autonomous** — no teammate support required
to break holes. Each should run an independent setup move (e.g., Swords Dance,
Dragon Dance, Calm Mind, Nasty Plot — verify each with `is_legal_move`). This
ensures that any bring-3 combination of lead + one sweeper is functional.
A **Mega sweeper** counts as one of these win-conditions; its pre-Mega form
should be threatening enough that opponents cannot safely ignore it before
the Mega trigger.

**Step 4 — Revenge Killer**
A Choice Scarf holder or priority-move user that cleans up weakened targets
and covers speed tiers the rest of the team cannot reach. Use
`speed_threshold` to confirm it outspeeds the relevant tier.

**Item Clause Buffer**
Because BSS leans on Choice Scarf and Focus Sash as staples, ensure no item
duplication across the 6. Spread secondary items from the Champions catalog:
Focus Band, Scope Lens, King's Rock, Bright Powder, White Herb, Mental Herb,
Sitrus Berry, Lum Berry, Shell Bell, Leftovers, Light Ball.
**Not in Champions**: Life Orb, Assault Vest, Rocky Helmet, Choice Band,
Choice Specs — do not suggest these.

---

## Champions pitfalls (check, don't trust memory)

- **Moves**: SV learnsets are wrong here. Always confirm via `get_pokemon_moves`.
- **Items**: many staples don’t exist (Life Orb, Assault Vest, Choice Specs/
  Band, Rocky Helmet, Eviolite, boots…). Only use items in the catalog.
- **Stats**: it’s Stat Points (66/32, 1 SP = +1), not 252-EV math.
- **Regional forms**: they share a name with the base and differ by typing —
  request them explicitly and trust `is_legal_pokemon`’s typing.
- **Megas**: some are Champions-original; capped at one active per battle.
- **Format mixing**: doubles has spread damage ×0.75 and Tailwind as a core
  mechanic; singles does not. The `game_type` in `calc_damage` and the
  `format` arg in meta tools **must** match the session format. Never use VGC
  threats or cores to evaluate a BSS team, and vice-versa.
- **Limitless tools** (`get_top_teams`, `suggest_cores`, `search_tournaments`,
  `get_tournament_standings`) are VGC-only. Do not call them in a BSS session.

## Tool quick map

| Need | Tool(s) |
|---|---|
| Rules / format / SP / item catalog | `get_game_rules_vgc` (doubles) **or** `get_game_rules_bss` (singles), `get_regulation`, `list_regulations` |
| Meta: usage stats | `get_usage_stats` (pass `format='vgc'` or `'bss'`) |
| Meta: VGC tournament data | `get_top_teams`, `suggest_cores`, `search_tournaments`, `get_tournament_standings` (**VGC only**) |
| Smogon analysis | `get_smogon_analysis` (pass `format='vgc'` or `'bss'`) |
| Legal picks | `is_legal_pokemon`, `list_legal_pokemon`, `pokemons_by_type`, `get_champions_roster` |
| Species data & moves | `get_pokemon`, `get_pokemon_moves`, `is_legal_move`, `get_move`, `get_type_matchups` |
| Items | `get_item` (legality via catalog in game rules) |
| Stat Points & speed | `validate_ev_spread`, `calc_stats`, `compute_speed`, `speed_threshold` |
| Damage (part of spread loop — **required** before finalising spread) | `calc_damage` (pass `game_type='doubles'` for VGC, `'singles'` for BSS) |
| Validation & analysis | `validate_team`, `analyze_team` (pass session `format`) |
| Export | `create_pokepaste` |


## Output

Present the final team as:

```
# <Team name / concept> — <Regulation>, <format>

<Gameplan: 1–3 sentences — the mode and how it wins>

## Team
### <Pokémon> @ <Item>  —  <Ability>  —  Tera/Mega: <if any>
- Nature: <nature>  |  Stat Points: <hp/atk/def/spa/spd/spe> (total/66)
- Moves: <m1> / <m2> / <m3> / <m4>
- Role: <one line>  |  Key calcs/speed: <e.g. out-speeds X; OHKOs Y>
(repeat for all 6)

## Meta coverage
<which top threats this beats and how; remaining risks + how to play them>

## Poképaste
<link from create_pokepaste>
```

Keep set rationale tied to the data you pulled (usage %, the calc result, the
speed benchmark) — that's what makes the team meta-accurate rather than a guess.
