# Adapted from Odysseus (https://github.com/odysseus-dev/odysseus), AGPL-3.0-or-later.
"""Base64 <-> Pillow helpers and request-body parsing shared by the image routes."""

from __future__ import annotations

import base64
import binascii
import io
from typing import Any

from fastapi import HTTPException, Request
from PIL import Image


def strip_data_url(value: str) -> str:
    if "," in value and value.split(",", 1)[0].startswith("data:"):
        return value.split(",", 1)[1]
    return value


def b64_to_bytes(value: str, *, what: str = "image") -> bytes:
    if not value or not isinstance(value, str):
        raise HTTPException(400, f"Missing {what}")
    try:
        return base64.b64decode(strip_data_url(value), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"Invalid {what}") from exc


def bytes_to_pil(raw: bytes, *, mode: str = "RGBA", what: str = "image") -> Image.Image:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        return img.convert(mode)
    except Image.DecompressionBombError as exc:  # Pillow's pixel-count guard
        raise HTTPException(413, f"{what.capitalize()} is too large") from exc
    except Exception as exc:
        raise HTTPException(400, f"Invalid {what}") from exc


def b64_to_pil(value: str, *, mode: str = "RGBA", what: str = "image") -> Image.Image:
    return bytes_to_pil(b64_to_bytes(value, what=what), mode=mode, what=what)


def b64_to_mask(value: str, *, what: str = "mask") -> Image.Image:
    """Decode a mask PNG to L mode, white = selected.

    The editor sends white-on-transparent canvases. A plain `.convert("L")`
    ignores alpha and relies on the browser zeroing RGB under transparent
    pixels, so weight luminance by alpha instead. Opaque black/white masks
    convert exactly as before.
    """
    img = bytes_to_pil(b64_to_bytes(value, what=what), mode="RGBA", what=what)
    lum = img.convert("L")
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] == 255:
        return lum
    from PIL import ImageChops

    return ImageChops.multiply(lum, alpha)


def pil_to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def pil_to_b64(img: Image.Image) -> str:
    return base64.b64encode(pil_to_png_bytes(img)).decode("ascii")


def split_alpha(img: Image.Image) -> tuple[Image.Image, Image.Image | None]:
    """Return (RGB image, alpha or None when fully opaque)."""
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    lo, _hi = alpha.getextrema()
    return rgba.convert("RGB"), (None if lo == 255 else alpha)


def with_alpha(rgb: Image.Image, alpha: Image.Image | None) -> Image.Image:
    """Re-attach an alpha channel, resizing it if the RGB result changed size."""
    if alpha is None:
        return rgb
    if alpha.size != rgb.size:
        alpha = alpha.resize(rgb.size, Image.LANCZOS)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


async def read_json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:  # JSONDecodeError / UnicodeDecodeError
        raise HTTPException(400, "Request body must be JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "Request body must be a JSON object")
    return body
