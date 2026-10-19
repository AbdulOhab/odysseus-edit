# Adapted from Odysseus (https://github.com/odysseus-dev/odysseus), AGPL-3.0-or-later.
"""Local image tools (no vendor): sharpen, denoise, upscale-local, mask,
remove-bg, enhance-face.

Shapes match upstream. All take a JSON body with `image` (base64 PNG, a
`data:` prefix is tolerated) and return `{"image": b64}`. When an optional
ML dependency is missing they answer exactly as upstream does: denoise and
upscale-local return HTTP 200 `{"error": "realesrgan not installed. ..."}`,
remove-bg returns 200 `{"error": "No background removal model available. ..."}`,
mask raises 501, enhance-face falls back to Pillow (`"method": "pil"`).

The editor's runner may attach `_endpoint`/`_model` to any tool request;
these routes ignore them and never make outbound calls.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from PIL import Image
from starlette.concurrency import run_in_threadpool

from app.imaging import (
    b64_to_mask,
    b64_to_pil,
    pil_to_b64,
    read_json_object,
    split_alpha,
    with_alpha,
)
from app.services import local_tools

logger = logging.getLogger(__name__)

router = APIRouter(tags=["image-local"])


def _number(body: dict, key: str, default: float) -> float:
    value = body.get(key, default)
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} must be a number") from None


def _require_image(body: dict) -> str:
    image_b64 = body.get("image")
    if not image_b64:
        raise HTTPException(400, "No image provided")
    return image_b64


@router.post("/api/image/sharpen")
async def sharpen(request: Request):
    """Body: image, amount (0..100, default 50)."""
    body = await read_json_object(request)
    img = b64_to_pil(_require_image(body), mode="RGBA")
    amount = max(0.0, _number(body, "amount", 50)) / 100.0
    out = await run_in_threadpool(local_tools.sharpen, img, amount)
    return {"image": pil_to_b64(out)}


@router.post("/api/image/denoise")
async def denoise(request: Request):
    """Body: image, strength (0..1, default 0.5). Real-ESRGAN general-x4v3 at 1x."""
    body = await read_json_object(request)
    img = b64_to_pil(_require_image(body), mode="RGBA")
    strength = max(0.0, min(1.0, _number(body, "strength", 0.5)))
    rgb, alpha = split_alpha(img)
    try:
        out = await run_in_threadpool(local_tools.realesrgan_denoise, rgb, strength)
    except ImportError:
        return {"error": local_tools.REALESRGAN_MISSING}
    except Exception:
        logger.warning("Denoise failed", exc_info=True)
        return {"error": "Denoise failed"}
    return {"image": pil_to_b64(with_alpha(out, alpha))}


@router.post("/api/image/upscale-local")
async def upscale_local(request: Request):
    """Body: image, scale (2 or 4, default 2). Real-ESRGAN x4plus."""
    body = await read_json_object(request)
    img = b64_to_pil(_require_image(body), mode="RGBA")
    try:
        scale = int(body.get("scale", 2))
    except (TypeError, ValueError):
        scale = 2
    scale = scale if scale in (2, 4) else 2
    rgb, alpha = split_alpha(img)
    try:
        out = await run_in_threadpool(local_tools.realesrgan_upscale, rgb, scale)
    except ImportError:
        return {"error": local_tools.REALESRGAN_MISSING}
    except Exception:
        logger.warning("AI upscale failed", exc_info=True)
        return {"error": "AI upscale failed"}
    return {"image": pil_to_b64(with_alpha(out, alpha))}


@router.post("/api/image/mask")
async def smart_mask(request: Request):
    """Body: image, and one of points [{x, y, label}], box [x1, y1, x2, y2],
    or text/query. Returns {mask (white = selected), bbox, model, device,
    grounding?}."""
    body = await read_json_object(request)
    image = b64_to_pil(body.get("image") or "", mode="RGB")
    points = body.get("points") or []
    box = body.get("box")
    text = str(body.get("text") or body.get("query") or "").strip()
    return await run_in_threadpool(local_tools.sam_mask, image, points, box, text)


@router.post("/api/image/remove-bg")
async def remove_background(request: Request):
    """Body: image, hint_mask? (white where the subject is, image-sized).
    Returns the pristine cutout; feather/grow are applied client-side."""
    body = await read_json_object(request)
    img = b64_to_pil(_require_image(body), mode="RGBA")
    hint = None
    if body.get("hint_mask"):
        try:
            hint = b64_to_mask(body["hint_mask"], what="hint mask")
            if hint.size != img.size:
                hint = hint.resize(img.size, Image.NEAREST)
        except HTTPException:
            hint = None  # upstream ignores an unreadable hint
    try:
        out = await run_in_threadpool(local_tools.remove_background, img, hint)
    except local_tools.BackgroundRemovalUnavailable as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("remove_background: failed")
        raise HTTPException(500, "Background removal failed") from None
    return {"image": pil_to_b64(out)}


@router.post("/api/image/enhance-face")
async def enhance_face(request: Request):
    """Body: image. GFPGAN v1.4 when installed, else a Pillow enhancement
    chain (response then carries "method": "pil")."""
    body = await read_json_object(request)
    img = b64_to_pil(_require_image(body), mode="RGBA")
    rgb, alpha = split_alpha(img)
    try:
        out, method = await run_in_threadpool(local_tools.enhance_face, rgb)
    except Exception:
        logger.exception("enhance_face: failed")
        raise HTTPException(500, "Face enhancement failed") from None
    result = {"image": pil_to_b64(with_alpha(out, alpha))}
    if method == "pil":
        result["method"] = "pil"
    return result
