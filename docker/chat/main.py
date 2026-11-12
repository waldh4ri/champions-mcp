"""Light chat bridge: LLM (local llama.cpp server or OpenRouter) ↔ champions-mcp tools ↔ web UI.

Initialises an MCP session to champions-mcp, auto-loads every tool definition,
and drives the tool-calling loop server-side (over an OpenAI-compatible
/chat/completions API) so the single-page UI in static/ stays dumb. Two
kinds of backend, all offered when available (the UI's model picker chooses,
per chat; see backends.py for how a choice is encoded):

  local       llama.cpp's llama-server in router mode: every model in ./models
              or downloaded from Hugging Face in the Models panel, loaded on
              demand: on CPU + RAM (--profile webchat) or an NVIDIA GPU
              (--profile webchat-gpu).
  openrouter  any tool-calling model on OpenRouter (OPENROUTER_API_KEY,
              OPENROUTER_MODEL, plus the Auto Router at each cost tier), no GPU
              needed. Available whenever the key is set.
  endpoints   any OpenAI-compatible API (OPENAI_COMPAT_* or added in the UI).

CHAT_PROVIDER=llama|openrouter restricts the chat to one of the first two.
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from typing import Any, AsyncIterator

try:  # in the image: src/champions_mcp/log.py copied next to this file
    import cmlog
except ImportError:  # running from a checkout
    import sys as _sys

    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from champions_mcp import log as cmlog

cmlog.setup()
log = cmlog.get("chat")

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import backends as bk
import settings as cfg

# Backends offered. "auto" (default): every available one — the local
# llama-server when it serves a model, OpenRouter when OPENROUTER_API_KEY is
# set. "llama" / "openrouter": only that one.
_ENV_OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
_provider = os.getenv("CHAT_PROVIDER", "auto").strip().lower() or "auto"
if _provider not in ("auto", "llama", "openrouter"):
    log.error("invalid CHAT_PROVIDER", value=_provider, expected="auto|llama|openrouter")
    raise SystemExit(f"[chat] CHAT_PROVIDER must be auto, llama or openrouter (got {_provider!r})")
LLAMA_ENABLED = _provider in ("auto", "llama")

# llama-server OpenAI-compatible endpoint (one model, served under an alias).
LLAMA_URL = os.getenv("LLAMA_BASE_URL", "http://llama-server:8080")
OPENROUTER_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
# OpenRouter models offered in the UI's model picker. Any tool-calling model
# on openrouter.ai/models works. "openrouter/auto (<tier>)" is OpenRouter's
# Auto Router at a cost tier: it classifies the prompt and picks a model in
# that cost band (see _openrouter_chat). All three tiers are always offered,
# first, in addition to the configured fixed models (OPENROUTER_MODEL), so the
# UI can trade cost for quality.
AUTO_ROUTER = "openrouter/auto"
AUTO_TIERS = ("low", "medium", "high")
_AUTO_ENTRIES = [f"{AUTO_ROUTER} ({t})" for t in AUTO_TIERS]
_ENV_OPENROUTER_MODELS = [
    m.strip() for m in os.getenv(
        "OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash,openai/gpt-6-luna,google/gemini-4-argon",
    ).split(",")
    if m.strip() and m.strip() != AUTO_ROUTER and not m.strip().startswith(AUTO_ROUTER + " (")
]


def _or_key() -> str:
    """OpenRouter API key: set in the Configuration view, else OPENROUTER_API_KEY."""
    return SETTINGS.openrouter_key(_ENV_OPENROUTER_KEY)[0]


def _or_enabled() -> bool:
    return _provider in ("auto", "openrouter") and bool(_or_key())


def _or_models() -> list[str]:
    """The Auto Router tiers (always), then the fixed models (UI list, else OPENROUTER_MODEL)."""
    return list(dict.fromkeys(_AUTO_ENTRIES + SETTINGS.openrouter_models(_ENV_OPENROUTER_MODELS)))


def _env_list(name: str, default: str = "") -> list[str]:
    return [m.strip() for m in os.getenv(name, default).split(",") if m.strip()]


# Auto Router candidate pool per tier (exact ids or wildcards like "deepseek/*").
# The tier is a cost *band*, not a ceiling: low is kept to cheap tool-calling
# models (the router alone ranks by popularity, not price); medium and high
# route over OpenRouter's whole ranking for their band unless a pool is set.
OPENROUTER_AUTO_POOLS = {
    "low": _env_list("OPENROUTER_AUTO_MODELS", "deepseek/deepseek-v4.1-flash,openai/gpt-6-luna"),
    "medium": _env_list("OPENROUTER_AUTO_MODELS_MEDIUM"),
    "high": _env_list("OPENROUTER_AUTO_MODELS_HIGH"),
}
OPENROUTER_AUTO_MODELS = OPENROUTER_AUTO_POOLS["low"]
# Price guardrail, in USD per million tokens (input and output): providers
# above it are skipped. OPENROUTER_MAX_PRICE covers the fixed models and the
# low tier; medium and high are uncapped unless their own variable is set,
# since pricier models are their point. Empty disables a cap.
# The fixed models' + low tier's cap is the Configuration view's "Price cap"
# (default OPENROUTER_MAX_PRICE); medium/high have their own env caps.
OPENROUTER_MAX_PRICES = {
    "medium": os.getenv("OPENROUTER_MAX_PRICE_MEDIUM", "").strip(),
    "high": os.getenv("OPENROUTER_MAX_PRICE_HIGH", "").strip(),
}

# Per-model provider routing (OpenRouter's `provider` object), as JSON keyed
# by model id. Pins a model to specific endpoints and replaces the generic
# price cap for it. Default: Gemini 4 Argon only on Google AI Studio's flex
# tier ($1/M in, $5/M out; its other endpoints run $2-3.60 / $10-18), with
# no fallback to a pricier endpoint.
_DEFAULT_ROUTES = {
    "google/gemini-4-argon": {
        "only": ["google-ai-studio/flex"],
        "allow_fallbacks": False,
        "max_price": {"prompt": 1, "completion": 5},
    },
}
try:
    OPENROUTER_MODEL_ROUTES = json.loads(os.getenv("OPENROUTER_MODEL_ROUTES", "") or "null") or _DEFAULT_ROUTES
except ValueError:
    log.warning("OPENROUTER_MODEL_ROUTES is not valid JSON; using the defaults")
    OPENROUTER_MODEL_ROUTES = _DEFAULT_ROUTES


def _auto_tier(model: str) -> str | None:
    """'openrouter/auto (medium)' -> 'medium'; plain 'openrouter/auto' -> 'low'."""
    model = (model or "").strip()
    if model == AUTO_ROUTER:
        return "low"
    for tier in AUTO_TIERS:
        if model == f"{AUTO_ROUTER} ({tier})":
            return tier
    return None
# OpenRouter's unified reasoning control: low | medium | high, or unset to
# leave it to the model's default. Ignored by models without reasoning.
MCP_URL = os.getenv("MCP_URL", "http://champions-mcp:8000/mcp")
# Context assumed for a model that doesn't report one (a local model before
# its first load, an endpoint without a configured `context`).
DEFAULT_CONTEXT = int(os.getenv("DEFAULT_CONTEXT_SIZE", "131072"))
LOCAL = bk.LocalModels(LLAMA_URL, LLAMA_ENABLED, DEFAULT_CONTEXT)
ENDPOINTS = bk.Endpoints(os.getenv("CHAT_DATA_DIR") or None)
ENDPOINTS_ENABLED = _provider == "auto"
SETTINGS = cfg.Settings(os.getenv("CHAT_DATA_DIR") or None)
_preset_signature: tuple = ()


async def _sync_presets(force: bool = False) -> None:
    """Render the inference settings to llama-server's preset file and make
    it reload them, when they or the set of local models (and their draft
    heads) changed. A running model whose settings changed is unloaded by
    llama-server and reloads with them on its next use."""
    global _preset_signature
    sig = tuple(sorted((m["id"], m.get("on_disk"), tuple(m.get("sidecars") or [])) for m in LOCAL.models))
    if not force and sig == _preset_signature:
        return
    _preset_signature = sig
    if SETTINGS.write_presets(LOCAL.models):
        log.info("inference settings written for llama-server", models=len(LOCAL.models),
                 speculative=[m["id"] for m in LOCAL.models if SETTINGS.draft_kind(m)
                              and SETTINGS.inference("speculative", m["id"]) == "auto"] or None)
        try:
            await LOCAL.reload(http_client)
        except httpx.HTTPError as exc:
            log.warning("llama-server did not reload its presets", error=str(exc))


async def _resolve_model(value: str) -> tuple[str, str]:
    """(provider, model) for a picker value. Bare ids (chats saved before
    values were provider-qualified) are matched against each backend."""
    provider, name = bk.split(value)
    if provider:
        return provider, name
    if name in _or_models() or _auto_tier(name):
        return bk.OPENROUTER, name
    if LOCAL.get(name):
        return bk.LOCAL, name
    for e in ENDPOINTS.items:
        if name in await ENDPOINTS.models(http_client, e):
            return f"ep:{e['id']}", name
    return "", name


async def _available(provider: str, name: str) -> bool:
    if provider == bk.LOCAL:
        await LOCAL.probe(http_client)
        return LOCAL.usable(name)
    if provider == bk.OPENROUTER:
        return _or_enabled()
    if provider.startswith("ep:") and ENDPOINTS_ENABLED:
        e = ENDPOINTS.get(provider[3:])
        return bool(e) and name in await ENDPOINTS.models(http_client, e)
    return False


async def _default_model() -> str:
    """The default picker value: a loaded local model (no per-token cost),
    else OpenRouter's first entry, else an endpoint's first model, else any
    local model (loaded on first use). Empty when nothing is available."""
    await LOCAL.probe(http_client)
    loaded = [m["id"] for m in LOCAL.models if m["status"] == "loaded"]
    if loaded:
        return bk.qualify(bk.LOCAL, loaded[0])
    if _or_enabled():
        return bk.qualify(bk.OPENROUTER, _or_models()[0])
    if ENDPOINTS_ENABLED:
        for e in ENDPOINTS.items:
            models = await ENDPOINTS.models(http_client, e)
            if models:
                return bk.qualify(f"ep:{e['id']}", models[0])
    usable = [m["id"] for m in LOCAL.models if LOCAL.usable(m["id"])]
    return bk.qualify(bk.LOCAL, usable[0]) if usable else ""

# Reasoning toggle for the hybrid-thinking Qwen model. Passed to the chat
# template via chat_template_kwargs.enable_thinking. On by default (matching
# docker-compose): this workload is multi-step tool planning over 30+ tools plus
# Stat-Point/damage arithmetic, which is where the reasoning block earns its
# token cost. It is bounded rather than free — llama-server's
# --reasoning-budget caps it per round, and MAX_REASONING_* below bound how much
# is replayed across turns. Set LLAMA_THINK=false for a latency-first setup that
# only does quick factual lookups.
def _think() -> bool:
    return bool(SETTINGS.chat("thinking"))

# Sampling. Nothing set these before, so requests inherited llama.cpp's generic
# defaults (temp 0.8 / top_p 0.95 / top_k 40 / min_p 0.05) rather than Qwen3's
# published values — looser than the model is tuned for, which on a strict
# tool-calling workload shows up as drift from tool results and malformed tool
# arguments. Qwen3 publishes a different set per mode and warns against greedy
# decoding (temp 0), which degrades into repetition.
#
# Note these are deliberately NOT raised to chase "creative" teams: sampling
# temperature buys randomness, not insight, and higher temp here mostly buys
# invented facts — which the DATA RULE forbids. Team-building creativity comes
# from the prompt and from actually searching the roster (usage bottom-ranking,
# search_pokemon_by_move), not from the sampler.
_SAMPLING_THINKING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0}
_SAMPLING_INSTRUCT = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0}


def _sampling() -> dict:
    """Build the sampling params for one request: the per-mode default profile,
    with any LLAMA_* env overrides applied and a presence penalty added."""
    base = dict(_SAMPLING_THINKING if _think() else _SAMPLING_INSTRUCT)
    for key in ("temperature", "top_p", "top_k", "min_p"):
        v = SETTINGS.chat(key)  # Configuration view / LLAMA_* env; unset = the mode's profile
        if v is not None:
            base[key] = v
    # Mild repetition penalty: with 30+ tools and long tool-calling chains the
    # model can otherwise re-issue an identical call instead of moving on (the
    # behaviour MAX_REPEATED_CALL exists to catch downstream).
    base["presence_penalty"] = SETTINGS.chat("presence_penalty") or 0.0
    return base
# Hard ceiling on tool-call rounds per request so a runaway model can't grow the
# context (and rack up calls) without bound. A round can carry several parallel
# tool_calls, so this is not a cap on calls — but a model that emits one call per
# round needs roughly: skill guide + get_game_rules + usage + speed tiers +
# list_roles + a couple of role briefs + 6x(get_pokemon, get_pokemon_moves,
# get_pokemon_sets) = ~25 rounds BEFORE the first calc_stats/calc_damage,
# validate_team, analyze_team and create_pokepaste. At 24 a full 6-Pokémon build
# could not finish; it ran out mid-workflow and answered from partial data.
_MAX_TOOL_ROUNDS_ENV = os.getenv("MAX_TOOL_ROUNDS", "").strip()
MAX_TOOL_ROUNDS = int(_MAX_TOOL_ROUNDS_ENV or "40")
# Rounds left at which the model is told to wrap up (validate, export, answer):
# a full sim-tested build ran out at 40 rounds mid-workflow, before exporting
# its team, because nothing told it the budget was nearly spent.
TOOL_ROUNDS_WARNING = int(os.getenv("TOOL_ROUNDS_WARNING", "4"))


def _round_budget(model: str) -> int:
    """Tool-call rounds per turn for `model`: 40 for the local 131k model,
    80 for 1M-context models, where a calc- and simulation-heavy build needs
    the room and the context can hold it. MAX_TOOL_ROUNDS overrides both."""
    if SETTINGS.chat("max_tool_rounds"):
        return int(SETTINGS.chat("max_tool_rounds"))
    return 80 if _context_size(model) >= 500_000 else 40
# Cap a single tool result fed back into context. ~4 chars/token, so this is
# roughly 4k tokens — 3% of the 131k window for one call. The previous 8000 cut
# catalogue-style results in half (list_roles returns ~8.2k for one format, a
# full movepool or a wide usage ranking can be larger), and a silently halved
# result is worse than a large one: the model can't tell what it didn't see.
# Scaled to the model's context when not set explicitly: 16k suits the local
# 131k-context model, but a 1M-context model (the OpenRouter defaults) gets
# 60k, so catalogue-sized results (usage top-60, threat profile, item pool)
# arrive whole instead of truncated mid-list.
_MAX_TOOL_RESULT_CHARS_ENV = os.getenv("MAX_TOOL_RESULT_CHARS", "").strip()
MAX_TOOL_RESULT_CHARS = int(_MAX_TOOL_RESULT_CHARS_ENV or "16000")
# A long tool-calling session naturally re-touches earlier lookups (e.g.
# re-checking a Pokémon it looked at 100 calls ago) — that's not a real loop.
# Only cut the round loop short once the *same* tool+args signature repeats
# this many times.
MAX_REPEATED_CALL = int(os.getenv("MAX_REPEATED_CALL", "3"))
# Context window llama-server was started with. Purely informational here (the
# server enforces the real limit) — lets the UI show a context-usage meter.
LLAMA_CTX_SIZE = DEFAULT_CONTEXT  # kept for the metrics default below
# Reasoning preservation replays reasoning_content from prior turns (see
# Message.reasoning_content) so --reasoning-preserve keeps the model's
# thinking coherent across the whole conversation. Left unbounded that grows
# context linearly with turn count, so we only replay it for the most recent
# N prior turns and cap each one's length; older turns keep their `content`
# but lose their reasoning trace.
MAX_REASONING_HISTORY_TURNS = int(os.getenv("MAX_REASONING_HISTORY_TURNS", "3"))
MAX_REASONING_CHARS = int(os.getenv("MAX_REASONING_CHARS", "4000"))

# Name of the champions-mcp MCP *prompt* that carries the full
# skills/champions-team-building/SKILL.md workflow guide, and of the synthetic
# tool we expose it under (see below — it's not a real MCP tool, so
# `_run_tool` intercepts it before touching the MCP session).
SKILL_PROMPT_NAME = "champions_team_building"
SKILL_GUIDE_TOOL_NAME = "get_team_building_skill_guide"

# Tight always-on operating instructions (~300 tokens). It does NOT re-document
# the tools — each champions-mcp tool description already says when to use it, so
# duplicating that here would just burn context every round. The full
# `champions_team_building` MCP prompt (== skills/champions-team-building/SKILL.md)
# is fetched once at startup (see _init_mcp) and exposed to the model as the
# synthetic SKILL_GUIDE_TOOL_NAME tool for on-demand deeper guidance, instead of
# being duplicated into every round's context here.
SYSTEM_PROMPT = (
    "You are a Pokémon Champions team-building assistant driven by the "
    "champions-mcp tools. Champions ≠ Scarlet/Violet: rebalanced movepools, a "
    "different item pool, and Stat Points (66 total, ≤32/stat, 1 SP = +1 stat) "
    "instead of EVs.\n\n"
    "DATA RULE — this is the most important rule:\n"
    "Your training knowledge of Pokémon is WRONG for Champions — types, stats, "
    "movepools, abilities, items and usage have all been rebalanced. Treat what "
    "you 'remember' as unreliable and never state it. Every concrete fact you "
    "give — a type, base stat, ability, move, item, matchup multiplier, usage % — "
    "must come from a champions-mcp tool result you obtained THIS conversation. If "
    "you have not called the tool for it, you do not know it: call the tool, don't "
    "guess, don't fill gaps from memory, and never assume a fact because it is "
    "'obvious' or 'standard'. If a tool can't supply something, say so plainly "
    "rather than inventing it.\n\n"
    f"- For anything beyond a single quick lookup — building, fixing, or "
    f"improving a team/core/set — call {SKILL_GUIDE_TOOL_NAME} FIRST, before any "
    "other tool. It returns the full step-by-step workflow (format setup, meta "
    "reading, legality, the Stat-Point/damage iteration loop, validation, export) "
    "and the pitfalls to check rather than assume. Skip it only for one-off "
    "factual questions (e.g. a single Pokémon's stats).\n"
    "Workflow:\n"
    "- Call get_game_rules first; pass its format ('vgc'=doubles, 'bss'=singles) "
    "and the matching game_type to every format-aware tool. Never mix formats.\n"
    "- Decide roles before Pokémon. For any team build, call list_roles(format=…) "
    "and choose what each of the 6 slots is FOR, then find species to fill those "
    "slots; get_role_description(role, format=…) explains a slot and gives its "
    "Champions-verified moves/items. Roles do not transfer between formats — "
    "redirection and spread attackers are core in VGC and do nothing in BSS; "
    "revenge killers and defensive pivots are core in BSS and marginal in VGC. "
    "Six strong Pokémon with no speed control and no answer to setup is a bad "
    "team.\n"
    "- Verify before asserting: get_pokemon for typing and stats; "
    "get_pokemon_moves before assigning moves; loop calc_stats↔calc_damage until "
    "benchmarks pass; validate_team before finalising.\n"
    "- TYPE MATCHUPS: never compute one in your head — call "
    "get_pokemon_weaknesses(name) or get_type_matchups(t1,t2) and quote what it "
    "returns. Mental type math is the most common source of wrong answers here, "
    "and it silently corrupts every threat call and switch-in that follows. Dual "
    "types MULTIPLY (4x/2x/1x/0.5x/0.25x/0x) and an immunity on either half wins "
    "outright; Champions typings differ from other games, especially regional "
    "forms and Megas; and resisting a type is not the same as beating the Pokémon "
    "— confirm that with calc_damage. Never state a multiplier no tool gave you.\n"
    "- A MODE ONLY EXISTS IF A TEAMMATE SETS IT. If any member depends on Trick "
    "Room, Tailwind, weather or terrain, someone on the team must actually carry "
    "the setter. A Trick Room core with no Trick Room user is not 'a risk' — it "
    "does not function. Never write 'assumes TR is already up', 'relies on Trick "
    "Room (implicit)', or 'consider adding a setter': an unmet dependency is a BUG "
    "TO FIX before you answer, not a caveat to report. Add the enabler or change "
    "the members, re-run analyze_team (it reports dependency_gaps), and only then "
    "present the team. If you cannot close the gap, say the concept fails and "
    "propose another — never hand over a team whose gameplan is unavailable to it.\n"
    "- BSS is bring-6-pick-3: build two or three overlapping 3-cores, not one "
    "team of six. Every core needs a real lead (useful on turn one blind: "
    "threatens immediately, pivots for momentum, or sets your mode — and is hard "
    "to lose to on turn one), a win condition, and an answer to its own weakness. "
    "A Pokémon in no viable core is a wasted slot.\n"
    "- Build like a professional: state the plan in one sentence (VGC: the "
    "ideal first three turns) and make every support serve that endgame; no "
    "attacking type may hit 3+ of the 6 uncovered (check get_pokemon_weaknesses "
    "on all six); give every slot a named job and matchup; and present bring "
    "plans (VGC: 3–4 lead+back pairs, each with its own speed control; BSS: "
    "which core to bring against what you see at preview).\n"
    "- KO benchmarks: offensively aim for a GUARANTEED OHKO on targets you must "
    "remove; a likely OHKO is fine if you state the risk; a 2HKO needs a reason; "
    "a 3HKO or worse never justifies a pick — find another move, item, spread or "
    "Pokémon. Defensively the goal INVERTS: a good answer wants to be 3HKO'd or "
    "worse by what it switches into. Quote the actual %HP range and KO chance.\n"
    "- Protect is a doubles move. Near-mandatory in VGC, usually a wasted turn in "
    "BSS (only 48 of 268 species run it in singles vs 250 in doubles). In BSS use "
    "it only for a concrete reason (banking a Speed Boost turn, scaling passive "
    "damage behind recovery, scouting a Choice lock), never as a default 4th move.\n"
    "- Champions Speed is on its own scale (roster 40-162, median ~98) — remembered "
    "numbers are meaningless. get_pokemon returns speed_context; a species in the "
    "slowest ~10% is a Trick Room piece unless it is slow on purpose (slow pivot, "
    "Baton Passer, Gyro Ball, a wall that never races).\n"
    "- MEGAS: many top threats are played as their Mega in almost every game, so "
    "you never face the base form. When a tool marks a Pokémon `played_as_mega` / "
    "`faced_as` / `as_mega`, the base stats, typing and ability from get_pokemon "
    "DO NOT apply to it — calc against it with its Mega Stone as the item "
    "(calc_damage auto-Mega-evolves) and take its real stats from list_megas. "
    "Never quote a base-form stat or Speed tier for a flagged Pokémon.\n"
    "- get_pokemon_sets is EVIDENCE, NOT A TEMPLATE. Its moves/items/natures/"
    "spreads are separate popularity distributions over all builds, not a set: "
    "the top four moves may never have been used together, and the top spread may "
    "come from a different build than the top item. Never paste the top of each "
    "list. Read what is viable and which Speed numbers recur, work out which "
    "distinct build fits this slot's role, then adapt it to YOUR team — its role, "
    "the threats it must beat, your speed mode, and the Item Clause. The final set "
    "is whichever fits the team best and is often not one of the listed sets; "
    "deviating is right when calc_damage/compute_speed_threshold back it. Only legality is "
    "non-negotiable — confirm moves with get_pokemon_moves.\n"
    "- Be economical: use query/limit args, never request full dumps, and never "
    "repeat a call you already made.\n"
    "- Be creative about PICKS, never about FACTS. Copying the top-6 usage list "
    "is a boring answer and a predictable team. Prefer a distinctive gameplan and "
    "genuinely slept-on picks — get_usage_stats(order='bottom') ranks every legal "
    "Pokémon, so you can find them instead of guessing. But an unconventional pick "
    "must clear the same bar as an obvious one: it legally learns the moves "
    "(get_pokemon_moves), fits the role's stat/typing profile (get_pokemon), hits "
    "its Speed or damage benchmark (compute_speed_threshold / calc_damage), and does "
    "something about a real top-usage threat. If it fails those, it is not "
    "creative — it is just worse. Never invent a fact to make a fun idea work; "
    "drop the idea instead.\n"
    "- Think briefly, then act. Answer concisely, citing the tool data (usage %, "
    "calc result, matchup) behind each claim — never generic Pokémon lore."
)

http_client: httpx.AsyncClient | None = None
mcp_session_id: str | None = None
mcp_tools: list[dict] = []  # OpenAI-format tool schemas, loaded from champions-mcp
skill_guide_text: str | None = None  # SKILL.md content, fetched via prompts/get


async def _parse_mcp_response(resp: httpx.Response) -> Any:
    """Extract the JSON-RPC `result` from an MCP HTTP response, handling both
    a plain JSON body and a text/event-stream response (takes the last
    `data:` line carrying a `result`)."""
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        result = None
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if "result" in data:
                        result = data["result"]
                    elif "error" in data:
                        raise RuntimeError(f"MCP error: {data['error']}")
                except json.JSONDecodeError:
                    pass
        return result or {}
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"MCP error: {body['error']}")
    return body.get("result", {})


async def mcp_rpc(method: str, params: dict = {}) -> Any:
    """Send one JSON-RPC request to champions-mcp and return its `result`.

    Args:
        method: The MCP JSON-RPC method name (e.g. "tools/call").
        params: The method's params object.

    Returns:
        The parsed `result` value.

    Raises:
        RuntimeError: The server responded with a JSON-RPC error.
        httpx.HTTPStatusError: The HTTP request itself failed.
    """
    global mcp_session_id
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if mcp_session_id:
        headers["Mcp-Session-Id"] = mcp_session_id
    rid = cmlog.request_id.get()
    if rid:  # champions-mcp tags its lines (and the calc/sim calls) with the same id
        headers["X-Request-Id"] = rid
    resp = await http_client.post(MCP_URL, json=payload, headers=headers)
    resp.raise_for_status()
    if "Mcp-Session-Id" in resp.headers:
        mcp_session_id = resp.headers["Mcp-Session-Id"]
    return await _parse_mcp_response(resp)


async def mcp_notify(method: str, params: dict = {}) -> None:
    """Send a fire-and-forget JSON-RPC notification to champions-mcp (no
    response is awaited; failures are swallowed since notifications don't
    carry an `id` to correlate an error against)."""
    global mcp_session_id
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if mcp_session_id:
        headers["Mcp-Session-Id"] = mcp_session_id
    try:
        await http_client.post(MCP_URL, json=payload, headers=headers)
    except Exception:
        pass


async def _fetch_skill_guide() -> str | None:
    """Fetch the champions_team_building MCP *prompt* (the SKILL.md workflow
    guide) via prompts/get and flatten its messages to plain text. Returns
    None on failure — callers fall back to telling the model it's unavailable
    rather than fabricating workflow guidance."""
    try:
        result = await mcp_rpc("prompts/get", {"name": SKILL_PROMPT_NAME})
        parts = []
        for m in result.get("messages", []):
            content = m.get("content")
            if isinstance(content, dict) and content.get("type") == "text":
                parts.append(content.get("text", ""))
            elif isinstance(content, str):
                parts.append(content)
        text = "\n".join(p for p in parts if p)
        return text or None
    except Exception as exc:
        log.warning("could not load the skill-guide prompt", prompt=SKILL_PROMPT_NAME, error=str(exc))
        return None


async def _init_mcp() -> None:
    global mcp_tools, skill_guide_text
    for attempt in range(20):
        try:
            await mcp_rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "champions-chat", "version": "2.0"},
            })
            await mcp_notify("notifications/initialized")
            result = await mcp_rpc("tools/list")
            tools = result.get("tools", [])
            mcp_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                }
                for t in tools
            ]
            # Synthetic tool: not backed by tools/call (_run_tool intercepts it),
            # it just hands back the SKILL.md guide fetched below via prompts/get.
            mcp_tools.append({
                "type": "function",
                "function": {
                    "name": SKILL_GUIDE_TOOL_NAME,
                    "description": (
                        "Load the full Champions team-building skill guide: format "
                        "setup, meta-reading order, legality, the Stat-Point/damage "
                        "iteration loop, validation/export steps, and Champions-"
                        "specific pitfalls. Call this first for any team/core/set "
                        "building, fixing, or improvement task — not needed for a "
                        "single quick factual lookup."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            })
            skill_guide_text = await _fetch_skill_guide()
            log.info("champions-mcp tools loaded", bold=True, url=MCP_URL, tools=len(mcp_tools),
                     skill_guide=f"{len(skill_guide_text)} chars" if skill_guide_text else "unavailable",
                     attempts=attempt + 1)
            return
        except Exception as exc:
            if attempt < 19:
                if attempt % 5 == 0:
                    log.info("champions-mcp not reachable yet; retrying (it may still be pulling data)",
                             url=MCP_URL, attempt=f"{attempt + 1}/20", error=f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(3)
            else:
                log.warning("could not load champions-mcp tools; will retry on the next chat turn",
                            url=MCP_URL, error=f"{type(exc).__name__}: {exc}")


app = FastAPI()


@app.middleware("http")
async def _revalidate_ui(request, call_next):
    """The page and its static files: always revalidate (ETag -> cheap 304).
    Without Cache-Control, browsers guess a freshness window from
    Last-Modified and keep showing the old UI after an upgrade."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Per-model context window, filled from OpenRouter's model list at startup
# (llama-server's comes from LLAMA_CTX_SIZE) — drives the UI's context meter.
model_context: dict[str, int] = {}


OPENROUTER_CATALOGUE: dict[str, dict] = {}  # id -> OpenRouter /models entry


async def _init_openrouter() -> None:
    """Read OpenRouter's model catalogue (search, prices, context sizes) and
    check each configured model against it."""
    if not _or_key():
        if _provider == "openrouter":
            log.error("CHAT_PROVIDER=openrouter but no OpenRouter API key is set")
        return
    try:
        resp = await http_client.get(f"{OPENROUTER_URL}/models", timeout=20.0)
        resp.raise_for_status()
        known = {m["id"]: m for m in resp.json().get("data", [])}
    except Exception as exc:  # the chat still works; only the context meter degrades
        log.warning("could not read OpenRouter's model list; context meter degraded", error=str(exc))
        return
    OPENROUTER_CATALOGUE.clear()
    OPENROUTER_CATALOGUE.update(known)
    pools = [m for pool in OPENROUTER_AUTO_POOLS.values() for m in pool if "*" not in m]
    for model in dict.fromkeys([m for m in _or_models() if not _auto_tier(m)] + pools):
        info = known.get(model)
        if not info:
            log.warning("model is not in OpenRouter's model list", model=model)
            continue
        if "tools" not in (info.get("supported_parameters") or []):
            log.warning("model does not support tool calling; champions-mcp tools won't work with it",
                        model=model)
        if info.get("context_length"):
            model_context[model] = int(info["context_length"])
    # The Auto Router lists a nominal context; the real limit is the routed
    # model's, so meter against the smallest of a tier's allowed candidates
    # (a tier with no pool keeps the router's listed context).
    nominal = int((known.get(AUTO_ROUTER) or {}).get("context_length") or 0)
    for model in _or_models():
        tier = _auto_tier(model)
        if not tier:
            continue
        pool = [model_context[m] for m in OPENROUTER_AUTO_POOLS[tier] if m in model_context]
        if pool or nominal:
            model_context[model] = min(pool) if pool else nominal


def _context_size(model: str) -> int:
    """Context window for a picker value (or a bare OpenRouter id)."""
    provider, name = bk.split(model)
    if provider == bk.LOCAL:
        return LOCAL.context(name)
    if provider and provider.startswith("ep:"):
        e = ENDPOINTS.get(provider[3:])
        return (e or {}).get("context") or DEFAULT_CONTEXT
    return model_context.get(name, DEFAULT_CONTEXT)


@app.on_event("startup")
async def startup() -> None:
    """Open the shared HTTP client, see which backends are up, and load the
    champions-mcp tool schemas."""
    global http_client
    http_client = httpx.AsyncClient(timeout=60.0)
    if _provider == "openrouter" and not _or_key():
        log.error("CHAT_PROVIDER=openrouter but no OpenRouter API key is set; no backend available")
    llama = await LOCAL.probe(http_client, force=True)
    # download/load progress arrives on llama-server's event stream
    asyncio.get_running_loop().create_task(LOCAL.watch(http_client))
    if _or_enabled():
        await _init_openrouter()
    default = await _default_model()
    log.info("chat backends", bold=True,
             local=(f"{len(LOCAL.models)} models ({LLAMA_URL})" if llama == "ready" else f"unreachable ({LLAMA_URL})")
             if LLAMA_ENABLED else "disabled (CHAT_PROVIDER)",
             openrouter=f"{len(_or_models())} models, providers by {SETTINGS.openrouter('provider_sort')}"
             if _or_enabled() else "disabled (no API key)" if _provider != "llama" else "disabled (CHAT_PROVIDER)",
             endpoints=len(ENDPOINTS.items) if ENDPOINTS_ENABLED else None,
             default_model=default or "none", thinking=_think() if LLAMA_ENABLED else None)
    await _sync_presets(force=True)
    if not default:
        log.warning("no chat model available yet: add one in the web UI's Models panel (a Hugging Face "
                    "model) or set OPENROUTER_API_KEY / an OpenAI-compatible API")
    await _init_mcp()


@app.on_event("shutdown")
async def shutdown() -> None:
    """Close the shared HTTP client."""
    if http_client:
        await http_client.aclose()


class Message(BaseModel):
    """One chat message, as sent by the web UI / OpenAI-style history."""

    role: str
    content: str | list | None = None
    # Echoed back by the client for prior assistant turns so llama-server's
    # --reasoning-preserve can replay reasoning across the whole conversation,
    # not just the current exchange's tool-calling rounds. Ignored (and
    # harmless) for user messages and for older history that predates this.
    reasoning_content: str | None = None
    # Earlier turns' tool chains, as returned in the `final` event's
    # `turn_messages`: assistant tool calls, tool results, OpenRouter's
    # reasoning_details. Without them a follow-up question only sees the
    # previous final answers, not the data behind them.
    tool_calls: list | None = None
    tool_call_id: str | None = None
    name: str | None = None
    reasoning_details: list | None = None


class ChatRequest(BaseModel):
    """Body of a POST /api/chat request."""

    model: str | None = None  # None = the backend's default model
    messages: list[Message]


@app.get("/")
async def index() -> FileResponse:
    """Serve the single-page chat UI."""
    return FileResponse("static/index.html")


@app.get("/api/models")
async def get_models():
    """Every model the chat can offer, grouped by backend, for the UI's picker
    and Models panel: local models with their state (downloading / loading /
    loaded / unloaded...), OpenRouter's configured models plus the Auto Router
    tiers (its full catalogue is hundreds long), and each OpenAI-compatible
    endpoint's models. `value` is what the chat request sends back."""
    llama = await LOCAL.probe(http_client)
    await _sync_presets()
    models: list[dict] = []
    for m in LOCAL.models:
        value = bk.qualify(bk.LOCAL, m["id"])
        models.append({**m, "value": value, "name": m["id"], "provider": bk.LOCAL,
                       "group": "Local models (llama.cpp)", "context_size": _context_size(value)})
    if _or_enabled():
        for name in _or_models():
            value = bk.qualify(bk.OPENROUTER, name)
            models.append({"value": value, "name": name, "provider": bk.OPENROUTER, "group": "OpenRouter",
                           "status": "ready", "context_size": _context_size(value)})
    endpoints = []
    if ENDPOINTS_ENABLED:
        for e in ENDPOINTS.items:
            names = await ENDPOINTS.models(http_client, e)
            endpoints.append({**ENDPOINTS.public(e), "available": names})
            for name in names:
                value = bk.qualify(f"ep:{e['id']}", name)
                models.append({"value": value, "name": name, "provider": "endpoint", "group": e["name"],
                               "status": "ready", "context_size": _context_size(value)})
    default = await _default_model()
    return {
        "models": models, "default": default, "context_size": _context_size(default) if default else 0,
        "providers": {
            "llama": llama if LLAMA_ENABLED else "disabled",
            "openrouter": "ready" if _or_enabled() else ("no-key" if _provider != "llama" else "disabled"),
        },
        "endpoints": endpoints,
        "suggested_local": SUGGESTED_LOCAL_MODELS,
    }


# Offered as one-click suggestions in the Models panel (download size, fit).
SUGGESTED_LOCAL_MODELS = [
    {"model": "unsloth/Qwen3.5-9B-MTP-GGUF:UD-Q4_K_XL", "size": "6.1 GB",
     "note": "recommended: strong tool calling, MTP speculative decoding; 128k context fits 12 GB GPUs"},
    {"model": "unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q4_K_XL", "size": "~3 GB", "note": "for 8 GB GPUs"},
]


class LocalModelRequest(BaseModel):
    model: str  # a Hugging Face link or user/repo[:quant] (add); a model id (load/unload/delete)


def _api_error(exc: Exception) -> HTTPException:
    status = 400 if isinstance(exc, (ValueError, KeyError)) else 502
    return HTTPException(status_code=status, detail=str(exc))


@app.post("/api/local-models")
async def add_local_model(req: LocalModelRequest):
    """Download a Hugging Face model into llama-server's cache (in the
    background; progress shows in /api/models). It loads on first use."""
    try:
        return {"model": await LOCAL.add(http_client, req.model)}
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        log.warning("could not add local model", source=req.model, error=str(exc))
        raise _api_error(exc)


@app.post("/api/local-models/{action}")
async def manage_local_model(action: str, req: LocalModelRequest):
    """load / unload / delete a local model (delete: downloaded models only)."""
    fn = {"load": LOCAL.load, "unload": LOCAL.unload, "delete": LOCAL.delete}.get(action)
    if not fn:
        raise HTTPException(status_code=404, detail=f"unknown action {action!r}")
    try:
        entry = LOCAL.get(req.model) or {}
        if action == "delete" and entry.get("preset_backed"):
            # llama.cpp won't delete a model its presets name: drop our
            # section and have it reload first.
            SETTINGS.exclude.add(req.model)
            await _sync_presets(force=True)
        await fn(http_client, req.model)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"could not {action} local model", model=req.model, error=str(exc))
        raise _api_error(exc)
    finally:
        if action == "delete":
            SETTINGS.exclude.discard(req.model)


@app.get("/api/config")
async def get_config():
    """The Configuration view: inference knobs (global + per local model)
    and chat knobs, each with its default, saved and effective value."""
    await LOCAL.probe(http_client)
    return {**SETTINGS.view(LOCAL.models),
            "local": {"status": LOCAL.status if LLAMA_ENABLED else "disabled", "url": LLAMA_URL}}


class ConfigUpdate(BaseModel):
    scope: str                 # "inference" | "chat"
    model: str | None = None   # inference: a local model id for per-model overrides; None = global
    values: dict[str, Any]     # null = back to the default / inherit the global value


@app.post("/api/config")
async def update_config(req: ConfigUpdate):
    try:
        changed = SETTINGS.update(req.scope, req.values, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.scope == "inference" and changed:
        await _sync_presets(force=True)
    return {"changed": changed, **SETTINGS.view(LOCAL.models)}


def _or_model_info(mid: str) -> dict:
    """One OpenRouter model as the Configuration view shows it."""
    m = OPENROUTER_CATALOGUE.get(mid) or {}
    pricing = m.get("pricing") or {}

    def per_m(v):
        try:
            return round(float(v) * 1e6, 3)
        except (TypeError, ValueError):
            return None

    return {"id": mid, "name": m.get("name") or mid, "known": bool(m),
            "context": m.get("context_length"),
            "prompt_per_m": per_m(pricing.get("prompt")), "completion_per_m": per_m(pricing.get("completion")),
            "tools": "tools" in (m.get("supported_parameters") or []) if m else None}


# Per-model provider endpoints (GET /models/{id}/endpoints), cached: the
# catalogue's `pricing` is the model's standard price, not what a request pays
# once routed (a pinned flex endpoint is half price; a price cap can rule
# every provider out).
_OR_ENDPOINTS: dict[str, tuple[float, list[dict]]] = {}
_OR_ENDPOINTS_TTL = 3600.0
# Service tiers OpenRouter only routes to when asked: `only` pins them (or the
# :floor / :nitro variants); default routing and `sort` use standard endpoints.
_OR_SPECIAL_TIERS = ("/flex", "/priority")


async def _or_endpoints(mid: str) -> list[dict]:
    cached = _OR_ENDPOINTS.get(mid)
    if cached and time.monotonic() - cached[0] < _OR_ENDPOINTS_TTL:
        return cached[1]
    try:
        r = await http_client.get(f"{OPENROUTER_URL}/models/{mid}/endpoints",
                                  headers={"Authorization": f"Bearer {_or_key()}"}, timeout=10.0)
        r.raise_for_status()
        eps = []
        for e in (r.json().get("data") or {}).get("endpoints") or []:
            p = e.get("pricing") or {}
            try:
                eps.append({"tag": e.get("tag") or e.get("provider_name") or "?",
                            "prompt_per_m": round(float(p.get("prompt")) * 1e6, 3),
                            "completion_per_m": round(float(p.get("completion")) * 1e6, 3)})
            except (TypeError, ValueError):
                continue
    except (httpx.HTTPError, ValueError):
        eps = cached[1] if cached else []
    _OR_ENDPOINTS[mid] = (time.monotonic(), eps)
    return eps


def _or_effective_price(mid: str, endpoints: list[dict]) -> dict:
    """What the chat's routing pays for `mid`: the model's route (`only` /
    `ignore` / its own cap) or standard endpoints under the global price cap,
    then the cheapest under "price" ordering (a range otherwise)."""
    if not endpoints:
        return {}
    route = OPENROUTER_MODEL_ROUTES.get(mid) or {}
    only, ignore = route.get("only"), set(route.get("ignore") or [])
    if route:
        cap = route.get("max_price") or {}
        cap_in, cap_out = cap.get("prompt"), cap.get("completion")
    else:
        g = SETTINGS.openrouter("max_price")
        cap_in = cap_out = g or None
    # price ordering requests :floor, which makes flex endpoints eligible too
    skip = ("/priority",) if SETTINGS.openrouter("provider_sort") == "price" else _OR_SPECIAL_TIERS
    eligible = [e for e in endpoints
                if (e["tag"] in only if only else not e["tag"].endswith(skip))
                and e["tag"] not in ignore
                and (cap_in is None or e["prompt_per_m"] <= cap_in)
                and (cap_out is None or e["completion_per_m"] <= cap_out)]
    if not eligible:
        return {"blocked": True, "cap": cap_in if cap_in == cap_out else [cap_in, cap_out]}
    cheapest = min(eligible, key=lambda e: (e["prompt_per_m"], e["completion_per_m"]))
    out = {"prompt_per_m": cheapest["prompt_per_m"], "completion_per_m": cheapest["completion_per_m"],
           "via": cheapest["tag"], "providers": len(eligible), "pinned": bool(only)}
    if SETTINGS.openrouter("provider_sort") != "price" and not only and len(eligible) > 1:
        out["max_prompt_per_m"] = max(e["prompt_per_m"] for e in eligible)
        out["max_completion_per_m"] = max(e["completion_per_m"] for e in eligible)
        out.pop("via")
    return out


async def _or_models_with_prices() -> list[dict]:
    ids = SETTINGS.openrouter_models(_ENV_OPENROUTER_MODELS)
    endpoints = await asyncio.gather(*(_or_endpoints(m) for m in ids)) if _or_key() else [[] for _ in ids]
    return [{**_or_model_info(m), "effective": _or_effective_price(m, eps)} for m, eps in zip(ids, endpoints)]


@app.get("/api/openrouter")
async def get_openrouter():
    """OpenRouter as the Configuration view edits it: key source, settings,
    the model list with prices/context from the catalogue."""
    if _or_key() and not OPENROUTER_CATALOGUE:
        await _init_openrouter()
    key, source = SETTINGS.openrouter_key(_ENV_OPENROUTER_KEY)
    return {
        "enabled": _or_enabled(), "restricted": _provider == "llama",
        "key": {"source": source, "hint": f"…{key[-4:]}" if len(key) > 8 else ""},
        "settings": SETTINGS.openrouter_view(),
        "auto_tiers": _AUTO_ENTRIES,
        "models": await _or_models_with_prices(),
        "models_from": "ui" if SETTINGS.data["openrouter"].get("models") is not None else "env",
        "catalogue_size": len(OPENROUTER_CATALOGUE),
    }


@app.get("/api/openrouter/catalogue")
async def openrouter_catalogue(q: str = "", limit: int = 40):
    """Search OpenRouter's catalogue for the model picker: tool-capable models
    only (the chat drives everything through tools), cheapest first."""
    if not OPENROUTER_CATALOGUE:
        await _init_openrouter()
    words = q.lower().split()
    hits = [m for m in OPENROUTER_CATALOGUE.values()
            if "tools" in (m.get("supported_parameters") or [])
            and all(w in (m["id"] + " " + (m.get("name") or "")).lower() for w in words)]
    infos = [_or_model_info(m["id"]) for m in hits]
    infos.sort(key=lambda i: ((i["prompt_per_m"] or 0) + (i["completion_per_m"] or 0), i["id"]))
    return {"models": infos[:max(1, min(limit, 200))], "total": len(infos)}


class OpenRouterUpdate(BaseModel):
    api_key: str | None = None       # "" = remove the key saved here (back to .env)
    models: list[str] | None = None  # the full fixed-model list
    values: dict[str, Any] | None = None


@app.post("/api/openrouter")
async def update_openrouter(req: OpenRouterUpdate):
    try:
        changed = SETTINGS.update_openrouter(api_key=req.api_key, models=req.models, values=req.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if "api_key" in changed and _or_key():
        try:  # check the key now rather than on the first chat
            r = await http_client.get(f"{OPENROUTER_URL}/key", headers={"Authorization": f"Bearer {_or_key()}"},
                                      timeout=10.0)
            if r.status_code == 401:
                SETTINGS.update_openrouter(api_key="")
                raise HTTPException(status_code=400, detail="OpenRouter rejected this API key")
        except httpx.HTTPError:
            pass
    if changed:
        _USAGE_CACHE.clear()
        await _init_openrouter()
    return {"changed": changed, **(await get_openrouter())}


_USAGE_CACHE: dict[str, Any] = {}


@app.get("/api/openrouter/usage")
async def openrouter_usage():
    """What the OpenRouter key has spent (USD): today, this month, in total,
    and the remaining limit, for the spend meter. Cached for 30 s."""
    if not _or_enabled():
        return {"enabled": False}
    now = time.monotonic()
    if _USAGE_CACHE.get("at", 0) > now - 30:
        return _USAGE_CACHE["data"]
    try:
        r = await http_client.get(f"{OPENROUTER_URL}/key", headers={"Authorization": f"Bearer {_or_key()}"},
                                  timeout=10.0)
        r.raise_for_status()
        d = r.json().get("data", {})
        budget = d.get("effective_budget") or {}  # the cap that blocks requests first, if any
        data = {"enabled": True, "today": d.get("usage_daily"), "week": d.get("usage_weekly"),
                "month": d.get("usage_monthly"), "total": d.get("usage"), "limit": d.get("limit"),
                "remaining": budget.get("remaining_usd", d.get("limit_remaining")),
                "budget": {k: budget.get(k) for k in ("scope", "limit_usd", "reset_interval")} if budget else None,
                "free_tier": d.get("is_free_tier")}
    except (httpx.HTTPError, ValueError) as exc:
        data = {"enabled": True, "error": str(exc)}
    _USAGE_CACHE.update(at=now, data=data)
    return data


class EndpointRequest(BaseModel):
    name: str = ""
    base_url: str
    api_key: str = ""
    models: list[str] = []
    context: int = 0


@app.post("/api/endpoints")
async def add_endpoint(req: EndpointRequest):
    """Add an OpenAI-compatible API (checked by listing its models first)."""
    if not ENDPOINTS_ENABLED:
        raise HTTPException(status_code=400, detail="CHAT_PROVIDER restricts the chat to one backend")
    try:
        return await ENDPOINTS.add(http_client, req.model_dump())
    except Exception as exc:  # noqa: BLE001
        log.warning("could not add endpoint", url=req.base_url, error=str(exc))
        raise _api_error(exc)


@app.delete("/api/endpoints/{eid}")
async def remove_endpoint(eid: str):
    try:
        ENDPOINTS.remove(eid)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc)


class TurnMetrics:
    """Accumulates llama-server's per-request `timings`/`usage` extensions
    (see docker/bench/bench.py for the same fields read off /completion) across
    every round of a single /api/chat turn, so the UI can show one t/s figure
    for the whole answer rather than a jumpy per-round number."""

    def __init__(self, context_max: int = 0) -> None:
        self.context_max = context_max or LLAMA_CTX_SIZE
        self.prompt_tokens_sum = 0
        self.prompt_ms_sum = 0.0
        self.predicted_tokens_sum = 0
        self.predicted_ms_sum = 0.0
        self.cached_tokens_sum = 0
        self.prompt_tokens_total_sum = 0  # incl. cached, for cache-hit %
        self.last_context_tokens = 0
        self.model = ""  # the model that actually answered (Auto Router resolves it)
        self.cost = 0.0  # USD, as reported by OpenRouter (usage.cost)
        self.has_cost = False

    def add(self, resp_json: dict, elapsed_ms: float = 0.0) -> None:
        """Fold one /v1/chat/completions response's timings/usage into the running totals.

        llama-server reports exact prefill/decode `timings`; OpenRouter
        doesn't, so there decode speed is approximated as completion tokens
        over the request's wall time (prefill and network included).
        """
        t = resp_json.get("timings") or {}
        usage = resp_json.get("usage") or {}
        if t:
            self.prompt_tokens_sum += t.get("prompt_n", 0) or 0
            self.prompt_ms_sum += t.get("prompt_ms", 0.0) or 0.0
            self.predicted_tokens_sum += t.get("predicted_n", 0) or 0
            self.predicted_ms_sum += t.get("predicted_ms", 0.0) or 0.0
        elif elapsed_ms:
            self.predicted_tokens_sum += usage.get("completion_tokens", 0) or 0
            self.predicted_ms_sum += elapsed_ms
        if resp_json.get("model"):
            self.model = resp_json["model"]
        self.last_provider = resp_json.get("provider")
        self.last_cost = None
        if usage.get("cost") is not None:
            self.last_cost = float(usage["cost"] or 0)
            self.cost += self.last_cost
            self.has_cost = True
        cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0
        self.cached_tokens_sum += cached
        self.prompt_tokens_total_sum += usage.get("prompt_tokens", 0) or 0
        total = usage.get("total_tokens")
        if total:
            self.last_context_tokens = total

    def summary(self) -> dict:
        """Compute prefill/decode tokens-per-second and cache-hit % from the
        accumulated totals, for display in the UI."""
        cache_denom = self.prompt_tokens_total_sum
        return {
            "prefill_tps": (self.prompt_tokens_sum / (self.prompt_ms_sum / 1000)) if self.prompt_ms_sum else 0.0,
            "decode_tps": (self.predicted_tokens_sum / (self.predicted_ms_sum / 1000)) if self.predicted_ms_sum else 0.0,
            "cache_hit_pct": (self.cached_tokens_sum / cache_denom * 100) if cache_denom else 0.0,
            "context_tokens": self.last_context_tokens,
            "context_max": self.context_max,
            **({"model": self.model} if self.model else {}),
            **({"cost": round(self.cost, 6)} if self.has_cost else {}),
        }


async def _llama_chat(model: str, messages: list[dict], use_tools: bool, metrics: TurnMetrics | None = None,
                      session_id: str | None = None, route_tier: str | None = None) -> dict:
    """One round-trip to llama-server's OpenAI chat endpoint; returns the raw
    assistant message (content, optional tool_calls, optional reasoning_content).
    Feeds the response's `timings`/`usage` blocks into `metrics` when given."""
    provider, name = bk.split(model)
    if provider == bk.OPENROUTER:
        return await _openrouter_chat(name, messages, use_tools, metrics, session_id, route_tier)
    if provider and provider.startswith("ep:"):
        return await _endpoint_chat(provider[3:], name, messages, use_tools, metrics)
    payload: dict = {
        "model": name,
        "messages": [{k: v for k, v in m.items() if k != "reasoning_details"} for m in messages],
        "stream": False,
        # Control the Qwen thinking block via the chat template (ignored by
        # servers that don't support chat_template_kwargs — harmless).
        "chat_template_kwargs": {"enable_thinking": _think()},
        **_sampling(),
    }
    if use_tools and mcp_tools:
        payload["tools"] = mcp_tools
        payload["tool_choice"] = "auto"
    # Generous timeout: the router loads the model on its first request.
    resp = await http_client.post(f"{LLAMA_URL}/v1/chat/completions", json=payload, timeout=600.0)
    if resp.status_code >= 400:
        raise RuntimeError(f"llama-server {resp.status_code}: {_error_detail(resp)}")
    body = resp.json()
    if metrics is not None:
        metrics.add(body)
    return body["choices"][0]["message"]


def _error_detail(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
        return (err.get("message") if isinstance(err, dict) else str(err or resp.text))[:400]
    except ValueError:
        return resp.text[:400]


async def _endpoint_chat(eid: str, model: str, messages: list[dict], use_tools: bool,
                         metrics: TurnMetrics | None) -> dict:
    """The same round-trip against an OpenAI-compatible API: plain OpenAI
    fields only (servers reject unknown ones), the model's own defaults."""
    e = ENDPOINTS.get(eid)
    if not e:
        raise RuntimeError("this endpoint was removed; pick another model")
    payload: dict = {
        "model": model, "stream": False,
        "messages": [{k: v for k, v in m.items() if k not in ("reasoning_content", "reasoning_details")}
                     for m in messages],
    }
    if use_tools and mcp_tools:
        payload["tools"] = mcp_tools
        payload["tool_choice"] = "auto"
    started = asyncio.get_running_loop().time()
    resp = await http_client.post(f"{e['base_url']}/chat/completions", json=payload,
                                  headers=bk.Endpoints.headers(e), timeout=600.0)
    elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
    if resp.status_code >= 400:
        raise RuntimeError(f"{e['name']} {resp.status_code}: {_error_detail(resp)}")
    body = resp.json()
    if metrics is not None:
        metrics.add(body, elapsed_ms)
    msg = body["choices"][0]["message"]
    if msg.get("reasoning") and not msg.get("reasoning_content"):
        msg["reasoning_content"] = msg["reasoning"]
    return msg


async def _openrouter_chat(model: str, messages: list[dict], use_tools: bool,
                           metrics: TurnMetrics | None, session_id: str | None = None,
                           route_tier: str | None = None) -> dict:
    """The same round-trip against OpenRouter's OpenAI-compatible API.

    Differences from llama-server: bearer auth; no llama.cpp-only fields
    (chat_template_kwargs, Qwen sampling presets — each model keeps its own
    defaults unless a temperature is configured); reasoning via OpenRouter's
    unified `reasoning` field, returned as `reasoning` (mapped to
    reasoning_content for the UI) plus `reasoning_details`, which is sent back
    on later rounds so reasoning models keep their chain across tool calls.
    """
    key = _or_key()
    if not key:
        raise RuntimeError("no OpenRouter API key: add one in ⚙ Configure → API providers (or OPENROUTER_API_KEY in .env)")
    outgoing = []
    for m in messages:
        m = dict(m)
        m.pop("reasoning_content", None)  # llama-server's field; OpenRouter replays reasoning_details
        outgoing.append(m)
    tier = _auto_tier(model)
    # Every response reports what it cost (usage.cost, USD) — the chat's
    # spend meter adds those up.
    payload: dict = {"model": AUTO_ROUTER if tier else model, "messages": outgoing, "stream": False}
    if use_tools and mcp_tools:
        payload["tools"] = mcp_tools
        payload["tool_choice"] = "auto"
    if tier:
        # Auto Router: route within this tier's cost band (and pool, if set).
        router: dict = {"id": "auto-router", "cost_tier": tier}
        if OPENROUTER_AUTO_POOLS[tier]:
            router["allowed_models"] = OPENROUTER_AUTO_POOLS[tier]
        payload["plugins"] = [router]
    if session_id:
        # Sticky routing (and caching) across this conversation's turns.
        payload["session_id"] = session_id
    # a model pinned after routing keeps its tier's price rule (route_tier)
    price_tier = tier or route_tier
    cap_setting = SETTINGS.openrouter("max_price")
    max_price = (OPENROUTER_MAX_PRICES[price_tier] if price_tier in OPENROUTER_MAX_PRICES
                 else (cap_setting or ""))
    route = OPENROUTER_MODEL_ROUTES.get(model)
    provider: dict = {}
    if route:
        # this model's own endpoint pinning + price cap (also when the Auto
        # Router picked it and it was pinned for the rest of the turn)
        provider = dict(route)
    elif max_price:
        cap = float(max_price)
        provider = {"max_price": {"prompt": cap, "completion": cap}}
    # Several providers serve most models at different prices: try them
    # cheapest first by default (Configuration view: Provider choice).
    sort = SETTINGS.openrouter("provider_sort")
    pinned = bool({"sort", "order", "only"} & provider.keys())
    if sort == "price" and not tier and not pinned and ":" not in model:
        # :floor = price-sorted *including* flex service-tier endpoints, which
        # plain sort=price skips (Gemini's flex is half price; OpenRouter falls
        # back to standard endpoints when flex is busy). Not for the Auto
        # Router itself; its pick is pinned for the turn's later rounds.
        payload["model"] = f"{model}:floor"
    elif sort and sort != "default" and not pinned:
        provider["sort"] = sort
    if provider:
        payload["provider"] = provider
    effort = SETTINGS.openrouter("reasoning_effort")
    if effort in ("low", "medium", "high"):
        payload["reasoning"] = {"effort": effort}
    if SETTINGS.openrouter("temperature") is not None:
        payload["temperature"] = float(SETTINGS.openrouter("temperature"))
    headers = {
        "Authorization": f"Bearer {key}",
        # Optional OpenRouter app attribution headers.
        "X-OpenRouter-Title": "champions-mcp chat",
        "HTTP-Referer": "https://github.com/waldh4ri/champions-mcp",
    }
    started = asyncio.get_running_loop().time()
    resp = await http_client.post(f"{OPENROUTER_URL}/chat/completions", json=payload,
                                  headers=headers, timeout=300.0)
    elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
    if resp.status_code >= 400:
        # OpenRouter puts the useful part (bad key, no credits, model down) in the body.
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"OpenRouter {resp.status_code}: {detail[:400]}")
    body = resp.json()
    if body.get("error"):  # provider errors can arrive with a 200
        raise RuntimeError(f"OpenRouter: {body['error'].get('message', body['error'])}")
    if metrics is not None:
        metrics.add(body, elapsed_ms)
    msg = body["choices"][0]["message"]
    if msg.get("reasoning") and not msg.get("reasoning_content"):
        msg["reasoning_content"] = msg["reasoning"]
    return msg


def _short_args(arguments: dict) -> str | None:
    """Tool arguments as a short one-line preview for the log."""
    if not arguments:
        return None
    text = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= 120 else text[:119] + "…"


def _result_cap(model: str) -> int:
    """Max characters of one tool result fed back to `model`."""
    if SETTINGS.chat("max_tool_result_chars"):
        return int(SETTINGS.chat("max_tool_result_chars"))
    return 60000 if _context_size(model) >= 500_000 else 16000


async def _run_tool(name: str, arguments: dict, cap: int = MAX_TOOL_RESULT_CHARS) -> tuple[str, bool]:
    """Returns (result_text, is_error) — is_error surfaces in the UI's tool-call
    status dot, so a genuine MCP failure reads differently from a normal
    result even though both are just text on the wire."""
    # Synthetic tool backed by the cached SKILL.md prompt, not a real MCP tool
    # call — never sent over mcp_rpc, and exempt from the truncation below
    # (it's pre-sized guidance text, not an open-ended data dump).
    if name == SKILL_GUIDE_TOOL_NAME:
        return skill_guide_text or (
            "Skill guide unavailable (could not load the "
            f"{SKILL_PROMPT_NAME} prompt from champions-mcp). Fall back to the "
            "workflow summary in the system prompt and the individual tools' "
            "own descriptions."
        ), False
    t0 = time.perf_counter()
    try:
        result = await mcp_rpc("tools/call", {"name": name, "arguments": arguments})
        parts = result.get("content", [])
        tool_text = "\n".join(
            c.get("text", "") for c in parts
            if isinstance(c, dict) and c.get("type") == "text"
        )
        is_error = bool(result.get("isError"))
    except Exception as exc:
        tool_text = f"Tool error: {exc}"
        is_error = True
        log.warning("tool call failed", tool=name, error=f"{type(exc).__name__}: {exc}")
    took = time.perf_counter() - t0
    # The MCP SDK pretty-prints dict results (indent=2); the indentation is a
    # third of a catalogue-sized result. Compact it so the model gets the
    # whole thing (and spends fewer tokens) before any truncation applies.
    if tool_text and tool_text[0] in "{[":
        try:
            tool_text = json.dumps(json.loads(tool_text), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    mark = "✗" if is_error else "✓"
    (log.warning if is_error else log.info)(
        f"{mark} tool {name}", took=cmlog.fmt_duration(took),
        result=f"{len(tool_text)} chars", truncated=f"to {cap}" if len(tool_text) > cap else None,
        args=_short_args(arguments))
    if len(tool_text) > cap:
        # Name the tool: not every tool takes `limit` (list_roles narrows with
        # format/category/query, search_* with query), so a generic "use the
        # limit argument" sends the model looking for an argument that isn't
        # in the schema.
        tool_text = tool_text[:cap] + (
            f"\n...[result truncated — this is a PARTIAL {name} result. Re-call "
            "it with narrower arguments (see its schema for which filters it "
            "takes) rather than drawing conclusions from what's shown.]"
        )
    return tool_text, is_error


# Share of the model's context (in ~4-char tokens) that earlier turns may take,
# leaving the rest for this turn's own tool rounds.
HISTORY_CONTEXT_SHARE = float(os.getenv("HISTORY_CONTEXT_SHARE", "0.5"))
OLD_TOOL_RESULT_CHARS = 1500


def _fit_history(messages: list[dict], upto: int, model: str) -> None:
    """Shrink earlier turns (messages[:upto]) until they fit their share of the
    model's context: first shorten their tool results, oldest first, then drop
    their tool chains entirely (assistant tool calls + results), oldest first,
    keeping every user message and final answer."""
    budget = int(_context_size(model) * 4 * HISTORY_CONTEXT_SHARE)
    size = lambda: sum(len(json.dumps(m, ensure_ascii=False, default=str)) for m in messages[:upto])  # noqa: E731
    if size() <= budget:
        return
    for m in messages[:upto]:
        if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > OLD_TOOL_RESULT_CHARS:
            m["content"] = (m["content"][:OLD_TOOL_RESULT_CHARS]
                            + "\n...[earlier tool result shortened to fit the context]")
            if size() <= budget:
                return
    i = 0
    while i < upto and size() > budget:
        m = messages[i]
        if m.get("role") == "tool" or (m.get("role") == "assistant" and m.get("tool_calls")):
            del messages[i]
            upto -= 1
        else:
            i += 1


def _reply(msg: dict) -> dict:
    """Normalise an assistant message into the {content, thinking} the UI reads."""
    return {
        "content": msg.get("content") or "",
        "thinking": msg.get("reasoning_content") or "",
    }


def _cap_reasoning_history(messages: list[dict]) -> None:
    """Keep reasoning_content only on the most recent MAX_REASONING_HISTORY_TURNS
    assistant messages (client-echoed prior turns) and truncate each of those;
    strip it from everything older. `content` is untouched either way — this
    only bounds how much *reasoning* gets replayed into context. Mutates
    `messages` in place. Must run before the tool-calling loop appends this
    exchange's own (uncapped) reasoning_content."""
    idxs = [
        i for i, m in enumerate(messages)
        if m.get("role") == "assistant" and m.get("reasoning_content")
    ]
    keep = set(idxs[-MAX_REASONING_HISTORY_TURNS:]) if MAX_REASONING_HISTORY_TURNS > 0 else set()
    for i in idxs:
        if i not in keep:
            del messages[i]["reasoning_content"]
        elif len(messages[i]["reasoning_content"]) > MAX_REASONING_CHARS:
            messages[i]["reasoning_content"] = (
                messages[i]["reasoning_content"][:MAX_REASONING_CHARS] + "\n...[reasoning truncated]"
            )


def _ndjson(obj: dict) -> bytes:
    """Encode one NDJSON line (a JSON object followed by a newline)."""
    return (json.dumps(obj, default=str) + "\n").encode()


async def _chat_events(req: ChatRequest) -> AsyncIterator[bytes]:
    """Drive the tool-calling loop, yielding one NDJSON line per event so the
    UI can render reasoning/tool calls and refresh the t/s meters as each
    round completes, instead of only once the whole turn is done. Event
    `type`s: metrics, thinking, tool_call, final, error."""
    rid = cmlog.new_request_id()
    cmlog.request_id.set(rid)  # this generator's own context: tags every line of the turn
    t_turn = time.perf_counter()
    rounds = 0
    tool_calls_made = 0
    try:
        # If the MCP server wasn't ready during startup (e.g. its first-run
        # data pull outlasted our retry window), load tools lazily here.
        if not mcp_tools:
            await _init_mcp()
        # Tools can load fine while the prompts endpoint is still warming up
        # (separate MCP capability) — retry the skill guide alone in that case.
        global skill_guide_text
        if not skill_guide_text:
            skill_guide_text = await _fetch_skill_guide()

        messages = [m.model_dump(exclude_none=True) for m in req.messages]
        _cap_reasoning_history(messages)
        if not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        # Everything after this index is this turn's own chain, returned to the
        # client in the final event so the next turn sees it too.
        turn_start = len(messages)
        nudge_index = None

        seen_calls: dict[str, int] = {}
        tool_cache: dict[str, tuple[str, bool]] = {}
        # Also accumulated for the final event, so a client that only reads
        # the last line still gets the complete trace (mirrors the old
        # non-streaming response shape).
        tool_events: list[dict] = []
        model = req.model or await _default_model()
        # One conversation = one OpenRouter session: derived from its first
        # user message, which the UI re-sends unchanged every turn.
        first_user = next((m.get("content") for m in messages if m.get("role") == "user"), "") or ""
        session_id = "champions-" + hashlib.sha1(
            json.dumps(first_user, default=str).encode()).hexdigest()[:16]
        # A model whose backend is unavailable (e.g. a saved chat's local model
        # while llama-server is idle) falls back to the current default.
        provider, name = await _resolve_model(model)
        if not provider or not await _available(provider, name):
            fallback = await _default_model()
            if not fallback:
                raise RuntimeError("No chat model is available: add one in the Models panel "
                                   "(a Hugging Face model) or configure "
                                   "OpenRouter / an OpenAI-compatible API.")
            log.warning("selected model is unavailable; using the default",
                        requested=model, using=fallback)
            provider, name = bk.split(fallback)
        model = bk.qualify(provider, name)
        _fit_history(messages, turn_start - 1, model)  # keep this turn's user message whole
        turn_start = len(messages)
        metrics = TurnMetrics(_context_size(model))
        route_tier = _auto_tier(name) if provider == bk.OPENROUTER else None  # survives pinning below
        budget = _round_budget(model)
        last_user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "") or ""
        log.info("▶ chat turn", bold=True, provider=provider, model=name,
                 history=len(messages), round_budget=budget,
                 prompt=" ".join(str(last_user).split())[:80] or None)

        def _turn_done(outcome: str, answer: dict | None = None) -> None:
            m = metrics.summary()
            log.info(f"✓ chat turn ({outcome})", took=cmlog.fmt_duration(time.perf_counter() - t_turn),
                     model=m.get("model") or model, rounds=rounds, tool_calls=tool_calls_made,
                     decode_tps=round(m["decode_tps"], 1) or None, prefill_tps=round(m["prefill_tps"], 1) or None,
                     cache_hit=f"{m['cache_hit_pct']:.0f}%" if m["cache_hit_pct"] else None,
                     context=f"{m['context_tokens']}/{m['context_max']}",
                     answer=f"{len((answer or {}).get('content') or '')} chars",
                     cost=f"${m['cost']:.4f}" if "cost" in m else None)

        for _round in range(budget):
            t_round = time.perf_counter()
            try:
                msg = await _llama_chat(model, messages, use_tools=True, metrics=metrics,
                                        session_id=session_id, route_tier=route_tier)
            except Exception as exc:
                log.error("✗ chat turn: model request failed", round=_round + 1, model=model,
                          error=f"{type(exc).__name__}: {exc}",
                          took=cmlog.fmt_duration(time.perf_counter() - t_turn))
                yield _ndjson({"type": "error", "detail": str(exc)})
                return
            rounds += 1
            calls = [tc["function"]["name"] for tc in msg.get("tool_calls") or []]
            tool_calls_made += len(calls)
            log.info(f"round {_round + 1}/{budget}", took=cmlog.fmt_duration(time.perf_counter() - t_round),
                     calls=calls or "none (answering)",
                     thinking=f"{len(msg['reasoning_content'])} chars" if msg.get("reasoning_content") else None,
                     context=metrics.last_context_tokens or None,
                     served_by=getattr(metrics, "last_provider", None),
                     cost=f"${metrics.last_cost:.5f}" if getattr(metrics, "last_cost", None) is not None else None)
            if _auto_tier(bk.split(model)[1]) and metrics.model and not metrics.model.startswith(AUTO_ROUTER):
                # The Auto Router classifies each request on its own: left alone it
                # could switch model between this turn's tool rounds, breaking the
                # reasoning chain and the prompt cache. Route once, then pin.
                log.info("auto-router picked a model; pinned for this turn", tier=route_tier, model=metrics.model)
                model = bk.qualify(bk.OPENROUTER, metrics.model)
            yield _ndjson({"type": "metrics", "metrics": metrics.summary()})
            if msg.get("reasoning_content"):
                yield _ndjson({"type": "thinking", "text": msg["reasoning_content"]})

            if not msg.get("tool_calls"):
                _turn_done("answered", msg)
                yield _ndjson({
                    "type": "final", "message": _reply(msg),
                    "tool_events": tool_events, "metrics": metrics.summary(),
                    "turn_messages": _turn_messages(messages, turn_start, nudge_index, msg),
                })
                return

            # Re-append the assistant turn. Carry reasoning_content back: with
            # the server's --reasoning-preserve, Qwen3.6 keeps its thinking
            # across the tool-calling rounds of this turn, so it doesn't lose
            # its plan mid-chain. The client also re-sends reasoning_content
            # for prior completed turns (see Message.reasoning_content), so
            # this replay spans the whole conversation, not just the current
            # exchange. Harmless/ignored when thinking is off.
            assistant_msg = {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": msg["tool_calls"],
            }
            if msg.get("reasoning_content"):
                assistant_msg["reasoning_content"] = msg["reasoning_content"]
            if msg.get("reasoning_details"):  # OpenRouter: replayed so reasoning survives tool rounds
                assistant_msg["reasoning_details"] = msg["reasoning_details"]
            round_start = len(messages)
            messages.append(assistant_msg)

            # Progress is judged per round, not per call: a round that fetched
            # something new is productive even if it also re-asked an exhausted
            # signature alongside it (models often batch a re-check with real
            # work). Only a round that produced no new information at all means
            # the model is genuinely stuck.
            round_fetched_something_new = False
            round_hit_repeat_cap = False

            for tc in msg["tool_calls"]:
                fn = tc["function"]
                name = fn["name"]
                # OpenAI passes arguments as a JSON string.
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                sig = name + "|" + json.dumps(args, sort_keys=True, default=str)
                count = seen_calls.get(sig, 0)
                is_error = False
                if count == 0:
                    seen_calls[sig] = 1
                    tool_text, is_error = await _run_tool(name, args, _result_cap(model))
                    tool_cache[sig] = (tool_text, is_error)
                    repeated = False
                    round_fetched_something_new = True
                else:
                    count += 1
                    seen_calls[sig] = count
                    repeated = True
                    if count >= MAX_REPEATED_CALL:
                        # Genuinely stuck re-asking the same thing — stop serving
                        # it. Whether that ends the turn is decided below, once
                        # we know if the round did anything else useful.
                        round_hit_repeat_cap = True
                        tool_text = (
                            "(You already called this tool with these exact arguments "
                            "above. Use that result — do not repeat it.)"
                        )
                    else:
                        # A single re-ask this deep into a long session is often a
                        # legitimate re-check, not a loop — hand back the real
                        # (cached) result instead of refusing it.
                        tool_text, is_error = tool_cache[sig]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": tool_text,
                })
                ev = {
                    "name": name, "arguments": args, "result": tool_text,
                    "repeated": repeated, "error": is_error,
                }
                tool_events.append(ev)
                yield _ndjson({"type": "tool_call", "event": ev})

            if round_hit_repeat_cap and not round_fetched_something_new:
                log.warning("model is repeating identical tool calls; forcing the final answer",
                            round=_round + 1)
                break

            # Tell the model when its tool budget is nearly spent, so it
            # validates and exports before the turn is cut off.
            left = budget - (_round + 1)
            if 0 < left <= TOOL_ROUNDS_WARNING and messages and messages[-1].get("role") == "tool":
                messages[-1]["content"] += (
                    f"\n\n[Turn budget: {left} tool round{'s' if left != 1 else ''} left. Stop "
                    "researching: finish with the calls that complete the deliverable (validate_team, "
                    "create_pokepaste), then write the final answer.]"
                )
            # This round's chain (tool calls + results), so a turn stopped or
            # failed later keeps what it already gathered, for any model.
            yield _ndjson({"type": "round", "messages": messages[round_start:]})
        else:
            log.warning("tool-round budget exhausted; forcing the final answer", rounds=budget)

        # Final turn: tools are disabled at the API level (use_tools=False), so
        # the model answers from the tool results already in `messages`. Without
        # an explicit nudge here the model can still be mid-plan (expecting to
        # make another tool call) and, cut off with no warning, emit only a
        # stray word instead of writing up what it already worked out. Ask it
        # to actually compose the answer, but scope the instruction to what's
        # already gathered so it doesn't coax fabrication of new facts.
        nudge_index = len(messages)
        messages.append({
            "role": "user",
            "content": (
                "You cannot call any more tools this turn. Write your complete "
                "final answer now, using only the information already gathered "
                "above. If something couldn't be confirmed via a tool result, "
                "say so plainly instead of guessing."
            ),
        })
        try:
            msg = await _llama_chat(model, messages, use_tools=False, metrics=metrics,
                                    session_id=session_id, route_tier=route_tier)
        except Exception as exc:
            log.error("✗ chat turn: final answer request failed", model=model,
                      error=f"{type(exc).__name__}: {exc}")
            yield _ndjson({"type": "error", "detail": str(exc)})
            return
        rounds += 1
        _turn_done("forced final answer", msg)
        yield _ndjson({"type": "metrics", "metrics": metrics.summary()})
        if msg.get("reasoning_content"):
            yield _ndjson({"type": "thinking", "text": msg["reasoning_content"]})
        yield _ndjson({
            "type": "final", "message": _reply(msg),
            "tool_events": tool_events, "metrics": metrics.summary(),
            "turn_messages": _turn_messages(messages, turn_start, nudge_index, msg),
        })
    except asyncio.CancelledError:
        # Stop button (or a closed tab): the client disconnected, so Starlette
        # cancels the stream, which also aborts the in-flight model request.
        log.info("■ chat turn stopped by the user", took=cmlog.fmt_duration(time.perf_counter() - t_turn),
                 rounds=rounds, tool_calls=tool_calls_made)
        raise
    except Exception as exc:
        log.error("✗ chat turn crashed", took=cmlog.fmt_duration(time.perf_counter() - t_turn), exc_info=True)
        yield _ndjson({"type": "error", "detail": str(exc)})


def _turn_messages(messages: list[dict], start: int, nudge_index: int | None, final: dict) -> list[dict]:
    """This turn's chain for the client's history: the assistant tool calls
    and tool results since `start`, minus the internal no-more-tools nudge,
    plus the final answer (with its reasoning, for replay)."""
    chain = [m for i, m in enumerate(messages) if i >= start and i != nudge_index]
    answer = {"role": "assistant", "content": final.get("content") or ""}
    for k in ("reasoning_content", "reasoning_details"):
        if final.get(k):
            answer[k] = final[k]
    return chain + [answer]


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream one chat turn as NDJSON events (see `_chat_events`)."""
    return StreamingResponse(_chat_events(req), media_type="application/x-ndjson")


app.mount("/static", StaticFiles(directory="static"), name="static")
