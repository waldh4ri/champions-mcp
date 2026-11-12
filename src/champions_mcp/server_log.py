"""What the MCP server tells ``docker compose logs``.

- :func:`request_logger` — MCP middleware: one line per client handshake and
  per tool call (arguments in, outcome out, duration), prompt and resource
  reads. Each call gets a request id (see :mod:`champions_mcp.log`) that the
  calc/sim sidecars log too.
- :func:`log_startup` — the data the server is about to serve, and a
  background check that logs once the server is listening and once each
  sidecar answers.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any

from . import log as logmod

log = logmod.get("mcp")
tool_log = logmod.get("tool")

# Tools that take seconds: also log when they start, not only when they end.
_SLOW_TOOLS = {"simulate_vs_meta", "compare_team_variants", "simulate_matchup", "simulate_battle_log",
               "create_pokepaste"}
_MAX_VALUE = 60


# ------------------------------------------------------------ arguments

def _trunc(s: str, n: int = _MAX_VALUE) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _paste_species(text: str) -> list[str]:
    out = []
    for block in text.replace("\r", "").split("\n\n"):
        first = block.strip().splitlines()[0] if block.strip() else ""
        name = first.split("@")[0].strip()
        if "(" in name and ")" in name and len(name.split("(")[1].split(")")[0]) > 1:
            name = name.split("(")[1].split(")")[0]  # "Nick (Species)"
        if name:
            out.append(name)
    return out


def _team_summary(v: Any) -> str | None:
    """A team argument as its species, or None if it isn't one."""
    if isinstance(v, list) and v and all(isinstance(m, dict) for m in v) \
            and any("species" in m or ("name" in m and "moves" in m) for m in v):
        return ",".join(str(m.get("species") or m.get("name") or "?") for m in v)
    if isinstance(v, str) and "\n" in v:
        species = _paste_species(v)
        if species:
            return ",".join(species)
    return None


def summarize_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Tool arguments as short log fields: teams become their species list,
    long values are truncated."""
    out: dict[str, Any] = {}
    for k, v in (args or {}).items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        team = _team_summary(v)
        if team is not None:
            out[k] = team
        elif isinstance(v, list) and v and all(_team_summary(x) is not None for x in v):
            out[k] = f"{len(v)} teams"
        elif isinstance(v, (dict, list)):
            out[k] = _trunc(json.dumps(v, ensure_ascii=False, separators=(",", ":")))
        else:
            out[k] = _trunc(v) if isinstance(v, str) else v
        if len(out) >= 10:
            out["…"] = f"+{len(args) - 10} more"
            break
    return out


# -------------------------------------------------------------- results

def _payload(result: Any) -> tuple[Any, bool, int]:
    """(decoded tool output, is_error, size in chars) from a tools/call result
    (pydantic model or wire dict)."""
    if hasattr(result, "model_dump"):
        result = result.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(result, dict):
        return None, False, 0
    is_error = bool(result.get("isError"))
    texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
    size = sum(len(t) for t in texts)
    payload = result.get("structuredContent")
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if payload is None and texts:
        try:
            payload = json.loads(texts[0]) if len(texts) == 1 else [json.loads(t) for t in texts]
        except ValueError:
            payload = texts[0]
    return payload, is_error, size


def _pct(lo: Any, hi: Any) -> str | None:
    return None if lo is None else f"{lo}-{hi}%"


def _sim_summary(p: dict[str, Any]) -> dict[str, Any]:
    ci = p.get("win_rate_ci95")
    out: dict[str, Any] = {
        "win_rate": p.get("win_rate"),
        "ci95": f"{ci[0]:.3f}-{ci[1]:.3f}" if isinstance(ci, list) and len(ci) == 2 else None,
        "games": p.get("games"), "opponents": p.get("opponents_used"),
    }
    worst = p.get("worst_matchups") or []
    if worst and isinstance(worst[0], dict):
        out["worst"] = f"{_trunc(worst[0].get('label') or worst[0].get('id'), 50)} ({worst[0].get('win_rate')})"
    if p.get("errors"):
        out["battle_errors"] = p["errors"]
    return out


def summarize_result(tool: str, p: Any) -> dict[str, Any]:
    """The few fields of a tool's output worth a log line."""
    if isinstance(p, list):
        return {"items": len(p)}
    if not isinstance(p, dict):
        return {"result": _trunc(p)} if p else {}
    if p.get("error"):
        return {"error": _trunc(str(p["error"]).strip('"'), 200),
                **({"problems": len(p["problems"])} if isinstance(p.get("problems"), list) else {})}
    if tool == "calc_damage":
        return {"calc": f"{p.get('attacker')} {p.get('move')} → {p.get('defender')}",
                "dmg": _pct(p.get("min_pct"), p.get("max_pct")), "ko": p.get("ko_chance") or None,
                "warnings": len(p["warnings"]) if p.get("warnings") else None}
    if tool in ("simulate_vs_meta", "simulate_matchup"):
        return _sim_summary(p)
    if tool == "compare_team_variants":
        parts = []
        for v in p.get("variants") or []:
            d = v.get("delta_vs_base") or {}
            delta = f" {d['win_rate']:+.3f}{'*' if d.get('significant') else ''}" if d else ""
            parts.append(f"{v.get('id')}={v.get('win_rate')}{delta}")
        return {"variants": " ".join(parts), "opponents": p.get("opponents_used")}
    if tool == "simulate_battle_log":
        return {"winner": p.get("winner"), "turns": p.get("turns")}
    if tool == "create_pokepaste":
        return {"url": p.get("url"), "missing": p.get("missing")}
    if "valid" in p:
        probs = p.get("problems") or p.get("issues") or []
        return {"valid": p["valid"], "problems": len(probs) if isinstance(probs, list) else probs}
    # Generic: a few short scalars, then the size of any lists.
    out: dict[str, Any] = {}
    for k, v in p.items():
        if isinstance(v, bool) or (isinstance(v, (int, float)) and not isinstance(v, bool)):
            out[k] = v
        elif isinstance(v, str) and len(v) <= 40:
            out[k] = v
        if len(out) >= 4:
            break
    shown = 0
    for k, v in p.items():
        if k in out or not isinstance(v, (list, dict)) or shown >= 3:
            continue
        shown += 1
        small = isinstance(v, list) and len(v) <= 4 and all(isinstance(x, (str, int, float)) for x in v)
        out[k] = ",".join(str(x) for x in v) if small else len(v)
    return out


# ----------------------------------------------------------- middleware

def _incoming_request_id(ctx: Any) -> str | None:
    """A caller-supplied X-Request-Id (the chat bridge sends its turn id), so
    one chat turn keeps one id from chat to mcp to calc/sim."""
    try:
        headers = ctx.headers or {}
        rid = next((v for k, v in headers.items() if k.lower() == "x-request-id"), None)
    except Exception:  # stdio / no transport headers
        return None
    return rid[:32] if rid and rid.replace("-", "").isalnum() else None


async def request_logger(ctx: Any, call_next: Any) -> Any:
    """MCP server middleware (``MCPServer.middleware``): log what each inbound
    request is and how it went. Notifications and pings stay at DEBUG."""
    method = getattr(ctx, "method", "?")
    params = getattr(ctx, "params", None) or {}
    if hasattr(params, "model_dump"):
        params = params.model_dump(by_alias=True, exclude_none=True)
    if getattr(ctx, "request_id", None) is None or method in ("ping",):
        return await call_next(ctx)

    rid = _incoming_request_id(ctx) or logmod.new_request_id()
    token = logmod.request_id.set(rid)
    t0 = time.perf_counter()
    tool = params.get("name") if method == "tools/call" else None
    args = summarize_args(params.get("arguments")) if tool else {}
    if tool:
        (tool_log.info if tool in _SLOW_TOOLS else tool_log.debug)(f"▶ {tool}", **_merge(args, {}))
    try:
        result = await call_next(ctx)
    except BaseException as exc:
        took = logmod.fmt_duration(time.perf_counter() - t0)
        target = tool or params.get("name") or params.get("uri") or ""
        (tool_log if tool else log).error(f"✗ {method} {target}".strip(), **_merge(
            args, {"error": f"{type(exc).__name__}: {exc}"}, took=took))
        raise
    finally:
        logmod.request_id.reset(token)
    took = logmod.fmt_duration(time.perf_counter() - t0)

    token = logmod.request_id.set(rid)
    try:
        _log_outcome(method, params, result, tool, args, took)
    except Exception:  # never let logging break a request
        log.warning("could not log request outcome", method=method, tool=tool, exc_info=True)
    finally:
        logmod.request_id.reset(token)
    return result


def _count(result: Any, key: str) -> int | None:
    if hasattr(result, "model_dump"):
        result = result.model_dump(by_alias=True)
    items = result.get(key) if isinstance(result, dict) else None
    return len(items) if isinstance(items, list) else None


_RESERVED = {"msg", "exc_info", "bold", "took", "out"}


def _merge(args: dict[str, Any], summary: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Arguments then outcome as one field dict. An outcome field that just
    echoes an argument is dropped; one that differs is shown as ``→key``."""
    fields = {**extra}
    for k, v in args.items():
        fields[f"{k}_" if k in _RESERVED else k] = v
    for k, v in summary.items():
        if k in fields or k in _RESERVED:
            if fields.get(k) == v:
                continue
            k = f"→{k}"
        fields[k] = v
    return fields


def _size(n: int) -> str:
    return f"{n}B" if n < 1024 else f"{n / 1024:.1f}KB"


def _log_outcome(method: str, params: dict[str, Any], result: Any, tool: str | None,
                 args: dict[str, Any], took: str) -> None:
    if method == "tools/call" and tool:
        payload, is_error, size = _payload(result)
        summary = summarize_result(tool, payload)
        if is_error or (isinstance(payload, dict) and payload.get("error")):
            tool_log.warning(f"✗ {tool}", **_merge(args, summary, took=took))
        else:
            tool_log.info(f"✓ {tool}", **_merge(args, summary, took=took, out=_size(size)))
    elif method == "initialize":
        client = params.get("clientInfo") or {}
        log.info("client connected", bold=True,
                 client=f"{client.get('name', '?')} {client.get('version', '')}".strip(),
                 protocol=params.get("protocolVersion"))
    elif method == "tools/list":
        log.info("client listed tools", tools=_count(result, "tools"), took=took)
    elif method == "prompts/get":
        log.info("prompt served", prompt=params.get("name"), took=took)
    elif method == "resources/read":
        log.info("resource read", uri=params.get("uri"), took=took)
    else:
        log.debug(method, took=took)


# -------------------------------------------------------------- startup

def _age(iso: str | None) -> str | None:
    from datetime import datetime, timezone

    if not iso:
        return None
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    except ValueError:
        return None
    hours = delta.total_seconds() / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def log_data_summary(db: Any, db_path: Any) -> None:
    """What the server is about to serve: season, build age, table sizes."""
    counts = db.meta("counts", {}) or {}
    usage_sources = (db.meta("usage", {}) or {}).get("sources") or {}
    log.info("data loaded", bold=True, db=db_path, season=db.meta("season", None),
             built=db.meta("generated_at", None), age=_age(db.meta("generated_at", None)),
             data_format=db.meta("data_format", None))
    log.info("data contents", pokemon=counts.get("pokemon"), moves=counts.get("moves"),
             items=counts.get("items"), megas=counts.get("mega_forms"), usage_rows=counts.get("usage"),
             observed_teams=counts.get("build_teams"), report_teams=counts.get("full_teams"),
             ladder={fmt: s.get("season") for fmt, s in usage_sources.items()} or None)


def _wait_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _probe(url: str | None, name: str, timeout: float) -> None:
    """Poll a sidecar's /health until it answers (or ``timeout``), then log it."""
    if not url:
        log.info(f"{name} sidecar not configured; using one-shot docker run")
        return
    import httpx

    health = url.rstrip("/") + "/health"
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            body = httpx.get(health, timeout=3).json()
            fields = {k: v for k, v in body.items() if k != "ok" and not isinstance(v, (dict, list))}
            if isinstance(body.get("formats"), (list, dict)):
                fields["formats"] = len(body["formats"])
            log.info(f"{name} sidecar ready", url=url, **fields)
            return
        except Exception as exc:  # noqa: BLE001 - still starting; retry
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2)
    log.warning(f"{name} sidecar not reachable; its tools will report errors until it is",
                url=url, waited=f"{timeout:.0f}s", error=last_error)


def log_startup(*, transport: str, port: int | None, tools: int, settings: Any) -> None:
    """Log the listening/ready line once the HTTP port accepts connections,
    then each sidecar's health, from a background thread."""

    def run() -> None:
        if port is not None:
            if not _wait_port(port, 60):
                log.error("server did not start listening", port=port, waited="60s")
                return
            public = os.environ.get("PUBLISHED_PORT") or port
            log.info("ready — MCP endpoint is up", bold=True, url=f"http://localhost:{public}/mcp",
                     transport=transport, tools=tools)
        else:
            log.info("ready — serving MCP", bold=True, transport=transport, tools=tools)
        _probe(settings.calc_http, "calc", 30)
        _probe(settings.sim_http, "sim", 180)

    threading.Thread(target=run, name="startup-probe", daemon=True).start()
