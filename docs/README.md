# champions-mcp documentation

`champions-mcp` connects an MCP-compatible AI assistant to Pokémon Champions data
and team-building tools. It supports **VGC** (doubles, bring 4 of 6) and **BSS**
(singles, bring 3 of 6).

## Guides

| Document | Covers |
|---|---|
| [Getting started](getting-started.md) | Docker, local installation, MCP client connections, optional web chat, troubleshooting |
| [Usage](usage.md) | Example requests, the team-building workflow, team input, calculations, simulation, export |
| [Configuration](configuration.md) | Environment variables, ports, pull settings, sidecars, OpenRouter, local-model settings |
| [Data and sources](data.md) | Upstream sources, evidence levels, pull pipeline, caching, refreshes, acknowledgements |
| [Tool reference](tools.md) | All 48 MCP tools, parameters and defaults; the prompt and resource |
| [Development](development.md) | Architecture, source map, tests, CI and sidecar development |

For the shortest setup, return to the [main README](../README.md).

## How it fits together

```text
MCP client / AI assistant
          |
          v
    champions-mcp --------> champions-calc (@smogon/calc)
          |  \-----------> champions-sim  (Pokémon Showdown)
          v
    champions.sqlite
          ^
          |
    champions-mcp-pull <--- upstream data sources
```

The Python server reads a local SQLite mirror instead of scraping during tool
calls. The pull command builds that mirror ahead of time. Damage calculations and
battle simulations use separate Node.js services; `create_pokepaste` uploads a
team to pokepast.es only when called.

The optional web chat connects to the same MCP endpoint and supplies the AI
tool-calling loop, using either a local llama.cpp model or OpenRouter. The core
MCP server does not need an LLM, GPU, or model API key of its own.

Champions has its own roster, movepools, item pool, Mega forms and Stat Points.
Data from other Pokémon games is not interchangeable. Start with
`get_game_rules(format)` to inspect the rules and data currently installed.
