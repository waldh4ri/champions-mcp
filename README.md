# champions-mcp

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants build and
improve **Pokémon Champions** teams for VGC doubles and BSS singles.

It provides Champions-specific Pokémon data, legal moves and items, usage stats,
team validation, damage calculations, battle simulation, and Poképaste export.
Game data is stored locally in SQLite; normal lookups do not scrape websites.

## Run with Docker

Requires Docker with Docker Compose. From the repository root:

```sh
docker compose up -d --build
```

To also start a web chat at http://localhost:3000 (models and API keys are set up
in its **⚙ Configure** view):

```sh
docker compose --profile webchat up -d --build
```

This starts the MCP server and its damage-calculator and battle-simulator services.
The first start downloads the data and can take several minutes. Check readiness:

```sh
docker compose logs -f champions-mcp
```

When the server reports **ready**, connect your MCP-compatible AI client to
**http://localhost:8000/mcp** using Streamable HTTP.

```sh
# Claude Code
claude mcp add --transport http champions http://localhost:8000/mcp

# Codex CLI
codex mcp add champions --url http://localhost:8000/mcp

# OpenCode (interactive)
opencode mcp add
```

To stop the stack without deleting its data:

```sh
docker compose down
```

## Use it

Once connected, ask your assistant:

- “Build me an offmeta bulky offense team for VGC around Mega Slowbro.”
- “Check whether this Champions BSS team is legal: …”
- “What are the most-used Pokémon in Champions singles?”

The assistant should call `get_game_rules` first, with `format="vgc"` or
`format="bss"`. The bundled `champions_team_building` prompt provides the full
workflow; clients without prompt support can use `get_team_building_guide`.

## Run locally

Requires Python 3.11+. From the repository root:

```sh
pip install -e .
champions-mcp-pull
champions-mcp
```

The server uses **stdio** by default: configure your MCP client to launch
`champions-mcp`. Damage calculations and battle simulation still need Docker
images or running sidecars; other data tools work without them.

## Documentation

See **[docs/README.md](docs/README.md)** for client configuration, optional web
chat, data refreshes, configuration, the tool reference, and development.

Unofficial fan project; not affiliated with Nintendo, Creatures Inc., or GAME FREAK.
