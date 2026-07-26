"""Light chat bridge: local LLM (llama.cpp server) ↔ champions-mcp tools ↔ web UI.

Initialises an MCP session to champions-mcp, auto-loads every tool definition,
and drives the tool-calling loop server-side (over llama-server's OpenAI-compatible
/v1/chat/completions API) so the single-page UI in static/ stays dumb. The backend
is llama.cpp serving Qwen3.6-35B-A3B (MoE, 8-of-256 experts active) with the MoE
expert layers offloaded to CPU — ~86 t/s on a 16 GB GPU with strong tool calling.
See README for the tuning + benchmark table.
"""

import asyncio
import json
import os
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# llama-server OpenAI-compatible endpoint (one model, served under an alias).
LLAMA_URL = os.getenv("LLAMA_BASE_URL", "http://llama-server:8080")
MCP_URL = os.getenv("MCP_URL", "http://champions-mcp:8000/mcp")
# Sent as the OpenAI `model` field. llama-server serves whatever GGUF it loaded
# regardless, but matching its --alias keeps /v1/models tidy.
DEFAULT_MODEL = os.getenv("LLAMA_MODEL_NAME", "qwen3.6-35b-a3b")

# Reasoning toggle for the hybrid-thinking Qwen model. Passed to the chat
# template via chat_template_kwargs.enable_thinking. On by default (matching
# docker-compose): this workload is multi-step tool planning over 30+ tools plus
# Stat-Point/damage arithmetic, which is where the reasoning block earns its
# token cost. It is bounded rather than free — llama-server's
# --reasoning-budget caps it per round, and MAX_REASONING_* below bound how much
# is replayed across turns. Set LLAMA_THINK=false for a latency-first setup that
# only does quick factual lookups.
THINK = os.getenv("LLAMA_THINK", "true").strip().lower() not in ("0", "false", "no", "")

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
    base = dict(_SAMPLING_THINKING if THINK else _SAMPLING_INSTRUCT)
    for key, cast in (("temperature", float), ("top_p", float),
                      ("top_k", int), ("min_p", float)):
        env = os.getenv(f"LLAMA_{key.upper()}")
        if env not in (None, ""):
            try:
                base[key] = cast(env)
            except ValueError:
                print(f"[chat] WARNING: ignoring LLAMA_{key.upper()}={env!r}", flush=True)
    # Mild repetition penalty: with 30+ tools and long tool-calling chains the
    # model can otherwise re-issue an identical call instead of moving on (the
    # behaviour MAX_REPEATED_CALL exists to catch downstream).
    base["presence_penalty"] = float(os.getenv("LLAMA_PRESENCE_PENALTY", "0.5"))
    return base
# Hard ceiling on tool-call rounds per request so a runaway model can't grow the
# context (and rack up calls) without bound. A round can carry several parallel
# tool_calls, so this is not a cap on calls — but a model that emits one call per
# round needs roughly: skill guide + get_game_rules + usage + speed tiers +
# list_roles + a couple of role briefs + 6x(get_pokemon, get_pokemon_moves,
# get_pokemon_sets) = ~25 rounds BEFORE the first calc_stats/calc_damage,
# validate_team, analyze_team and create_pokepaste. At 24 a full 6-Pokémon build
# could not finish; it ran out mid-workflow and answered from partial data.
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "40"))
# Cap a single tool result fed back into context. ~4 chars/token, so this is
# roughly 4k tokens — 3% of the 131k window for one call. The previous 8000 cut
# catalogue-style results in half (list_roles returns ~8.2k for one format, a
# full movepool or a wide usage ranking can be larger), and a silently halved
# result is worse than a large one: the model can't tell what it didn't see.
MAX_TOOL_RESULT_CHARS = int(os.getenv("MAX_TOOL_RESULT_CHARS", "16000"))
# A long tool-calling session naturally re-touches earlier lookups (e.g.
# re-checking a Pokémon it looked at 100 calls ago) — that's not a real loop.
# Only cut the round loop short once the *same* tool+args signature repeats
# this many times.
MAX_REPEATED_CALL = int(os.getenv("MAX_REPEATED_CALL", "3"))
# Context window llama-server was started with. Purely informational here (the
# server enforces the real limit) — lets the UI show a context-usage meter.
LLAMA_CTX_SIZE = int(os.getenv("LLAMA_CTX_SIZE", "131072"))
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
    "- KO benchmarks: offensively aim for a GUARANTEED OHKO on targets you must "
    "remove; a likely OHKO is fine if you state the risk; a 2HKO needs a reason; "
    "a 3HKO or worse never justifies a pick — find another move, item, spread or "
    "Pokémon. Defensively the goal INVERTS: a good answer wants to be 3HKO'd or "
    "worse by what it switches into. Quote the actual %HP range and KO chance.\n"
    "- Protect is a doubles move. Near-mandatory in VGC, usually a wasted turn in "
    "BSS (only 42 of 235 species run it in singles vs 224 in doubles). In BSS use "
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
        print(f"[chat] WARNING: Could not load {SKILL_PROMPT_NAME} prompt: {exc}", flush=True)
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
            print(f"[chat] Loaded {len(mcp_tools)} tools from champions-mcp", flush=True)
            skill_guide_text = await _fetch_skill_guide()
            if skill_guide_text:
                print(f"[chat] Loaded {SKILL_PROMPT_NAME} skill guide "
                      f"({len(skill_guide_text)} chars)", flush=True)
            return
        except Exception as exc:
            if attempt < 19:
                await asyncio.sleep(3)
            else:
                print(f"[chat] WARNING: Could not load MCP tools: {exc}", flush=True)


app = FastAPI()


@app.on_event("startup")
async def startup() -> None:
    """Open the shared HTTP client and load the champions-mcp tool schemas."""
    global http_client
    http_client = httpx.AsyncClient(timeout=60.0)
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


class ChatRequest(BaseModel):
    """Body of a POST /api/chat request."""

    model: str = DEFAULT_MODEL
    messages: list[Message]


@app.get("/")
async def index() -> FileResponse:
    """Serve the single-page chat UI."""
    return FileResponse("static/index.html")


@app.get("/api/models")
async def get_models():
    """Normalise llama-server's OpenAI /v1/models to the shape the UI expects."""
    try:
        resp = await http_client.get(f"{LLAMA_URL}/v1/models")
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = [{"name": m.get("id", DEFAULT_MODEL)} for m in data] or [{"name": DEFAULT_MODEL}]
        return {"models": models, "context_size": LLAMA_CTX_SIZE}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class TurnMetrics:
    """Accumulates llama-server's per-request `timings`/`usage` extensions
    (see docker/bench/bench.py for the same fields read off /completion) across
    every round of a single /api/chat turn, so the UI can show one t/s figure
    for the whole answer rather than a jumpy per-round number."""

    def __init__(self) -> None:
        self.prompt_tokens_sum = 0
        self.prompt_ms_sum = 0.0
        self.predicted_tokens_sum = 0
        self.predicted_ms_sum = 0.0
        self.cached_tokens_sum = 0
        self.prompt_tokens_total_sum = 0  # incl. cached, for cache-hit %
        self.last_context_tokens = 0

    def add(self, resp_json: dict) -> None:
        """Fold one /v1/chat/completions response's timings/usage into the running totals."""
        t = resp_json.get("timings") or {}
        usage = resp_json.get("usage") or {}
        self.prompt_tokens_sum += t.get("prompt_n", 0) or 0
        self.prompt_ms_sum += t.get("prompt_ms", 0.0) or 0.0
        self.predicted_tokens_sum += t.get("predicted_n", 0) or 0
        self.predicted_ms_sum += t.get("predicted_ms", 0.0) or 0.0
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
            "context_max": LLAMA_CTX_SIZE,
        }


async def _llama_chat(model: str, messages: list[dict], use_tools: bool, metrics: TurnMetrics | None = None) -> dict:
    """One round-trip to llama-server's OpenAI chat endpoint; returns the raw
    assistant message (content, optional tool_calls, optional reasoning_content).
    Feeds the response's `timings`/`usage` blocks into `metrics` when given."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        # Control the Qwen thinking block via the chat template (ignored by
        # servers that don't support chat_template_kwargs — harmless).
        "chat_template_kwargs": {"enable_thinking": THINK},
        **_sampling(),
    }
    if use_tools and mcp_tools:
        payload["tools"] = mcp_tools
        payload["tool_choice"] = "auto"
    resp = await http_client.post(f"{LLAMA_URL}/v1/chat/completions", json=payload, timeout=180.0)
    resp.raise_for_status()
    body = resp.json()
    if metrics is not None:
        metrics.add(body)
    return body["choices"][0]["message"]


async def _run_tool(name: str, arguments: dict) -> tuple[str, bool]:
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
    if len(tool_text) > MAX_TOOL_RESULT_CHARS:
        # Name the tool: not every tool takes `limit` (list_roles narrows with
        # format/category/query, search_* with query), so a generic "use the
        # limit argument" sends the model looking for an argument that isn't
        # in the schema.
        tool_text = tool_text[:MAX_TOOL_RESULT_CHARS] + (
            f"\n...[result truncated — this is a PARTIAL {name} result. Re-call "
            "it with narrower arguments (see its schema for which filters it "
            "takes) rather than drawing conclusions from what's shown.]"
        )
    return tool_text, is_error


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

        seen_calls: dict[str, int] = {}
        tool_cache: dict[str, tuple[str, bool]] = {}
        # Also accumulated for the final event, so a client that only reads
        # the last line still gets the complete trace (mirrors the old
        # non-streaming response shape).
        tool_events: list[dict] = []
        metrics = TurnMetrics()

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                msg = await _llama_chat(req.model, messages, use_tools=True, metrics=metrics)
            except Exception as exc:
                yield _ndjson({"type": "error", "detail": str(exc)})
                return
            yield _ndjson({"type": "metrics", "metrics": metrics.summary()})
            if msg.get("reasoning_content"):
                yield _ndjson({"type": "thinking", "text": msg["reasoning_content"]})

            if not msg.get("tool_calls"):
                yield _ndjson({
                    "type": "final", "message": _reply(msg),
                    "tool_events": tool_events, "metrics": metrics.summary(),
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
                    tool_text, is_error = await _run_tool(name, args)
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
                break

        # Final turn: tools are disabled at the API level (use_tools=False), so
        # the model answers from the tool results already in `messages`. Without
        # an explicit nudge here the model can still be mid-plan (expecting to
        # make another tool call) and, cut off with no warning, emit only a
        # stray word instead of writing up what it already worked out. Ask it
        # to actually compose the answer, but scope the instruction to what's
        # already gathered so it doesn't coax fabrication of new facts.
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
            msg = await _llama_chat(req.model, messages, use_tools=False, metrics=metrics)
        except Exception as exc:
            yield _ndjson({"type": "error", "detail": str(exc)})
            return
        yield _ndjson({"type": "metrics", "metrics": metrics.summary()})
        if msg.get("reasoning_content"):
            yield _ndjson({"type": "thinking", "text": msg["reasoning_content"]})
        yield _ndjson({
            "type": "final", "message": _reply(msg),
            "tool_events": tool_events, "metrics": metrics.summary(),
        })
    except Exception as exc:
        yield _ndjson({"type": "error", "detail": str(exc)})


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream one chat turn as NDJSON events (see `_chat_events`)."""
    return StreamingResponse(_chat_events(req), media_type="application/x-ndjson")


app.mount("/static", StaticFiles(directory="static"), name="static")
