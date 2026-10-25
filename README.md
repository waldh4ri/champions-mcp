<div align="center">

# champions-mcp

MCP server for AI-assisted Pokémon **Champions** team building (VGC doubles and BSS singles).

</div>

## What it does

Game data is pulled once into a local SQLite database (`champions.sqlite`):
[pokechamp.gg](https://pokechamp.gg) for the current legal roster, Champions
stats/types/abilities, movepools, moves/abilities/items reference text, and per-format
usage stats, and [PokeAPI](https://pokeapi.co) for the (game-invariant) type chart and
localized species names, cached permanently. Two further sources supply a corpus of
**complete teams people actually brought** — Limitless tournament teamlists for VGC and
the champs.pokedb.tokyo ranked-ladder export for BSS. A fifth source, Pokémon
Showdown's published sets (every generation/tier Smogon curates), is ingested as a
**historical-builds fallback** — cross-game evidence, legality-checked against
Champions at read time, surfaced automatically when the real observed-team corpus is
thin (e.g. right after a regulation rollover). Every tool call is then answered from
that local file, no live lookups, no scraping, no runtime juggling of any upstream.

The only outbound calls happen at two points, both opt-in:

- `champions-mcp-pull` fetches the roster/reference/usage data, runs the PokeAPI
  enrichment pass, ingests the Smogon/Showdown historical-sets pool, and ingests the
  observed-team corpus (`--skip-builds` to omit the last).
- `create_pokepaste` uploads a finished team to pokepast.es.

42 MCP tools cover the Pokedex, legal rosters and movepools, usage stats/sets/teammates,
observed builds and team context, historical (cross-game) sets, Stat Point and speed
math, team validation, damage calculation, and Pokepaste export. A bundled skill walks
an LLM through the whole team-building workflow end to end.

## Quick start

### Docker

Builds and starts both `champions-calc` (the damage sidecar) and `champions-mcp` (the
server), and runs the data pull automatically on first start, persisted in the
`champions-data` volume. Expect several minutes on the first build: the observed-team
stage paces itself against Limitless's unauthenticated rate limit (50 requests per
5 minutes). Later starts reuse the pull cache and fetch only genuinely new events.
Set `CHAMPIONS_MCP_BUILDS_SKIP=1` to skip that stage and get the ~30-second build.

```bash
docker compose up -d --build
```

```
MCP URL : http://localhost:8000/mcp
```

Force a data refresh with `CHAMPIONS_MCP_PULL=1 docker compose up -d`.

To stop and remove the containers:

```bash
docker compose down             # stop, keep champions.sqlite in the champions-data volume
docker compose down -v          # also delete the volume, next start does a full re-pull
```

### Local (pip, no Docker)

For development, or if you want to run the MCP server directly without Docker:

Requires Python 3.11+ and MCP SDK 2.x (`mcp>=2.0.0` — the 2.0 release replaced the
1.x `FastMCP` class with `MCPServer`, so a 1.x install will not run this server).

```bash
pip install -e ".[dev]"    # or pip install -r requirements.txt
champions-mcp-pull         # build data/champions.sqlite (downloads + enriches + observed teams)
champions-mcp-pull --skip-builds   # ...without the observed-team corpus (much faster)
champions-mcp              # run the MCP server (stdio by default)
pytest                     # offline tests against the built DB
```

### Optional: local LLM + web chat

Adds a self-contained chat UI on top of the same MCP server, backed by a local model
served through llama.cpp. No API key, no external client needed. A model is not shipped
with the project: put a GGUF file in `./models` first (or set `LLAMA_MODEL_HF` to
auto-download one from Hugging Face).

```bash
docker compose --profile ai up -d --build
```

Open **http://localhost:3000**. This starts two more services: `llama-server`
(llama.cpp, OpenAI-compatible API, GPU-backed) and `champions-chat`, a web UI that
opens an MCP session to `champions-mcp`, auto-loads every tool, and runs the
tool-calling loop for you. Requires an NVIDIA GPU. Everything (model file, context
size, GPU/CPU offload, sampling, and more) is configured through environment
variables, see [`.env.example`](.env.example) for the full list.

## Tools

Call `get_game_rules(format)` first (`format='vgc'` for doubles, `'bss'` for singles).

**Pokedex** - `get_pokemon`, `search_pokemon`, `get_random_pokemon`, `list_megas`,
`get_move`, `search_moves`, `list_items`, `get_item`, `search_items`, `get_ability`,
`get_type_matchups`, `get_pokemon_weaknesses`, `get_field_effects` (what a weather,
terrain, Trick Room, Tailwind or screen does). A Mega name passed to `get_pokemon`
returns the Mega itself. `list_items` returns the whole item
pool grouped by category (Mega Stones hidden by default - they are around half of
the ~166 items); if an item is not in it, it does not exist in Champions.

**Roster and movepool** - `list_pokemon`, `search_pokemon_by_type`, `search_pokemon_by_ability`,
`search_pokemon_by_move`, `get_pokemon_moves`, `is_legal_move`, `is_legal_pokemon`

**Meta, both formats** - `get_threat_profile` (the threat map in one call: played
form, typing, Speed tier, weaknesses, common moves/items), `get_usage_stats` (rows
carry the played form's typing/ability/Speed), `get_pokemon_sets`, `get_top_teammates`,
`suggest_cores`, `get_speed_tiers`

**Observed builds** - `get_observed_sets`, `get_team_context`, `get_sample_teams`.
The usage tools above return *marginal* distributions: how often a Pokémon runs each
move, and how often it holds each item, computed independently. They cannot tell you
which moves go with which item, so stacking their top picks yields a build nobody
plays. These three read a corpus of complete teams people actually brought, which
makes the conditional question answerable — a Choice Scarf Garchomp runs different
moves *and* different teammates than a Life Orb one. Every answer splits each
decision into `locked` (near-universal), `contested` (where strong players disagree —
your call, from your own matchups) and `fringe` (rare but attested), and
`marginal_stacking_check` reports how few real builds run the naive
"four most popular moves" set. `get_team_context` conditions partners on your whole
core and scores each by `affinity` — how much a partner prefers *your* core over
being merely popular — surfacing under-explored options alongside the obvious ones.

**Historical sets** - `get_historical_sets` — curated Smogon/Pokémon Showdown sets
(every generation/tier, filtered only to doubles vs. singles), used as a fallback for
when the real observed-team corpus above is thin or empty (e.g. right after a
regulation rollover). Not Champions tournament data: every set is checked against the
current Champions roster and carries `legal_in_champions` (overall, plus per-move/
item/ability) so a build that doesn't translate is visible rather than presented as
fact. `get_observed_sets` surfaces this automatically as `historical_sets` when its own
corpus is thin.

**Type & stat profiles** - `list_type_profiles`, `list_profile_tags`,
`search_pokemon_by_profile`. Every typing (18 singles + 153 pairs) is graded
defensively and offensively against all the others, and every Pokémon is tagged
from its typing plus its stat spread *relative to the Champions roster* (which
rescaled base stats, so absolute thresholds from other games are meaningless).
Search the whole legal pool by tag - `build:glass-cannon`, `build:trick-room`,
`def:fortress`, `off:elite-coverage`. `get_pokemon` returns each Pokémon's tags.

**Competitive roles** - `list_roles`, `get_role_description`. The role vocabulary
(wallbreaker, offensive pivot, redirection support, Trick Room setter, ...) graded
per format, since VGC and BSS need very different slots. Each brief's signature
moves, items and abilities are checked against the Champions data before being
returned.

**Stat Points and speed** - `calc_stats`, `compute_speed`, `compute_speed_threshold`

**Team** - `validate_team`, `analyze_team`

**Damage and export** - `calc_damage` (via the `champions-calc` sidecar),
`create_pokepaste` (uploads to pokepast.es, the only runtime outbound call besides the pull)

A team-building [`SKILL.md`](skills/champions-team-building/SKILL.md) ships with the repo
and is exposed as the `champions_team_building` MCP prompt. It is a short core (hard
rules, order of work with commit points, pre-flight, output format); the deeper
guidance lives in `skills/champions-team-building/sections/` and is served one
section at a time by the `get_team_building_guide(section)` tool. A `pokedex://champions` MCP
resource exposes the full roster as JSON.

## How the data pull works

`champions-mcp-pull` (`python -m champions_mcp.pull`):

1. **Fetch the roster** from [pokechamp.gg](https://pokechamp.gg): the current legal
   roster (species/forms + Mega/Z-Mega forms), full movepools, and the whole move/
   ability/item reference pool — a handful of whole-game requests, plus one page fetch
   per legal (non-Mega) roster entry for its per-format usage stats (moves, items,
   abilities, natures, SP spreads, teammates, usage rank).
2. **Derive Mega forms** (`mega_forms`) from the Mega/Z-Mega entries collected above:
   stats, ability, typing, and the Mega Stone that unlocks each.
3. **Enrich** from PokeAPI, cached permanently: the 18-type chart (game-invariant) and
   localized species names (used to match pokedb's Japanese-language BSS export).
4. **Ingest Smogon/Showdown historical sets** into `smogon_sets`: every generation and
   tier [Pokémon Showdown](https://play.pokemonshowdown.com) publishes, matched to the
   Champions roster by species name and split only into doubles/singles — a fallback
   pool for when the observed-team corpus below is thin.
5. **Ingest observed teams** into `build_teams`/`build_members`:
   - **VGC** from the [Limitless](https://play.limitlesstcg.com) public API
     (`/tournaments/{id}/standings`). These events enforce open team sheets, so each
     response carries every player's full list — species, item, ability, nature and all
     four moves. No API key needed; unauthenticated access is rate-limited to 50
     requests per 5 minutes, so the first run paces itself over several minutes.
   - **BSS** from the [champs.pokedb.tokyo](https://champs.pokedb.tokyo/guide/opendata)
     ranked-ladder export. Japan is where singles is actually played, and this is the
     top of the official ladder per season. It carries species and held item but **no
     moves**, so those rows are marked `team_and_item` and the tools never present a
     singles moveset as observed.
6. Write build markers and report row counts.

Re-running is idempotent and nearly free: every upstream response is cached in
`pull-cache.sqlite`, so a rebuild costs no network beyond what's genuinely new or
changed. `--skip-builds` omits the observed-team stage (step 5). A pull is only
swapped in after passing sanity checks, so a bad or empty upstream response never
clobbers a good existing database.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CHAMPIONS_MCP_DATA_DIR` | `<repo>/data` | Data directory |
| `CHAMPIONS_MCP_DB` | `<data>/champions.sqlite` | Served database |
| `CHAMPIONS_MCP_PULL` | `0` | Force a refresh on container start (`1`) |
| `CHAMPIONS_MCP_POKECHAMP_BASE` | `https://pokechamp.gg` | Roster/movepool/reference/usage source (pull) |
| `CHAMPIONS_MCP_POKECHAMP_CONCURRENCY` | `6` | Max in-flight per-Pokémon page requests (pull) |
| `CHAMPIONS_MCP_POKEAPI_BASE` | `https://pokeapi.co/api/v2` | Type chart + localized names (pull) |
| `CHAMPIONS_MCP_CACHE_DB` | `<data>/pull-cache.sqlite` | Pull cache (PokeAPI + observed teams) |
| `CHAMPIONS_MCP_LIMITLESS_BASE` | `https://play.limitlesstcg.com/api` | VGC observed builds (pull) |
| `CHAMPIONS_MCP_LIMITLESS_KEY` | unset | Optional Limitless API key — only raises the rate limit |
| `CHAMPIONS_MCP_POKEDB_BASE` | `https://champs.pokedb.tokyo` | BSS observed builds (pull) |
| `CHAMPIONS_MCP_BUILDS_REGULATIONS` | `M-C` | Regulations to ingest, comma-separated |
| `CHAMPIONS_MCP_BUILDS_MAX_EVENTS` | `150` | Cap on tournaments fetched per regulation |
| `CHAMPIONS_MCP_BUILDS_MIN_PLAYERS` | `16` | Skip events smaller than this |
| `CHAMPIONS_MCP_BUILDS_HALF_LIFE_DAYS` | `21` | Recency half-life for observation weights |
| `CHAMPIONS_MCP_BUILDS_SKIP` | `0` | Skip the observed-team stage on container start (`1`) |
| `CHAMPIONS_MCP_POKEDB_SEASON_REGS` | built-in | Override the pokedb season → regulation map |
| `CHAMPIONS_MCP_CALC_HTTP` | unset | Calc sidecar URL (else one-shot `docker run`) |
| `CHAMPIONS_MCP_CALC_IMAGE` | `champions-calc:latest` | Damage-calc image tag |
| `MCP_TRANSPORT` | `stdio` | `stdio`, `streamable-http`, or `sse` |
| `FASTMCP_HOST` / `FASTMCP_PORT` | `127.0.0.1` / `8000` | Bind address (HTTP transports only) |

See [`.env.example`](.env.example) for the full list, including pull and calc-sidecar
timeouts.

## Refreshing the data

pokechamp.gg updates as each regulation rolls over, and new tournaments finish daily. To
pick up both:

```bash
CHAMPIONS_MCP_PULL=1 docker compose up -d   # docker
# or
champions-mcp-pull                          # local
```

A refresh re-reads everything already cached and fetches only what is new, so it is
much faster than the first build.

The active season, data version and observed-team corpus (team counts, regulations,
evidence level) are reported by `get_game_rules` under `data`.

## Acknowledgements

| Source | Used for |
|---|---|
| [pokechamp.gg](https://pokechamp.gg) | Primary source: roster, Champions stats/types/abilities, movepools, move/ability/item reference text, usage data |
| [PokeAPI](https://pokeapi.co) | Type chart + localized species names |
| [Limitless](https://play.limitlesstcg.com) | VGC observed builds: tournament open team sheets via their public API |
| [バトルデータベース チャンピオンズ](https://champs.pokedb.tokyo) | BSS observed builds: ranked-ladder team export, published as open data ([@pokedb_tokyo](https://x.com/pokedb_tokyo)) |
| [Pokémon Showdown](https://play.pokemonshowdown.com) | Historical-builds fallback: every generation/tier of Smogon-curated sets |
| [smogon/damage-calc](https://github.com/smogon/damage-calc) | `@smogon/calc` with native Champions (gen 0) support |
| [pokepast.es](https://pokepast.es) | Team paste hosting for `create_pokepaste` |

Pokémon is © Nintendo / Creatures Inc. / GAME FREAK inc. This project is an unofficial
fan tool, not affiliated with or endorsed by any of the above.
