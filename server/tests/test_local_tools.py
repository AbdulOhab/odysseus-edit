"""Local tools on small generated images.

Optional ML backends are replaced by in-process fakes (or forced missing via
`sys.modules[name] = None`), so these tests never download model weights.
Tests against the real backends run only with ODYSSEUS_EDIT_TEST_MODELS=1
and the package installed, because first use downloads weights.
"""

from __future__ import annotations

import importlib.machinery
import os
import sys
import types

import numpy as np
import pytest
from conftest import decode_png, png_b64
from PIL import Image

from app.services import local_tools

RUN_MODEL_TESTS = os.environ.get("ODYSSEUS_EDIT_TEST_MODELS") == "1"


def _gradient(w=8, h=6, transparent_corner=True) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), (x * 30 % 256, y * 40 % 256, 128, 255))
    if transparent_corner:
        img.putpixel((0, 0), (0, 0, 0, 0))
    return img


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _force_missing(monkeypatch, *names):
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)


# --------------------------------------------------------------- sharpen ---

def test_sharpen(client):
    src = _gradient()
    r = client.post("/api/image/sharpen", json={"image": png_b64(src), "amount": 80})
    assert r.status_code == 200
    out = decode_png(r.json()["image"])
    assert out.size == src.size
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0  # transparency survives


def test_sharpen_zero_is_identity_and_accepts_data_url(client):
    src = _gradient(transparent_corner=False)
    r = client.post("/api/image/sharpen", json={"image": "data:image/png;base64," + png_b64(src), "amount": 0})
    assert r.status_code == 200
    assert decode_png(r.json()["image"]).convert("RGB").tobytes() == src.convert("RGB").tobytes()


def test_sharpen_ignores_endpoint_selection(client, recorder):
    # applyImageTool attaches _endpoint/_model to every request.
    body = {"image": png_b64(_gradient()), "amount": 50, "_endpoint": "http://169.254.169.254", "_model": "x"}
    assert client.post("/api/image/sharpen", json=body).status_code == 200
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("body", "status"),
    [({}, 400), ({"image": "!!!notbase64"}, 400), ({"image": "aGVsbG8="}, 400),
     ({"image": png_b64(Image.new("RGB", (2, 2))), "amount": "lots"}, 400)],
)
def test_sharpen_bad_input(client, body, status):
    r = client.post("/api/image/sharpen", json=body)
    assert r.status_code == status
    assert "detail" in r.json()


def test_json_body_must_be_object(client):
    assert client.post("/api/image/sharpen", json=[1, 2]).status_code == 400


# ----------------------------------------------------- Real-ESRGAN (fake) ---

@pytest.fixture
def fake_realesrgan(monkeypatch):
    calls: dict[str, list] = {"init": [], "enhance": []}

    class FakeRealESRGANer:
        def __init__(self, scale, model_path, model, dni_weight=None, tile=0, tile_pad=10, pre_pad=0, half=False):
            calls["init"].append({"scale": scale, "model_path": model_path, "dni_weight": dni_weight,
                                  "model": model[0], "tile": tile, "half": half})

        def enhance(self, arr, outscale=4):
            calls["enhance"].append(outscale)
            assert arr.ndim == 3 and arr.shape[2] == 3
            k = int(outscale)
            return np.ascontiguousarray(arr.repeat(k, axis=0).repeat(k, axis=1)), "RGB"

    modules = {
        "realesrgan": _module("realesrgan", RealESRGANer=FakeRealESRGANer),
        "realesrgan.archs": _module("realesrgan.archs"),
        "realesrgan.archs.srvgg_arch": _module("realesrgan.archs.srvgg_arch",
                                               SRVGGNetCompact=lambda **kw: ("srvgg", kw)),
        "basicsr": _module("basicsr"),
        "basicsr.archs": _module("basicsr.archs"),
        "basicsr.archs.rrdbnet_arch": _module("basicsr.archs.rrdbnet_arch", RRDBNet=lambda **kw: ("rrdb", kw)),
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return calls


def test_denoise_with_backend(client, fake_realesrgan):
    src = _gradient()
    r = client.post("/api/image/denoise", json={"image": png_b64(src), "strength": 0.55})
    assert r.status_code == 200, r.text
    out = decode_png(r.json()["image"])
    assert out.size == src.size
    assert out.getpixel((0, 0))[3] == 0
    (init,) = fake_realesrgan["init"]
    assert init["model"] == "srvgg"
    assert init["model_path"].endswith("realesr-general-x4v3.pth")
    assert init["dni_weight"] == [0.55, pytest.approx(0.45)]
    assert fake_realesrgan["enhance"] == [1]


@pytest.mark.parametrize(("requested", "expected"), [(2, 2), (4, 4), (3, 2), ("x", 2)])
def test_upscale_local_with_backend(client, fake_realesrgan, requested, expected):
    src = _gradient(4, 3)
    r = client.post("/api/image/upscale-local", json={"image": png_b64(src), "scale": requested})
    assert r.status_code == 200, r.text
    out = decode_png(r.json()["image"])
    assert out.size == (4 * expected, 3 * expected)
    assert out.mode == "RGBA"
    assert fake_realesrgan["init"][0]["model"] == "rrdb"
    assert fake_realesrgan["enhance"] == [expected]


def test_realesrgan_upsampler_is_cached(client, fake_realesrgan):
    body = {"image": png_b64(_gradient()), "scale": 2}
    client.post("/api/image/upscale-local", json=body)
    client.post("/api/image/upscale-local", json=body)
    assert len(fake_realesrgan["init"]) == 1
    assert len(fake_realesrgan["enhance"]) == 2


@pytest.mark.parametrize("path", ["/api/image/denoise", "/api/image/upscale-local"])
def test_realesrgan_missing_returns_upstream_error(client, monkeypatch, path):
    _force_missing(monkeypatch, "realesrgan", "basicsr")
    r = client.post(path, json={"image": png_b64(_gradient())})
    assert r.status_code == 200
    error = r.json()["error"]
    # ai-tool-runner.js matches "<pkg> not installed" to offer an install hint.
    assert error.startswith("realesrgan not installed")


def test_realesrgan_runtime_failure(client, fake_realesrgan, monkeypatch):
    def broken(self, arr, outscale=4):
        raise RuntimeError("CUDA error")

    monkeypatch.setattr(sys.modules["realesrgan"].RealESRGANer, "enhance", broken)
    r = client.post("/api/image/upscale-local", json={"image": png_b64(_gradient())})
    assert r.json() == {"error": "AI upscale failed"}


def test_patch_realesrgan_torchvision_compat(monkeypatch):
    functional = _module("torchvision.transforms.functional", rgb_to_grayscale=lambda x: "gray", resize=lambda: 1)
    monkeypatch.setitem(sys.modules, "torchvision", _module("torchvision"))
    monkeypatch.setitem(sys.modules, "torchvision.transforms", _module("torchvision.transforms", functional=functional))
    monkeypatch.setitem(sys.modules, "torchvision.transforms.functional", functional)
    monkeypatch.delitem(sys.modules, "torchvision.transforms.functional_tensor", raising=False)
    local_tools.patch_realesrgan_torchvision_compat()
    shim = sys.modules["torchvision.transforms.functional_tensor"]
    assert shim.rgb_to_grayscale(None) == "gray"
    monkeypatch.delitem(sys.modules, "torchvision.transforms.functional_tensor")


# ---------------------------------------------------------- remove-bg ---

@pytest.fixture
def fake_rembg(monkeypatch):
    calls = {"sessions": [], "sizes": []}

    def new_session(name):
        calls["sessions"].append(name)
        return f"session:{name}"

    def remove(img, session=None):
        assert session == "session:u2net"
        calls["sizes"].append(img.size)
        out = img.convert("RGBA")
        w, h = out.size
        for y in range(h):  # pretend the left half is background
            for x in range(w // 2):
                r, g, b, _ = out.getpixel((x, y))
                out.putpixel((x, y), (r, g, b, 0))
        return out

    monkeypatch.setitem(sys.modules, "onnxruntime", _module("onnxruntime"))
    monkeypatch.setitem(sys.modules, "rembg", _module("rembg", new_session=new_session, remove=remove))
    return calls


def test_remove_bg_whole_image(client, fake_rembg):
    src = _gradient(8, 4, transparent_corner=False)
    r = client.post("/api/image/remove-bg", json={"image": png_b64(src)})
    assert r.status_code == 200, r.text
    out = decode_png(r.json()["image"])
    assert out.size == (8, 4)
    assert out.getpixel((1, 1))[3] == 0 and out.getpixel((6, 1))[3] == 255
    assert fake_rembg["sizes"] == [(8, 4)]


def test_remove_bg_with_hint_mask(client, fake_rembg):
    src = Image.new("RGBA", (40, 20), (200, 10, 10, 255))
    hint = Image.new("RGBA", (40, 20), (0, 0, 0, 0))  # white-on-transparent, like the editor
    for y in range(5, 15):
        for x in range(20, 30):
            hint.putpixel((x, y), (255, 255, 255, 255))
    r = client.post("/api/image/remove-bg", json={"image": png_b64(src), "hint_mask": png_b64(hint)})
    assert r.status_code == 200, r.text
    out = decode_png(r.json()["image"])
    assert out.size == (40, 20)
    # The model only saw the hint bbox plus 8 px padding.
    assert fake_rembg["sizes"] == [(26, 20)]
    assert out.getpixel((25, 10))[3] == 255  # inside hint, foreground
    assert out.getpixel((35, 10))[3] == 0    # model foreground but outside the hint
    assert out.getpixel((5, 10))[3] == 0     # outside the crop entirely


def test_remove_bg_session_cached(client, fake_rembg):
    body = {"image": png_b64(_gradient())}
    client.post("/api/image/remove-bg", json=body)
    client.post("/api/image/remove-bg", json=body)
    assert fake_rembg["sessions"] == ["u2net"]


def test_remove_bg_missing_returns_upstream_error(client, monkeypatch):
    _force_missing(monkeypatch, "rembg", "transformers")
    r = client.post("/api/image/remove-bg", json={"image": png_b64(_gradient())})
    assert r.status_code == 200
    assert r.json()["error"].startswith("No background removal model available")


def test_remove_bg_without_onnxruntime_never_imports_rembg(client, monkeypatch):
    # Real rembg calls sys.exit() when onnxruntime is missing.
    def exploding_import():
        raise AssertionError("rembg must not be imported without onnxruntime")

    _force_missing(monkeypatch, "onnxruntime", "transformers")
    monkeypatch.setitem(sys.modules, "rembg", _module("rembg", __getattr__=lambda name: exploding_import()))
    r = client.post("/api/image/remove-bg", json={"image": png_b64(_gradient())})
    assert r.json()["error"].startswith("No background removal model available")


# ------------------------------------------------------------------ mask ---

def test_mask_missing_sam_is_501(client, monkeypatch):
    _force_missing(monkeypatch, "torch", "transformers")
    r = client.post("/api/image/mask", json={"image": png_b64(_gradient()), "points": [{"x": 1, "y": 1, "label": 1}]})
    assert r.status_code == 501
    assert r.json()["detail"].startswith("SAM mask tools are not installed")


def test_mask_text_missing_grounding_is_501(client, monkeypatch):
    _force_missing(monkeypatch, "torch", "transformers")
    r = client.post("/api/image/mask", json={"image": png_b64(_gradient()), "text": "cat"})
    assert r.status_code == 501
    assert r.json()["detail"].startswith("Object mask tools are not installed")


def test_mask_requires_hint(client):
    r = client.post("/api/image/mask", json={"image": png_b64(_gradient())})
    assert r.status_code == 400
    assert r.json()["detail"] == "Provide at least one point, box, or object text"
    assert client.post("/api/image/mask", json={"points": [{"x": 1, "y": 1}]}).json()["detail"] == "Missing image"


# ---------------------------------------------------------- enhance-face ---

def test_enhance_face_pil_fallback(client, monkeypatch):
    _force_missing(monkeypatch, "gfpgan")
    src = _gradient()
    r = client.post("/api/image/enhance-face", json={"image": png_b64(src)})
    assert r.status_code == 200
    data = r.json()
    assert data["method"] == "pil"
    out = decode_png(data["image"])
    assert out.size == src.size
    assert out.getpixel((0, 0))[3] == 0


def test_enhance_face_needs_image(client):
    assert client.post("/api/image/enhance-face", json={}).status_code == 400


# ------------------------------------------------- real backends (opt-in) ---

@pytest.mark.skipif(
    not (RUN_MODEL_TESTS and local_tools.has_module("rembg") and local_tools.has_module("onnxruntime")),
    reason="needs rembg + onnxruntime and ODYSSEUS_EDIT_TEST_MODELS=1 (downloads weights)",
)
def test_remove_bg_real_rembg(client):
    src = Image.new("RGB", (64, 64), "white")
    for y in range(16, 48):
        for x in range(16, 48):
            src.putpixel((x, y), (200, 30, 30))
    r = client.post("/api/image/remove-bg", json={"image": png_b64(src)})
    assert r.status_code == 200 and "image" in r.json()


@pytest.mark.skipif(
    not (RUN_MODEL_TESTS and local_tools.has_module("realesrgan")),
    reason="needs realesrgan and ODYSSEUS_EDIT_TEST_MODELS=1 (downloads weights)",
)
def test_upscale_real_realesrgan(client):
    r = client.post("/api/image/upscale-local", json={"image": png_b64(_gradient(16, 16)), "scale": 2})
    assert decode_png(r.json()["image"]).size == (32, 32)


@pytest.mark.skipif(
    not (RUN_MODEL_TESTS and local_tools.has_module("torch") and local_tools.has_module("transformers")),
    reason="needs torch + transformers and ODYSSEUS_EDIT_TEST_MODELS=1 (downloads weights)",
)
def test_mask_real_sam(client):
    src = Image.new("RGB", (64, 64), "white")
    for y in range(16, 48):
        for x in range(16, 48):
            src.putpixel((x, y), (20, 20, 200))
    r = client.post("/api/image/mask", json={"image": png_b64(src), "points": [{"x": 32, "y": 32, "label": 1}]})
    assert r.status_code == 200 and r.json()["bbox"]
