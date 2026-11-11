# Getting started

[Documentation index](README.md)

Run commands from the repository root. Choose Docker for the complete stack, or
a local Python installation if your MCP client should launch the server directly.

## Docker

Requires Docker Engine or Docker Desktop with the Compose plugin. The optional
chat profiles use `depends_on.required`, so use Compose 2.20+.

```sh
docker compose up -d --build
docker compose logs -f champions-mcp
```

The core stack starts:

| Service | Purpose | Host endpoint |
|---|---|---|
| `champions-mcp` | MCP server, Streamable HTTP | http://localhost:8000/mcp |
| `champions-calc` | Damage calculator | Internal Docker network only |
| `champions-sim` | Battle simulator | Internal Docker network only |

The first start builds the images and pulls game data into the `champions-data`
volume. Allow several minutes: the observed-team stage respects Limitless's
unauthenticated rate limit of 50 requests per five minutes. Wait for the
`champions-mcp v2 ready` banner before connecting.

For a faster initial setup, add `CHAMPIONS_MCP_BUILDS_SKIP=1` to a repository-root
`.env` file before starting. This omits observed teams **and BSS team reports**;
basic data tools still work, but meta simulations need that corpus. Remove the
setting and [refresh the data](data.md#refreshing-the-data) to add it later.

```sh
docker compose ps
docker compose down
```

`down` keeps the data volumes. **`docker compose down -v` deletes them**, including
the game database, pull cache, and any model downloads in `llama_models`.

## Connect an MCP client

### Docker / Streamable HTTP

In a client that supports local HTTP MCP servers, add a server with:

- **Name:** `champions`
- **Transport:** Streamable HTTP
- **URL:** `http://localhost:8000/mcp`

Some clients accept a configuration like this; use your client's schema if it
differs:

```json
{
  "mcpServers": {
    "champions": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

The MCP endpoint is a protocol endpoint, not a web page or chat interface. A
client limited to stdio should use the local installation below.

### Local Python / stdio

Requires Python 3.11+ and MCP SDK **2.x** (`mcp>=2.0.0`, installed by the package).
SDK 1.x does not provide the `MCPServer` API used here.

Create and activate a virtual environment:

```sh
python -m venv .venv
```

macOS / Linux:

```sh
. .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Then install and build the database:

```sh
pip install -e .
champions-mcp-pull
```

`champions-mcp-pull --skip-builds` is the faster alternative without observed
teams or reports. `python -m champions_mcp.pull` is equivalent to the pull command.

Configure your client to launch the installed executable. Generic stdio example
(replace all paths with absolute paths on your machine):

```json
{
  "mcpServers": {
    "champions": {
      "command": "/absolute/path/to/repo/.venv/bin/champions-mcp",
      "env": {
        "CHAMPIONS_MCP_DATA_DIR": "/absolute/path/to/repo/data"
      }
    }
  }
}
```

On Windows, use `C:\\path\\to\\repo\\.venv\\Scripts\\champions-mcp.exe` and
`C:\\path\\to\\repo\\data` in JSON. Keep the editable checkout available: it
contains the static format rules and bundled team-building skill.

Running `champions-mcp` manually starts stdio mode; waiting for protocol input is
normal. Your client usually starts and stops this process itself.

For a local HTTP server instead, set `MCP_TRANSPORT=streamable-http` before
launching `champions-mcp`. For example, in PowerShell:

```powershell
$env:MCP_TRANSPORT = "streamable-http"
champions-mcp
```

The default bind address is `127.0.0.1:8000`. On a POSIX shell, use
`MCP_TRANSPORT=streamable-http champions-mcp`.

### Damage and simulation with a local server

The Python installation does not install the Node.js engines. To enable them,
build their images once:

```sh
docker build -f docker/calc/Dockerfile -t champions-calc:latest .
docker build -f docker/sim/Dockerfile -t champions-sim:latest .
```

By default the local MCP server invokes these images with one-shot `docker run`
calls. Alternatively set `CHAMPIONS_MCP_CALC_HTTP` and `CHAMPIONS_MCP_SIM_HTTP`
to sidecar URLs reachable from the Python process. Compose's service names are
only reachable inside its Docker network; its sidecar ports are not published.
See [configuration](configuration.md#calculation-and-simulation).

## Optional web chat

Both options below serve a chat UI at **http://localhost:3000** and use the core
MCP stack. You do not need a separate MCP client for this interface.

### OpenRouter (no GPU)

Create or edit `.env` in the repository root and add your key:

```dotenv
OPENROUTER_API_KEY=sk-or-your-key
```

```sh
docker compose --profile openrouter up -d --build
```

Choose a model in the UI and ask a Champions question. OpenRouter sends prompts
and tool results to external model providers and may incur charges. Its default
low-tier router has a price cap; the medium/high tiers are uncapped unless
configured. See [OpenRouter settings](configuration.md#openrouter).

### Local model (NVIDIA GPU)

Requires an NVIDIA GPU, Docker GPU support (NVIDIA Container Toolkit on Linux),
and sufficient GPU/system memory for the model. No model file is shipped.

1. Put a tool-capable GGUF in `models/`.
2. Set `LLAMA_MODEL_FILE` to its filename in `.env`. The default is
   `unsloth_Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`; the other defaults are tuned for that
   model, not arbitrary GGUFs.
3. Start the profile:

```sh
docker compose --profile ai up -d --build
```

Alternatively set `LLAMA_MODEL_HF` to a llama.cpp-compatible Hugging Face reference,
such as `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M`. It takes precedence over the local
filename and downloads into `llama_models`.

This adds `llama-server` at port 8080 and `champions-chat` at port 3000. The chat
waits for the model to load. With `CHAT_PROVIDER=auto`, a reachable local model is
preferred over OpenRouter even when an API key is present.

## Troubleshooting

| Symptom | Check |
|---|---|
| MCP connection refused on first start | Follow `docker compose logs -f champions-mcp`; the initial data pull may still be running. |
| `champions database not found` | Run `champions-mcp-pull`; ensure the client uses the same data path. |
| Import error for `mcp.server.mcpserver` | Install the project dependencies in the interpreter used by your MCP client; SDK 2.x is required. |
| No observed teams / no meta opponents | Remove `CHAMPIONS_MCP_BUILDS_SKIP=1` or omit `--skip-builds`, then refresh. |
| Damage or simulation engine unavailable | Check `docker compose logs champions-calc champions-sim`; for a local server, build the images or configure reachable sidecar URLs. |
| Local model fails to load / runs out of memory | Check `docker compose logs llama-server`; verify the GGUF filename, GPU access and memory; reduce context or increase CPU MoE offload. |
| Chat cannot reach a provider | Check `docker compose logs champions-chat`, the API key, and `CHAT_PROVIDER`. |
| Host port already in use | Set `MCP_PORT`, `WEBUI_PORT` or `LLAMA_PORT` in `.env`, then run Compose `up -d` again. |

These services do not configure authentication. Compose publishes ports on host
interfaces by default; use them only on trusted networks, or add loopback-only
port bindings / an authenticated proxy before exposing them remotely.
