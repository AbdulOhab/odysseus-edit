"""Health, capability discovery, and the image-model list the editor reads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app import __version__
from app.config import Settings
from app.deps import get_settings
from app.services.local_tools import availability

router = APIRouter(tags=["meta"])


@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__}


@router.get("/api/capabilities")
async def capabilities(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """What this install can do. Cheap: import-spec probes only, no network.

    Never includes API keys; `has_key` only says whether one is set.
    """
    return {
        "version": __version__,
        "local_tools": availability(),
        "image_providers": [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "models": list(p.models),
                "capabilities": sorted(p.capabilities),
            }
            for p in settings.image_providers()
        ],
        "llm_providers": [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "models": list(p.models),
                "vision": p.supports("vision"),
                "has_key": p.has_key,
            }
            for p in settings.chat_providers()
        ],
        "defaults": dict(settings.defaults),
    }


@router.get("/api/model-endpoints")
async def model_endpoints(settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    """Image providers in the shape `web/js/editor/ai-models.js` expects.

    The editor encodes each option as `<base_url>::<model>` and sends
    `base_url` back as `_endpoint`; the AI routes resolve it against this
    same provider list. `online` is computed from config (no probe): an
    enabled provider is online unless it needs a key that is not set.
    """
    out = []
    for p in settings.image_providers():
        out.append({
            "id": p.id,
            "name": p.name,
            "base_url": p.base_url,
            "models": list(p.models),
            "model_type": "image",
            "kind": p.kind,
            "capabilities": sorted(p.capabilities),
            "is_enabled": p.enabled,
            "online": p.enabled and (p.has_key or not p.api_key_env),
            "has_key": p.has_key,
        })
    return out
