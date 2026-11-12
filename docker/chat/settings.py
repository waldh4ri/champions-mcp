"""Settings edited in the web chat's Configuration view.

Two kinds:

- **Inference** (llama.cpp): context size, KV cache types, GPU layers, MoE
  offload, flash attention, reasoning budget, speculative decoding. Global
  defaults plus per-model overrides, rendered to the llama.cpp *preset* file
  llama-server reads (``presets.ini`` in the shared chat-data volume). A change
  takes effect when a model next loads: llama-server unloads a running model
  whose preset changed (``GET /models?reload=1``).
- **Chat**: thinking, sampling, tool-round and tool-result budgets. Read per
  request, so they apply to the next message.

Defaults come from the environment (the LLAMA_* / MAX_* variables in
docker-compose.yml); what the UI changes is saved in ``settings.json`` next to
the presets and wins over them. "Reset" drops a saved value.
"""

from __future__ import annotations

import json
import re
import os
from pathlib import Path
from typing import Any

try:
    import cmlog
except ImportError:  # running from a checkout
    from champions_mcp import log as cmlog

log = cmlog.get("chat.config")

KV_TYPES = ["f16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl"]
# draft-head sidecar kind -> llama.cpp --spec-type
SPEC_TYPES = {"mtp": "draft-mtp", "dflash": "draft-dflash", "eagle3": "draft-eagle3", "dspark": "draft-dspark"}
DRAFT_SIDECARS = list(SPEC_TYPES)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str) -> int | None:
    v = _env(name)
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _env_float(name: str) -> float | None:
    v = _env(name)
    try:
        return float(v) if v else None
    except ValueError:
        return None


# key, preset key (None = not a llama.cpp arg), type, choices, label, help
INFERENCE_KNOBS: list[dict[str, Any]] = [
    {"key": "ctx", "arg": "ctx-size", "type": "int", "label": "Context size (tokens)",
     "help": "Empty = auto: llama.cpp fits the largest context the GPU holds for each model. "
             "Pin a size to leave room for other models (too big and the model fails to load).",
     "default": _env_int("LLAMA_CTX_SIZE"), "min": 1024},
    {"key": "cache_type_k", "arg": "cache-type-k", "type": "choice", "choices": KV_TYPES,
     "label": "KV cache type (K)", "help": "q8_0 halves the KV cache vs f16 at practically the same quality.",
     "default": _env("LLAMA_CACHE_TYPE_K", "q8_0")},
    {"key": "cache_type_v", "arg": "cache-type-v", "type": "choice", "choices": KV_TYPES,
     "label": "KV cache type (V)", "help": "Quantized V cache needs flash attention on.",
     "default": _env("LLAMA_CACHE_TYPE_V", "q8_0")},
    {"key": "n_gpu_layers", "arg": "n-gpu-layers", "type": "int", "label": "GPU layers",
     "help": "Layers offloaded to the GPU; 99 = all.", "default": _env_int("LLAMA_N_GPU_LAYERS") or 99, "min": 0},
    {"key": "n_cpu_moe", "arg": "n-cpu-moe", "type": "int", "label": "MoE layers on CPU",
     "help": "Mixture-of-Experts models bigger than VRAM: keep this many layers' experts in system RAM "
             "(e.g. 18 for Qwen3.6-35B-A3B on 16 GB). Empty = off.",
     "default": _env_int("LLAMA_N_CPU_MOE"), "min": 0},
    {"key": "flash_attn", "arg": "flash-attn", "type": "choice", "choices": ["on", "auto", "off"],
     "label": "Flash attention", "default": _env("LLAMA_FLASH_ATTN", "on")},
    {"key": "reasoning_budget", "arg": "reasoning-budget", "type": "int", "label": "Reasoning budget (tokens)",
     "help": "Thinking tokens per generation: -1 unlimited, 0 none. A team build runs dozens of tool "
             "rounds; a cap keeps each one planning rather than musing.",
     "default": _env_int("LLAMA_REASONING_BUDGET") if _env("LLAMA_REASONING_BUDGET") else 2048, "min": -1},
    {"key": "speculative", "arg": None, "type": "choice", "choices": ["auto", "off"],
     "label": "Speculative decoding (MTP)",
     "help": "auto = use the model's own draft head (MTP, DFlash, EAGLE3) when it has one: faster "
             "generation, same output. Draft heads are downloaded with the model when the repo has them.",
     "default": "off" if _env("LLAMA_MTP_ENABLED") == "0" else "auto"},
]

CHAT_KNOBS: list[dict[str, Any]] = [
    {"key": "thinking", "type": "bool", "label": "Thinking (local models)",
     "help": "Qwen-style reasoning block before answers and tool calls. Worth its tokens for multi-step "
             "team building; turn off for quick lookups.",
     "default": _env("LLAMA_THINK", "true").lower() not in ("0", "false", "no")},
    {"key": "temperature", "type": "float", "label": "Temperature",
     "help": "Empty = the model's recommended value (Qwen: 0.6 thinking / 0.7 instruct). Higher buys "
             "randomness, not better teams.", "default": _env_float("LLAMA_TEMPERATURE"), "min": 0},
    {"key": "top_p", "type": "float", "label": "Top-p", "default": _env_float("LLAMA_TOP_P"), "min": 0, "max": 1},
    {"key": "top_k", "type": "int", "label": "Top-k", "default": _env_int("LLAMA_TOP_K"), "min": 0},
    {"key": "min_p", "type": "float", "label": "Min-p", "default": _env_float("LLAMA_MIN_P"), "min": 0, "max": 1},
    {"key": "presence_penalty", "type": "float", "label": "Presence penalty",
     "help": "Mild penalty that stops the model re-issuing identical tool calls.",
     "default": _env_float("LLAMA_PRESENCE_PENALTY") if _env("LLAMA_PRESENCE_PENALTY") else 0.5},
    {"key": "max_tool_rounds", "type": "int", "label": "Tool rounds per turn",
     "help": "Empty = by context: 40 (local), 80 (1M-context models). A full team build needs ~30.",
     "default": _env_int("MAX_TOOL_ROUNDS"), "min": 1},
    {"key": "max_tool_result_chars", "type": "int", "label": "Max characters per tool result",
     "help": "Empty = by context: 16k (local), 60k (1M-context models).",
     "default": _env_int("MAX_TOOL_RESULT_CHARS"), "min": 1000},
]

OPENROUTER_KNOBS: list[dict[str, Any]] = [
    {"key": "provider_sort", "type": "choice", "choices": ["price", "throughput", "latency", "default"],
     "label": "Provider choice",
     "help": "When several providers serve a model: price = cheapest first (default), throughput = "
             "fastest generation, latency = quickest first token, default = OpenRouter's load balancing.",
     "default": _env("OPENROUTER_PROVIDER_SORT", "price") or "price"},
    {"key": "max_price", "type": "float", "label": "Price cap (USD per 1M tokens)",
     "help": "Providers charging more (input or output) are skipped, for the fixed models and the "
             "Auto Router's low tier. 0 = no cap. A per-model route (OPENROUTER_MODEL_ROUTES) replaces it.",
     "default": _env_float("OPENROUTER_MAX_PRICE") if _env("OPENROUTER_MAX_PRICE") else 1.0, "min": 0},
    {"key": "reasoning_effort", "type": "choice", "choices": ["low", "medium", "high"],
     "label": "Reasoning effort", "help": "For reasoning models; empty = each model's default.",
     "default": _env("OPENROUTER_REASONING_EFFORT").lower() or None},
    {"key": "temperature", "type": "float", "label": "Temperature",
     "help": "Empty = each model's default.", "default": _env_float("OPENROUTER_TEMPERATURE"), "min": 0},
]

_INF = {k["key"]: k for k in INFERENCE_KNOBS}
_CHAT = {k["key"]: k for k in CHAT_KNOBS}
_OR = {k["key"]: k for k in OPENROUTER_KNOBS}


def _coerce(knob: dict[str, Any], value: Any) -> Any:
    """Validate one value for a knob; None/"" means "unset" (inherit/default)."""
    if value is None or value == "":
        return None
    t = knob["type"]
    try:
        if t == "bool":
            v: Any = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
        elif t == "int":
            v = int(value)
        elif t == "float":
            v = float(value)
        else:
            v = str(value)
    except (TypeError, ValueError):
        raise ValueError(f"{knob['label']}: {value!r} is not a valid {t}")
    if t == "choice" and v not in knob["choices"]:
        raise ValueError(f"{knob['label']}: {v!r} must be one of {', '.join(knob['choices'])}")
    if t in ("int", "float"):
        if "min" in knob and v < knob["min"]:
            raise ValueError(f"{knob['label']}: must be at least {knob['min']}")
        if "max" in knob and v > knob["max"]:
            raise ValueError(f"{knob['label']}: must be at most {knob['max']}")
    return v


class Settings:
    def __init__(self, data_dir: str | None) -> None:
        self.dir = Path(data_dir) if data_dir else None
        self.path = self.dir / "settings.json" if self.dir else None
        self.preset_path = self.dir / "presets.ini" if self.dir else None
        self.data: dict[str, Any] = {"inference": {"global": {}, "models": {}}, "chat": {}, "openrouter": {}}
        if self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text("utf-8"))
                self.data["inference"]["global"] = saved.get("inference", {}).get("global", {})
                self.data["inference"]["models"] = saved.get("inference", {}).get("models", {})
                self.data["chat"] = saved.get("chat", {})
                self.data["openrouter"] = saved.get("openrouter", {})
            except (OSError, ValueError) as exc:
                log.warning("could not read saved settings; using defaults", file=str(self.path), error=str(exc))

    # ------------------------------------------------------------ chat side
    def chat(self, key: str) -> Any:
        """Effective chat setting: saved value, else the env/built-in default."""
        saved = self.data["chat"].get(key)
        return saved if saved is not None else _CHAT[key]["default"]

    # ------------------------------------------------------------ OpenRouter
    def openrouter(self, key: str) -> Any:
        saved = self.data["openrouter"].get(key)
        return saved if saved is not None else _OR[key]["default"]

    def openrouter_key(self, env_key: str) -> tuple[str, str]:
        """(API key, where it comes from: "ui" | "env" | "")."""
        ui = self.data["openrouter"].get("api_key") or ""
        return (ui, "ui") if ui else ((env_key, "env") if env_key else ("", ""))

    def openrouter_models(self, env_models: list[str]) -> list[str]:
        """The fixed models offered: the UI's list once edited, else the env's."""
        saved = self.data["openrouter"].get("models")
        return list(saved) if saved is not None else list(env_models)

    def update_openrouter(self, *, api_key: str | None = None, models: list[str] | None = None,
                          values: dict[str, Any] | None = None) -> dict[str, Any]:
        """api_key: "" removes the UI key (back to .env); models: the full
        fixed-model list; values: knobs (None = default)."""
        target, changed = self.data["openrouter"], {}
        if api_key is not None:
            key = api_key.strip()
            if key and not key.startswith("sk-"):
                raise ValueError("an OpenRouter API key starts with sk-or-…")
            if (target.get("api_key") or "") != key:
                changed["api_key"] = "set" if key else "removed"
                if key:
                    target["api_key"] = key
                else:
                    target.pop("api_key", None)
        if models is not None:
            clean = list(dict.fromkeys(m.strip() for m in models if m and m.strip()))
            bad = [m for m in clean if "/" not in m]
            if bad:
                raise ValueError(f"not OpenRouter model ids (vendor/model): {', '.join(bad)}")
            if target.get("models") != clean:
                changed["models"] = len(clean)
                target["models"] = clean
        for key, raw in (values or {}).items():
            if key not in _OR:
                raise ValueError(f"unknown setting {key!r}")
            v = _coerce(_OR[key], raw)
            if target.get(key) != v:
                changed[key] = "default" if v is None else v
                if v is None:
                    target.pop(key, None)
                else:
                    target[key] = v
        if changed:
            self._save()
            log.info("OpenRouter settings changed", **changed)
        return changed

    def openrouter_view(self) -> dict[str, Any]:
        return {"knobs": OPENROUTER_KNOBS,
                "saved": {k["key"]: self.data["openrouter"].get(k["key"]) for k in OPENROUTER_KNOBS},
                "effective": {k["key"]: self.openrouter(k["key"]) for k in OPENROUTER_KNOBS}}

    # ------------------------------------------------------- inference side
    def inference(self, key: str, model: str | None = None) -> Any:
        """Effective inference setting for a model (or the global default)."""
        if model:
            v = self.data["inference"]["models"].get(model, {}).get(key)
            if v is not None:
                return v
        v = self.data["inference"]["global"].get(key)
        return v if v is not None else _INF[key]["default"]

    # ---------------------------------------------------------------- views
    def view(self, local_models: list[dict[str, Any]]) -> dict[str, Any]:
        """Everything the Configuration view shows: knob schemas, defaults,
        saved values (global + per model) and effective values."""
        inf = self.data["inference"]
        return {
            "inference": {
                "knobs": INFERENCE_KNOBS,
                "global": {k["key"]: inf["global"].get(k["key"]) for k in INFERENCE_KNOBS},
                "effective": {k["key"]: self.inference(k["key"]) for k in INFERENCE_KNOBS},
                "models": {
                    m["id"]: {
                        "saved": {k["key"]: inf["models"].get(m["id"], {}).get(k["key"]) for k in INFERENCE_KNOBS},
                        "effective": {k["key"]: self.inference(k["key"], m["id"]) for k in INFERENCE_KNOBS},
                        "draft": self.draft_kind(m),
                    } for m in local_models
                },
            },
            "chat": {
                "knobs": CHAT_KNOBS,
                "saved": {k["key"]: self.data["chat"].get(k["key"]) for k in CHAT_KNOBS},
                "effective": {k["key"]: self.chat(k["key"]) for k in CHAT_KNOBS},
            },
        }

    # -------------------------------------------------------------- updates
    def update(self, scope: str, values: dict[str, Any], model: str | None = None) -> dict[str, Any]:
        """Save values (None = reset to inherit/default). Returns what changed."""
        if scope == "chat":
            knobs, target = _CHAT, self.data["chat"]
        elif scope == "inference" and model:
            knobs, target = _INF, self.data["inference"]["models"].setdefault(model, {})
        elif scope == "inference":
            knobs, target = _INF, self.data["inference"]["global"]
        else:
            raise ValueError(f"unknown settings scope {scope!r}")
        changed: dict[str, Any] = {}
        for key, raw in values.items():
            if key not in knobs:
                raise ValueError(f"unknown setting {key!r}")
            v = _coerce(knobs[key], raw)
            if target.get(key) != v:
                changed[key] = v
                if v is None:
                    target.pop(key, None)
                else:
                    target[key] = v
        if scope == "inference" and model and not target:
            self.data["inference"]["models"].pop(model, None)
        if changed:
            self._save()
            log.info("settings changed", scope=scope, model=model, **{k: ("default" if v is None else v)
                                                                       for k, v in changed.items()})
        return changed

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        tmp.replace(self.path)

    # -------------------------------------------------------------- presets
    @staticmethod
    def draft_kind(model: dict[str, Any]) -> str | None:
        """The model's draft head (mtp, dflash...): a downloaded sidecar, or
        MTP layers embedded in the GGUF itself, which repos/files mark with
        "MTP" in their name (unsloth's *-MTP-GGUF)."""
        kind = next((k for k in DRAFT_SIDECARS if k in (model.get("sidecars") or [])), None)
        if not kind and re.search(r"(^|[-_/.])MTP([-_.:]|$)", model.get("id") or "", re.I):
            kind = "mtp"
        return kind

    #: models being deleted: their sections are left out (llama.cpp refuses to
    #: delete a model a preset section names)
    exclude: set[str] = set()

    def render_presets(self, local_models: list[dict[str, Any]]) -> str:
        """llama.cpp preset INI: global defaults in [*], then each model's
        overrides and its speculative-decoding type."""
        lines = ["version = 1",
                 "; Generated by champions-chat from its Configuration view: edits here are overwritten.",
                 "", "[*]"]

        def arg_lines(values: dict[str, Any]) -> list[str]:
            out = []
            for k in INFERENCE_KNOBS:
                if k["arg"] and values.get(k["key"]) is not None:
                    out.append(f"{k['arg']} = {values[k['key']]}")
            return out

        lines += arg_lines({k["key"]: self.inference(k["key"]) for k in INFERENCE_KNOBS})
        for m in local_models:
            if not m.get("on_disk", True) or m["id"] in self.exclude:
                continue  # downloading (section comes once the files are there) / being deleted
            section = arg_lines(self.data["inference"]["models"].get(m["id"], {}))
            kind = self.draft_kind(m)
            if kind and self.inference("speculative", m["id"]) == "auto":
                section.append(f"spec-type = {SPEC_TYPES[kind]}")
            if section:
                lines += ["", f"[{m['id']}]", *section]
        return "\n".join(lines) + "\n"

    def write_presets(self, local_models: list[dict[str, Any]]) -> bool:
        """Write presets.ini if it changed; True when it did."""
        if not self.preset_path:
            return False
        text = self.render_presets(local_models)
        old = self.preset_path.read_text("utf-8") if self.preset_path.exists() else None
        if text == old:
            return False
        self.preset_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.preset_path.with_suffix(".tmp")
        tmp.write_text(text, "utf-8")
        tmp.replace(self.preset_path)
        return True
