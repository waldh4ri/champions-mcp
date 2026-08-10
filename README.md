<div align="center">

# champions-mcp

MCP server for AI-assisted Pokémon **Champions** team building (VGC doubles and BSS singles).

</div>

## What it does

Game data is pulled once into a local SQLite database (`champions.sqlite`):
[championsbattledata.com](https://championsbattledata.com/api_guide) for the roster,
Champions stats/types/abilities, forms, movepools, and usage data, and
[PokeAPI](https://pokeapi.co) for a one-time enrichment pass (move/ability/item
descriptions, the type chart, localized names), cached permanently. Two further sources
supply a corpus of **complete teams people actually brought** — Limitless tournament
teamlists for VGC and the champs.pokedb.tokyo ranked-ladder export for BSS. Every tool
call is then answered from that local file, no live lookups, no scraping, no runtime
juggling of any upstream.

The only outbound calls happen at two points, both opt-in:

- `champions-mcp-pull` fetches the source index, runs the PokeAPI enrichment pass, and
  ingests the observed-team corpus (`--skip-builds` to omit the last).
- `create_pokepaste` uploads a finished team to pokepast.es.

40 MCP tools cover the Pokedex, legal rosters and movepools, usage stats/sets/teammates,
observed builds and team context, Stat Point and speed math, team validation, damage
calculation, and Pokepaste export. A bundled skill walks an LLM through the whole
team-building workflow end to end.

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
`get_type_matchups`, `get_pokemon_weaknesses`. `list_items` returns the whole item
pool grouped by category (Mega Stones hidden by default - they are 75 of the 148
items); if an item is not in it, it does not exist in Champions.

**Roster and movepool** - `list_pokemon`, `search_pokemon_by_type`, `search_pokemon_by_ability`,
`search_pokemon_by_move`, `get_pokemon_moves`, `is_legal_move`, `is_legal_pokemon`

**Meta, both formats** - `get_usage_stats`, `get_pokemon_sets`, `get_top_teammates`,
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
and is exposed as the `champions_team_building` MCP prompt. A `pokedex://champions` MCP
resource exposes the full roster as JSON.

## How the data pull works

`champions-mcp-pull` (`python -m champions_mcp.pull`):

1. **Download** `pokemon-index.json` from championsbattledata.com.
2. **Parse** it into the SQLite schema: roster, Champions stats/types/abilities, forms
   (including Megas), full movepools, and all usage rows (moves, items, abilities,
   natures, SP spreads, teammates) for both Doubles and Singles, plus the usage ranking.
3. **Enrich** the bounded reference universe from PokeAPI once, cached permanently:
   around 539 move descriptions, 200 ability descriptions, 143 item descriptions, the
   18-type chart, and localized species names.
4. **Ingest observed teams** into `build_teams`/`build_members`:
   - **VGC** from the [Limitless](https://play.limitlesstcg.com) public API
     (`/tournaments/{id}/standings`). These events enforce open team sheets, so each
     response carries every player's full list — species, item, ability, nature and all
     four moves. No API key needed; unauthenticated access is rate-limited to 50
     requests per 5 minutes, so the first run paces itself over several minutes.
   - **BSS** from the [champs.pokedb.tokyo](https://champs.pokedb.tokyo/guide/opendata)
     ranked-ladder export. Japan is where singles is actually played, and this is the
     top of the official ladder per season. It carries species and held item but **no
     moves**, so those rows are marked `team_and_item` and the tools never present a
     singles moveset as observed. Western singles tournaments are not a substitute:
     across 916 Champions events on Limitless only 9 were singles.
5. Write build markers and report row counts.

Re-running is idempotent and nearly free: every upstream response is cached in
`pull-cache.sqlite`, and a finished tournament is immutable, so a rebuild costs no
network and only genuinely new events are fetched. Use `--offline` to rebuild from the
saved index copy instead of downloading again, and `--skip-builds` to omit the
observed-team stage. A pull is only swapped in after passing sanity checks, so a bad or
empty upstream response never clobbers a good existing database.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CHAMPIONS_MCP_DATA_DIR` | `<repo>/data` | Data directory |
| `CHAMPIONS_MCP_DB` | `<data>/champions.sqlite` | Served database |
| `CHAMPIONS_MCP_PULL` | `0` | Force a refresh on container start (`1`) |
| `CHAMPIONS_MCP_SOURCE_BASE` | `https://championsbattledata.com` | Data source (pull) |
| `CHAMPIONS_MCP_POKEAPI_BASE` | `https://pokeapi.co/api/v2` | Enrichment source (pull) |
| `CHAMPIONS_MCP_CACHE_DB` | `<data>/pull-cache.sqlite` | Pull cache (PokeAPI + observed teams) |
| `CHAMPIONS_MCP_LIMITLESS_BASE` | `https://play.limitlesstcg.com/api` | VGC observed builds (pull) |
| `CHAMPIONS_MCP_LIMITLESS_KEY` | unset | Optional Limitless API key — only raises the rate limit |
| `CHAMPIONS_MCP_POKEDB_BASE` | `https://champs.pokedb.tokyo` | BSS observed builds (pull) |
| `CHAMPIONS_MCP_BUILDS_REGULATIONS` | `M-B` | Regulations to ingest, comma-separated |
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

championsbattledata updates each season, and new tournaments finish daily. To pick
up both:

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
| [championsbattledata.com](https://championsbattledata.com) | Primary source: roster, Champions stats/types/abilities, forms, movepools, usage data |
| [PokeAPI](https://pokeapi.co) | One-time enrichment: move/ability/item descriptions, type chart, localized names |
| [Limitless](https://play.limitlesstcg.com) | VGC observed builds: tournament open team sheets via their public API |
| [バトルデータベース チャンピオンズ](https://champs.pokedb.tokyo) | BSS observed builds: ranked-ladder team export, published as open data ([@pokedb_tokyo](https://x.com/pokedb_tokyo)) |
| [smogon/damage-calc](https://github.com/smogon/damage-calc) | `@smogon/calc` with native Champions (gen 0) support |
| [pokepast.es](https://pokepast.es) | Team paste hosting for `create_pokepaste` |

Pokémon is © Nintendo / Creatures Inc. / GAME FREAK inc. This project is an unofficial
fan tool, not affiliated with or endorsed by any of the above.
