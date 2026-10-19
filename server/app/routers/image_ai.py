# Adapted from Odysseus (https://github.com/odysseus-dev/odysseus), AGPL-3.0-or-later.
"""Image-model routes: inpaint, harmonize, style transfer, AI upscale.

Request/response shapes match upstream `routes/gallery/gallery_routes.py`
so the unmodified editor JS works. The browser may name an endpoint
(`_endpoint`, the `base_url` from /api/model-endpoints); it is resolved to a
configured provider or rejected, and the API key always comes from config.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile

from app.config import Settings
from app.deps import get_settings, get_transport
from app.imaging import read_json_object
from app.services import image_vendors as vendors

router = APIRouter(tags=["image-ai"])


def _pop_selection(body: dict[str, Any]) -> tuple[str, str]:
    endpoint = body.pop("_endpoint", "") or ""
    model = body.pop("_model", "") or ""
    if not isinstance(endpoint, str) or not isinstance(model, str):
        raise HTTPException(400, "_endpoint and _model must be strings")
    return endpoint.strip(), model.strip()


async def _read_upload(form, field: str = "image") -> bytes:
    file = form.get(field)
    if not file or not isinstance(file, UploadFile):
        raise HTTPException(400, "No image")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "No image")
    vendors.image_bytes_ok(raw)
    return raw


def _form_str(form, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


@router.post("/api/image/inpaint")
async def inpaint(request: Request, settings: Settings = Depends(get_settings)):
    """Body: image, mask (b64 PNG, white = regenerate), prompt, width, height,
    strength, feather, _endpoint, _model. Returns {"image": b64}."""
    body = await read_json_object(request)
    endpoint, model = _pop_selection(body)
    provider = vendors.resolve_image_provider(settings, endpoint, capability="image-edit")
    return await vendors.inpaint(provider, body, model, get_transport(request))


@router.post("/api/image/harmonize")
async def harmonize(request: Request, settings: Settings = Depends(get_settings)):
    """Body: image, prompt, color_match, seam_fix, body_mask, seam_mask?,
    _endpoint?, _model?. Returns {"image": b64}."""
    body = await read_json_object(request)
    endpoint, model = _pop_selection(body)
    if not body.get("image"):
        raise HTTPException(400, "No image provided")
    provider = vendors.resolve_image_provider(
        settings, endpoint, capability="image-edit", prefer_kinds=("diffusion",),
    )
    return await vendors.harmonize(provider, body, model, get_transport(request))


@router.post("/api/gallery/style-transfer")
async def style_transfer(request: Request, settings: Settings = Depends(get_settings)):
    """Multipart: image (file), prompt, strength, and optionally _endpoint /
    _model (the stock editor does not send them). Returns {"image": b64} or
    {"error": "..."} with HTTP 200, as upstream does."""
    form = await request.form()
    raw = await _read_upload(form)
    prompt = _form_str(form, "prompt")
    try:
        strength = float(form.get("strength") or 0.55)
    except (TypeError, ValueError):
        raise HTTPException(400, "strength must be a number") from None
    provider = vendors.resolve_image_provider(settings, _form_str(form, "_endpoint"), capability="image-edit")
    return await vendors.style_transfer(
        provider, raw, prompt, strength, _form_str(form, "_model"), get_transport(request),
    )


@router.post("/api/gallery/ai-upscale")
async def ai_upscale(request: Request, settings: Settings = Depends(get_settings)):
    """Multipart: image (file), scale. Diffusion `/v1/images/upscale` only.
    Not called by the stock editor (it uses /api/image/upscale-local)."""
    form = await request.form()
    raw = await _read_upload(form)
    try:
        scale = int(form.get("scale") or 2)
    except (TypeError, ValueError):
        raise HTTPException(400, "scale must be an integer") from None
    provider = vendors.resolve_image_provider(
        settings, _form_str(form, "_endpoint"), capability="image-edit", prefer_kinds=("diffusion",),
    )
    return await vendors.ai_upscale(provider, raw, scale, get_transport(request))
