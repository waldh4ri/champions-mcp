# Prerequisite

This skill requires the `champions-mcp` MCP server. If its tools are not
available, tell the user the server isn't connected and stop — do not fall back
to memory/Scarlet-Violet assumptions.

### Connecting via docker-compose (recommended)

```bash
docker compose up -d --build
```

On first start the server builds its local database from pokechamp.gg
(takes ~30s), then prints the ready URL:

```
MCP URL : http://localhost:8000/mcp
```

Register it in VS Code `settings.json` or any MCP-HTTP client:

```json
"mcp": { "servers": { "champions-mcp": { "type": "http",
  "url": "http://localhost:8000/mcp" } } }
```

### Connecting via stdio (local dev)

```bash
pip install -e ".[dev]"
champions-mcp-pull     # build the local database once
champions-mcp          # stdio MCP server
```
