# Adapted from Odysseus (https://github.com/odysseus-dev/odysseus), AGPL-3.0-or-later.
"""Image-model dispatch for configured providers.

Two provider kinds handle images:

- `diffusion`: a self-hosted server with the Odysseus Cookbook API
  (`/v1/images/edits`, `/v1/images/inpaint`, `/v1/images/harmonize`, ...).
  Request shapes are kept identical to upstream Odysseus.
- `openai-images`: an OpenAI-compatible Images API (`/v1/images/edits`,
  `/v1/images/generations`); OpenRouter's `/v1/images` reference-image API
  is used when the base URL is on openrouter.ai, as upstream does.

Every outbound URL is a configured provider base URL plus a constant path
from `_ENDPOINT_PATHS`; nothing from the request body is spliced into it.
"""

from __future__ import annotations

import base64
import io
import logging
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from PIL import Image
from starlette.concurrency import run_in_threadpool

from app.config import Provider, Settings
from app.imaging import b64_to_mask, b64_to_pil, bytes_to_pil, pil_to_png_bytes
from app.url_safety import block_private_default, check_outbound_url

logger = logging.getLogger(__name__)

_ENDPOINT_PATHS = frozenset({
    "/images",
    "/images/edits",
    "/images/generations",
    "/images/harmonize",
    "/images/img2img",
    "/images/inpaint",
    "/images/upscale",
    "/images/variations",
    "/sdapi/v1/img2img",
})

_DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1"

# Seconds. Cold-start SDXL can take 60-90 s before the first step runs.
TIMEOUT_OPENAI = 120.0
TIMEOUT_DIFFUSION = 240.0
TIMEOUT_STYLE = 180.0
TIMEOUT_UPSCALE = 120.0
TIMEOUT_RESULT_FETCH = 60.0


# ------------------------------------------------------------ resolution ---

def resolve_image_provider(
    settings: Settings,
    requested_base: str,
    *,
    capability: str = "image-edit",
    prefer_kinds: Iterable[str] = (),
) -> Provider:
    """Map the browser's `_endpoint` to a configured provider, or pick a default.

    An unknown URL is rejected; the server never calls a URL the browser chose.
    """
    requested_base = (requested_base or "").strip()
    if requested_base:
        provider = settings.find_image_provider(requested_base)
        if provider is None:
            raise HTTPException(
                400,
                "Unknown image endpoint. Choose a model listed by /api/model-endpoints "
                "(providers are configured in config/providers.toml).",
            )
        if not provider.supports(capability):
            raise HTTPException(
                400, f"{provider.name} is not configured for {capability} (add it to the provider's capabilities)."
            )
        return provider
    provider = settings.default_image_provider(capability, prefer_kinds=prefer_kinds)
    if provider is None:
        raise HTTPException(
            400,
            "No image generation endpoint configured. Enable an image provider in config/providers.toml.",
        )
    return provider


def resolve_model(provider: Provider, chosen: str) -> str:
    chosen = (chosen or "").strip()
    if chosen:
        if provider.models and chosen not in provider.models:
            raise HTTPException(400, f"Model {chosen!r} is not configured for {provider.name}.")
        return chosen
    if provider.models:
        return provider.models[0]
    return _DEFAULT_OPENAI_IMAGE_MODEL if provider.kind == "openai-images" else ""


def api_base(provider: Provider) -> str:
    base = provider.base_url.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def join_endpoint(base: str, path: str) -> str:
    if path not in _ENDPOINT_PATHS:
        raise ValueError(f"Unexpected image endpoint path: {path!r}")
    return base + path


def is_openrouter(provider: Provider) -> bool:
    host = (urlsplit(provider.base_url).hostname or "").lower().rstrip(".")
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def _auth_headers(provider: Provider, *, required: bool) -> dict[str, str]:
    key = provider.api_key
    if not key:
        if required:
            hint = (f"Set {provider.api_key_env} in .env." if provider.api_key_env
                    else "Add api_key_env to this provider in config/providers.toml.")
            raise HTTPException(400, f"{provider.name} has no API key. {hint}")
        return {}
    return {"Authorization": f"Bearer {key}"}


def make_client(timeout: float, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        transport=transport,
        follow_redirects=False,
    )


def _vendor_error(r: httpx.Response) -> str:
    """Best-effort short error message from a vendor error response."""
    try:
        data = r.json()
    except Exception:
        return ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        msg = err or data.get("detail") or data.get("message") or ""
        return str(msg)[:300]
    return ""


async def fetch_result_image_b64(client: httpx.AsyncClient, url: str) -> str | None:
    """Download an image URL returned by a vendor (validated first)."""
    ok, reason = await run_in_threadpool(check_outbound_url, url, block_private=block_private_default())
    if not ok:
        raise HTTPException(502, f"Upstream returned an unsafe image URL: {reason}")
    r = await client.get(url, timeout=TIMEOUT_RESULT_FETCH)
    if r.status_code == 200:
        return base64.b64encode(r.content).decode()
    return None


async def _image_from_openai_result(client: httpx.AsyncClient, result: Any) -> str | None:
    if isinstance(result, dict) and isinstance(result.get("data"), list) and result["data"]:
        item = result["data"][0]
        if not isinstance(item, dict):
            return None
        if item.get("b64_json"):
            return item["b64_json"]
        if item.get("url"):
            return await fetch_result_image_b64(client, item["url"])
    return None


def openai_size(width: int, height: int) -> str:
    """gpt-image-1 edit sizes: 1024x1024, 1024x1536, 1536x1024; closest aspect."""
    if width > height * 1.15:
        return "1536x1024"
    if height > width * 1.15:
        return "1024x1536"
    return "1024x1024"


def _int_field(body: dict, key: str, default: int) -> int:
    try:
        return int(body.get(key) or default)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} must be an integer") from None


def _data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


# ---------------------------------------------------------------- inpaint ---

async def inpaint(
    provider: Provider,
    body: dict,
    chosen_model: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """`body` is the editor request minus `_endpoint`/`_model`."""
    if provider.kind == "openai-images":
        return await _inpaint_openai(provider, body, chosen_model, transport)
    return await _inpaint_diffusion(provider, body, chosen_model, transport)


async def _inpaint_openai(provider, body, chosen_model, transport) -> dict:
    # Mask conventions differ:
    #   editor / SD: white = regenerate, black = keep
    #   OpenAI:      transparent = regenerate, opaque = keep
    headers = _auth_headers(provider, required=True)
    oa_model = resolve_model(provider, chosen_model)
    if "dall-e-3" in oa_model:
        raise HTTPException(400, "dall-e-3 doesn't support image edits — pick gpt-image-1 or dall-e-2")

    source_png = b64_to_pil(body.get("image"), mode="RGBA")
    mask_png = b64_to_mask(body.get("mask"))
    if mask_png.size != source_png.size:
        mask_png = mask_png.resize(source_png.size, Image.NEAREST)
    oa_mask = Image.new("RGBA", source_png.size, (255, 255, 255, 255))
    oa_mask.putalpha(mask_png.point(lambda p: 255 - p))
    src_bytes = pil_to_png_bytes(source_png)

    size = openai_size(_int_field(body, "width", 1024), _int_field(body, "height", 1024))
    prompt = str(body.get("prompt", ""))
    base = api_base(provider)

    try:
        async with make_client(TIMEOUT_OPENAI, transport) as client:
            if is_openrouter(provider):
                # Reference-based edit; the local composite below keeps
                # pixels outside the mask unchanged.
                payload = {
                    "model": oa_model, "size": size, "n": 1,
                    "output_format": "png",
                    "prompt": "Edit the first image. The second image is a mask: white marks the region to edit, "
                              "black marks the region to preserve. Keep composition and framing unchanged. "
                              "Requested edit: " + prompt,
                    "input_references": [
                        {"type": "image_url", "image_url": {"url": _data_url(raw)}}
                        for raw in (src_bytes, pil_to_png_bytes(mask_png))
                    ],
                }
                r = await client.post(join_endpoint(base, "/images"), headers=headers, json=payload)
            else:
                files = {
                    "image": ("source.png", src_bytes, "image/png"),
                    "mask": ("mask.png", pil_to_png_bytes(oa_mask), "image/png"),
                }
                data = {"model": oa_model, "prompt": prompt, "size": size, "n": "1"}
                r = await client.post(join_endpoint(base, "/images/edits"), headers=headers, data=data, files=files)
            if r.status_code != 200:
                logger.error("inpaint %s: status %s", provider.id, r.status_code)
                msg = _vendor_error(r)
                raise HTTPException(r.status_code, f"OpenAI edit failed: {msg}" if msg else "OpenAI edit failed")
            raw_b64 = await _image_from_openai_result(client, r.json())
    except httpx.TimeoutException:
        raise HTTPException(504, f"OpenAI inpaint timed out ({int(TIMEOUT_OPENAI)}s)") from None
    except httpx.HTTPError as exc:
        logger.warning("inpaint %s: %s", provider.id, exc)
        raise HTTPException(502, f"Can't reach {provider.name}") from None
    except ValueError:
        raise HTTPException(502, "OpenAI returned an invalid response") from None
    if not raw_b64:
        raise HTTPException(502, "OpenAI returned no image")

    # gpt-image-1 regenerates the whole frame; composite onto the source
    # with the editor's mask so only the masked region changes.
    try:
        generated = bytes_to_pil(base64.b64decode(raw_b64), mode="RGBA")
        if generated.size != source_png.size:
            generated = generated.resize(source_png.size, Image.LANCZOS)
        blended = Image.composite(generated, source_png, mask_png)
        return {"image": base64.b64encode(pil_to_png_bytes(blended)).decode()}
    except Exception as exc:
        # Never return a full-image edit when masking fails.
        logger.warning("inpaint compose failed: %s", exc)
        raise HTTPException(502, "Could not apply the edit within the selected region") from None


async def _inpaint_diffusion(provider, body, chosen_model, transport) -> dict:
    # Newer Odysseus image wrappers expose OpenAI-style multipart
    # /v1/images/edits; older diffusion_server.py only has JSON
    # /v1/images/inpaint. Try edits first, then fall back.
    model = resolve_model(provider, chosen_model)
    if model:
        body["model"] = model
    source_png = b64_to_pil(body.get("image"), mode="RGBA")
    # Local MLX / Diffusers wrappers treat white mask pixels as "edit".
    mask_png = b64_to_mask(body.get("mask"))
    width = _int_field(body, "width", source_png.width)
    height = _int_field(body, "height", source_png.height)
    files = {
        "image": ("source.png", pil_to_png_bytes(source_png), "image/png"),
        "mask": ("mask.png", pil_to_png_bytes(mask_png), "image/png"),
    }
    data = {"model": model, "prompt": str(body.get("prompt", "")), "size": f"{width}x{height}", "n": "1"}
    headers = _auth_headers(provider, required=False)
    base = api_base(provider)

    try:
        async with make_client(TIMEOUT_DIFFUSION, transport) as client:
            r = await client.post(join_endpoint(base, "/images/edits"), data=data, files=files, headers=headers)
            if r.status_code == 200:
                result = r.json()
                image = await _image_from_openai_result(client, result)
                if image:
                    return {"image": image}
                if isinstance(result, dict) and result.get("image"):
                    return {"image": result["image"]}
                raise HTTPException(502, "Image edit endpoint returned no image")
            if r.status_code not in (404, 405):
                logger.warning("inpaint %s /images/edits: status %s", provider.id, r.status_code)
                detail = _vendor_error(r) or "Image edit request failed"
                # A plain SD/SDXL checkpoint rejects /images/edits, but the
                # dedicated /images/inpaint route can still derive an
                # inpaint, img2img or txt2img+composite fallback.
                if not (r.status_code == 400 and "does not support image edits" in detail.lower()):
                    raise HTTPException(r.status_code, detail)

            r = await client.post(join_endpoint(base, "/images/inpaint"), json=body, headers=headers)
            if r.status_code != 200:
                logger.error("inpaint %s /images/inpaint: status %s", provider.id, r.status_code)
                raise HTTPException(r.status_code, "Inpaint request failed")
            return r.json()
    except httpx.TimeoutException:
        raise HTTPException(504, f"Inpaint request timed out ({int(TIMEOUT_DIFFUSION)}s)") from None
    except httpx.ConnectError:
        raise HTTPException(502, "Can't reach diffusion server") from None
    except (httpx.HTTPError, ValueError):
        logger.exception("inpaint %s: request failed", provider.id)
        raise HTTPException(502, "Inpaint request failed") from None


# -------------------------------------------------------------- harmonize ---

HARMONIZE_NEEDS_IMG2IMG = (
    "Harmonize needs a diffusion server that supports img2img "
    "(SD WebUI / Forge / Comfy). OpenAI's API doesn't expose one. "
    "Add a provider with kind = \"diffusion\" in config/providers.toml."
)


def _clamp(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


async def harmonize(
    provider: Provider,
    body: dict,
    model: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """img2img harmonize: Reinhard colour match on a body mask + optional seam inpaint.

    Tries the img2img-shaped routes in order; the first 200 with an image wins.
    """
    image_b64 = body.get("image")
    if not image_b64:
        raise HTTPException(400, "No image provided")
    # OpenAI's Images API has no img2img: edits regenerate from the prompt.
    if provider.kind == "openai-images":
        raise HTTPException(400, HARMONIZE_NEEDS_IMG2IMG)
    model = resolve_model(provider, model)

    prompt = body.get("prompt") or "natural lighting, harmonious color, seamless blend"
    # Legacy single `strength` maps to color_match for old clients.
    strength = _clamp(body.get("strength", 0.45), 0.45, 0.05, 0.95)
    color_match = _clamp(body.get("color_match", strength), strength, 0.0, 1.0)
    seam_fix = _clamp(body.get("seam_fix", 0.0), 0.0, 0.0, 1.0)
    body_mask_b64 = body.get("body_mask") or body.get("mask")
    seam_mask_b64 = body.get("seam_mask")

    harmonize_payload: dict[str, Any] = {
        "image": image_b64,
        "prompt": prompt,
        "color_match": color_match,
        "seam_fix": seam_fix,
        # Legacy names so an older diffusion server still finds the mask;
        # newer servers prefer body_mask over mask.
        "strength": color_match,
    }
    if body_mask_b64:
        harmonize_payload["body_mask"] = body_mask_b64
        harmonize_payload["mask"] = body_mask_b64
    if seam_mask_b64:
        harmonize_payload["seam_mask"] = seam_mask_b64
    model_field = {"model": model} if model else {}
    candidates = [
        ("/images/harmonize", harmonize_payload),
        ("/images/img2img", {"image": image_b64, "prompt": prompt, "strength": strength, **model_field}),
        ("/images/variations", {"image": image_b64, "prompt": prompt, "strength": strength, **model_field}),
        # AUTOMATIC1111-style last resort.
        ("/sdapi/v1/img2img", {
            "init_images": [f"data:image/png;base64,{image_b64}"],
            "prompt": prompt,
            "denoising_strength": strength,
            "steps": 30,
            **({"override_settings": {"sd_model_checkpoint": model}} if model else {}),
        }),
    ]

    base = api_base(provider)
    base_root = base[:-3]  # A1111 lives at /sdapi/v1, not under /v1
    headers = _auth_headers(provider, required=False)

    async with make_client(TIMEOUT_DIFFUSION, transport) as client:
        for path, payload in candidates:
            target = join_endpoint(base_root if path.startswith("/sdapi") else base, path)
            try:
                r = await client.post(target, json=payload, headers=headers)
                if r.status_code != 200:
                    if r.status_code != 404:
                        logger.warning("harmonize: %s returned %s", path, r.status_code)
                    continue
                data = r.json()
                if isinstance(data, dict):
                    # An explicit error with 200: surface it rather than bury
                    # it under 404s from the remaining routes.
                    if data.get("error") and not data.get("image"):
                        logger.warning("harmonize: server error at %s: %s", path, data.get("error"))
                        raise HTTPException(502, f"Diffusion server error at {path}")
                    if data.get("image"):
                        return {"image": data["image"]}
                    images = data.get("images")
                    if images and isinstance(images, list) and isinstance(images[0], str):
                        img0 = images[0]
                        if img0.startswith("data:"):
                            img0 = img0.split(",", 1)[1]
                        return {"image": img0}
                    image = await _image_from_openai_result(client, data)
                    if image:
                        return {"image": image}
            except httpx.ConnectError:
                logger.warning("harmonize: can't reach diffusion server at %s", base)
                raise HTTPException(502, "Can't reach diffusion server") from None
            except httpx.TimeoutException:
                raise HTTPException(
                    504,
                    f"Harmonize timed out ({int(TIMEOUT_DIFFUSION)}s) — restart the diffusion server "
                    "or lower Color match / disable Seam fix",
                ) from None
            except (httpx.HTTPError, ValueError):
                logger.exception("harmonize: %s failed", path)
                continue
    raise HTTPException(
        502,
        "No supported img2img route responded. Your diffusion server needs to expose one of: "
        "/v1/images/harmonize, /v1/images/img2img, /v1/images/variations, /sdapi/v1/img2img.",
    )


# --------------------------------------------------------- style transfer ---

STYLE_PROMPT_OPENAI = (
    "Restyle this image: {prompt}. Keep the composition, framing and subjects unchanged."
)


async def style_transfer(
    provider: Provider,
    image_bytes: bytes,
    prompt: str,
    strength: float,
    model: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Returns `{"image": b64}` or upstream's `{"error": ...}` shape (HTTP 200)."""
    model = resolve_model(provider, model)
    if provider.kind == "openai-images":
        return await _style_transfer_openai(provider, image_bytes, prompt, model, transport)

    b64 = base64.b64encode(image_bytes).decode()
    payload: dict[str, Any] = {"prompt": prompt, "image": b64, "strength": strength, "response_format": "b64_json"}
    if model:
        payload["model"] = model
    try:
        async with make_client(TIMEOUT_STYLE, transport) as client:
            r = await client.post(
                join_endpoint(api_base(provider), "/images/generations"),
                json=payload,
                headers=_auth_headers(provider, required=False),
            )
            if r.status_code == 200:
                image = await _image_from_openai_result(client, r.json())
                if image:
                    return {"image": image}
            return {"error": f"Style transfer failed ({r.status_code})"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("style_transfer: request failed")
        return {"error": "Style transfer failed"}


async def _style_transfer_openai(provider, image_bytes, prompt, model, transport) -> dict:
    # No img2img strength in the Images API: a mask-less edit with the
    # style as the instruction is the closest equivalent.
    headers = _auth_headers(provider, required=True)
    source = bytes_to_pil(image_bytes, mode="RGBA")
    src_bytes = pil_to_png_bytes(source)
    size = openai_size(source.width, source.height)
    instruction = STYLE_PROMPT_OPENAI.format(prompt=prompt)
    base = api_base(provider)
    try:
        async with make_client(TIMEOUT_OPENAI, transport) as client:
            if is_openrouter(provider):
                r = await client.post(join_endpoint(base, "/images"), headers=headers, json={
                    "model": model, "size": size, "n": 1, "output_format": "png", "prompt": instruction,
                    "input_references": [{"type": "image_url", "image_url": {"url": _data_url(src_bytes)}}],
                })
            else:
                r = await client.post(
                    join_endpoint(base, "/images/edits"),
                    headers=headers,
                    data={"model": model, "prompt": instruction, "size": size, "n": "1"},
                    files={"image": ("source.png", src_bytes, "image/png")},
                )
            if r.status_code != 200:
                msg = _vendor_error(r)
                return {"error": f"Style transfer failed ({r.status_code}){': ' + msg if msg else ''}"}
            image = await _image_from_openai_result(client, r.json())
            return {"image": image} if image else {"error": "Style transfer failed (no image returned)"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("style_transfer: OpenAI request failed")
        return {"error": "Style transfer failed"}


# ------------------------------------------------------------- AI upscale ---

async def ai_upscale(
    provider: Provider,
    image_bytes: bytes,
    scale: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Diffusion server `/v1/images/upscale`; `{"error": ...}` shape on failure."""
    if provider.kind != "diffusion":
        return {"error": f"Upscale endpoint not available ({provider.name} has no upscale route)"}
    b64 = base64.b64encode(image_bytes).decode()
    try:
        async with make_client(TIMEOUT_UPSCALE, transport) as client:
            r = await client.post(
                join_endpoint(api_base(provider), "/images/upscale"),
                json={"image": b64, "scale": scale},
                headers=_auth_headers(provider, required=False),
            )
            if r.status_code == 200:
                data = r.json()
                return {"image": (data.get("data") or [{}])[0].get("b64_json", "")}
            return {"error": f"Upscale endpoint not available ({r.status_code})"}
    except Exception:
        logger.exception("ai_upscale: request failed")
        return {"error": "Upscale request failed"}


def image_bytes_ok(raw: bytes) -> None:
    """Reject non-image uploads early with upstream's 400 shape."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
    except Exception:
        raise HTTPException(400, "Invalid image") from None
