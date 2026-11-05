"""Minimal decoder for React Router "turbo-stream" payloads embedded in pokesol.app pages.

The page calls `streamController.enqueue("<json string>")`; the joined string's first line
is a flat JSON array where objects look like {"_<keyIdx>": <valueIdx>} and every value is an
index into that same array (negative numbers are sentinels such as undefined/null).
"""
from __future__ import annotations

import json
import re
import sys

_ENQUEUE = re.compile(r'enqueue\(("(?:[^"\\]|\\.)*")\)')
_TYPED = {"D", "P", "S", "Y", "R", "E", "M", "Z"}  # Date, Promise, Symbol, … kept as-is


def decode_page(page_html: str):
    chunks = _ENQUEUE.findall(page_html)
    if not chunks:
        return None
    raw = "".join(json.loads(c) for c in chunks)
    first = next((l for l in raw.split("\n") if l.strip()), None)
    if not first:
        return None
    arr = json.loads(first)
    memo: dict[int, object] = {}
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

    def dec(i):
        if isinstance(i, int) and i < 0:
            return None
        if i in memo:
            return memo[i]
        v = arr[i]
        if isinstance(v, dict):
            o: dict = {}
            memo[i] = o
            for k, vi in v.items():
                o[arr[int(k[1:])]] = dec(vi)
            return o
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in _TYPED:
                memo[i] = v
                return v
            o2: list = []
            memo[i] = o2
            o2.extend(dec(x) for x in v)
            return o2
        memo[i] = v
        return v

    return dec(0)
