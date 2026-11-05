# Battle simulator integration — research & plan

Status (2026-11-05): **implemented**. This file is the original research
and plan, kept for its measurements. The shipped design is:

- `docker/sim/`: the sidecar. `engine.js` runs battles, `policies.js` holds the
  AI and the information modes, and `pool.js` is a worker pool sized to the
  CPUs and memory available. `selftest.js` gates every image build.
- `src/champions_mcp/sim.py` and the four `simulate_*` / `compare_team_variants`
  tools; reference in [tools.md](tools.md#battle-simulation).
- `src/champions_mcp/pull/reports.py`: complete BSS opponent teams from
  published team reports.

Prototype code: `experiments/battle-sim/` (gitignored).

## Goal

Simulate large numbers of battles under real Pokémon Champions rules (Reg M-C,
VGC doubles and BSS singles) and expose the results through MCP tools: matchup
win rates, team-vs-meta scores, and A/B comparisons of set changes.

## Engine options

| Engine | Champions mechanics | Doubles | Speed | Verdict |
|---|---|---|---|---|
| **Pokémon Showdown sim** (`smogon/pokemon-showdown`, `champions` mod) | Native: SP stat formula, 20 PP cap, Megas + Z-Megas, persistent Megas, no Tera, Champions abilities (Dragonize, Eelevate, Fire Mane, Mega Sol, Piercing Drill, Spicy Spray), Reg M-C VGC + BSS formats and validator | Yes | ~90–150 battles/s/core | **Use this** |
| `@pkmn/sim` + `@pkmn/mods` | Same engine (synced fork), npm-pinnable, includes `champions` mod | Yes | Same | Viable alternative; doesn't ship the format list, so rules must be hand-assembled |
| `pokemon-showdown` npm package | 0.11.11 (Jul 2026) stops at Reg M-B | Yes | Same | Stale, avoid |
| poke-engine (Rust, pmariglia) | None (would need a port of SP, Megas, new abilities) | **No** (singles) | Very fast, MCTS-oriented | Rejected: no doubles, no Champions |
| pkmn/engine (Zig) | None | No | Fastest | Rejected: early gens only |
| battler / PokemonBattleEngine (Rust) | None | Partial | Fast | Rejected: would need a full Champions port |
| poke-env (Python) | Uses a Showdown server underneath | Yes | Slow (websocket protocol) | Only interesting later, for RL agent training |

Showdown is the only engine with Champions mechanics, and it is actively
maintained: the `champions` mod was updated 2026-11-03.

## What was tested (prototype results)

All runs in Docker (`node:22-alpine`), Showdown built from `master` @ `16db150`.

**Real-team coverage.** 5,115 complete observed Reg M-C teams were exported from
`champions.sqlite` (Limitless corpus, spreads = per-species modal SP spread from
usage). **4,977 (97.3%) pass Showdown's own Reg M-C validator.** The 138
failures are bad source data: typos like `lastrespect`, `adament` and
`lightingrod`, plus Item Clause violations.

**Mechanics fidelity.**
- Stats: all **268/268** roster entries (Megas included), each with a random nature and SP spread, match `champions_stats.compute_stat` exactly.
- Damage: **9/9** scenarios produce the identical set of damage rolls to our @smogon/calc gen-0 image (crits off). The scenarios cover Life Orb, Mega Mawile Huge Power, Mega Charizard Y Drought + sun + doubles spread, Mega-Z Absol Sharpness, Mega-Z Garchomp, and doubles single-target hits.
- Gotcha: Choice Specs, Choice Band and Assault Vest are "Past" items in Champions. The sim still applies them in an unvalidated Custom Game, so **always validate teams against the real format before simulating**.

**Throughput** (one process, steady state):

| Format | Policy | Battles/s | Avg turns | ms/turn |
|---|---|---|---|---|
| VGC Reg M-C | random | 50 | 11.2 | 1.8 |
| VGC Reg M-C | greedy | 91 | 5.0 | 2.2 |
| BSS Reg M-C | random | 90 | 17.7 | 0.6 |
| BSS Reg M-C | greedy | 148 | 7.8 | 0.9 |

**Scaling** across processes (VGC, greedy, 24-thread machine): 1→87, 4→321,
8→527, 16→645, 22→**704 battles/s**. So 10k battles take ~15 s and 100k take ~2.5 min.

**Stability.** 0 sim errors over more than 10,000 battles; 0.1–1% of choices
needed a fallback to `default`, all from the random policy's doubles targeting.

**Determinism.** Same seeds give an identical outcome hash across runs, so
results are reproducible and A/B tests can use common random numbers.

**Profile.** ~50% of CPU is Showdown's event dispatch (`getCallback` and
`findEventHandlers`), and the policy is about 1.6%. Stubbing the protocol log
gives no speedup. Scaling out with processes is the lever, not micro-optimising.

**Search cost.** Serialize, restore and play one turn of a battle state takes
0.8 ms (73 KB state). A pruned 1-ply lookahead is therefore affordable as an
opt-in "deep" mode; full MCTS is not viable for bulk runs.

## Architecture

```
MCP tool (Python, server.py)
   └─ sim.py  ── HTTP ──>  champions-sim sidecar (Node 22, docker/sim/)
                             ├─ Showdown sim, cloned at a pinned SHA (like docker/calc)
                             ├─ worker_threads pool (size = cores - 1)
                             └─ POST /validate, /simulate, /replay
```

It mirrors the existing `champions-calc` pattern: a sidecar image, an HTTP
service in compose, and `CHAMPIONS_MCP_SIM_HTTP` in the MCP container.

- **Image:** clone `smogon/pokemon-showdown` at a pinned commit and run `node build`. A bump job compares upstream `data/mods/champions` and the Reg M-C format and re-runs the fidelity suite before moving the pin.
- **Run loop:** direct synchronous `Battle` objects (as in `bench.js`), not `BattleStream` or the text protocol. Each request is sharded across workers by seed range, and the shard results are merged.
- **Input:** Showdown paste text or our member dicts. A new `team_to_showdown(..., for_sim=True)` must use real Showdown names, because `pokepaste._PASTE_NAME` maps `Floette-Eternal` to `Floette` for pokepast.es sprites and that broke 647 teams in testing.
- **Validation:** every team goes through `TeamValidator` for the target format first, and the problems are returned as tool errors.

## AI policies (the real quality lever)

The engine is exact; how meaningful a simulated win rate is depends entirely
on the decision policy.

1. **random**: baseline and smoke tests only.
2. **greedy** (prototype): best base power × STAB × effectiveness × stat ratio, always Mega, random team preview. Cheap; plays at a "spam the best move" level.
3. **heuristic** (target for v1): uses the sim's own damage function on the live state. It adds:
   - KO awareness and priority use (Sucker Punch, Fake Out on turn 1)
   - Protect when threatened
   - speed-order awareness, including Trick Room and Tailwind
   - switching out of bad matchups
   - lead and bring selection scored by a matchup matrix
   - Mega timing

   Expected cost is 2–5× greedy.
4. **lookahead** (opt-in "deep" mode): 1-ply over the top-k joint actions per side using state cloning. Estimated ~1–3 battles/s/core, so it suits a few hundred battles, not 10k.

Every result reports which policy produced it, and the tool docs state that
win rates are policy-relative, not ladder predictions.

## MCP tools

| Tool | Purpose | Defaults |
|---|---|---|
| `simulate_matchup(team_a, team_b, format, n, policy, seed)` | Head-to-head win rate | n=1000, heuristic |
| `simulate_vs_meta(team, format, n, opponents)` | Win rate vs top observed M-C teams (weighted by `build_teams.weight`) | 50 opponents × 40 games |
| `compare_team_variants(base_team, variants, ...)` | A/B a set, item or spread change vs the same opponents and seeds (paired, so far lower variance) | |
| `simulate_battle_log(team_a, team_b, seed)` | One battle, condensed turn-by-turn, so the LLM can explain *why* | |

Each tool returns:
- win rate with a Wilson 95% CI (n=1000 gives ±3.1 pts at 50%)
- average turns and ties
- per-member stats: brought %, lead %, KOs, faints, Mega usage
- the best and worst opponents
- the policy and the Showdown SHA

Guard rails:
- an n cap per call (e.g. ≤20k, about 30 s)
- MCP progress notifications on long runs
- optionally async jobs for anything larger

## Data gaps to close

- **Spreads:** observed teams have no SP spreads. Use the modal spread per species and format (as the prototype does), and label results accordingly.
- **Singles teams:** pokedb singles teams are missing items and abilities, so 0 complete BSS teams could be built. Fill them from the usage modal item and ability, or keep doubles-derived teams as the BSS opponent pool.
- **Corpus hygiene:** validate at ingest (`pull/builds.py`) and drop or flag the ~2.7% invalid teams.
- **Mega stone names:** a few corpus items (`staraptorite`, `dragonitite`) don't resolve. Add aliases to `Staraptite` and `Dragoninite`.

## Milestones

1. **Sidecar**: `docker/sim/` image + HTTP server + worker pool, with `/validate` and `/simulate` (greedy). Port `bench.js` and `fidelity.*` into CI-style tests.
2. **MCP wiring**: `sim.py` client, `simulate_matchup` and `simulate_battle_log`, the sim-safe name export, and validation errors surfaced.
3. **Heuristic policy**, plus `simulate_vs_meta` and `compare_team_variants` with common random numbers.
4. **Hardening**: pin-bump script with the fidelity gate, ingest-time validation, and BSS opponent pool.
5. **Optional**: lookahead "deep" mode, async jobs for very large runs.

## Reproducing the prototype

```bash
cd experiments/battle-sim
docker build -t champions-sim-proto .
python -I export_teams.py <repo> teams.json
docker run --rm -v "<dir>:/bench:ro" champions-sim-proto /bench/bench.js /bench/teams.json gen9championsvgc2026regmc 2000 greedy validate
python -I fidelity.py <repo> "<dir>"
```
