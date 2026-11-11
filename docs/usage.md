# Usage

[Documentation index](README.md) · [Tool reference](tools.md)

## Ask your assistant

After [connecting the server](getting-started.md#connect-an-mcp-client), use
ordinary language:

- “Build a Pokémon Champions VGC team around Garchomp.”
- “Make a Champions BSS team with a clear bring-three plan.”
- “Check this Champions team for illegal moves and shared weaknesses: …”
- “Show the top ten most-used Pokémon in Champions singles.”
- “Compare this team's current spread with a faster version.”

State the format and any preferred Pokémon, playstyle, or constraints. A team
can be legal but strategically weak; ask for analysis and testing, not just a
legality check.

## Start a session

The assistant should first call `get_game_rules` with a consistent format:

| Format | Battles | Team selection | Damage `game_type` |
|---|---|---|---|
| `vgc` | Doubles | Bring 4 of 6 | `doubles` |
| `bss` | Singles | Bring 3 of 6 | `singles` |

The response includes game rules, the Stat Point system, Mega rules, the data
season/build date, and observed-corpus availability. The format is an argument,
not persistent session state: pass it to later tools too.

Load the `champions_team_building` MCP prompt once if the client supports prompts.
Otherwise call `get_team_building_guide()` for the same core workflow and a list
of deeper sections. Fetch sections as needed, for example `workflow`, `roles`,
`creative`, `simulate`, or `iterate`.

## A typical team-building workflow

1. **Read the field:** `get_threat_profile` and, if needed, `get_usage_stats`.
2. **Choose a plan and roles:** browse `list_roles`, profile tags, and the legal
   roster instead of assuming another game's Pokémon or items exist here.
3. **Choose a core:** use `get_team_context`, `get_observed_sets`, and
   `get_sample_teams` for evidence from real teams.
4. **Build sets:** check `get_pokemon_moves`, `list_items`, and abilities before
   committing. Use usage distributions as evidence, not a ready-made set.
5. **Tune Stat Points:** use `compute_speed_threshold`, `calc_stats`, and
   `calc_damage` against the threats the team must handle.
6. **Check the team:** `validate_team` checks legality; `analyze_team` adds shared
   weaknesses, meta context and missing weather/terrain/speed dependencies.
7. **Test and revise:** `simulate_vs_meta` finds bad matchups;
   `compare_team_variants` tests a proposed change on matched opponents and seeds.
8. **Finish:** re-check the final team, explain brings/leads and weak matchups,
   then call `create_pokepaste` if a public paste is wanted.

The complete competitive workflow and output checklist live in the
[bundled skill](../skills/champions-team-building/SKILL.md).

## Team input

Validation, analysis and export accept a list of member objects. For example,
this is **one member**, not a complete six-Pokémon team:

```json
{
  "species": "Garchomp",
  "item": "Life Orb",
  "ability": "Rough Skin",
  "nature": "Jolly",
  "moves": ["Earthquake", "Dragon Claw", "Rock Slide", "Protect"],
  "stat_points": {"hp": 2, "attack": 32, "speed": 32}
}
```

Use the tools to verify sets against the installed roster; a documentation
example is not a current metagame recommendation. Canonical Stat Point keys are
`hp`, `attack`, `defense`, `special-attack`, `special-defense`, and `speed`.

Champions uses a **66-SP total budget**, at most **32 per stat**, not 510 EVs.
Listed `base_stats` are the level-50 values at 0 SP (31 IVs), not the classic
base stats used in other games. `calc_stats` adds SP and applies the nature.
For a finished exported set, allocate the full 66 points.

Many name lookups accept localized species names and form names. Be explicit
about regional forms and Megas; `get_pokemon("Mega Charizard X")` returns that
Mega's own data. Use the appropriate stone when specifying a team member.

## Read evidence correctly

- `get_pokemon_sets` contains **independent popularity distributions**. The top
  item, four moves and spread do not necessarily belong to the same set.
- `get_observed_sets` contains **joint observations** of sets. Check its
  `evidence` field: ladder exports may contain only species/items, while
  tournament sheets and resolved BSS reports contain moves, abilities and natures.
- `locked` choices are near-universal, `contested` choices vary among players,
  and `fringe` choices are rare but observed. None replaces matchup reasoning.
- `get_historical_sets` is a **cross-game fallback**, not Champions tournament
  evidence. Read its legality flags and design a real Champions SP spread.

See [data and evidence](data.md#evidence-and-limitations) for source coverage.

## Damage calculations

`calc_damage` needs the calculator image or HTTP sidecar. Always set
`game_type="doubles"` for VGC or `game_type="singles"` for BSS, plus the actual
items, abilities, natures, spreads and relevant field effects. For a Mega, use its
exact name or give the base species its Mega Stone.

The result includes rolls, HP percentages, KO information, a description, and an
`explain` block describing the defender's battle form, typing and effectiveness.
Read those before treating a surprising result as a bug.

## Battle simulation

Simulation uses Pokémon Showdown's Champions mod and its pinned Reg M-C rules,
with an AI policy playing both sides. It is a matchup diagnostic, **not a ladder
win-rate prediction**.

| Tool | Use it for |
|---|---|
| `simulate_vs_meta` | Test against real corpus teams; inspect worst matchups, member bring rates and threats. |
| `compare_team_variants` | A/B test full alternative teams against the same opponents and seeds. |
| `simulate_matchup` | Run many games between two specified teams. |
| `simulate_battle_log` | Inspect one game's turns to understand a result. |

Teams can be member lists, Showdown paste text, or an opponent `id` returned in
`worst_matchups` / `best_matchups`. That ID lets you replay the exact opponent.
Missing spread/nature/ability in member-list input is filled where possible from
usage and reported under `assumptions`; moves must be supplied. The simulator's
own validator rejects illegal teams before playing.

`information` controls what the AI knows:

- `open_sheets` (default): revealed sets, with usual spreads/natures assumed.
- `closed`: species and usage-based guesses until details are revealed.
- `omniscient`: exact sets, an upper bound.

Keep the `seed` fixed when comparing runs. Report confidence intervals, read
assumptions about opponent completion, and do not label an insignificant A/B
delta an improvement. The default per-call cap is 20,000 battles across all
opponents and variants. Detailed parameters are in the
[simulation reference](tools.md#battle-simulation).

## Export

`create_pokepaste` uploads to **pokepast.es**, so call it only when a public paste
is intended. Validate the team first. By default each member must include its
species, item, ability, nature, four moves and full 66-SP spread; incomplete
members are rejected without uploading. `force=True` is reserved for an explicitly
requested partial paste.

The tool returns the URL and paste text. SP are written on the Showdown-style
`EVs:` line; those numbers remain Champions Stat Points, not conventional EVs.

The MCP resource `pokedex://champions` exposes the complete roster as JSON when a
client needs an unpaginated catalogue.
