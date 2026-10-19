# Adapted from Odysseus (https://github.com/odysseus-dev/odysseus), AGPL-3.0-or-later.
"""Image-model routes against mocked vendors (httpx.MockTransport, no network)."""

from __future__ import annotations

import base64
import io

import httpx
import pytest
from conftest import (
    OPENAI_KEY,
    OPENROUTER_KEY,
    decode_png,
    parse_multipart,
    png_b64,
    png_bytes,
    request_json,
)
from PIL import Image

from app import url_safety

OA = "https://api.openai.com/v1"
DIFF = "http://127.0.0.1:7860"
OR = "https://openrouter.ai/api/v1"


def _mask_right_half(w=2, h=1) -> Image.Image:
    """White-on-transparent mask like the editor's dilateMask output."""
    mask = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(h):
        for x in range(w // 2, w):
            mask.putpixel((x, y), (255, 255, 255, 255))
    return mask


def _inpaint_body(**extra):
    body = {
        "image": png_b64(Image.new("RGB", (2, 1), "red")),
        "mask": png_b64(_mask_right_half()),
        "prompt": "make it blue",
        "width": 2, "height": 1, "strength": 0.75, "feather": 0,
    }
    body.update(extra)
    return body


def _b64_json(color="blue", size=(2, 1)):
    return {"data": [{"b64_json": png_b64(Image.new("RGB", size, color))}]}


@pytest.fixture
def fake_dns(monkeypatch):
    table = {"cdn.example.com": ["93.184.216.34"], "metadata.evil": ["169.254.169.254"]}
    monkeypatch.setattr(url_safety, "_default_resolver", lambda host: table[host])


# ------------------------------------------------------------------ SSRF ---

@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:7861",            # unconfigured local port
        "http://10.0.0.5:7860",             # configured but disabled
        "https://api.z.ai/api/anthropic",   # configured chat provider, not an image provider
        "file:///etc/passwd",
        "https://api.openai.com.evil.example/v1",
    ],
)
def test_inpaint_rejects_unknown_endpoint(client, recorder, endpoint):
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=endpoint, _model="gpt-image-1"))
    assert r.status_code == 400
    assert "Unknown image endpoint" in r.json()["detail"]
    assert recorder.requests == []


@pytest.mark.parametrize("path", ["/api/image/harmonize"])
def test_json_routes_reject_unknown_endpoint(client, recorder, path):
    r = client.post(path, json={"image": png_b64(Image.new("RGB", (2, 2))), "_endpoint": "http://evil.example"})
    assert r.status_code == 400
    assert recorder.requests == []


@pytest.mark.parametrize("path", ["/api/gallery/style-transfer", "/api/gallery/ai-upscale"])
def test_form_routes_reject_unknown_endpoint(client, recorder, path):
    files = {"image": ("x.png", png_bytes(Image.new("RGB", (2, 2))), "image/png")}
    r = client.post(path, files=files, data={"prompt": "x", "_endpoint": "http://evil.example"})
    assert r.status_code == 400
    assert recorder.requests == []


def test_capability_is_enforced(client, recorder):
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint="http://127.0.0.1:9999"))
    assert r.status_code == 400
    assert "image-edit" in r.json()["detail"]
    assert recorder.requests == []


def test_unlisted_model_rejected(client, recorder):
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA, _model="gpt-image-99"))
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]
    assert recorder.requests == []


def test_api_key_comes_from_config_not_request(client, recorder):
    recorder.on("POST", f"{OA}/images/edits", httpx.Response(200, json=_b64_json()))
    body = _inpaint_body(_endpoint=OA, api_key="attacker-key", Authorization="Bearer attacker")
    assert client.post("/api/image/inpaint", json=body).status_code == 200
    (req,) = recorder.requests
    assert req.headers["authorization"] == f"Bearer {OPENAI_KEY}"


def test_unsafe_result_url_not_fetched(client, recorder, fake_dns):
    recorder.on("POST", f"{OA}/images/edits",
                httpx.Response(200, json={"data": [{"url": "http://metadata.evil/latest"}]}))
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA))
    assert r.status_code == 502
    assert "unsafe image URL" in r.json()["detail"]
    assert recorder.urls() == [f"{OA}/images/edits"]


# --------------------------------------------------------- OpenAI inpaint ---

def test_openai_inpaint_reshapes_and_composites(client, recorder):
    recorder.on("POST", f"{OA}/images/edits", httpx.Response(200, json=_b64_json("blue", (4, 2))))
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA, _model="gpt-image-1"))
    assert r.status_code == 200, r.text
    out = decode_png(r.json()["image"]).convert("RGBA")
    assert out.size == (2, 1)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)  # unmasked: original kept
    assert out.getpixel((1, 0)) == (0, 0, 255, 255)  # masked: model output

    (req,) = recorder.requests
    parts = parse_multipart(req)
    assert parts["model"] == b"gpt-image-1"
    assert parts["prompt"] == b"make it blue"
    assert parts["n"] == b"1"
    assert parts["size"] == b"1536x1024"  # 2x1 is landscape
    oa_mask = Image.open(io.BytesIO(parts["mask"])).convert("RGBA")
    # OpenAI convention: transparent = regenerate, opaque = keep.
    assert oa_mask.getpixel((0, 0))[3] == 255
    assert oa_mask.getpixel((1, 0))[3] == 0


def test_openai_inpaint_defaults_model_and_fetches_url(client, recorder, fake_dns):
    blue = png_bytes(Image.new("RGB", (2, 1), "blue"))
    recorder.on("POST", f"{OA}/images/edits",
                httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/out.png"}]}))
    recorder.on("GET", "https://cdn.example.com/out.png", httpx.Response(200, content=blue))
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA))
    assert r.status_code == 200
    assert parse_multipart(recorder.requests[0])["model"] == b"gpt-image-1"
    assert "authorization" not in recorder.requests[1].headers  # key not sent to the CDN


def test_openai_inpaint_dalle3_refused(client, recorder):
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA, _model="dall-e-3"))
    assert r.status_code == 400
    assert "dall-e-3" in r.json()["detail"]
    assert recorder.requests == []


def test_openai_inpaint_without_key(client, recorder, monkeypatch):
    monkeypatch.delenv("TEST_OPENAI_KEY")
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA))
    assert r.status_code == 400
    assert "TEST_OPENAI_KEY" in r.json()["detail"]
    assert recorder.requests == []


def test_openai_inpaint_vendor_error_passthrough(client, recorder):
    recorder.on("POST", f"{OA}/images/edits",
                httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}}))
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA))
    assert r.status_code == 401
    assert r.json()["detail"] == "OpenAI edit failed: Incorrect API key provided"


def test_openai_inpaint_timeout(client, recorder):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    recorder.on("POST", f"{OA}/images/edits", slow)
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA))
    assert r.status_code == 504


def test_openrouter_inpaint_uses_reference_api(client, recorder):
    def respond(request):
        payload = request_json(request)
        assert payload["model"] == "openai/gpt-5-image"
        assert len(payload["input_references"]) == 2
        assert payload["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json=_b64_json("blue"))

    recorder.on("POST", f"{OR}/images", respond)
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OR, _model="openai/gpt-5-image"))
    assert r.status_code == 200, r.text
    out = decode_png(r.json()["image"]).convert("RGBA")
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((1, 0)) == (0, 0, 255, 255)
    assert recorder.requests[0].headers["authorization"] == f"Bearer {OPENROUTER_KEY}"


def test_bad_inpaint_input(client, recorder):
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=OA, mask=""))
    assert r.status_code == 400
    r = client.post("/api/image/inpaint", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert recorder.requests == []


# ------------------------------------------------------ diffusion inpaint ---

def test_diffusion_inpaint_edits_route(client, recorder):
    result = png_b64(Image.new("RGB", (2, 1), "green"))
    recorder.on("POST", f"{DIFF}/v1/images/edits", httpx.Response(200, json={"data": [{"b64_json": result}]}))
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=DIFF))
    assert r.status_code == 200
    assert r.json() == {"image": result}  # self-hosted output is returned as-is
    parts = parse_multipart(recorder.requests[0])
    assert parts["size"] == b"2x1"
    assert parts["prompt"] == b"make it blue"
    mask = Image.open(io.BytesIO(parts["mask"]))
    assert mask.mode == "L"
    assert mask.getpixel((0, 0)) == 0 and mask.getpixel((1, 0)) == 255  # white = edit
    assert "authorization" not in recorder.requests[0].headers


@pytest.mark.parametrize(
    "edits_response",
    [
        httpx.Response(404, json={"detail": "Not Found"}),
        httpx.Response(405),
        httpx.Response(400, json={"detail": "Model does not support image edits"}),
    ],
)
def test_diffusion_inpaint_falls_back_to_inpaint_route(client, recorder, edits_response):
    def inpaint(request):
        body = request_json(request)
        assert {"image", "mask", "prompt", "width", "height", "strength", "feather"} <= set(body)
        assert "_endpoint" not in body and "_model" not in body
        return httpx.Response(200, json={"image": "QUJD"})

    recorder.on("POST", f"{DIFF}/v1/images/edits", edits_response)
    recorder.on("POST", f"{DIFF}/v1/images/inpaint", inpaint)
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=DIFF))
    assert r.status_code == 200
    assert r.json() == {"image": "QUJD"}
    assert recorder.urls() == [f"{DIFF}/v1/images/edits", f"{DIFF}/v1/images/inpaint"]


def test_diffusion_inpaint_error_detail(client, recorder):
    recorder.on("POST", f"{DIFF}/v1/images/edits", httpx.Response(500, json={"detail": "CUDA out of memory"}))
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=DIFF))
    assert r.status_code == 500
    assert r.json()["detail"] == "CUDA out of memory"


def test_diffusion_unreachable(client, recorder):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    recorder.on("POST", f"{DIFF}/v1/images/edits", refuse)
    r = client.post("/api/image/inpaint", json=_inpaint_body(_endpoint=DIFF))
    assert r.status_code == 502
    assert r.json()["detail"] == "Can't reach diffusion server"


# -------------------------------------------------------------- harmonize ---

def _harmonize_body(**extra):
    body = {"image": png_b64(Image.new("RGB", (4, 4), "gray")), "prompt": "warm light",
            "color_match": 0.65, "seam_fix": 0.2, "body_mask": "Ym9keQ==", "seam_mask": "c2VhbQ=="}
    body.update(extra)
    return body


def test_harmonize_openai_refused_with_img2img_hint(client, recorder):
    r = client.post("/api/image/harmonize", json=_harmonize_body(_endpoint=OA))
    assert r.status_code == 400
    assert "img2img" in r.json()["detail"]
    assert recorder.requests == []


def test_harmonize_payload_and_route_fallback(client, recorder):
    seen = {}

    def harmonize(request):
        seen.update(request_json(request))
        return httpx.Response(404)

    recorder.on("POST", f"{DIFF}/v1/images/harmonize", harmonize)
    recorder.on("POST", f"{DIFF}/v1/images/img2img",
                httpx.Response(200, json={"images": ["data:image/png;base64,QUJD"]}))
    r = client.post("/api/image/harmonize", json=_harmonize_body(_endpoint=DIFF))
    assert r.status_code == 200
    assert r.json() == {"image": "QUJD"}
    assert seen["color_match"] == 0.65 and seen["seam_fix"] == 0.2
    assert seen["body_mask"] == seen["mask"] == "Ym9keQ=="
    assert seen["seam_mask"] == "c2VhbQ=="
    assert seen["strength"] == 0.65


def test_harmonize_default_prefers_diffusion(client, recorder):
    # "oa" is listed first but cannot img2img; the default skips to "diff".
    recorder.on("POST", f"{DIFF}/v1/images/harmonize", httpx.Response(200, json={"image": "QUJD"}))
    r = client.post("/api/image/harmonize", json=_harmonize_body())
    assert r.status_code == 200
    assert recorder.urls() == [f"{DIFF}/v1/images/harmonize"]


def test_harmonize_a1111_route_and_no_route(client, recorder):
    for path in ("harmonize", "img2img", "variations"):
        recorder.on("POST", f"{DIFF}/v1/images/{path}", httpx.Response(404))
    recorder.on("POST", f"{DIFF}/sdapi/v1/img2img", httpx.Response(404))
    r = client.post("/api/image/harmonize", json=_harmonize_body(_endpoint=DIFF))
    assert r.status_code == 502
    assert "No supported img2img route" in r.json()["detail"]
    assert recorder.urls()[-1] == f"{DIFF}/sdapi/v1/img2img"


def test_harmonize_server_error_surfaces(client, recorder):
    recorder.on("POST", f"{DIFF}/v1/images/harmonize", httpx.Response(200, json={"error": "boom"}))
    r = client.post("/api/image/harmonize", json=_harmonize_body(_endpoint=DIFF))
    assert r.status_code == 502
    assert "Diffusion server error at /images/harmonize" in r.json()["detail"]


def test_harmonize_needs_image(client):
    assert client.post("/api/image/harmonize", json={"prompt": "x"}).status_code == 400


# --------------------------------------------------------- style transfer ---

def _style_files():
    return {"image": ("style.png", png_bytes(Image.new("RGB", (3, 2), "red")), "image/png")}


def test_style_transfer_diffusion(client, recorder, settings):
    settings.defaults["image"] = "diff"

    def respond(request):
        body = request_json(request)
        assert body["prompt"] == "oil painting"
        assert body["strength"] == 0.4
        assert body["response_format"] == "b64_json"
        assert base64.b64decode(body["image"]).startswith(b"\x89PNG")
        return httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})

    recorder.on("POST", f"{DIFF}/v1/images/generations", respond)
    r = client.post("/api/gallery/style-transfer", files=_style_files(),
                    data={"prompt": "oil painting", "strength": "0.4"})
    assert r.status_code == 200
    assert r.json() == {"image": "QUJD"}


def test_style_transfer_error_shape(client, recorder, settings):
    settings.defaults["image"] = "diff"
    recorder.on("POST", f"{DIFF}/v1/images/generations", httpx.Response(500))
    r = client.post("/api/gallery/style-transfer", files=_style_files(), data={"prompt": "x"})
    assert r.status_code == 200  # the editor reads {"error"} from a 200
    assert r.json() == {"error": "Style transfer failed (500)"}


def test_style_transfer_openai_edit_without_mask(client, recorder):
    recorder.on("POST", f"{OA}/images/edits", httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]}))
    r = client.post("/api/gallery/style-transfer", files=_style_files(),
                    data={"prompt": "watercolor", "strength": "0.5", "_endpoint": OA, "_model": "gpt-image-1"})
    assert r.status_code == 200
    assert r.json() == {"image": "QUJD"}
    parts = parse_multipart(recorder.requests[0])
    assert "mask" not in parts
    assert b"watercolor" in parts["prompt"]
    assert parts["size"] == b"1536x1024"
    assert recorder.requests[0].headers["authorization"] == f"Bearer {OPENAI_KEY}"


def test_style_transfer_requires_image(client):
    assert client.post("/api/gallery/style-transfer", data={"prompt": "x"}).status_code == 400
    files = {"image": ("x.png", b"not an image", "image/png")}
    assert client.post("/api/gallery/style-transfer", files=files, data={"prompt": "x"}).status_code == 400


def test_no_image_provider_configured(web_dir):
    from fastapi.testclient import TestClient

    from app.config import parse_settings
    from app.main import create_app

    app = create_app(parse_settings({}), web_dir=web_dir)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.post("/api/gallery/style-transfer", files=_style_files(), data={"prompt": "x"})
        assert r.status_code == 400
        assert "No image generation endpoint configured" in r.json()["detail"]
        assert c.get("/api/model-endpoints").json() == []


# ------------------------------------------------------------- AI upscale ---

def test_ai_upscale_diffusion(client, recorder):
    def respond(request):
        assert request_json(request)["scale"] == 4
        return httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})

    recorder.on("POST", f"{DIFF}/v1/images/upscale", respond)
    r = client.post("/api/gallery/ai-upscale", files=_style_files(), data={"scale": "4"})
    assert r.status_code == 200
    assert r.json() == {"image": "QUJD"}


def test_ai_upscale_openai_not_available(client, recorder):
    r = client.post("/api/gallery/ai-upscale", files=_style_files(), data={"_endpoint": OA})
    assert r.status_code == 200
    assert r.json()["error"].startswith("Upscale endpoint not available")
    assert recorder.requests == []
