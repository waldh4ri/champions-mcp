"""Chat backends besides OpenRouter: local models (llama.cpp router) and any
OpenAI-compatible API.

Model choices travel between the UI and the server as provider-qualified
values, ``"<provider>::<model>"``:

    local::unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL    a llama-server router model
    openrouter::openrouter/auto (low)            OpenRouter
    ep:<id>::<model>                             an OpenAI-compatible endpoint

Model ids alone are ambiguous (Hugging Face and OpenRouter ids both contain a
"/"), hence the prefix. A bare id from an older saved chat is resolved by
:func:`main._resolve_model`.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

try:
    import cmlog
except ImportError:  # running from a checkout
    from champions_mcp import log as cmlog

log = cmlog.get("chat.models")

DRAFT_SIDECARS = ["mtp", "dflash", "eagle3", "dspark"]

SEP = "::"
LOCAL = "local"
OPENROUTER = "openrouter"


def qualify(provider: str, name: str) -> str:
    return f"{provider}{SEP}{name}"


def split(value: str) -> tuple[str | None, str]:
    """``"local::x"`` -> ("local", "x"); a bare id -> (None, id)."""
    value = (value or "").strip()
    if SEP in value:
        provider, name = value.split(SEP, 1)
        return provider, name
    return None, value


# ------------------------------------------------------------ HF references

_HF_REF = re.compile(r"^[\w.-]+/[\w.-]+(:[\w.+-]+)?$")
_QUANT_TAG = re.compile(r"[-_.]((?:UD-)?(?:I?Q\d\w*|TQ\d\w*|MXFP4\w*|NVFP4\w*|BF16|F16|F32))$", re.I)


def parse_hf_ref(text: str) -> str:
    """A Hugging Face model reference as llama.cpp's ``-hf`` takes it
    (``user/repo[:quant]``), from what people paste:

        https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
        https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/blob/main/Qwen3.5-9B-UD-Q4_K_XL.gguf
        hf.co/unsloth/Qwen3.5-9B-GGUF:Q4_K_M
        unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL

    Raises:
        ValueError: not something that names a Hugging Face repo.
    """
    t = (text or "").strip().strip("'\"")
    t = re.sub(r"^(https?://)?(www\.)?(huggingface\.co|hf\.co)/", "", t)
    t = t.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    quant = ""
    parts = t.split("/")
    if len(parts) >= 2 and parts[-1].lower().endswith(".gguf"):
        # .../blob|resolve/<rev>/[subdir/]File-Q4_K_M.gguf -> the file's quant tag
        stem = re.sub(r"(-\d{5}-of-\d{5})?\.gguf$", "", parts[-1], flags=re.I)
        # the quant tag ends the file name (files don't always repeat the repo
        # name: Qwen3.5-9B-MTP-GGUF ships Qwen3.5-9B-UD-Q4_K_XL.gguf)
        m = _QUANT_TAG.search(stem)
        quant = m.group(1) if m else stem
        t = "/".join(parts[:2])
    elif len(parts) > 2 and parts[2] in ("tree", "blob", "resolve"):
        t = "/".join(parts[:2])
    if quant and ":" not in t:
        t = f"{t}:{quant}"
    if not _HF_REF.match(t):
        raise ValueError(f"{text!r} is not a Hugging Face model: expected a huggingface.co link "
                         "or user/repo[:quant], e.g. unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL")
    return t


# -------------------------------------------------------- local (llama.cpp)

class LocalModels:
    """llama-server in router mode: what it can serve, and managing it."""

    PROBE_SECONDS = 2.0

    def __init__(self, url: str, enabled: bool, default_ctx: int) -> None:
        self.url = url.rstrip("/")
        self.enabled = enabled
        self.default_ctx = default_ctx
        self.status = "off"          # router: "ready" | "off"
        self.models: list[dict[str, Any]] = []
        self._checked = 0.0
        self._prev: dict[str, str] = {}
        self._logged_pct: dict[str, int] = {}
        self._n_ctx: dict[str, int] = {}
        # Live progress from /models/sse (GET /models doesn't carry it):
        # model id -> {"done", "total"} while downloading, {"value"} while loading.
        self._live: dict[str, dict[str, Any]] = {}

    async def watch(self, client: httpx.AsyncClient) -> None:
        """Follow llama-server's event stream for download/load progress and
        status changes; reconnects for as long as the chat runs."""
        if not self.enabled:
            return
        import asyncio

        while True:
            try:
                async with client.stream("GET", f"{self.url}/models/sse", timeout=None) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            self._on_event(json.loads(line[5:].strip() or "{}"))
            except (httpx.HTTPError, ValueError, OSError):
                pass
            await asyncio.sleep(5)

    def _on_event(self, ev: dict[str, Any]) -> None:
        mid, kind, data = ev.get("model"), ev.get("event"), ev.get("data") or {}
        if not mid:
            return
        if kind == "download_progress" and isinstance(data, dict):
            # {"progress": {url: {done,total}}} (current builds) or {url: {...}} (README)
            per_file = data.get("progress") if isinstance(data.get("progress"), dict) else data
            files = [f for f in per_file.values() if isinstance(f, dict) and "total" in f]
            self._live[mid] = {"done": sum(f.get("done", 0) for f in files),
                               "total": sum(f.get("total", 0) for f in files)}
        elif kind == "model_status":
            prog = data.get("progress")
            if data.get("status") == "loading" and isinstance(prog, dict):
                self._live[mid] = {"value": prog.get("value")}
            elif data.get("status") != "downloading":
                self._live.pop(mid, None)
            self._checked = 0.0  # status changed: the next probe re-reads the list
        elif kind in ("download_finished", "download_failed", "model_remove"):
            self._live.pop(mid, None)
            self._checked = 0.0
            if kind == "download_failed" and not (isinstance(data, dict) and data.get("stopped")):
                log.warning("local model download failed", model=mid, detail=str(data)[:300])

    async def probe(self, client: httpx.AsyncClient, force: bool = False) -> str:
        """Refresh the router's model list (cached briefly); logs every change."""
        if not self.enabled or client is None:
            return "off"
        now = time.monotonic()
        if not force and now - self._checked < self.PROBE_SECONDS:
            return self.status
        self._checked = now
        try:
            resp = await client.get(f"{self.url}/models", timeout=3.0)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            status = "ready"
        except (httpx.HTTPError, ValueError):
            data, status = [], "off"
        if status != self.status:
            (log.info if status == "ready" else log.warning)(
                "local model server " + ("reachable" if status == "ready" else "unreachable"),
                url=self.url, models=len(data) if status == "ready" else None)
            self.status = status
        entries = [self._entry(m) for m in data if m.get("id")]
        self.models = [e for e in entries if not e.pop("phantom")]
        for m in self.models:
            await self._track(client, m)
        return status

    def _entry(self, m: dict[str, Any]) -> dict[str, Any]:
        st = m.get("status") or {}
        value = st.get("value", "unloaded")
        out: dict[str, Any] = {"id": m["id"], "status": value}
        if st.get("failed"):
            out["status"] = "failed"
            out["exit_code"] = st.get("exit_code")
        live = self._live.get(m["id"], {})
        if value == "downloading" and live.get("total"):
            out.update(done=live["done"], total=live["total"], progress=round(live["done"] / live["total"], 3))
        elif value == "loading" and live.get("value") is not None:
            out["progress"] = live["value"]
        # Where the router found it: "cache" (downloaded), the models dir
        # (./models), or "preset" (only a preset section names it). Older
        # builds report a file `path` instead.
        path = m.get("path") or ""
        src = m.get("source") or ("cache" if path and not path.startswith("/models/") else "dir" if path else "")
        on_disk = value != "downloading" and src not in ("", "preset")
        out["on_disk"] = on_disk
        out["source"] = "cache" if src == "cache" else "file" if on_disk else src
        out["removable"] = bool(m.get("can_remove", src == "cache"))
        # While downloading, the router echoes the sidecars *requested*; only
        # trust them once the files are on disk.
        out["sidecars"] = list(m.get("sidecars") or []) if on_disk else []
        # A preset-only entry that isn't being fetched comes from a stale
        # section we wrote (a deleted model): never show or re-render it.
        out["phantom"] = src == "preset" and value not in ("downloading", "incomplete", "loading", "loaded")
        return out

    async def _track(self, client: httpx.AsyncClient, m: dict[str, Any]) -> None:
        """Log status transitions and download progress (every 25%)."""
        mid, st = m["id"], m["status"]
        prev = self._prev.get(mid)
        if st != prev:
            self._prev[mid] = st
            level = log.warning if st == "failed" else log.info
            if prev is not None or st in ("downloading", "loading", "failed"):
                level(f"local model {st}", model=mid, previous=prev,
                      size=_gb(m.get("total")) if st == "downloading" else None,
                      exit_code=m.get("exit_code"))
            if st == "loaded":
                self._n_ctx.pop(mid, None)  # re-read: --fit may pick a new size
        if st == "downloading" and m.get("progress") is not None:
            pct = int(m["progress"] * 100) // 25 * 25
            if pct > self._logged_pct.get(mid, -1):
                self._logged_pct[mid] = pct
                if pct:
                    log.info("local model download progress", model=mid, progress=f"{pct}%",
                             downloaded=f"{_gb(m['done'])}/{_gb(m['total'])}")
        if st == "loaded" and mid not in self._n_ctx:
            try:
                props = (await client.get(f"{self.url}/props", params={"model": mid}, timeout=3.0)).json()
                n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx")
                if n_ctx:
                    self._n_ctx[mid] = int(n_ctx)
                    log.info("local model ready", model=mid, context=int(n_ctx))
            except (httpx.HTTPError, ValueError):
                pass

    def context(self, mid: str) -> int:
        return self._n_ctx.get(mid, self.default_ctx)

    def get(self, mid: str) -> dict[str, Any] | None:
        return next((m for m in self.models if m["id"] == mid), None)

    def usable(self, mid: str) -> bool:
        """Can a chat use it now (loads on demand if unloaded)?"""
        m = self.get(mid)
        return bool(m) and self.status == "ready" and m["status"] in ("loaded", "unloaded", "sleeping", "loading")

    async def _call(self, client: httpx.AsyncClient, method: str, path: str, **kw: Any) -> dict[str, Any]:
        if self.status != "ready":
            await self.probe(client, force=True)
        if self.status != "ready":
            raise RuntimeError("the local model server (llama-server) is not running: start the "
                               "webchat profile with the GPU override (see the README)")
        resp = await client.request(method, f"{self.url}{path}", timeout=30.0, **kw)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            err = body.get("error") if isinstance(body, dict) else None
            msg = err.get("message") if isinstance(err, dict) else (err or resp.text[:300])
            raise RuntimeError(f"llama-server: {msg}")
        self._checked = 0.0  # next probe re-reads the list
        return body

    async def add(self, client: httpx.AsyncClient, source: str) -> str:
        """Start downloading a Hugging Face model (non-blocking); returns its id."""
        ref = parse_hf_ref(source)
        log.info("local model download requested", model=ref, source=source if source != ref else None)
        # Draft heads (MTP & co.) when the repo has them — speculative decoding
        # then turns on by itself (settings.py); no vision projector, the chat
        # is text-only.
        await self._call(client, "POST", "/models", json={"model": ref, "sidecars": DRAFT_SIDECARS})
        return ref

    async def reload(self, client: httpx.AsyncClient) -> None:
        """Make llama-server re-read its presets (a running model whose
        settings changed is unloaded; its next use loads it with them)."""
        if self.status == "ready":
            await client.get(f"{self.url}/models", params={"reload": 1}, timeout=10.0)
            self._checked = 0.0

    async def load(self, client: httpx.AsyncClient, mid: str) -> None:
        log.info("local model load requested", model=mid)
        await self._call(client, "POST", "/models/load", json={"model": mid})

    async def unload(self, client: httpx.AsyncClient, mid: str) -> None:
        log.info("local model unload requested", model=mid)
        await self._call(client, "POST", "/models/unload", json={"model": mid})

    async def delete(self, client: httpx.AsyncClient, mid: str) -> None:
        log.info("local model delete requested", model=mid)
        await self._call(client, "DELETE", f"/models?model={quote(mid, safe='')}")


def _gb(n: Any) -> str | None:
    return f"{n / 1e9:.1f}GB" if isinstance(n, (int, float)) and n else None


# ------------------------------------------------- OpenAI-compatible APIs

class Endpoints:
    """OpenAI-compatible APIs: one from the environment (OPENAI_COMPAT_*) plus
    any added in the UI, persisted (with their API keys) in a server-side
    JSON file — keys are never sent back to the browser."""

    MODELS_TTL = 60.0

    def __init__(self, data_dir: str | None) -> None:
        self.path = Path(data_dir) / "endpoints.json" if data_dir else None
        self.items: list[dict[str, Any]] = []
        self._models: dict[str, tuple[float, list[str]]] = {}
        base = os.getenv("OPENAI_COMPAT_BASE_URL", "").strip()
        if base:
            self.items.append(self._clean({
                "id": "env", "name": os.getenv("OPENAI_COMPAT_NAME", "").strip(),
                "base_url": base, "api_key": os.getenv("OPENAI_COMPAT_API_KEY", "").strip(),
                "models": [m.strip() for m in os.getenv("OPENAI_COMPAT_MODELS", "").split(",") if m.strip()],
                "context": int(os.getenv("OPENAI_COMPAT_CONTEXT", "0") or 0), "from_env": True,
            }))
        if self.path and self.path.exists():
            try:
                self.items += [self._clean(e) for e in json.loads(self.path.read_text("utf-8"))]
            except (OSError, ValueError, KeyError) as exc:
                log.warning("could not read saved endpoints; ignoring them", file=str(self.path), error=str(exc))
        if self.items:
            log.info("OpenAI-compatible endpoints configured",
                     endpoints=[f"{e['name']} ({e['base_url']})" for e in self.items])

    @staticmethod
    def _clean(e: dict[str, Any]) -> dict[str, Any]:
        base = str(e["base_url"]).strip().rstrip("/")
        if not re.match(r"^https?://", base):
            raise ValueError("the base URL must start with http:// or https://")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        return {
            "id": str(e.get("id") or secrets.token_hex(3)),
            "name": (str(e.get("name") or "").strip() or urlsplit(base).netloc)[:40],
            "base_url": base, "api_key": str(e.get("api_key") or "").strip(),
            "models": [str(m).strip() for m in e.get("models") or [] if str(m).strip()],
            "context": int(e.get("context") or 0), "from_env": bool(e.get("from_env")),
        }

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([e for e in self.items if not e["from_env"]], indent=2), "utf-8")
        tmp.replace(self.path)

    def get(self, eid: str) -> dict[str, Any] | None:
        return next((e for e in self.items if e["id"] == eid), None)

    @staticmethod
    def public(e: dict[str, Any]) -> dict[str, Any]:
        key = e["api_key"]
        return {"id": e["id"], "name": e["name"], "base_url": e["base_url"], "models": e["models"],
                "context": e["context"], "from_env": e["from_env"],
                "api_key": (f"…{key[-4:]}" if len(key) > 8 else "set") if key else ""}

    @staticmethod
    def headers(e: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {e['api_key']}"} if e["api_key"] else {}

    async def models(self, client: httpx.AsyncClient, e: dict[str, Any]) -> list[str]:
        """The endpoint's configured models, else its /models list (cached)."""
        if e["models"]:
            return e["models"]
        cached = self._models.get(e["id"])
        if cached and time.monotonic() - cached[0] < self.MODELS_TTL:
            return cached[1]
        try:
            resp = await client.get(f"{e['base_url']}/models", headers=self.headers(e), timeout=5.0)
            resp.raise_for_status()
            names = sorted(m["id"] for m in resp.json().get("data", []) if m.get("id"))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if not cached:
                log.warning("could not list an endpoint's models", endpoint=e["name"],
                            url=e["base_url"], error=f"{type(exc).__name__}: {exc}")
            names = cached[1] if cached else []
        self._models[e["id"]] = (time.monotonic(), names)
        return names

    async def add(self, client: httpx.AsyncClient, data: dict[str, Any]) -> dict[str, Any]:
        e = self._clean({**data, "id": None, "from_env": False})
        models = await self.models(client, e)
        if not models:
            raise ValueError(f"no models found at {e['base_url']}/models: check the URL and key, "
                             "or list the model names explicitly")
        self.items.append(e)
        self._save()
        log.info("OpenAI-compatible endpoint added", endpoint=e["name"], url=e["base_url"],
                 models=len(models), key="set" if e["api_key"] else "none")
        return {**self.public(e), "models": models}

    def remove(self, eid: str) -> None:
        e = self.get(eid)
        if not e:
            raise KeyError(eid)
        if e["from_env"]:
            raise ValueError("this endpoint comes from .env (OPENAI_COMPAT_*); remove it there")
        self.items.remove(e)
        self._models.pop(eid, None)
        self._save()
        log.info("OpenAI-compatible endpoint removed", endpoint=e["name"], url=e["base_url"])
