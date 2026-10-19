"""Request-scoped accessors for app state (settings, outbound HTTP transport)."""

from __future__ import annotations

import httpx
from fastapi import Request

from app.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_transport(request: Request) -> httpx.AsyncBaseTransport | None:
    """Outbound transport override; tests inject `httpx.MockTransport` here."""
    return getattr(request.app.state, "http_transport", None)
