# Configuration

[Documentation index](README.md) · [Example environment file](../.env.example)

## How settings are applied

- **Docker Compose:** create or edit `.env` in the repository root, then run the
  same `docker compose [--profile ...] up -d` command again. Compose recreates
  services whose configuration changed. `restart` alone does not apply new values.
- **Local Python:** export variables in the shell or set them in your MCP
  client's `env` object. The Python server and pull command **do not load `.env`
  automatically**.
- **Scope matters:** Compose forwards only variables explicitly wired in
  [`docker-compose.yml`](../docker-compose.yml). Setting an arbitrary Python
  setting in `.env` does not inject it into a container. For additional settings,
  add them to the appropriate service's `environment` with a Compose override.

All settings are optional except a model source for local chat or an API key for
OpenRouter chat. Do not commit real API keys. Defaults below refer to local
Python unless a setting is identified as Compose- or sidecar-specific.

## Server, paths and ports

| Variable | Default | Purpose |
|---|---|---|
| `CHAMPIONS_MCP_DATA_DIR` | `<repo>/data` | Data directory; Compose uses `/data`. |
| `CHAMPIONS_MCP_DB` | `<data>/champions.sqlite` | Served database; Compose uses `/data/champions.sqlite`. |
| `CHAMPIONS_MCP_CACHE_DB` | `<data>/pull-cache.sqlite` | Pull-time HTTP cache; Compose uses `/data/pull-cache.sqlite`. |
| `MCP_TRANSPORT` | `stdio` | `stdio`, `streamable-http`, or legacy `sse`; Compose fixes this to `streamable-http`. |
| `FASTMCP_HOST` | `127.0.0.1` | HTTP bind address; Compose fixes it to `0.0.0.0` inside the container. |
| `FASTMCP_PORT` | `8000` | HTTP listen port; Compose fixes it to `8000` inside the container. |
| `MCP_PORT` | `8000` | Compose host port for the MCP endpoint. |
| `WEBUI_PORT` | `3000` | Compose host port for optional web chat. |
| `LLAMA_PORT` | `8080` | Compose host port for optional llama-server. |

`FASTMCP_*` names are retained for configuration compatibility; the implementation
uses MCP SDK 2.x `MCPServer`, not SDK 1.x `FastMCP`. HTTP bind variables have no
effect on stdio mode. Changing `MCP_PORT` does not require changing internal chat
or sidecar URLs.

Use absolute data paths in MCP client configurations. Keep the database, pull
cache and report cache on persistent storage if you want to reuse downloads.

## Pull and freshness

| Variable | Default | Purpose |
|---|---|---|
| `CHAMPIONS_MCP_PULL` | `0` | Container entrypoint: `1` forces a pull at each start while set. |
| `CHAMPIONS_MCP_MAX_DATA_AGE_DAYS` | `7` | Container startup freshness limit; `0` disables only the age check. Older data-format versions still trigger a pull. |
| `CHAMPIONS_MCP_POKECHAMP_BASE` | `https://pokechamp.gg` | Roster, movepools and reference source. |
| `CHAMPIONS_MCP_POKEDB_BASE` | `https://champs.pokedb.tokyo` | Ranked usage and observed ladder teams. |
| `CHAMPIONS_MCP_POKEDB_CONCURRENCY` | `4` | Maximum concurrent pokedb usage-page requests. |
| `CHAMPIONS_MCP_POKEAPI_BASE` | `https://pokeapi.co/api/v2` | Type chart and localized species names. |
| `CHAMPIONS_MCP_HTTP_TIMEOUT` | `20` | Pull HTTP timeout, seconds. |
| `CHAMPIONS_MCP_USER_AGENT` | Project user agent | Pull request identification header. |
| `CHAMPIONS_MCP_LIMITLESS_BASE` | `https://play.limitlesstcg.com/api` | Tournament teamlist API. |
| `CHAMPIONS_MCP_LIMITLESS_KEY` | Unset | Optional API key; raises the rate limit, not required for access. |
| `CHAMPIONS_MCP_BUILDS_REGULATIONS` | `M-C` | Comma-separated regulations to ingest. |
| `CHAMPIONS_MCP_BUILDS_MAX_EVENTS` | `150` | Maximum tournaments per regulation. |
| `CHAMPIONS_MCP_BUILDS_MIN_PLAYERS` | `16` | Skip smaller events. |
| `CHAMPIONS_MCP_BUILDS_HALF_LIFE_DAYS` | `21` | Recency half-life for observation weights. |
| `CHAMPIONS_MCP_BUILDS_SKIP` | `0` | Container entrypoint: `1` passes `--skip-builds` to the pull. |
| `CHAMPIONS_MCP_REPORTS_SKIP` | `0` | `1` skips BSS report scraping while retaining the other observed-team sources. |
| `CHAMPIONS_MCP_POKEDB_SEASON_REGS` | Built-in map | Override season-to-regulation mapping, e.g. `1:M-A,2:M-A,3:M-A,4:M-B,5:M-B,6:M-C,7:M-C,8:M-C`. |

`CHAMPIONS_MCP_PULL`, `CHAMPIONS_MCP_MAX_DATA_AGE_DAYS`, and
`CHAMPIONS_MCP_BUILDS_SKIP` are startup-script controls, not switches read by the
local server. Locally, explicitly run `champions-mcp-pull` and use `--skip-builds`
to omit the observed-team stage. The historical Smogon sets are still pulled with
that flag. Reports run inside the observed-team stage, so that flag skips them too.

Freshness is checked **on container start**, not on a background schedule. See
[refreshing data](data.md#refreshing-the-data) for commands and failure behavior.

## Calculation and simulation

| Variable | Default | Purpose |
|---|---|---|
| `CHAMPIONS_MCP_CALC_HTTP` | Unset | Damage sidecar URL; Compose sets `http://champions-calc:3000/`. |
| `CHAMPIONS_MCP_DOCKER` | `docker` | Docker executable for one-shot engine calls. |
| `CHAMPIONS_MCP_CALC_IMAGE` | `champions-calc:latest` | One-shot calculator image. |
| `CHAMPIONS_MCP_CALC_TIMEOUT` | `30` | Damage request timeout, seconds. |
| `CHAMPIONS_MCP_SIM_HTTP` | Unset | Simulator URL; Compose sets `http://champions-sim:3000/`. |
| `CHAMPIONS_MCP_SIM_IMAGE` | `champions-sim:latest` | One-shot simulator image. |
| `CHAMPIONS_MCP_SIM_TIMEOUT` | `300` | Simulation request timeout, seconds. |
| `CHAMPIONS_MCP_SIM_MAX_BATTLES` | `20000` | Total battle budget per MCP call, including variants. |
| `CALC_IMAGE` | `champions-calc:latest` | Compose calculator image tag. |
| `SIM_IMAGE` | `champions-sim:latest` | Compose simulator image tag. |
| `SIM_WORKERS` | `0` | Sidecar worker count; `0` automatically uses allowed CPUs, capped by available memory. |
| `SIM_MAX_BATTLES` | `50000` | Sidecar's own per-request battle cap. |

An HTTP URL selects a long-running sidecar. Without it, Python uses a one-shot
container; the damage container runs with networking disabled. The core Compose
stack uses HTTP internally and does not require a Docker socket in the MCP
container. `calc_stats` and the speed tools run in Python and do not need a sidecar.

To reduce simulator CPU use, set `SIM_WORKERS` to a positive integer, or apply
CPU/memory limits to its service. Automatic worker sizing respects container
quotas and memory limits. Performance depends on hardware, team complexity and
the number of games; no fixed completion time is guaranteed.

## Web chat

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_PROVIDER` | `auto` | `auto`, `llama`, or `openrouter`. Auto probes local llama-server first, then uses OpenRouter if a key exists. |
| `MCP_URL` | `http://champions-mcp:8000/mcp` | MCP endpoint reached by the chat service. |
| `LLAMA_BASE_URL` | `http://llama-server:8080` | Local OpenAI-compatible model API. |
| `MAX_TOOL_ROUNDS` | Context-dependent | 40 rounds normally, 80 for models with at least 500k context; explicit value overrides both. |
| `MAX_TOOL_RESULT_CHARS` | Context-dependent | Normally 16,000 characters per tool result, 60,000 for large-context models. |
| `MAX_REASONING_HISTORY_TURNS` | `3` | Number of previous turns whose reasoning is replayed. |
| `MAX_REASONING_CHARS` | `4000` | Character cap on each replayed reasoning block. |
| `HISTORY_CONTEXT_SHARE` | `0.5` | Context share available for history before older tool chains are shortened/dropped. |

The tool-round/result limits scale automatically when unset. Avoid hard-coding
the local-model limits for a much larger-context OpenRouter model. A round can
contain multiple tool calls. The chat keeps earlier answers while trimming older
tool details as a conversation grows.

`MCP_URL` and `LLAMA_BASE_URL` are fixed internal URLs in the supplied Compose
file. `HISTORY_CONTEXT_SHARE` is a chat-process setting but is not forwarded by
the supplied Compose file; use a service environment override to change it there.

## OpenRouter

Start with `docker compose --profile openrouter up -d --build`. This runs the chat
without llama-server and requires `OPENROUTER_API_KEY`.

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | Unset | API key, kept server-side in the chat container. |
| `OPENROUTER_MODEL` | See below | Comma-separated UI model list; first is the default. |
| `OPENROUTER_AUTO_MODELS` | `deepseek/deepseek-v4.1-flash,openai/gpt-6-luna` | Low-tier candidate pool; exact IDs or wildcards such as `deepseek/*`. |
| `OPENROUTER_AUTO_MODELS_MEDIUM` | Empty | Medium-tier pool; empty uses the router's ranking for that band. |
| `OPENROUTER_AUTO_MODELS_HIGH` | Empty | High-tier pool; empty uses the router's ranking for that band. |
| `OPENROUTER_MAX_PRICE` | `1` | Maximum USD per million input **and** output tokens for fixed models and the low tier; empty disables the cap. |
| `OPENROUTER_MAX_PRICE_MEDIUM` | Empty | Medium-tier price cap; empty means no cap. |
| `OPENROUTER_MAX_PRICE_HIGH` | Empty | High-tier price cap; empty means no cap. |
| `OPENROUTER_MODEL_ROUTES` | Built-in Gemini route | JSON mapping of model IDs to OpenRouter provider-routing objects; a matching route replaces the general price cap. |
| `OPENROUTER_REASONING_EFFORT` | Unset | `low`, `medium`, or `high`; unset leaves the model default. |
| `OPENROUTER_TEMPERATURE` | Unset | Sampling override; unset leaves the model default. |

The default model selector contains:

```text
openrouter/auto (low)
openrouter/auto (medium)
openrouter/auto (high)
deepseek/deepseek-v4.1-flash
openai/gpt-6-luna
google/gemini-4-argon
```

These are project defaults, not guarantees of ongoing upstream availability.
Choose any available tool-calling OpenRouter model with `OPENROUTER_MODEL`.

The Auto Router chooses once per turn; the chat pins that model for the remaining
tool rounds. A per-conversation `session_id` helps preserve routing across turns.
The UI metrics show the model actually used.

By default `google/gemini-4-argon` is restricted to `google-ai-studio/flex`, with
fallback disabled and route-specific prompt/completion caps of `$1`/`$5` per
million tokens. `OPENROUTER_MODEL_ROUTES` accepts the same shape for custom routes:

```json
{
  "google/gemini-4-argon": {
    "only": ["google-ai-studio/flex"],
    "allow_fallbacks": false,
    "max_price": {"prompt": 1, "completion": 5}
  }
}
```

Check provider pricing before use. Caps restrict eligible providers; they are
not a total spending budget. **Medium and high tiers have no default price cap.**

## Local llama.cpp model

Start with `docker compose --profile ai up -d --build`. The supplied image and
Compose service expect NVIDIA GPU access. Defaults are tuned for the bundled
configuration's Qwen3.6-35B-A3B GGUF; other models/hardware need adjustment.

| Variable | Compose default | Purpose |
|---|---|---|
| `LLAMA_IMAGE` | `champions-llama:latest` | llama.cpp image tag. |
| `LLAMA_MODEL_FILE` | `unsloth_Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | Filename inside the bind-mounted `models/` directory. |
| `LLAMA_MODEL_HF` | Empty | Hugging Face model reference; overrides the filename and caches downloads in `llama_models`. |
| `LLAMA_MODEL_NAME` | `qwen3.6-35b-a3b` | API alias and model name sent by the chat. |
| `LLAMA_CTX_SIZE` | `131072` | Context tokens; also used by the chat's context meter. |
| `LLAMA_N_GPU_LAYERS` | `99` | GPU-offloaded layers. |
| `LLAMA_N_CPU_MOE` | `18` | llama.cpp `--n-cpu-moe` offload setting. |
| `LLAMA_PARALLEL` | `1` | Concurrent inference slots. |
| `LLAMA_CACHE_TYPE_K` / `LLAMA_CACHE_TYPE_V` | `q8_0` | KV-cache quantization. |
| `LLAMA_FLASH_ATTN` | `on` | Flash attention setting. |
| `LLAMA_NO_MMAP` | `1` | Select eager loading (`--load-mode none`) when memory locking is off. |
| `LLAMA_MLOCK` | `0` | `1` selects `--load-mode mmap+mlock`; takes precedence over `LLAMA_NO_MMAP`. |
| `LLAMA_THINK` | `true` | Enable the Qwen reasoning template. |
| `LLAMA_REASONING_PRESERVE` | `1` | Preserve reasoning through llama.cpp. |
| `LLAMA_REASONING_BUDGET` | `2048` | Thinking tokens per generation; `-1` unrestricted, `0` none, `off` omits the flag. |
| `LLAMA_TEMPERATURE` | Mode-dependent | Thinking: `0.6`; non-thinking: `0.7`. |
| `LLAMA_TOP_P` | Mode-dependent | Thinking: `0.95`; non-thinking: `0.8`. |
| `LLAMA_TOP_K` | `20` | Sampling top-k. |
| `LLAMA_MIN_P` | `0` | Sampling min-p. |
| `LLAMA_PRESENCE_PENALTY` | `0.5` | Mild repetition penalty. |
| `LLAMA_EXTRA_ARGS` | Empty | Additional llama-server flags, appended by the entrypoint. |

Optional speculative decoding is off by default:

| Variable | Effective default when enabled | Purpose |
|---|---|---|
| `LLAMA_MTP_ENABLED` | `0` (disabled) | `1` enables draft/speculative flags. |
| `LLAMA_MTP_SPEC_TYPE` | `draft-mtp` | Speculation type. |
| `LLAMA_MTP_MODEL_FILE` | Unset | Draft GGUF filename inside `models/`. |
| `LLAMA_MTP_MODEL_HF` | Unset | Draft Hugging Face reference; overrides the draft filename. |
| `LLAMA_MTP_DRAFT_MIN` / `LLAMA_MTP_DRAFT_MAX` | Unset | Optional draft-length flags. |
| `LLAMA_MTP_P_MIN` | Unset | Optional minimum draft probability. |
| `LLAMA_MTP_EXTRA_ARGS` | Empty | Extra speculation flags. |

If the GPU runs out of memory, reduce context or increase CPU MoE offload. A
smaller KV format can save memory at an accuracy cost. Speculative decoding is
hardware/model-dependent and can be slower; benchmark before enabling it.
