from __future__ import annotations

import httpx
from conftest import OPENAI_KEY, OPENROUTER_KEY, ZAI_KEY, png_b64
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.services import local_tools

SECRETS = (OPENAI_KEY, OPENROUTER_KEY, ZAI_KEY)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": "0.1.0"}


def test_model_endpoints_shape(client):
    r = client.get("/api/model-endpoints")
    assert r.status_code == 200
    endpoints = r.json()
    assert isinstance(endpoints, list)
    by_id = {e["id"]: e for e in endpoints}
    # Only enabled image providers; chat-only and disabled ones are left out.
    assert list(by_id) == ["oa", "diff", "or", "genonly"]
    for e in endpoints:
        assert {"name", "base_url", "models", "model_type", "is_enabled", "online"} <= set(e)
        assert e["model_type"] == "image"
        assert e["is_enabled"] is True
        assert isinstance(e["models"], list)
        assert isinstance(e["online"], bool)
    assert by_id["oa"]["base_url"] == "https://api.openai.com/v1"
    assert by_id["oa"]["models"] == ["gpt-image-1", "dall-e-2", "dall-e-3"]
    assert by_id["diff"]["models"] == []
    assert not any(s in r.text for s in SECRETS)


def test_model_endpoints_online_reflects_missing_key(client, monkeypatch):
    monkeypatch.delenv("TEST_OPENAI_KEY")
    by_id = {e["id"]: e for e in client.get("/api/model-endpoints").json()}
    assert by_id["oa"]["online"] is False and by_id["oa"]["has_key"] is False
    assert by_id["diff"]["online"] is True  # no key needed


def test_capabilities_shape_and_no_key_leak(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == "0.1.0"
    assert set(data["local_tools"]) == {"sharpen", "denoise", "upscale_local", "remove_bg", "mask", "enhance_face"}
    assert all(isinstance(v, bool) for v in data["local_tools"].values())
    assert data["local_tools"]["sharpen"] is True
    assert [p["id"] for p in data["image_providers"]] == ["oa", "diff", "or", "genonly"]
    assert set(data["image_providers"][0]) == {"id", "name", "kind", "models", "capabilities"}
    llm = {p["id"]: p for p in data["llm_providers"]}
    assert set(llm) == {"zai", "claude"}
    assert llm["zai"] == {"id": "zai", "name": "Z.ai GLM", "kind": "anthropic", "models": ["glm-5.3"],
                          "vision": False, "has_key": True}
    assert llm["claude"]["vision"] is True and llm["claude"]["has_key"] is False
    assert data["defaults"] == {"llm": "zai", "llm_model": "glm-5.3"}
    assert not any(s in r.text for s in SECRETS)
    assert "api_key" not in r.text


def test_capabilities_follow_installed_modules(client, monkeypatch):
    installed = {"rembg", "onnxruntime"}
    monkeypatch.setattr(local_tools, "has_module", lambda name: name in installed)
    tools = client.get("/api/capabilities").json()["local_tools"]
    assert tools == {"sharpen": True, "denoise": False, "upscale_local": False, "remove_bg": True,
                     "mask": False, "enhance_face": True}
    installed.update({"torch", "transformers", "realesrgan", "basicsr"})
    tools = client.get("/api/capabilities").json()["local_tools"]
    assert all(tools.values())


def test_static_index_and_no_cache(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Odysseus-Edit test" in r.text
    assert r.headers["cache-control"] == "no-cache"
    js = client.get("/js/app.js")
    assert js.status_code == 200
    assert js.headers["cache-control"] == "no-cache"
    assert "javascript" in js.headers["content-type"]
    etag = js.headers.get("etag")
    assert etag
    assert client.get("/js/app.js", headers={"If-None-Match": etag}).status_code == 304


def test_api_routes_win_over_static(settings, web_dir):
    (web_dir / "api").mkdir()
    (web_dir / "api" / "health").write_text("static file, should never be served")
    with TestClient(create_app(settings, web_dir=web_dir), base_url="http://127.0.0.1") as c:
        assert c.get("/api/health").json()["ok"] is True
        r = c.get("/api/does-not-exist")
        assert r.status_code == 404
        assert r.json()["detail"].startswith("Unknown API route")


def test_missing_web_dir_still_serves_api(settings, tmp_path):
    with TestClient(create_app(settings, web_dir=tmp_path / "missing"), base_url="http://127.0.0.1") as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/").status_code == 404


def test_body_size_limit(settings, web_dir):
    app = create_app(settings, web_dir=web_dir, max_body_bytes=2048)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.post("/api/image/sharpen", json={"image": "A" * 4096})
        assert r.status_code == 413
        assert "too large" in r.json()["detail"]
        small = png_b64(Image.new("RGB", (2, 2), "white"))
        assert c.post("/api/image/sharpen", json={"image": small}).status_code == 200


def test_body_size_limit_without_content_length(settings, web_dir):
    app = create_app(settings, web_dir=web_dir, max_body_bytes=2048)

    def chunks():
        for _ in range(8):
            yield b'{"image": "' + b"A" * 512 + b'"}'

    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.post("/api/image/sharpen", content=chunks(), headers={"content-type": "application/json"})
        assert r.status_code == 413


def test_foreign_host_header_rejected(settings, web_dir):
    # DNS rebinding: a page on evil.example resolving to 127.0.0.1.
    with TestClient(create_app(settings, web_dir=web_dir), base_url="http://evil.example:7020") as c:
        assert c.get("/api/capabilities").status_code == 400


def test_cross_origin_post_rejected(client):
    body = {"image": png_b64(Image.new("RGB", (2, 2)))}
    r = client.post("/api/image/sharpen", json=body, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/image/sharpen", json=body, headers={"Origin": "http://127.0.0.1:9999"})
    assert r.status_code == 403
    r = client.post("/api/image/sharpen", json=body, headers={"Origin": "http://127.0.0.1:7020"})
    assert r.status_code == 200
    # Cross-origin GETs are harmless reads of public info and stay allowed.
    assert client.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 200


def test_no_outbound_calls_for_meta(settings, web_dir):
    def explode(request):
        raise AssertionError(f"unexpected network call {request.url}")

    app = create_app(settings, web_dir=web_dir, http_transport=httpx.MockTransport(explode))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        for path in ("/api/health", "/api/capabilities", "/api/model-endpoints"):
            assert c.get(path).status_code == 200
