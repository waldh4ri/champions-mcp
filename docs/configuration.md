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

`docker compose --profile webchat up -d --build` serves the chat at
**http://localhost:3000**, with local models on CPU + RAM; `--profile
webchat-gpu` runs them on an NVIDIA GPU instead (either can be set as
`COMPOSE_PROFILES` in `.env`). Most of its settings live in its **⚙ Configure**
view and apply without a restart; the environment variables below set their
defaults, and what you change in the view is saved in the `chat-data` volume
(API keys included, server-side only) and wins over them.

| Configure tab | What it controls |
|---|---|
| **Local models** | Download a model from Hugging Face (paste a link or `user/repo:quant`), watch download/load progress, load, unload, pause/resume, delete. |
| **Inference** | llama.cpp settings, for all models or one model: context size, KV cache types, GPU layers, MoE CPU offload, flash attention, reasoning budget, speculative decoding (MTP). |
| **Chat** | Thinking, sampling, tool-round and tool-result budgets. |
| **API providers** | OpenRouter (key, provider choice, price cap, reasoning effort, the model list with catalogue search and prices, account spend) and OpenAI-compatible APIs. |

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_PROVIDER` | `auto` | `auto` offers every available backend; `llama` or `openrouter` restricts the chat to that one. |
| `MCP_URL` | `http://champions-mcp:8000/mcp` | MCP endpoint reached by the chat service. |
| `LLAMA_BASE_URL` | `http://llama-server:8080` | The local llama-server (router). |
| `CHAT_DATA_DIR` | `/data` | Where the Configure view's settings, endpoints and llama presets are stored (`chat-data` volume). |
| `MAX_TOOL_ROUNDS` | Context-dependent | Default for *Tool rounds per turn*: 40, or 80 for models with at least 500k context. |
| `MAX_TOOL_RESULT_CHARS` | Context-dependent | Default for *Max characters per tool result*: 16,000, or 60,000 for large-context models. |
| `MAX_REASONING_HISTORY_TURNS` | `3` | Number of previous turns whose reasoning is replayed. |
| `MAX_REASONING_CHARS` | `4000` | Character cap on each replayed reasoning block. |
| `HISTORY_CONTEXT_SHARE` | `0.5` | Context share available for history before older tool chains are shortened/dropped. |
| `DEFAULT_CONTEXT_SIZE` | `131072` | Context assumed for a model that doesn't report one (a local model before its first load, an endpoint without a configured size). |

The model picker groups models by backend; a chat remembers its model. Each
chat turn's id (`[3f9c1a]` in `docker compose logs`) also tags the MCP, calc
and sim lines it caused.

## OpenRouter

Offered whenever an API key is set: in **⚙ Configure → API providers**, or as
`OPENROUTER_API_KEY` in `.env`. No GPU needed. Prompts and tool results go to
the model's provider and are billed to the key; the header's **$** meter shows
the current chat's cost (from each response's `usage.cost`) and, on hover, the
key's spend today / this month and the remaining budget.

The model picker always offers OpenRouter's **Auto Router** at three cost
tiers, `openrouter/auto (low|medium|high)`: it picks a model in that cost band
once per turn, then the chat keeps it for the turn's tool rounds. Add fixed
models from the Configure view (search the catalogue: tool-capable models only,
cheapest first, with prices and context) or with `OPENROUTER_MODEL`.

When several providers serve a model, requests go to the **cheapest first**
(OpenRouter's `provider.sort = "price"`); the Configure view switches this to
throughput, latency or OpenRouter's default load balancing.

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | Unset | API key (a key saved in the Configure view takes precedence). |
| `OPENROUTER_MODEL` | `deepseek/deepseek-v4.1-flash,openai/gpt-6-luna,google/gemini-4-argon` | Fixed models, until the list is edited in the Configure view. |
| `OPENROUTER_PROVIDER_SORT` | `price` | `price`, `throughput`, `latency`, or `default` (OpenRouter's load balancing). |
| `OPENROUTER_AUTO_MODELS` | `deepseek/deepseek-v4.1-flash,openai/gpt-6-luna` | Low-tier candidate pool; exact IDs or wildcards such as `deepseek/*`. |
| `OPENROUTER_AUTO_MODELS_MEDIUM` | Empty | Medium-tier pool; empty uses the router's ranking for that band. |
| `OPENROUTER_AUTO_MODELS_HIGH` | Empty | High-tier pool; empty uses the router's ranking for that band. |
| `OPENROUTER_MAX_PRICE` | `1` | Default price cap, USD per million input **and** output tokens, for fixed models and the low tier; `0` = no cap. |
| `OPENROUTER_MAX_PRICE_MEDIUM` | Empty | Medium-tier price cap; empty means no cap. |
| `OPENROUTER_MAX_PRICE_HIGH` | Empty | High-tier price cap; empty means no cap. |
| `OPENROUTER_MODEL_ROUTES` | Built-in Gemini route | JSON mapping of model IDs to OpenRouter provider-routing objects; a matching route replaces the price cap and provider sort. |
| `OPENROUTER_REASONING_EFFORT` | Unset | Default `low`, `medium`, or `high`; unset leaves the model default. |
| `OPENROUTER_TEMPERATURE` | Unset | Default sampling override; unset leaves the model default. |

These fixed models are project defaults, not guarantees of ongoing upstream
availability. By default `google/gemini-4-argon` is restricted to
`google-ai-studio/flex`, with fallback disabled and route-specific
prompt/completion caps of `$1`/`$5` per million tokens.
`OPENROUTER_MODEL_ROUTES` accepts the same shape for custom routes:

```json
{
  "google/gemini-4-argon": {
    "only": ["google-ai-studio/flex"],
    "allow_fallbacks": false,
    "max_price": {"prompt": 1, "completion": 5}
  }
}
```

Caps restrict eligible providers; they are not a total spending budget (set a
budget on the key at openrouter.ai). **Medium and high tiers have no default
price cap.**

## OpenAI-compatible APIs

Any server that speaks the OpenAI chat API with tool calling (vLLM, LM Studio,
Ollama's `/v1`, a hosted provider) can back the chat: add it in **⚙ Configure →
API providers** (name, base URL ending in `/v1`, optional key, optional model
list, context size) or with these variables. From inside the containers, your
host machine is `host.docker.internal`.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_COMPAT_BASE_URL` | Unset | e.g. `http://host.docker.internal:1234/v1`. |
| `OPENAI_COMPAT_API_KEY` | Unset | Sent as a bearer token. |
| `OPENAI_COMPAT_MODELS` | Empty | Comma-separated models; empty = the API's `/models` list. |
| `OPENAI_COMPAT_NAME` | URL host | Label of its group in the model picker. |

## Local llama.cpp models

`llama-server` runs in llama.cpp's **router mode**: it starts with no model
loaded and loads whichever model a chat picks (unloading the previous one when
`LLAMA_MODELS_MAX` are loaded), so switching models needs no restart. Models
come from:

- **⚙ Configure → Local models**: paste a Hugging Face link (a repo, or a
  specific `.gguf` file) or `user/repo:quant`. It downloads in the background
  into the `llama_models` volume, together with the model's draft head (MTP,
  DFlash, EAGLE3) when the repo has one, and loads on first use. The view
  suggests `unsloth/Qwen3.5-9B-MTP-GGUF:UD-Q4_K_XL` (6 GB: strong tool calling,
  its full context fits a 12 GB GPU).
- `.gguf` files you put in `models/` (multi-file models in a subdirectory).

`--profile webchat` runs it on CPU + system RAM (`llama-server`, llama.cpp's CPU
build): any machine, a few tokens/s, small models fit best. `--profile
webchat-gpu` runs it on an NVIDIA GPU (`llama-server-gpu`, CUDA build), which
reserves the GPU: on a machine without the NVIDIA container runtime that `up`
fails. Both share the downloaded models and these settings; GPU-only ones (GPU
layers, MoE offload) have no effect on the CPU.

**Inference settings** are edited in **⚙ Configure → Inference**, for all
models or per model. The chat writes them to llama.cpp's preset file
(`presets.ini` in the `chat-data` volume, read by `llama-server`) and has it
reload: a running model whose settings changed is unloaded, and its next use
loads it with them. Their defaults come from these `champions-chat` variables:

| Variable | Default | Configure field |
|---|---|---|
| `LLAMA_CTX_SIZE` | Empty | Context size. Empty = llama.cpp's `--fit` picks the largest context the GPU holds, per model. |
| `LLAMA_CACHE_TYPE_K` / `LLAMA_CACHE_TYPE_V` | `q8_0` | KV cache types (q8_0: half of f16's memory, practically the same quality). |
| `LLAMA_N_GPU_LAYERS` | `99` | GPU layers (99 = all). |
| `LLAMA_N_CPU_MOE` | Empty | MoE layers whose experts stay in system RAM, for MoE models bigger than VRAM (e.g. 18 for Qwen3.6-35B-A3B on 16 GB). |
| `LLAMA_FLASH_ATTN` | `on` | Flash attention. |
| `LLAMA_REASONING_BUDGET` | `2048` | Thinking tokens per generation; `-1` unrestricted, `0` none. |
| `LLAMA_MTP_ENABLED` | `1` | Speculative decoding with the model's own draft head when it has one (a downloaded draft sidecar, or MTP layers built into the GGUF — repos named `*-MTP-GGUF`). |
| `LLAMA_THINK` | `true` | Thinking (Chat tab). |
| `LLAMA_TEMPERATURE`, `LLAMA_TOP_P`, `LLAMA_TOP_K`, `LLAMA_MIN_P` | Mode-dependent | Sampling (Chat tab): Qwen's thinking `0.6/0.95/20/0` or instruct `0.7/0.8/20/0` profile when unset. |
| `LLAMA_PRESENCE_PENALTY` | `0.5` | Mild repetition penalty (Chat tab). |

Server-wide settings stay on the `llama-server` service:

| Variable | Default | Purpose |
|---|---|---|
| `LLAMA_IMAGE` | `champions-llama:latest` | llama.cpp image tag. |
| `LLAMA_MODELS_MAX` | `1` | Models loaded at once. |
| `LLAMA_PARALLEL` | `1` | Concurrent inference slots. |
| `LLAMA_REASONING_PRESERVE` | `1` | Keep reasoning across turns where the template supports it. |
| `LLAMA_NO_MMAP` | `1` | Read weights into RAM up front (`--load-mode none`): steadier decode, exact load progress. |
| `LLAMA_MLOCK` | `0` | `1` selects `--load-mode mmap+mlock`; takes precedence over `LLAMA_NO_MMAP`. |
| `HF_TOKEN` | Unset | Hugging Face token, for gated or private repos. |
| `LLAMA_EXTRA_ARGS` | Empty | Additional llama-server flags for every model. They override the Configure view's settings. |

If a model fails to load (it shows *failed* in the Local models tab),
`docker compose logs llama-server` (or `llama-server-gpu`) has llama.cpp's reason: usually memory —
pin a smaller context, use a q4_0 KV cache, or offload MoE experts to the CPU.
