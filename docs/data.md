# Data and sources

[Documentation index](README.md)

## Sources

| Source | Used for |
|---|---|
| [pokechamp.gg](https://pokechamp.gg) | Current legal roster, Champions stats/types/abilities, movepools, and move/ability/item reference text. |
| [バトルデータベース チャンピオンズ](https://champs.pokedb.tokyo) | Ranked usage for both formats and the [ranked-ladder open-data export](https://champs.pokedb.tokyo/guide/opendata) (species and items). |
| [PokeAPI](https://pokeapi.co) | Game-invariant type chart and localized species names; Japanese reference names also help resolve team reports. Not Champions learnsets. |
| [Limitless](https://play.limitlesstcg.com) | Observed VGC tournament teams from public open team sheets. |
| [Pokémon Showdown / Smogon](https://play.pokemonshowdown.com) | Curated historical sets across published generations and tiers, used only as cross-game fallback evidence. |
| [pokedb article search](https://champs.pokedb.tokyo/article/search), [yakkun](https://yakkun.com/bbs/party/), [damekei](https://damekei.com/build-articles), [pokemon-teams](https://pokemon-teams.pages.dev) | Indexes of published BSS team reports. |
| [pokesol.app](https://pokesol.app), yakkun and players' original articles | Complete report sets, including Stat Points when fully resolvable; original article URLs are retained. |

Other integrations:

- [smogon/damage-calc](https://github.com/smogon/damage-calc): native Champions
  generation-0 damage engine behind `champions-calc`.
- [smogon/pokemon-showdown](https://github.com/smogon/pokemon-showdown): pinned
  Champions battle engine behind `champions-sim`.
- [pokepast.es](https://pokepast.es): public hosting for exported teams.

## What is local and what uses the network

`champions-mcp-pull` downloads the game-data mirror and supporting corpora.
Ordinary MCP lookups then query `champions.sqlite`, without live upstream
lookups or runtime scraping.

`calc_damage` and the four simulation tools contact local/ configured sidecars,
or launch their Docker images. `create_pokepaste` publishes a team to an external
service. Image builds download dependencies and engine sources; optional Hugging
Face model loading downloads a model. OpenRouter chat sends conversation/tool
data to external model providers. A separate MCP client's AI provider has its own
data-handling policy.

Thus the game-data tools can work offline after setup, but “local database” does
not mean that every optional chat backend or export stays offline.

## Pull pipeline

Run `champions-mcp-pull` (or `python -m champions_mcp.pull`):

1. **Roster and reference:** read pokechamp.gg's whole-game payloads for legal
   species/forms, movepools, moves, abilities, items and regulations.
2. **Mega forms:** derive each Mega/Z-Mega's typing, ability, stats, stat changes
   and unlocking stone.
3. **Ranked usage:** read pokedb's current season for both formats. Store moves,
   items, abilities, natures, exact SP spreads, teammates and usage ranking,
   matched to the roster by game IDs. In an early season with fewer than 100
   ranked entries, the preceding season can be used if its regulation matches.
4. **PokeAPI enrichment:** store the type chart and localized species names.
5. **Historical sets:** ingest Showdown's published Smogon sets across generations
   and tiers, separated into singles/doubles. Legality is checked at read time.
6. **Observed teams:** ingest Limitless tournament lists and pokedb ladder
   exports. No Limitless key is required; unauthenticated requests are paced.
7. **BSS reports:** find current-regulation articles through the report indexes,
   parse structured team pages and Japanese blog text, and keep teams that fully
   resolve to six members with items, abilities, natures, four moves and valid
   spreads. Store them in `full_teams` and also add their sets to the observed
   `build_teams` / `build_members` corpus.
8. **Metadata and checks:** record the data format, generation time, regulation
   and counts. Validate minimum roster/usage counts before replacing the served
   database.

`--skip-builds` omits steps 6–7, **not** the historical-set fallback. To keep the
ordinary observed sources but omit report scraping alone, set
`CHAMPIONS_MCP_REPORTS_SKIP=1`.

## Evidence and limitations

| Dataset | What it proves | What it does not prove |
|---|---|---|
| Ranked usage (`get_pokemon_sets`) | Per-choice popularity, including common SP spreads. | That the top item, moves and spread occur together. |
| VGC open team sheets | Joint species/item/ability/nature/move choices actually brought. | Exact Stat Point spreads. |
| BSS ladder export | Six-member team composition and held items. | Moves, ability, nature or spread. |
| Fully resolved BSS reports | Actual complete report sets and spreads. | That a scraped or published team is optimal or representative of the whole ladder. |
| Historical Smogon sets | Curated cross-game precedents with per-choice Champions legality flags. | Current Champions tournament evidence or directly transferable EV numbers. |

Observed-build tools return `full_set` when recorded moves are available, or
`team_and_item` for composition/item-only evidence. BSS can contain both kinds
because reports supplement ladder exports. Read the evidence and provenance of
the returned result rather than assuming all BSS data is incomplete.

Report spreads are retained in `full_teams` for simulation; the observed-set
tables do not carry SP columns. Use `get_pokemon_sets` for the general spread
distribution. The `locked` / `contested` / `fringe` bands describe agreement among
observations, not mandatory design choices.

For simulation, complete reports are preferred, with highest recorded ratings
first, then ranked teams without ratings, then unranked teams. Tournament/ladder
teams fill the remaining pool; missing details are inferred from usage and
reported as assumptions. If the BSS pool is too small, VGC teams can be added as
bring-three opponents with an explicit note. Those inferred or cross-format
opponents are not evidence of an actual complete BSS set.

The upstream data mirror can advance independently of the simulator, whose
Showdown commit and formats are pinned to Reg M-C. A new regulation may need an
engine update as well as a data pull. Simulation rates measure the AI policy's
performance, not human ladder odds.

## Caching and safe rebuilds

| Location | Contents / behavior |
|---|---|
| `champions.sqlite` | Served mirror, replaced only after a validated build. |
| `pull-cache.sqlite` | Parsed pull responses; unchanged cached data is reused. Roster/reference, ranked usage and historical sets generally have daily expiry; PokeAPI enrichment is cached permanently. |
| `reports-cache/` | Report listings and fetched articles; listings refresh twice a day. |

These live under `data/` locally and `/data` in the Compose `champions-data`
volume. Expiry makes a response eligible for refetch during the **next pull**;
it is not a background refresh timer. A forced pull still respects valid caches.
Later pulls are usually much cheaper, but expired pages and new events still
need network access.

The command builds into a temporary database on the same filesystem, then swaps
it into place after minimum-data checks. A bad or empty upstream response does
not replace a good existing mirror. If the primary build fails validation, the
command may independently refresh observed teams against the existing mirror,
but still exits nonzero. The container logs the failed refresh and uses an
existing database if one is available; a failed first pull without any database
prevents startup.

## Refreshing the data

Container startup pulls when the database is missing, when its data-format
version is older than the code needs, when it exceeds the default seven-day age
limit, or when `CHAMPIONS_MCP_PULL=1` is set. There is no periodic runtime job.

For an explicit Compose refresh, stop the server, run the pull against its
persistent volume, and start it again:

```sh
docker compose stop champions-mcp
docker compose run --rm --no-deps --entrypoint champions-mcp-pull champions-mcp
docker compose up -d champions-mcp
```

Run the final start command even if the pull reports a failure, if you want to
resume serving a preserved existing database. This one-off command runs the pull
directly: it does not use the startup script's `CHAMPIONS_MCP_BUILDS_SKIP` switch.
Append `--skip-builds` explicitly only if you want a mirror without that corpus.

Alternatively set `CHAMPIONS_MCP_PULL=1` in `.env` and run Compose `up -d` to
recreate the service with the new setting. Remove it after the refresh and apply
the change with `up -d` again, or every subsequent start will request a pull.

Locally, stop the MCP process (or disable it in the client), then run:

```sh
champions-mcp-pull
```

Restart/reconnect the MCP process afterward. It keeps an open database handle
and cached read models; replacing the database is not a live reload, and an open
handle can prevent replacement on Windows.

Call `get_game_rules(format)` after reconnecting to check `data.generated_at`,
`season`, `data_version`, counts and observed-build coverage. The `season` field
describes the roster regulation; ranked usage's ladder season is selected
separately by the pull.

## Acknowledgements

Thanks to the data providers and open-source projects linked above, the team
report authors, and [@pokedb_tokyo](https://x.com/pokedb_tokyo) for the ranked
open-data exports.

Pokémon is © Nintendo / Creatures Inc. / GAME FREAK inc. This is an unofficial
fan tool, not affiliated with or endorsed by those companies or the listed
providers.
