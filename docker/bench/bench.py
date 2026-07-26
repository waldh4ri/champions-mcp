#!/usr/bin/env python3
"""Throughput benchmark: Ollama vs llama.cpp server on the same model.

Fires an identical prompt at each backend that is reachable and reports prompt
(prefill) and generation (decode) tokens/sec, read straight from each server's
own timing fields — so the numbers are the engines' measurement, not wall-clock
guesswork. Run them one at a time (a big MoE won't fit twice in a small GPU):

    # terminal: docker compose --profile ai up -d        # Ollama
    python docker/bench/bench.py
    # then swap engines on the GPU:
    docker compose --profile ai down
    docker compose --profile llama up -d --build         # llama.cpp
    python docker/bench/bench.py

Stdlib only — no pip install. Override endpoints/params via the flags below.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Three workloads of rising prompt length + answer depth. Short prompts isolate
# raw decode; the long one stresses prompt processing (prefill) — the exact thing
# that's CPU-bound when experts sit on the CPU, so it's the most telling case.
PROMPTS: list[tuple[str, str]] = [
    (
        "easy",
        "What are Garchomp's base stats in Pokemon Champions? List HP, Attack, "
        "Defense, Special Attack, Special Defense and Speed.",
    ),
    (
        "medium",
        "I'm building a Choice Scarf attacker with a Jolly nature for Champions "
        "singles (BSS). I want it to outspeed the entire top-32 Speed tier of the "
        "current meta, assuming those threats run a Jolly nature with no Speed "
        "Stat Points. Walk through the speed math: what final Speed do I need to "
        "beat, what base Speed clears it with a Scarf, and how many Speed Stat "
        "Points (1 SP = +1 stat, max 32 per stat) does each candidate need?",
    ),
    (
        "hard",
        "Build me a complete, legal 6-Pokemon VGC doubles team for the current "
        "Pokemon Champions season. Requirements: a Trick Room mode setter and a "
        "fast-mode backup plan in the same team so it works in both speed modes; "
        "balanced offensive typing with answers to the top-5 meta threats; at "
        "least one Intimidate user and one redirection or speed-control option; "
        "and no two Pokemon sharing a weakness to the same common attacking type. "
        "For each of the six, give the species, item, ability, four moves, nature, "
        "and a full 66-point Stat Point spread (<=32 per stat), and justify every "
        "choice with damage benchmarks and the meta usage it's built to handle. "
        "Then summarise the team's win conditions and its two hardest matchups.",
    ),
]


def _post(url: str, payload: dict, timeout: float) -> dict:
    """POST a JSON payload and return the parsed JSON response (stdlib only,
    no `requests`/`httpx` dependency needed for this script)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def bench_ollama(base: str, model: str, prompt: str, n_predict: int, ctx: int, timeout: float) -> dict | None:
    """Benchmark one prompt against an Ollama backend via /api/generate
    (non-stream); Ollama returns nanosecond timings.

    Args:
        base: Ollama base URL.
        model: Ollama model tag to request.
        prompt: The prompt text to send.
        n_predict: Maximum tokens to generate.
        ctx: Context window size (Ollama `num_ctx`).
        timeout: Per-request timeout in seconds.

    Returns:
        A dict with `engine`, `prompt_tokens`, `prompt_tps`, `gen_tokens`,
        `gen_tps`, or None if the backend is unreachable.
    """
    url = base.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": n_predict, "num_ctx": ctx},
    }
    try:
        r = _post(url, payload, timeout)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        print(f"  ollama unreachable at {base}: {exc}", file=sys.stderr)
        return None
    pe_n, pe_ns = r.get("prompt_eval_count", 0), r.get("prompt_eval_duration", 0)
    ev_n, ev_ns = r.get("eval_count", 0), r.get("eval_duration", 0)
    return {
        "engine": "ollama",
        "prompt_tokens": pe_n,
        "prompt_tps": (pe_n / (pe_ns / 1e9)) if pe_ns else 0.0,
        "gen_tokens": ev_n,
        "gen_tps": (ev_n / (ev_ns / 1e9)) if ev_ns else 0.0,
    }


def bench_llama(base: str, prompt: str, n_predict: int, timeout: float) -> dict | None:
    """Benchmark one prompt against a llama.cpp backend via its native
    /completion endpoint, which returns a `timings` block.

    Args:
        base: llama-server base URL.
        prompt: The prompt text to send.
        n_predict: Maximum tokens to generate.
        timeout: Per-request timeout in seconds.

    Returns:
        A dict with `engine`, `prompt_tokens`, `prompt_tps`, `gen_tokens`,
        `gen_tps`, or None if the backend is unreachable.
    """
    url = base.rstrip("/") + "/completion"
    payload = {"prompt": prompt, "n_predict": n_predict, "stream": False, "cache_prompt": False}
    try:
        r = _post(url, payload, timeout)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        print(f"  llama-server unreachable at {base}: {exc}", file=sys.stderr)
        return None
    t = r.get("timings", {})
    return {
        "engine": "llama.cpp",
        "prompt_tokens": int(t.get("prompt_n", 0)),
        "prompt_tps": float(t.get("prompt_per_second", 0.0)),
        "gen_tokens": int(t.get("predicted_n", 0)),
        "gen_tps": float(t.get("predicted_per_second", 0.0)),
    }


def main() -> int:
    """CLI entry point: run every workload in `PROMPTS` against each reachable backend.

    Returns:
        Process exit code: 0 if at least one backend answered, 1 otherwise.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ollama", default="http://localhost:11434", help="Ollama base URL")
    ap.add_argument("--llama", default="http://localhost:8080", help="llama-server base URL")
    ap.add_argument("--model", default="qwen3:30b-a3b", help="Ollama model tag")
    ap.add_argument("--n-predict", type=int, default=256, help="tokens to generate")
    ap.add_argument("--ctx", type=int, default=32768, help="context window (Ollama num_ctx)")
    ap.add_argument("--timeout", type=float, default=600.0, help="per-request timeout (s)")
    ap.add_argument("--warmup", action="store_true", help="discard one run first (load model)")
    args = ap.parse_args()

    def run(prompt: str, n: int) -> list[dict]:
        out = [
            bench_ollama(args.ollama, args.model, prompt, n, args.ctx, args.timeout),
            bench_llama(args.llama, prompt, n, args.timeout),
        ]
        return [r for r in out if r]

    if args.warmup:
        print("Warming up (loading model, result discarded)…")
        run(PROMPTS[0][1], 8)

    hdr = f"{'workload':<8} {'engine':<10} {'prompt tok':>10} {'prefill t/s':>12} {'decode t/s':>11} {'gen tok':>8}"
    print(f"\nGenerating up to {args.n_predict} tokens per workload.\n")
    print(hdr)
    print("-" * len(hdr))
    any_ok = False
    for name, prompt in PROMPTS:
        rows = run(prompt, args.n_predict)
        any_ok = any_ok or bool(rows)
        for r in rows:
            print(
                f"{name:<8} {r['engine']:<10} {r['prompt_tokens']:>10} "
                f"{r['prompt_tps']:>12.1f} {r['gen_tps']:>11.1f} {r['gen_tokens']:>8}"
            )
        print("-" * len(hdr))
    if not any_ok:
        print("No backend reachable. Bring one up first.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
