# Getting started

[Documentation index](README.md)

Run commands from the repository root. Choose Docker for the complete stack, or
a local Python installation if your MCP client should launch the server directly.

## Docker

Requires Docker Engine or Docker Desktop with the Compose plugin. The optional
web chat profile uses `depends_on.required`, so use Compose 2.20+.

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

The first start builds the images and pulls the core game data into the
`champions-data` volume (about 2 minutes) before serving. The observed-team
corpus (tournament and ladder teams, used by meta simulations and observed sets)
is then fetched in the background: Limitless's unauthenticated rate limit of 50
requests per five minutes makes that about 15 minutes cold. Until it finishes,
`simulate_vs_meta` / `compare_team_variants` report that the corpus is still
being fetched; everything else works. The log shows each pull stage and its
progress. Wait for the `ready — MCP endpoint is up` line
(it prints the URL to use) before connecting. The lines after it confirm the
calc and sim sidecars are reachable.

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

### Coding agents (Claude Code, Codex, OpenCode, pi)


```sh
# Claude Code
claude mcp add --transport http champions http://localhost:8000/mcp

# Codex CLI
codex mcp add champions --url http://localhost:8000/mcp
```

**OpenCode** — `opencode mcp add` (interactive), or add to `opencode.json`:

```json
{ "mcp": { "champions": { "type": "remote", "url": "http://localhost:8000/mcp", "enabled": true } } }
```

**pi** has no built-in MCP support; install the adapter (`pi install npm:pi-mcp-adapter`),
then add to `~/.config/mcp/mcp.json` (or a project `.mcp.json`) and restart pi:

```json
{ "mcpServers": { "champions": { "url": "http://localhost:8000/mcp" } } }
```

Changed `MCP_PORT`? Use that port instead of 8000.

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

A chat UI at **http://localhost:3000** on top of the core MCP stack; you do not
need a separate MCP client for this interface. Two profiles:

```sh
docker compose --profile webchat up -d --build       # local models on CPU + RAM (any machine)
docker compose --profile webchat-gpu up -d --build   # local models on an NVIDIA GPU
docker compose --profile "*" down                    # stop, whichever was started
```

Both offer the same chat, API providers and model management; they differ in
where local models run. `webchat-gpu` reserves the GPU, which Docker cannot make
optional: on a machine without the NVIDIA container runtime its `up` fails, so
use `webchat` there. Downloaded models are shared by both.

Compose only acts on the profiles active for each command: a chat started with
`--profile …` is skipped by a plain `docker compose down`, which then fails with
*network … still in use*. Stop it with the same profile or `--profile "*"`. To
avoid the flags, set the profile once in `.env`, and plain `up`, `down`, `logs`
and `ps` include it:

```dotenv
COMPOSE_PROFILES=webchat-gpu
```

Then open **⚙ Configure** (top right). Everything below is set up there, no
restart needed; the model picker groups what is available by backend:

| Backend | Setup | Cost |
|---|---|---|
| **OpenRouter** | *API providers*: paste your key (or `OPENROUTER_API_KEY` in `.env`). The Auto Router at low/medium/high cost tiers is always offered; search the catalogue to add models. | Billed per token; cheapest provider first by default. The header's **$** meter shows the chat's spend. |
| **Local model** (llama.cpp) | *Local models*: pick a suggested model or paste a Hugging Face link. It downloads in the background, then loads on first use. | Free; runs on your CPU (`webchat`) or GPU (`webchat-gpu`). |
| **Any OpenAI-compatible API** | *API providers*: base URL (+ key). LM Studio, Ollama, vLLM, a hosted provider. | Per that API. |

**OpenRouter** sends prompts and tool results to external model providers and
may incur charges. Its default low tier has a price cap; the medium/high tiers
are uncapped unless configured. See [OpenRouter settings](configuration.md#openrouter).

**Local models** run on CPU + system RAM with `webchat`: any machine, but slow —
on 24 cores, Qwen3.5-9B generates ~12 tokens/s and reads ~50 tokens/s, so the
first message (about 15k tokens of tool definitions) takes several minutes before
later turns reuse it; the smaller suggested model is faster. With `webchat-gpu` they
run on an NVIDIA GPU, which needs Docker GPU support (NVIDIA Container Toolkit
on Linux, WSL2 GPU support with Docker Desktop) and enough GPU memory. No model
is shipped or downloaded until you pick one.

The suggested model, `unsloth/Qwen3.5-9B-MTP-GGUF:UD-Q4_K_XL` (6 GB), calls
tools reliably and fits a 12 GB card with its full context; on a 16 GB RTX 4080
it generates ~115 tokens/s with MTP speculative decoding. Context size, KV
cache, MoE offload, reasoning budget and MTP are per-model settings in
*Inference* ([details](configuration.md#local-llamacpp-models)). You can also
drop `.gguf` files into `models/`.

`docker compose logs -f champions-chat llama-server` shows which backends are
up, download/load progress, each chat turn with its tool rounds, token rates
and cost.

## Update, refresh, reset


| Goal | Command | Data |
|---|---|---|
| Update to new code | `git pull && docker compose up -d --build` | kept; re-pulled automatically only if the new code needs a newer data format |
| Refresh game data now | `docker compose exec champions-mcp champions-mcp-pull && docker compose restart champions-mcp` | rebuilt from a warm cache (~5 min) while the server keeps serving; the restart picks it up |
| Rebuild images from scratch | `docker compose build --no-cache && docker compose up -d` | kept |
| Full reset | `docker compose --profile "*" down -v && docker compose up -d --build` | **deleted** (also the pull cache and `llama_models`); serving again after the core pull (~2 min), corpus in the background |

On start, data older than 7 days (`CHAMPIONS_MCP_MAX_DATA_AGE_DAYS`) or a
`CHAMPIONS_MCP_PULL=1` setting refreshes **in the background**: the current data
keeps being served and the new data is picked up when the pull finishes. Only
missing data, or data built by an older version, is rebuilt before serving.
Follow any of these with `docker compose logs -f champions-mcp`: each pull stage
logs its progress, and `background … refresh finished` marks the switch.

## Logs


```sh
docker compose logs -f                  # the whole stack
docker compose logs -f champions-mcp    # pull, readiness, every tool call
```

Every service writes one colored line per event, such as:

```text
INFO  tool   [d44aa6] ✓ calc_damage took=22ms attacker=Garchomp move=Earthquake defender=Incineroar dmg=81.2-96.5% ko="guaranteed 2HKO"
INFO  calc   [d44aa6] Garchomp Earthquake → Incineroar dmg=81.2-96.5% ko="guaranteed 2HKO" hp=170 ms=3.9
INFO  sim    [8c68a1] … simulate running battles=4224/8400 elapsed=30.0s rate=141/s
```

The `[id]` ties a tool call to the calc/sim work it caused (and a web-chat turn to
all its tool calls). Set `LOG_LEVEL=DEBUG` for more detail, or `LOG_COLOR=0` for
plain text, in `.env`.

## Troubleshooting

| Symptom | Check |
|---|---|
| MCP connection refused on first start | Follow `docker compose logs -f champions-mcp`; the initial data pull may still be running (its `[n/7]` stage lines show where it is). |
| A tool call misbehaves | Find its `✗ <tool>` line in `docker compose logs champions-mcp`, then grep the whole stack for its `[id]` to see the calc/sim side; `LOG_LEVEL=DEBUG` adds detail. |
| `champions database not found` | Run `champions-mcp-pull`; ensure the client uses the same data path. |
| Import error for `mcp.server.mcpserver` | Install the project dependencies in the interpreter used by your MCP client; SDK 2.x is required. |
| No observed teams / no meta opponents | Remove `CHAMPIONS_MCP_BUILDS_SKIP=1` or omit `--skip-builds`, then refresh. |
| Damage or simulation engine unavailable | Check `docker compose logs champions-calc champions-sim`; for a local server, build the images or configure reachable sidecar URLs. |
| Local models unavailable in ⚙ Configure | `docker compose logs llama-server` (or `llama-server-gpu`) states why it idles. |
| `--profile webchat-gpu up` fails with *could not select device driver "nvidia"* | Docker has no NVIDIA GPU runtime on this machine: install the NVIDIA Container Toolkit (Linux) or enable WSL2 GPU support (Docker Desktop), or use `--profile webchat`. |
| A local model shows *failed* | `docker compose logs llama-server` (or `llama-server-gpu`) has llama.cpp's reason, usually memory: in *Inference*, pin a smaller context, use q4_0 KV cache, or offload MoE experts to the CPU. |
| Local model fails to load / runs out of memory | Check `docker compose logs llama-server` (or `llama-server-gpu`); verify the GGUF, GPU access and memory; reduce context or increase CPU MoE offload. |
| Chat cannot reach a provider | Check `docker compose logs champions-chat`, the API key, and `CHAT_PROVIDER`. |
| `down` fails with *network … still in use* | The web chat was started with a `--profile` but stopped without it. Run `docker compose --profile "*" down`, or set `COMPOSE_PROFILES` in `.env` so plain commands include it. |
| Host port already in use | Set `MCP_PORT`, `WEBUI_PORT` or `LLAMA_PORT` in `.env`, then run Compose `up -d` again. |

These services do not configure authentication. Compose publishes ports on host
interfaces by default; use them only on trusted networks, or add loopback-only
port bindings / an authenticated proxy before exposing them remotely.
