# Development

[Documentation index](README.md)

## Local setup

Use an editable checkout with Python 3.11+ (the Docker server and CI use Python
3.12). From an activated virtual environment at the repository root:

```sh
pip install -e ".[dev]"
champions-mcp-pull
pytest
```

See [getting started](getting-started.md) for environment activation, client
configuration, and optional engine images. The MCP dependency must remain on
SDK 2.x: the server imports `mcp.server.mcpserver.MCPServer`.

## Architecture and source map

| Path | Responsibility |
|---|---|
| `src/champions_mcp/server.py` | MCP registration, prompt/resource, lazy app singleton, tool argument handling, transport startup. |
| `src/champions_mcp/config.py` | Environment-driven settings and paths. |
| `src/champions_mcp/db.py` | SQLite schema, queries, data-format version and build metadata. |
| `src/champions_mcp/pull/` | Upstream clients/parsers, ranked usage, reports, observed teams, historical sets, freshness and atomic rebuild CLI. |
| `src/champions_mcp/cache.py` | Persistent pull-response cache. |
| `src/champions_mcp/pokedex.py`, `reference.py`, `roster.py` | Species/reference lookups, roster searches and movepools. |
| `src/champions_mcp/usage.py`, `builds.py`, `historical_sets.py` | Marginal usage, joint observed-build evidence and legality-flagged historical fallback. |
| `src/champions_mcp/profiles.py`, `roles.py`, `fields.py` | Type/stat profiles, competitive roles and field-effect reference. |
| `src/champions_mcp/champions_stats.py` | Champions SP, nature and Speed arithmetic. |
| `src/champions_mcp/legality.py`, `teambuilder.py` | Team validation, shared weaknesses, meta context and dependency gaps. |
| `src/champions_mcp/damage.py`, `sim.py` | Python bridges to the Node.js engines; name resolution and opponent completion. |
| `src/champions_mcp/pokepaste.py` | Showdown-style serialization and public paste upload. |
| `src/champions_mcp/names.py`, `normalize.py`, `formats.py` | Name/alias resolution, normalization and format rules. |
| `data/formats.json` | Static VGC/BSS format rules. |
| `skills/champions-team-building/` | Core skill and on-demand guidance sections. |
| `docker/mcp/` | Python server image and pull/freshness startup script. |
| `docker/calc/` | `@smogon/calc` build, Champions data patch, stdin bridge and HTTP wrapper. |
| `docker/sim/` | Pinned Showdown build, API, battle engine, AI policies, worker pool and self-tests. |
| `docker/chat/` | FastAPI chat/tool-calling bridge and single-page UI. |
| `docker/llama/` | llama.cpp image and configurable launcher. |
| `docker/bench/`, `experiments/` | Benchmarking and experiments outside the core MCP server. |
| `tests/` | Unit/data/integration tests and recorded upstream fixtures. |

### Important boundaries

- Pull-time code fetches external game data; ordinary read models must work
  against the local mirror without scraping.
- Usage distributions and observed joint sets are different evidence. Preserve
  provenance and distinguish inferred details from recorded ones.
- Champion stats are level-50 zero-SP values. Use the SP helpers, not conventional
  EV or base-stat formulas.
- Format-specific calls must use matching VGC/BSS rules and damage `game_type`.
- The MCP app opens its database lazily and keeps it for the process lifetime.
  Restart after replacing the mirror.
- When a new data shape/content is required, bump `DATA_FORMAT` in `db.py` so
  container startup requests a compatible pull. Keep atomic replacement and
  validation safeguards intact.

## Tests

```sh
pytest -v -rs
```

The default suite excludes `live` tests. Data-dependent tests use
`data/champions.sqlite` and skip if it has not been built. Simulator integration
tests also skip when Docker/the simulator image is unavailable; inspect the
skip summary rather than treating a mostly skipped run as full coverage.

Live endpoint checks intentionally contact upstreams:

```sh
pytest -m live -v
```

To run a particular area:

```sh
pytest tests/test_stats.py
pytest tests/test_reports.py
pytest tests/test_sim.py tests/test_server_sim.py
```

### Recorded corpus

CI avoids a long full scrape by pulling the roster/usage mirror without observed
teams, then replaying recorded upstream responses through the real ingest code:

```sh
champions-mcp-pull --skip-builds
python tests/fixtures/corpus.py data/champions.sqlite
```

**The fixture command replaces the target database's observed-team/report
corpus.** Use a development database, not one whose live observations you want
to retain. `tests/fixtures/record_corpus.py` records the upstream slice used for
that replay; fixture changes should be deliberate and reviewed.

### Sidecar checks

```sh
docker build -f docker/sim/Dockerfile -t champions-sim:latest .
docker run --rm champions-sim:latest /app/selftest.js
docker build -f docker/calc/Dockerfile -t champions-calc:latest .
```

The simulator Docker build runs the quick self-test; the explicit command runs
the full test. The simulator uses Node.js 22 and a pinned Showdown commit
(`SHOWDOWN_SHA` in its Dockerfile). The calculator uses Node.js 20 and currently
clones upstream damage-calc at build time, with a local Champions species patch.
Review upstream changes and regulation support when rebuilding/updating engines.

The chat is a separate image with its own dependencies. A lightweight syntax
check is:

```sh
python -m py_compile docker/chat/main.py
```

### CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on pull requests,
pushes to `main`, manual dispatch, and a daily schedule. It checks:

1. Simulator image build and full self-tests.
2. Python tests against a pulled database plus the replayed observed corpus and
   simulator image, with `CHAMPIONS_MCP_STRICT_TESTS=1` so skips are failures.
3. Separate live upstream endpoint checks to detect changed response shapes.

## Updating tools and documentation

When a tool changes, update its schema/docstring in `server.py`, its
[reference entry](tools.md), and affected guidance sections. Keep the tool list
and documented defaults aligned with the source. The MCP prompt and
`get_team_building_guide` serve the files under `skills/champions-team-building/`;
the Docker image copies that directory alongside the editable package.

The main README should remain a short introduction, basic startup and example
usage. Keep advanced setup, configuration and architectural details here in
`docs/`.
