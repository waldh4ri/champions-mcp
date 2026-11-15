"""Keeping a long chat's history valid for the model APIs.

Providers (OpenAI-style, including Bedrock behind OpenRouter) reject a request
whose tool messages don't pair up: every ``role: tool`` result must answer a
``tool_calls`` entry of the assistant message right before it, and every call
needs its result. Trimming a long history must therefore never cut a chain in
half. Pure functions, no app state, so they are unit-testable on their own.
"""

from __future__ import annotations

import json
from typing import Any


def fit_history(messages: list[dict], upto: int, budget_chars: int,
                old_tool_result_chars: int) -> None:
    """Shrink earlier turns (messages[:upto]) until they fit ``budget_chars``:
    first shorten their tool results, oldest first, then drop their tool chains
    (an assistant's tool calls together with all of their results), oldest
    first, keeping every user message and final answer. A chain is dropped
    whole: stopping part-way would leave results with no calls."""
    size = lambda: sum(len(json.dumps(m, ensure_ascii=False, default=str)) for m in messages[:upto])  # noqa: E731
    if size() <= budget_chars:
        return
    for m in messages[:upto]:
        if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > old_tool_result_chars:
            m["content"] = (m["content"][:old_tool_result_chars]
                            + "\n...[earlier tool result shortened to fit the context]")
            if size() <= budget_chars:
                return
    i = 0
    while i < upto and size() > budget_chars:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            j = i + 1
            while j < upto and messages[j].get("role") == "tool":
                j += 1
            del messages[i:j]
            upto -= j - i
        elif m.get("role") == "tool":  # a stray result: no call to keep it for
            del messages[i]
            upto -= 1
        else:
            i += 1


def repair_tool_pairs(messages: list[dict]) -> int:
    """Drop what a provider would reject, in place; returns how many messages
    were removed. A tool result with no matching call right before it is
    removed, and an assistant tool-call message whose results are not all
    present is removed along with the results it does have. Runs on every
    request, so a history damaged anywhere (an old client, an interrupted
    turn) cannot turn into a 400 that repeats on every retry."""
    out: list[dict] = []
    removed = 0
    i, n = 0, len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = {tc.get("id") for tc in m["tool_calls"]}
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                j += 1
            results = messages[i + 1:j]
            if ids <= {r.get("tool_call_id") for r in results}:
                kept = [r for r in results if r.get("tool_call_id") in ids]
                out.append(m)
                out.extend(kept)
                removed += len(results) - len(kept)
            else:
                removed += 1 + len(results)
            i = j
        elif m.get("role") == "tool":
            removed += 1
            i += 1
        else:
            out.append(m)
            i += 1
    messages[:] = out
    return removed


def openrouter_error(err: Any) -> str:
    """An OpenRouter ``error`` object as one readable line. Its ``message`` is
    often just "Provider returned error"; the real reason (which upstream
    provider, and what it said) sits in ``metadata``."""
    if not isinstance(err, dict):
        return str(err)
    text = str(err.get("message") or "error")
    meta = err.get("metadata")
    if isinstance(meta, dict):
        raw = meta.get("raw")
        if raw is not None and not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False, default=str)
        extra = ": ".join(x for x in (str(meta.get("provider_name") or ""), (raw or "").strip()) if x)
        if extra:
            text += f" ({extra[:500]})"
    return text
