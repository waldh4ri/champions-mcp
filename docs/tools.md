# champions-mcp Tool Reference

`champions-mcp` exposes 42 MCP tools over the local `champions.sqlite` mirror
(built by `champions-mcp-pull` from pokechamp.gg + PokeAPI (type chart,
localized names), plus a corpus of complete teams from Limitless and
champs.pokedb.tokyo).
Two tools (`calc_damage`, `create_pokepaste`) make outbound calls; everything
else is a local read against the SQLite mirror.

Every tool also accepts localized Pokémon/move/item names where noted, and
most take an optional `format` argument, always `"vgc"` (doubles) or `"bss"`
(singles), since roles, usage data and legality can differ sharply between
the two.

> Start every session with [`get_game_rules`](#get_game_rules).

## Contents

- [Session](#session)
- [Pokédex](#pokédex)
- [Roster & Movepool](#roster--movepool)
- [Profiles](#profiles)
- [Roles](#roles)
- [Meta / Usage Stats](#meta--usage-stats)
- [Observed Builds](#observed-builds)
- [Stat Points & Speed](#stat-points--speed)
- [Team Analysis](#team-analysis)
- [Damage Calc](#damage-calc)
- [Export](#export)
- [Non-tool endpoints](#non-tool-endpoints)

## Quick reference

| Tool | Category | Summary |
|---|---|---|
| [`get_game_rules`](#get_game_rules) | Session | Format rules, Stat-Point system, Mega rules, tool guide; call first |
| [`get_team_building_guide`](#get_team_building_guide) | Session | The skill's core workflow, or one deeper section on demand |
| [`get_pokemon`](#get_pokemon) | Pokédex | Types, abilities, base stats, Mega forms |
| [`search_pokemon`](#search_pokemon) | Pokédex | Substring search over the roster |
| [`get_random_pokemon`](#get_random_pokemon) | Pokédex | One uniformly random roster Pokémon |
| [`list_megas`](#list_megas) | Pokédex | Every Mega Evolution: stone, typing/ability change, stat deltas |
| [`get_move`](#get_move) | Pokédex | Type, category, power, accuracy, priority, effect |
| [`search_moves`](#search_moves) | Pokédex | Search moves by name/type/category |
| [`get_item`](#get_item) | Pokédex | Item category, effect text, Mega-Stone flag |
| [`list_items`](#list_items) | Pokédex | The full Champions item pool, grouped by category |
| [`search_items`](#search_items) | Pokédex | Substring search of items |
| [`get_ability`](#get_ability) | Pokédex | Effect text for an ability |
| [`get_type_matchups`](#get_type_matchups) | Pokédex | Offensive/defensive effectiveness for a type or dual-type |
| [`get_pokemon_weaknesses`](#get_pokemon_weaknesses) | Pokédex | Full defensive type chart for a Pokémon or type pair |
| [`get_field_effects`](#get_field_effects) | Pokédex | What a weather/terrain/Trick Room/Tailwind/screen does |
| [`list_pokemon`](#list_pokemon) | Roster | Roster pick list, filterable, optionally usage-sorted |
| [`search_pokemon_by_type`](#search_pokemon_by_type) | Roster | Pokémon of a given type or exact type pair |
| [`search_pokemon_by_ability`](#search_pokemon_by_ability) | Roster | Pokémon with a given ability |
| [`search_pokemon_by_move`](#search_pokemon_by_move) | Roster | Pokémon that can learn a given move |
| [`get_pokemon_moves`](#get_pokemon_moves) | Roster | Full legal movepool + weakness-coverage suggestions |
| [`is_legal_move`](#is_legal_move) | Roster | Can this Pokémon use this move? |
| [`is_legal_pokemon`](#is_legal_pokemon) | Roster | Is this Pokémon in the current roster? |
| [`list_type_profiles`](#list_type_profiles) | Profiles | Rank every typing offensively/defensively |
| [`list_profile_tags`](#list_profile_tags) | Profiles | The profile-tag vocabulary |
| [`search_pokemon_by_profile`](#search_pokemon_by_profile) | Profiles | Find roster Pokémon by profile tag |
| [`list_roles`](#list_roles) | Roles | Competitive role vocabulary, graded per format |
| [`get_role_description`](#get_role_description) | Roles | Full brief on one role: signature, SP guidance, pairings |
| [`get_threat_profile`](#get_threat_profile) | Meta | Threat map in one call: played form, typing, Speed, weaknesses, moves/items |
| [`get_usage_stats`](#get_usage_stats) | Meta | Usage ranking, top or bottom, with the played form's typing/ability/Speed |
| [`get_pokemon_sets`](#get_pokemon_sets) | Meta | Popularity distributions: moves/items/spreads/teammates |
| [`get_top_teammates`](#get_top_teammates) | Meta | Most-paired Pokémon by usage |
| [`suggest_cores`](#suggest_cores) | Meta | Most frequent Pokémon pairs in the format |
| [`get_speed_tiers`](#get_speed_tiers) | Meta | Speed tiers for the most-used Pokémon |
| [`get_observed_sets`](#get_observed_sets) | Builds | Complete builds real players brought, conditionable by item/teammate |
| [`get_team_context`](#get_team_context) | Builds | Partners for your whole core, scored by affinity |
| [`get_sample_teams`](#get_sample_teams) | Builds | Whole teams with event, record and source |
| [`get_historical_sets`](#get_historical_sets) | Builds | Cross-game Smogon/Showdown sets, legality-flagged — fallback when the above is thin |
| [`calc_stats`](#calc_stats) | Stats | Final Lv-50 stats from an SP spread + nature |
| [`compute_speed`](#compute_speed) | Stats | Effective in-battle Speed with modifiers |
| [`compute_speed_threshold`](#compute_speed_threshold) | Stats | Minimum Speed SP to outspeed a target |
| [`validate_team`](#validate_team) | Team | Full legality report; call last |
| [`analyze_team`](#analyze_team) | Team | Legality + meta context + dependency gaps |
| [`calc_damage`](#calc_damage) | Damage | Damage rolls / %HP / KO chance via @smogon/calc |
| [`create_pokepaste`](#create_pokepaste) | Export | Upload a finished team to pokepast.es |

---

## Session

### `get_game_rules`

**Call first in any session.** Returns the format rules, the Champions
Stat-Point system, Mega rules, the active data season, and a tool guide. All
later tools take the same `format` argument this call establishes.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `format` | `str` | `"vgc"` | `"vgc"` (doubles, bring 4) or `"bss"` (singles, bring 3) |

### `get_team_building_guide`

The team-building skill, one piece at a time. With no argument it returns the
**core** (hard rules, the order of work with its commit points, the pre-flight
checklist, the output format) — the same text as the `champions_team_building`
prompt — plus the section index. Pass a section name to get that section:
`lenses`, `roles`, `workflow`, `principles`, `creative`, `pitfalls`,
`iterate`, `setup`. Meant to be called at the step that needs a section, so a
small model never has to hold the whole guide at once.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `section` | `str \| None` | `None` | Section name; omit for the core + index |

---

## Pokédex

### `get_pokemon`

Champions types, abilities, base stats and Mega forms for a Pokémon. Accepts
localized and form names. A Mega name (`"Mega Altaria"`, `"Mega Charizard X"`)
returns that Mega's own typing, ability, stats and stone, with `base_form`
alongside. An unknown name says it is not in the Champions roster and suggests
the closest roster names. Its `base_stats` are the Lv-50 stats at 0 SP (31 IVs), not classic
base stats; use
`calc_stats` for final stats from an SP spread.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name (localized or form names accepted) |

### `search_pokemon`

Substring search over the Champions roster. Returns `{slug, name}` pairs.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *(required)* | Name substring to search for |
| `limit` | `int` | `25` | Maximum results |

### `get_random_pokemon`

A uniformly random Pokémon from the current Champions roster, same shape as
`get_pokemon`. Useful for inspiration, random-team challenges, or breaking a
tie between candidates.

*No parameters.*

### `list_megas`

Species with a Mega Evolution in Champions: the Mega Stone that unlocks it,
typing/ability change, and stat deltas vs. the base form. Champions has 80+
Megas (growing every regulation), so omitting `query` returns a compact index
(stone, typing, ability, BST change); pass a substring for full base stats
and per-stat changes.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str \| None` | `None` | Species/Mega name substring; omit for the compact index of all Megas |
| `limit` | `int` | `100` | Maximum results |

### `get_move`

Type, category, power, accuracy, priority and effect text for a move.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Move name |

### `search_moves`

Search moves by name substring, type and/or category (physical/special/
status). `legal_only` restricts to moves learnable by some Champions Pokémon.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str \| None` | `None` | Name substring |
| `type` | `str \| None` | `None` | Move type filter |
| `category` | `str \| None` | `None` | `physical`, `special`, or `status` |
| `legal_only` | `bool` | `True` | Restrict to moves learnable by some Champions Pokémon |
| `limit` | `int` | `50` | Maximum results |

### `get_item`

Item category, effect text and Mega-Stone flag.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Item name |

### `list_items`

The full Champions item pool, grouped by category with effect text; call
this before assigning items rather than reaching for an item remembered from
another game. Champions' pool is much smaller: Choice Scarf is the only
Choice item, and there is no Assault Vest, Heavy-Duty Boots, Covert Cloak or
Safety Goggles. **If an item is not in this list, it does not exist here.**
Mega Stones (around half of the ~166-item pool) are hidden by default.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | `str \| None` | `None` | e.g. `berry`, `power-boost`, `stat-boost`, `effect-extend`, `recovery`, `defense`, `other` (pokechamp's own category labels) |
| `query` | `str \| None` | `None` | Name substring |
| `include_mega_stones` | `bool` | `False` | Include the Mega Stones |
| `limit` | `int` | `200` | Maximum results |

### `search_items`

Substring search of items by name, for when you know roughly what you want.
To browse everything that exists, use `list_items` instead.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str \| None` | `None` | Name substring |
| `limit` | `int` | `50` | Maximum results |

### `get_ability`

Effect text for an ability.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Ability name |

### `get_type_matchups`

Offensive and defensive effectiveness for a type or dual-type combo.
Offensive lists the best-case STAB coverage the typing reaches; defensive
stacks incoming multipliers across both types, grouping all 18 attacking
types by multiplier (4×/2×/1×/½×/¼×/0×).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `type1` | `str` | *(required)* | First (or only) type |
| `type2` | `str \| None` | `None` | Second type, for a dual-type combo |

### `get_pokemon_weaknesses`

Full defensive type chart for a Pokémon (Champions typing) or an explicit
type pair. Groups all 18 attacking types by multiplier. **Call this instead
of working the matchup out yourself**: dual-type multipliers stack into a
product and an immunity on either half wins outright.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str \| None` | `None` | Pokémon name (picks up its Champions typing) |
| `type1` | `str \| None` | `None` | Explicit first type, used if `name` is omitted |
| `type2` | `str \| None` | `None` | Explicit second type |

Both type-chart tools carry a `chart` line stating the Champions chart is
authoritative, so a model does not argue with a correct result from memory.

### `get_field_effects`

What a field effect actually does: sun, rain, sand, snow, the four terrains,
Trick Room, Tailwind and screens — modifiers, accuracy changes, Speed
abilities, setters, duration and extender item (from the Champions data), and
the `calc_damage` argument for it. The move/ability text in the data only says
"Summons snow for 5 turns", so without this a model recalls mechanics from
older games (snow is not hail: no chip damage, no Ice power boost).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str \| None` | `None` | Effect, setting move or ability (`"snow"`, `"Snowscape"`, `"Snow Warning"`); `"weather"`/`"terrain"` for a group; omit for all |

---

## Roster & Movepool

### `list_pokemon`

Roster pick list (every entry is legal this season). Filter by name
substring and/or a single type; pass a format to sort by usage rank. The
roster is 235 Pokémon, so this returns the first `limit` matches; narrow
with `query`/`type` rather than raising `limit` blindly. To rank by usage,
use `get_usage_stats` instead.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str \| None` | `None` | Name substring |
| `type` | `str \| None` | `None` | Single type filter |
| `format` | `str \| None` | `None` | `"vgc"`/`"bss"`; sorts results by usage rank |
| `limit` | `int` | `60` | Maximum results |

### `search_pokemon_by_type`

Champions Pokémon of a given type or exact type pair (Champions typing).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `type1` | `str` | *(required)* | First (or only) type |
| `type2` | `str \| None` | `None` | Second type, for an exact-pair match |

### `search_pokemon_by_ability`

Champions Pokémon with a given ability (checks Mega-form abilities too).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `ability` | `str` | *(required)* | Ability name |

### `search_pokemon_by_move`

Champions Pokémon that can learn a given move (Champions movepools).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `move` | `str` | *(required)* | Move name |

### `get_pokemon_moves`

**Call before assigning moves.** Full Champions-legal movepool for a Pokémon
(pokechamp.gg learnsets; SV/PokeAPI learnsets are wrong). Also returns
`coverage_for_weaknesses`: legal moves that hit back super-effectively
against each type this Pokémon is weak to: a consideration, not a rule.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |

### `is_legal_move`

Quick check: can this Pokémon use this move in Champions?

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |
| `move` | `str` | *(required)* | Move name |

### `is_legal_pokemon`

Quick check: is this Pokémon in the current Champions roster?

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |

---

## Profiles

### `list_type_profiles`

Rank every typing, all 18 single types and 153 pairs, by defensive and
offensive quality, each graded as a percentile against all the others. The
two axes are independent and often opposed.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sort` | `str` | `"defensive"` | `defensive`, `offensive`, `worst_defensive`, `worst_offensive`, or `balanced` |
| `type` | `str \| None` | `None` | Filter to typings containing this type |
| `dual_only` | `bool` | `False` | Drop the 18 single types, keep only pairs |
| `limit` | `int` | `30` | Maximum results |

### `list_profile_tags`

The profile-tag vocabulary, with each tag's meaning and how many roster
Pokémon carry it; read before searching so real tags are used. Four
namespaces: `def:`/`off:` (typing quality), `stat:` (base-stat percentile),
`build:` (archetype, e.g. `build:glass-cannon`, `build:trick-room`). All
percentiles are relative to the Champions roster.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | `str \| None` | `None` | Restrict to one namespace: `def`, `off`, `stat`, or `build` |

### `search_pokemon_by_profile`

Find roster Pokémon by profile tag, the fastest way to fill a role slot
from the whole legal pool instead of from memory (e.g.
`["build:physical-attacker","build:very-fast"]`). Call `list_profile_tags`
first; unknown tags are rejected rather than silently ignored. Mega forms
are searched as their own Pokémon, flagged `is_mega` with `mega_stone`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tags` | `list[str]` | *(required)* | Profile tags to match |
| `match` | `str` | `"all"` | `"all"` (every tag required) or `"any"` (union) |
| `format` | `str \| None` | `None` | Sort results by usage rank |
| `limit` | `int` | `40` | Maximum results |
| `include_megas` | `bool` | `True` | Include Mega forms as their own search hits |

---

## Roles

### `list_roles`

The competitive role vocabulary: what each slot on a team is *for*,
grouped by category with a viability grade per format. **Pass
`format="vgc"` or `"bss"`**: roles are not transferable between the two
(e.g. redirection is core in VGC and does nothing in BSS). With a format
set, unviable roles move to `excluded_for_this_format`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | `str \| None` | `None` | `offense`, `defense`, `pivot`, `speed_control`, `support`, `hazards`, or `lead` |
| `format` | `str \| None` | `None` | `"vgc"` or `"bss"` |
| `query` | `str \| None` | `None` | Name/alias substring |

### `get_role_description`

Full brief on one competitive role: what it does and why, the stat/typing
signature to look for, signature moves/items/abilities checked against
Champions data, Stat-Point guidance for the 66-SP budget, pairing roles, and
answering roles. `not_in_champions` items do not exist in this game. Accepts
an id, name or alias; ambiguous input returns candidates.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `role` | `str` | *(required)* | Role id, name, or alias (e.g. `"scarfer"`, `"follow me user"`) |
| `format` | `str \| None` | `None` | `"vgc"`/`"bss"` for an explicit verdict, including a warning if the role doesn't work there |

---

## Meta / Usage Stats

### `get_threat_profile`

**The threat map in one call.** For each top-usage Pokémon: the form actually
played (Megas resolved), typing, ability, Speed tier (neutral/positive max),
4x/2x weaknesses and immunities, and the top moves and items the field runs on
it. A `field_summary` ranks attacking types by how many threats they hit
super-effectively — with, per type, the threats weak to it, resisting it and
immune to it — and lists the STAB types the threats attack with.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `top` | `int` | `12` | Threats to profile (1–30) |

### `get_usage_stats`

Usage ranking for VGC or BSS. Each row carries `played_as` (the form actually
met — the Mega's name when `played_as_mega`) and that form's `types`,
`abilities` and `base_speed`.
`order="top"` returns the most-used Pokémon
first; `order="bottom"` ranks every legal Pokémon with no cutoff, the
correct way to find real off-meta/underdog picks.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `top` | `int` | `30` | Number of entries to return |
| `order` | `str` | `"top"` | `"top"` (most-used first) or `"bottom"` (least-used first) |
| `megas_only` | `bool` | `False` | Restrict ranking to species with a Mega Evolution |

### `get_pokemon_sets`

Evidence for how a Pokémon is played: most-common moves, items, abilities,
natures, SP spreads and teammates with usage %. **These are per-slot
popularity distributions, not ready-made sets**: the top four moves may
never have occurred together. Use it to see what's proven viable, then build
the set the team actually needs.

When the observed-team corpus covers this Pokémon, the response carries a
`joint_data_available` block pointing at
[`get_observed_sets`](#get_observed_sets), which answers the same question
jointly. Prefer it when committing to a set. This tool remains the only source
of **Stat Point spreads**, which neither observed-build upstream publishes.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |

### `get_top_teammates`

Pokémon most frequently brought alongside this one, by usage. For partners
conditioned on your *whole* core, with an affinity score and under-explored
options, use [`get_team_context`](#get_team_context).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |

### `suggest_cores`

Most frequent Pokémon pairs. The response's `basis` field says which
computation produced them:

- `observed_teams` — pairs that genuinely shared a team, reported as
  `percent_of_teams`. Used whenever the observed-team corpus is present.
- `usage_teammate_lists` — the fallback: how many per-Pokémon top-teammate
  lists two Pokémon both appear in. A weaker signal, and not a percentage of
  anything.

A popular core is a known quantity to your opponents too — read these as the
field you are building against as much as options to join.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `limit` | `int` | `15` | Maximum pairs to return |

### `get_speed_tiers`

Speed tiers (base + neutral-max + positive-nature-max Lv-50 Speed) for the
most-used Pokémon in the format. Species predominantly played as their Mega
are tiered on the **Mega's** Speed and flagged `as_mega`, since the base-form
tier is a benchmark never actually faced.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `limit` | `int` | `40` | Maximum entries |

---

## Observed Builds

The Meta tools above return **marginal** distributions — how often a Pokémon
runs each move, how often it holds each item — each computed independently.
They cannot say which moves accompany which item, so stacking their top picks
produces a build nobody plays.

These three read `build_teams`/`build_members`: complete six-Pokémon lists
people actually brought, which makes the joint question answerable.

**Evidence levels.** Every response carries an `evidence` field:

| Level | Source | Contains |
|---|---|---|
| `full_set` | Limitless tournament open team sheets (VGC) | species, item, ability, nature, 4 moves |
| `team_and_item` | champs.pokedb.tokyo ranked ladder (BSS) | species and held item only |

BSS therefore has **no observed movesets**. Item and teammate correlations are
real there; any moveset is your own inference and should be justified with
`get_pokemon_moves` + `calc_damage` rather than presented as observed. Neither
source carries Stat Points — those still come from `get_pokemon_sets`.

**Reading the bands.** Each decision (item, ability, nature, and every move) is
labelled:

- `locked` — ≥85% of builds agree. Deviating is a real cost; be able to name
  what it buys.
- `contested` — strong players genuinely disagree. This is design space, to be
  decided from your team's matchups rather than by a few points of popularity.
- `fringe` — <15%, but *attested*. Precedent for a sharp idea, not an error.

If the corpus has not been ingested, these tools return an explanatory `error`
rather than failing, and the rest of the server is unaffected.

### `get_observed_sets`

Complete builds this Pokémon was actually given, optionally conditioned on the
item it held or a teammate it appeared with. Also returns
`marginal_stacking_check` — the share of real builds running exactly the four
most individually popular moves, i.e. how wrong the naive build would be — and
`effective_distinct_sets`, the perplexity of the set distribution (1.0 means
everyone brings the same thing; higher means the species is a toolbox).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `item` | `str \| None` | `None` | Restrict to builds holding this item (English name works for both formats) |
| `with_teammate` | `str \| None` | `None` | Restrict to teams also containing this Pokémon |
| `limit` | `int` | `6` | Maximum distinct sets to return |

### `get_team_context`

Given the Pokémon already on your team, what real teams put alongside that
exact combination. `affinity` divides a partner's rate with *this* core by its
format-wide rate, separating "genuinely drawn to your structure" from "merely
popular". `underexplored_partners` lists high-affinity, low-usage options — the
cheapest place to differentiate. A core with no matches is reported as untried,
not refuted.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `team` | `list[str]` | *(required)* | Pokémon already chosen |
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `limit` | `int` | `12` | Maximum partner suggestions |

### `get_sample_teams`

Whole teams from the corpus with their event, record and source, deduplicated
so the same list entered at several events appears once. Read them for
structure, not as templates — copying a list also copies whatever it loses to.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `pokemon` | `str \| None` | `None` | Restrict to teams containing this Pokémon |
| `limit` | `int` | `3` | Maximum teams |

### `get_historical_sets`

Curated sets from Smogon/Pokémon Showdown — every generation and tier Smogon
has ever published, filtered only to this format's battle type (doubles for
`vgc`, singles for `bss`; the specific tier/generation inside that doesn't
matter). **Not Champions tournament data** — Champions predates all of it, has
a much smaller item pool, and a different movepool per species, so every set
carries `legal_in_champions` (overall) plus per-move/item/ability legality
flags. `invested_stats` names which stats the original EV spread favoured;
turn that into a real Stat Point spread with `calc_stats`/
`compute_speed_threshold` rather than converting the EV numbers directly —
Champions' 66-SP budget doesn't map 1:1 onto 510 EVs. `get_observed_sets`
surfaces this same data automatically as `historical_sets` when its own
(Champions-specific) corpus is thin or empty.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |
| `limit` | `int` | `8` | Maximum distinct sets to return |

---

## Stat Points & Speed

### `calc_stats`

Compute all six final Champions stats (Lv 50, 31 IVs) for an SP spread +
nature; validates the 66-total / 32-per-stat budget. Accepts localized
names and Mega names (a Mega uses its own stats). The listed stats every tool
returns are the Lv-50 values at 0 SP, so final stat = listed + SP, then nature
(HP: listed + SP) — the same numbers `calc_damage` uses.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |
| `stat_points` | `dict[str, int] \| None` | `None` | SP per stat, e.g. `{"attack": 32, "speed": 32}` (66-point budget, 32 max per stat) |
| `nature` | `str` | `"serious"` | Nature name |

### `compute_speed`

Effective in-battle Speed with modifiers applied in order: raw → stage →
Choice Scarf (×1.5) → Speed-doubling ability (×2) → Tailwind (×2) →
paralysis (×0.5), flooring each step. Accepts Mega names.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Pokémon name |
| `speed_sp` | `int` | `0` | Speed Stat Points invested |
| `nature` | `str` | `"serious"` | Nature name |
| `stage` | `int` | `0` | Speed stat-stage modifier (-6 to +6) |
| `choice_scarf` | `bool` | `False` | Apply the ×1.5 Choice Scarf modifier |
| `speed_ability` | `bool` | `False` | A Speed-doubling ability is active (Swift Swim, Chlorophyll, Sand Rush, Slush Rush, Surge Surfer) |
| `tailwind` | `bool` | `False` | Apply the ×2 Tailwind modifier |
| `paralyzed` | `bool` | `False` | Apply the ×0.5 paralysis modifier |

### `compute_speed_threshold`

Minimum Speed SP for `attacker` to outspeed `defender` (per nature), with SP
left from the 66 budget. Default attacker natures checked: neutral + Jolly.
Both sides accept Mega names — pass `"Mega Lucario Z"`, not `"Lucario"`, to
tier against the Mega.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `attacker` | `str` | *(required)* | Attacking Pokémon name |
| `defender` | `str` | *(required)* | Defending Pokémon name |
| `defender_speed_sp` | `int` | `0` | Defender's invested Speed SP |
| `defender_nature` | `str` | `"serious"` | Defender's nature |
| `attacker_natures` | `list[str] \| None` | `None` | Natures to solve for; defaults to `["serious", "jolly"]` |
| `tie_is_enough` | `bool` | `False` | Treat a Speed tie as sufficient (instead of requiring strictly faster) |
| `attacker_stage` | `int` | `0` | Attacker's Speed stat-stage |
| `attacker_choice_scarf` | `bool` | `False` | Attacker holds Choice Scarf |
| `attacker_tailwind` | `bool` | `False` | Attacker's side has Tailwind |
| `attacker_paralyzed` | `bool` | `False` | Attacker is paralyzed |
| `defender_stage` | `int` | `0` | Defender's Speed stat-stage |
| `defender_choice_scarf` | `bool` | `False` | Defender holds Choice Scarf |
| `defender_tailwind` | `bool` | `False` | Defender's side has Tailwind |
| `defender_paralyzed` | `bool` | `False` | Defender is paralyzed |
| `attacker_speed_ability` | `bool` | `False` | Attacker's Speed-doubling ability is active |
| `defender_speed_ability` | `bool` | `False` | Defender's Speed-doubling ability is active |

---

## Team Analysis

Both tools take a `team` as `list[dict[str, Any]]`, one dict per member:

```
{"species": str, "item"?: str, "ability"?: str, "moves"?: list[str],
 "nature"?: str, "stat_points"?: dict[str, int]}
```

### `validate_team`

**Call last.** Full legality report for a team: roster membership, legal
movepool, Item/Species Clause, SP budget and natures.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `team` | `list[dict[str, Any]]` | *(required)* | Team members (see shape above) |
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |

### `analyze_team`

Legality + meta context for a team: per-member battle typing (the Mega's when
it holds its stone)/BST/usage rank and the top meta threats not on the team.
`shared_weaknesses` lists every attacking type that hits 2+ members
super-effectively (typing plus listed abilities such as Levitate or Thick Fat),
with the members that resist it; `flagged` marks types hitting 3+ members.
For a BSS team carrying several Mega Stones, `mega_per_bring` restates that
only one Pokémon may Mega Evolve per battle, so no 3-core may depend on two.
Also reports `dependency_gaps`: members
whose plan needs a field effect nobody on the team provides. **Fix these
before presenting the team**: an unmet dependency is a broken build, not a
caveat. Singles teams also get `protect_in_singles`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `team` | `list[dict[str, Any]]` | *(required)* | Team members (see shape above) |
| `format` | `str` | `"vgc"` | `"vgc"` or `"bss"` |

---

## Damage Calc

### `calc_damage`

Champions damage calc via `@smogon/calc` (gen-0 native mode), Lv 50 / 31
IVs. For a Mega, pass the Mega Stone as the item or the Mega's name
(`"Mega Lucario Z"`); the server resolves the exact Mega forme itself,
including the Z-Megas. **Always set `game_type`.** A move that is not in
Champions is refused rather than reported as 0% damage. Requires the
`champions-calc` Docker image (or `CHAMPIONS_MCP_CALC_HTTP`). Returns rolls,
%HP, KO chance, a description, and `explain`: the move's type, the defender's
battle form, typing and ability, and the type effectiveness applied — read it
when a number surprises you instead of re-deriving the formula.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `attacker` | `str` | *(required)* | Attacking Pokémon name |
| `move` | `str` | *(required)* | Move used |
| `defender` | `str` | *(required)* | Defending Pokémon name |
| `attacker_ability` | `str \| None` | `None` | Attacker's ability |
| `attacker_item` | `str \| None` | `None` | Attacker's held item (Mega Stone for Mega forms) |
| `attacker_nature` | `str` | `"serious"` | Attacker's nature |
| `attacker_stat_points` | `dict[str, int] \| None` | `None` | Attacker's SP spread, e.g. `{"attack": 32, "speed": 32}` |
| `attacker_boosts` | `dict[str, int] \| None` | `None` | Attacker's active stat stages |
| `attacker_status` | `str \| None` | `None` | Attacker's status condition |
| `defender_ability` | `str \| None` | `None` | Defender's ability |
| `defender_item` | `str \| None` | `None` | Defender's held item (Mega Stone for Mega forms) |
| `defender_nature` | `str` | `"serious"` | Defender's nature |
| `defender_stat_points` | `dict[str, int] \| None` | `None` | Defender's SP spread |
| `defender_boosts` | `dict[str, int] \| None` | `None` | Defender's active stat stages |
| `defender_status` | `str \| None` | `None` | Defender's status condition |
| `weather` | `str \| None` | `None` | Active weather |
| `terrain` | `str \| None` | `None` | Active terrain |
| `game_type` | `str` | `"doubles"` | `"doubles"` (VGC) or `"singles"` (BSS); always set explicitly |
| `helping_hand` | `bool` | `False` | Attacker has Helping Hand support |
| `attacker_tailwind` | `bool` | `False` | Attacker's side has Tailwind |
| `crit` | `bool` | `False` | Force a critical hit |
| `move_hits` | `int \| None` | `None` | Number of hits, for multi-hit moves |

---

## Export

### `create_pokepaste`

Upload a finalised team to pokepast.es; returns `{"url", "paste"}`. Call
after `validate_team` passes. SP appear on the EVs line (1 SP ≠ 4 EVs;
budget 66). **Every member must be complete**: species, item, ability,
nature, four moves and a full 66-SP spread. Otherwise the request is
rejected with the per-member list of what's missing (nothing is published),
since an incomplete set still renders as a valid-looking Showdown block.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `team` | `list[dict[str, Any]]` | *(required)* | Team members (same shape as [Team Analysis](#team-analysis)); each must be complete unless `force=True` |
| `title` | `str` | `""` | Paste title |
| `author` | `str` | `""` | Paste author |
| `notes` | `str` | `""` | Paste notes |
| `force` | `bool` | `False` | Publish anyway even if members are incomplete; only when the user explicitly asked for a partial paste |

---

## Non-tool endpoints

These aren't MCP tools but are part of the same server:

| Name | Kind | Description |
|---|---|---|
| `champions_team_building` | Prompt | The team-building skill guide (rules, workflow, tool guide). Load once per session. |
| `pokedex://champions` | Resource | The whole roster as JSON (no limit, unlike `list_pokemon`). |
