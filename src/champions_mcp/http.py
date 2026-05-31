"""Shared httpx client factory.

All HTTP clients that use the standard User-Agent / timeout / follow-redirects
configuration should be created via :func:`make_http_client` to avoid repeated
construction boilerplate.
"""

from __future__ import annotations

import httpx

from .config import Settings


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    """Return a configured :class:`httpx.AsyncClient` context manager."""
    return httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    )
