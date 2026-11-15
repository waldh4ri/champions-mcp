"""Chat history trimming must keep tool calls and their results paired: a
provider answers a stray tool result with a 400 that repeats on every retry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker" / "chat"))

import history as hist  # noqa: E402


def _chat(turns: int, parallel: int = 3) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": "q0"}]
    for t in range(turns):
        calls = [{"id": f"c{t}_{k}", "type": "function",
                  "function": {"name": "x", "arguments": "{}"}} for k in range(parallel)]
        msgs.append({"role": "assistant", "content": None, "tool_calls": calls})
        msgs += [{"role": "tool", "tool_call_id": c["id"], "content": "r" * 150} for c in calls]
        msgs.append({"role": "assistant", "content": f"answer {t}"})
        msgs.append({"role": "user", "content": f"q{t + 1}"})
    return msgs


def _paired(msgs: list[dict]) -> bool:
    """Every tool result answers a call of the assistant message before it, and
    every call has its result."""
    pending: set[str] = set()
    for m in msgs:
        if m["role"] == "assistant" and m.get("tool_calls"):
            if pending:
                return False
            pending = {c["id"] for c in m["tool_calls"]}
        elif m["role"] == "tool":
            if m["tool_call_id"] not in pending:
                return False
            pending.discard(m["tool_call_id"])
        elif pending:
            return False
    return not pending


@pytest.mark.parametrize("budget", range(600, 2400, 100))
def test_fit_history_never_leaves_a_chain_half_cut(budget):
    """Dropping one message at a time and stopping on the budget left results
    with no call (a 400 from OpenAI-style providers) for about half of these."""
    msgs = _chat(5)
    hist.fit_history(msgs, len(msgs), budget, old_tool_result_chars=200)
    assert _paired(msgs), budget
    # what the user said and what was answered always survives
    assert [m["content"] for m in msgs if m["role"] == "user"] == [f"q{i}" for i in range(6)]
    assert [m["content"] for m in msgs if m["role"] == "assistant" and not m.get("tool_calls")] \
        == [f"answer {i}" for i in range(5)]


def test_fit_history_leaves_a_small_history_alone():
    msgs = _chat(2)
    before = [dict(m) for m in msgs]
    hist.fit_history(msgs, len(msgs), 10**9, old_tool_result_chars=200)
    assert msgs == before


def test_fit_history_only_touches_earlier_turns():
    msgs = _chat(4)
    upto = len(msgs) - 1                    # the latest user message is the new question
    last = dict(msgs[-1])
    hist.fit_history(msgs, upto, 300, old_tool_result_chars=50)
    assert msgs[-1] == last and _paired(msgs)


def test_repair_drops_orphans_and_incomplete_chains():
    msgs = _chat(3)
    del msgs[1]                              # the first call is gone, its results remain
    assert not _paired(msgs)
    assert hist.repair_tool_pairs(msgs) == 3 and _paired(msgs)

    msgs = _chat(2)
    del msgs[-4]                             # the last chain lost one of its results
    assert not _paired(msgs)
    hist.repair_tool_pairs(msgs)
    assert _paired(msgs)


def test_repair_keeps_a_valid_history_untouched():
    msgs = _chat(3)
    before = [dict(m) for m in msgs]
    assert hist.repair_tool_pairs(msgs) == 0
    assert msgs == before


def test_openrouter_error_surfaces_the_provider_reason():
    err = {"message": "Provider returned error", "code": 400,
           "metadata": {"provider_name": "Amazon Bedrock",
                        "raw": '{"message": "unexpected tool_use_id"}'}}
    text = hist.openrouter_error(err)
    assert text.startswith("Provider returned error")
    assert "Amazon Bedrock" in text and "unexpected tool_use_id" in text
    assert hist.openrouter_error({"message": "No credits"}) == "No credits"
    assert hist.openrouter_error({"message": "x", "metadata": {"raw": {"a": 1}}}) == 'x ({"a": 1})'
    assert hist.openrouter_error("plain") == "plain"
