"""Decoder for Nuxt's SSR ``_payload.json`` wire format.

pokechamp.gg's ``/database/*`` pages are server-rendered Nuxt pages; each
ships its data as a `devalue <https://github.com/Rich-Harris/devalue>`_-style
flat array: syntactically plain JSON, but every object/array value that isn't
a raw literal is an *index* into that same top-level array, so shared values
(interned strings/numbers, repeated sub-objects) are stored once and pointed
to from everywhere they occur. A handful of two-element arrays are tagged
reconstruction hints (``["Reactive", i]``, ``["Set", ...]``, ...) rather than
literal two-item lists.

Only this reference-resolution is implemented — enough to read the data these
pages embed. Nothing here is Nuxt/Vue-specific beyond unwrapping the
reactivity tags, and nothing here makes network calls.
"""

from __future__ import annotations

from typing import Any

_REACTIVITY_TAGS = frozenset({
    "Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef",
})


def decode(raw: list[Any]) -> Any:
    """Fully resolve a devalue-style payload array into plain Python data.

    Args:
        raw: The parsed top-level JSON array (``json.loads`` of the payload).

    Returns:
        ``raw[0]`` with every internal index reference recursively resolved
        to its pointed-to value.
    """
    memo: dict[int, Any] = {}
    _RESOLVING = object()  # cycle guard sentinel

    def resolve(i: int) -> Any:
        if i in memo:
            cached = memo[i]
            return None if cached is _RESOLVING else cached
        v = raw[i]
        if v is None or isinstance(v, (str, bool, int, float)):
            memo[i] = v
            return v
        if isinstance(v, dict):
            memo[i] = _RESOLVING
            out = {k: resolve(ref) if isinstance(ref, int) else ref
                   for k, ref in v.items()}
            memo[i] = out
            return out
        if isinstance(v, list):
            memo[i] = _RESOLVING
            if v and isinstance(v[0], str) and v[0] in _REACTIVITY_TAGS:
                out = resolve(v[1]) if len(v) > 1 and isinstance(v[1], int) else None
            elif v and v[0] == "Set":
                out = [resolve(e) if isinstance(e, int) else e for e in v[1:]]
            elif v and v[0] == "Map":
                pairs = v[1] if len(v) > 1 else []
                out = {
                    (resolve(k) if isinstance(k, int) else k):
                        (resolve(val) if isinstance(val, int) else val)
                    for k, val in pairs
                }
            elif v and v[0] == "Date":
                out = v[1] if len(v) > 1 else None
            else:
                out = [resolve(e) if isinstance(e, int) else e for e in v]
            memo[i] = out
            return out
        memo[i] = v
        return v

    return resolve(0)
