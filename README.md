<div align="center">

<img src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/80.png" height="130" alt="Slowbro" title="Slowbro"/>
<img src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/248.png" height="130" alt="Tyranitar" title="Tyranitar"/>
<img src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/637.png" height="130" alt="Volcarona" title="Volcarona"/>

# champions-mcp

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE)

</div>

An MCP server for AI-assisted Pokémon **Champions** team building, exposing 35 tools
across seven layers:

- **Pokédex** — species, stats, types, abilities, moves, items from [PokeAPI](https://pokeapi.co),
  permanently mirrored to SQLite. PokeAPI is never contacted twice for the same resource.
- **Regulation / legality** — curated regulation sets (active: **M-A**, 2026-04-08 → 2026-06-17).
  Champions item catalog (139 items, Serebii-verified), Item Clause and Species Clause enforcement.
- **Movesets** — per-species Champions movepools (186 species / 490 moves, Serebii-verified).
- **Roster** — 213 pickable entries (incl. regional forms) from the M-A allowlist
  ([Bulbapedia](https://bulbapedia.bulbagarden.net/wiki/Regulation_Set_M-A)), `roster_verified: true`.
- **Meta** — Smogon chaos stats (usage %, sets, spreads) + Limitless tournament data (top teams, cores).
- **Stat Points** — Champions SP math: 66 total, max 32/stat, 1 SP = +1 stat at Lv 50.
  Speed threshold, stat calc, spread validation.
- **Damage calc** — [`@smogon/calc`](https://github.com/smogon/damage-calc) in native Champions
  mode (gen 0): Champions species/items/moves, SP spread passed straight through, singles/doubles.

## Run

```bash
git clone https://github.com/waldh4ri/champions-mcp
cd champions-mcp
docker compose up -d --build
```

The server is ready when the log prints:

```
 MCP URL   : http://localhost:8000/mcp
```

Register in VS Code `settings.json`:

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

The compose stack builds two services: **`champions-calc`** (`@smogon/calc` HTTP sidecar,
internal only) and **`champions-mcp`** (Python MCP server, port 8000). Once running the
server is accessible at **http://localhost:8000**. A named volume `champions-data` persists
the SQLite mirror across restarts. On first start the server prewarms the PokeAPI cache;
set `CHAMPIONS_MCP_PREWARM=0` to skip after the first run.

> **Security note** — This server is intended for local use only. There is no TLS and no
> authentication. Do not expose it to the internet or to untrusted networks.

## Caching

The project is a **good citizen towards every upstream API**: no resource is fetched more
than once if it can be avoided. All runtime data is stored in a single SQLite file.

| Data | TTL | Policy |
|---|---|---|
| PokeAPI Pokédex (species, moves, items, names) | **permanent** | Written on first fetch, served from SQLite forever — PokeAPI never hit twice for the same resource |
| Smogon chaos stats + Limitless meta | 6 h (`CHAMPIONS_MCP_META_TTL`) | Re-fetched at most every 6 h; stale data served on error |
| Tournament listings / standings | 1 h (`CHAMPIONS_MCP_TOURNAMENT_TTL`) | Bounded even during active events |
| Static JSON files (roster, movesets, items) | **never auto-fetched** | Only regenerated via `champions-mcp-prewarm` |

The volume `champions-data` persists the cache across container restarts and rebuilds.
Delete it to force a full re-prewarm:

```bash
docker compose down -v && docker compose up -d --build
```

## Tools

### Session setup

| Tool | Description |
|---|---|
| `get_game_rules_vgc` | **Call first for any VGC session.** Returns the complete doubles baseline: format rules (bring 4), SP system, item catalog, Mega rules, active regulation, and a step-by-step tool guide for the session. |
| `get_game_rules_bss` | **Call first for any BSS session.** Same as above for singles (bring 3). |

### Pokédex

| Tool | Description |
|---|---|
| `get_pokemon` | Types, abilities, base stats and Mega forms for a Pokémon. Accepts localized names. Returns base stats — use `calc_stats` for final Lv 50 values. |
| `search_pokemon` | Substring search across the full PokeAPI Pokémon universe. Returns matching slugs. For Champions-filtered search use `list_legal_pokemon` instead. |
| `get_move` | Type, category, power, accuracy, priority and effect for a move. |
| `search_moves` | Search Champions-known moves by name substring, type and/or damage category. Only moves present in at least one Champions moveset are searched. |
| `get_item` | Item category, effect and Mega Stone flag. |
| `search_items` | Substring search of the Champions item catalog (139 items). Omit query to list the full catalog. |
| `get_type_matchups` | Offensive and defensive type effectiveness for a single type. |
| `get_pokemon_weaknesses` | Full defensive type chart for a Pokémon or an explicit type pair. Returns all 18 attacking types grouped by multiplier (4×/2×/1×/½×/¼×/0×). |

### Regulation & legality

| Tool | Description |
|---|---|
| `list_regulations` | List all curated regulation set IDs. |
| `get_regulation` | Full rules for a regulation (`"current"` for the active one). |
| `validate_team` | **Call last.** Full legality report for a complete team: species roster, items, moves, Item/Species Clause, SP spreads, Mega rules. Accepts localized names. |

### Roster (build-time)

| Tool | Description |
|---|---|
| `list_legal_pokemon` | Regulation-filtered pick list — excludes Legendaries, Mythicals and Restricted species. Supports substring `query`. Fast, no network. Primary filter when starting a build. |
| `get_champions_roster` | Raw global roster (all 213 entries incl. regional forms and Megas). Use when exploring what exists in Champions; `list_legal_pokemon` is better for team building. |
| `is_legal_pokemon` | Quick single check: is this Pokémon legal to pick? Returns reasons if not. |
| `pokemons_by_type` | Legal Champions Pokémon of a given type or type combination. Uses Champions-accurate typing (not PokeAPI). |
| `pokemons_by_ability` | Legal Champions Pokémon that have a given ability (regular or hidden). Checks Mega-form abilities too. |
| `pokemons_by_move` | Legal Champions Pokémon that can learn a given move, using Champions-native learnsets. |

### Movesets (build-time)

| Tool | Description |
|---|---|
| `get_pokemon_moves` | **Call before assigning any moves.** Full Champions-legal moveset for a Pokémon. PokeAPI learnsets are wrong for Champions (rebalanced movepools). |
| `is_legal_move` | Quick single check: can this Pokémon use this move in Champions? |

### Meta

| Tool | Description |
|---|---|
| `get_usage_stats` | Smogon ladder usage rankings (top-N by usage %) for VGC or BSS from monthly chaos JSON (rating 1760). Format-wide overview. |
| `get_pokemon_sets` | **Primary source for how a Pokémon is actually played.** Most common moves, items, abilities, SP spreads, teammates and checks/counters with usage % from rated ladder games. |
| `get_smogon_analysis` | Written Smogon strategy overview and named sets from the Smogon dex. Returns `available: false` when no analysis exists (normal — the metagame is new). |
| `get_top_teams` | Top-cut team lists from recent Limitless VGC tournaments. VGC only. |
| `search_tournaments` | List Limitless VGC tournaments, filtered by name or format. |
| `get_tournament_standings` | Full standings (placements, records, decklists) for a tournament by ID. |

### Team analysis

| Tool | Description |
|---|---|
| `analyze_team` | Legality check + meta context for a complete team: per-member types/BST, usage % benchmarks, top meta threats not on the team. Pass `format` matching the session (vgc/bss). |
| `suggest_cores` | Co-occurrence patterns from Limitless top-cut VGC teams. Returns the most frequent Pokémon pairs. VGC only. Use early in the build for core inspiration. |

### Stat Points & Speed

| Tool | Description |
|---|---|
| `calc_stats` | Compute all six final Lv 50 stats for a Champions SP spread + nature. Validates the budget. Use after picking a spread from `get_pokemon_sets`. |
| `validate_ev_spread` | Validate a SP spread (budget 66, max 32/stat) without computing stats. Use `calc_stats` when you also need the resulting values. |
| `compute_speed` | Effective in-battle Speed with all modifiers: stat stage → Choice Scarf (×1.5) → Tailwind (×2) → paralysis (×0.5). |
| `speed_threshold` | Minimum Speed SP for the attacker to outspeed a target. Checks multiple natures and returns SP remaining for other stats. |

### Damage calculator

| Tool | Description |
|---|---|
| `calc_damage` | Champions damage calc via `@smogon/calc` (gen 0 native mode). Pass SP spreads directly — no EV conversion. Handles ability, item, Mega (from stone), weather, terrain, status, boosts, singles/doubles (spread-move 0.75×). Returns rolls, %HP, KO chance and a description. Requires the `champions-calc` Docker image. |

### Export

| Tool | Description |
|---|---|
| `create_pokepaste` | Upload a finalised team to [pokepast.es](https://pokepast.es) and return the shareable URL. Call after `validate_team` confirms legality. SP values appear in the EVs line (note: 1 SP ≠ 4 EVs). |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CHAMPIONS_MCP_DATA_DIR` | `<package>/data` | Regulation JSON + cache root |
| `CHAMPIONS_MCP_CACHE_DB` | `<data>/cache.sqlite` | SQLite mirror path |
| `LIMITLESS_API_KEY` | _(unset)_ | Unlocks the Limitless `/decks` endpoint |
| `CHAMPIONS_MCP_META_TTL` | `21600` | Meta cache TTL (seconds) |
| `CHAMPIONS_MCP_TOURNAMENT_TTL` | `3600` | Tournament cache TTL (seconds) |
| `CHAMPIONS_MCP_HTTP_TIMEOUT` | `20` | Outbound HTTP timeout (seconds) |
| `CHAMPIONS_MCP_DOCKER` | `docker` | Docker binary (stdio damage calc) |
| `CHAMPIONS_MCP_CALC_IMAGE` | `champions-calc:latest` | Damage-calc image tag |
| `CHAMPIONS_MCP_CALC_HTTP` | _(unset)_ | Calc sidecar URL (HTTP instead of docker) |
| `CHAMPIONS_MCP_CALC_TIMEOUT` | `30` | Per-call damage calc timeout (seconds) |
| `MCP_TRANSPORT` | `stdio` | `stdio` \| `streamable-http` \| `sse` |
| `FASTMCP_HOST` | `127.0.0.1` | Bind address (HTTP transports) |
| `FASTMCP_PORT` | `8000` | Bind port (HTTP transports) |
| `MCP_PORT` | `8000` | Host-side published port (docker-compose) |
| `CHAMPIONS_MCP_PREWARM` | `1` | Prewarm on container startup (`0` to skip) |

## Tests

```bash
pytest
```

Most tests are offline — PokeAPI/Limitless/meta HTTP calls are stubbed. `test_data_files.py`
will run the Serebii scraper if any `champions_*.json` is missing; the files are committed
so a normal `pytest` run is fully offline.

## Caveats

- Re-verify `data/regulations/M-A.json` each regulation cycle (current source: Bulbapedia).
- Regenerate the item catalog with `champions-mcp-prewarm` when Champions changes its item pool.
- Meta scrapers degrade gracefully: a blocked source returns empty with a health note.

## Acknowledgements

| Source | Used for |
|---|---|
| [PokeAPI](https://pokeapi.co) | Species, stats, types, abilities, learnsets, multi-locale names |
| [Bulbapedia — Regulation Set M-A](https://bulbapedia.bulbagarden.net/wiki/Regulation_Set_M-A) | Champions roster allowlist (213 species) |
| [Serebii — Champions Pokédex](https://www.serebii.net/pokemonchampions/pokemon.shtml) | Per-species movepools (186 species / 490 moves), item catalog (139 items) |
| [Smogon stats](https://www.smogon.com/stats/) | Monthly chaos JSON — usage %, sets, spreads |
| [Smogon Strategy Dex](https://www.smogon.com/dex/champions/) | Per-Pokémon strategy analyses |
| [Limitless VGC](https://limitlesstcg.com) ([API docs](https://docs.limitlesstcg.com/developer.html)) | Tournament listings, standings, top-team decklists |
| [smogon/damage-calc](https://github.com/smogon/damage-calc) | `@smogon/calc` with native Champions (gen 0) support |

| [pokepast.es](https://pokepast.es) | Team paste hosting used by `create_pokepaste` |
Pokémon is © Nintendo / Creatures Inc. / GAME FREAK inc. Pokémon Champions is developed
by The Pokémon Company. This project is an unofficial fan tool and is not affiliated with
or endorsed by any of the above.
