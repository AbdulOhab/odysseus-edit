"""Tests for /api/ai/providers and /api/ai/command (no network: MockTransport)."""

import json

import httpx
import pytest

from tests.conftest import ZAI_KEY

ZAI_URL = "https://api.z.ai/api/anthropic/v1/messages"
PREVIEW = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD"


def anthropic_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"model": "glm-5.3", "content": [{"type": "text", "text": text}]})


def test_providers_lists_chat_models_and_default(client):
    data = client.get("/api/ai/providers").json()
    assert data["default"] == {"provider": "zai", "model": "glm-5.3"}
    by_id = {p["id"]: p for p in data["providers"]}
    assert by_id["zai"]["ready"] is True and by_id["zai"]["reason"] is None
    assert by_id["claude"]["ready"] is False
    assert "TEST_MISSING_KEY" in by_id["claude"]["reason"]
    assert by_id["claude"]["vision"] is True
    assert ZAI_KEY not in json.dumps(data)


def test_command_plans_with_default_provider(client, recorder):
    plan = {"message": "Warmer.", "actions": [{"type": "white_balance", "temperature": 30},
                                              {"type": "remove_background"}]}
    recorder.on("POST", ZAI_URL, anthropic_reply("```json\n" + json.dumps(plan) + "\n```"))

    res = client.post("/api/ai/command", json={
        "prompt": "make it warmer",
        "context": {"width": 640, "height": 480, "layers": [{"name": "Photo", "kind": "raster"}]},
        "image": PREVIEW,
    })

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["provider"] == "zai" and body["model"] == "glm-5.3"
    assert body["message"] == "Warmer."
    assert body["actions"] == [{"type": "white_balance", "temperature": 30.0, "tint": 0}]
    # remove_background is dropped unless rembg is installed on this machine
    request = recorder.requests[0]
    assert request.headers["authorization"] == f"Bearer {ZAI_KEY}"
    sent = json.loads(request.content)
    assert sent["model"] == "glm-5.3"
    assert "make it warmer" in sent["messages"][0]["content"][-1]["text"]
    # zai has no vision capability, so the preview must not be sent
    assert all(block["type"] == "text" for block in sent["messages"][0]["content"])


def test_command_explicit_model_must_be_listed(client):
    res = client.post("/api/ai/command", json={"prompt": "x", "provider": "zai", "model": "glm-unknown"})
    assert res.status_code == 400
    assert "not listed" in res.json()["detail"]


def test_command_provider_without_key(client):
    res = client.post("/api/ai/command", json={"prompt": "x", "provider": "claude"})
    assert res.status_code == 400
    assert "TEST_MISSING_KEY" in res.json()["detail"]


@pytest.mark.parametrize("provider_id", ["oa", "nope", "off"])
def test_command_rejects_non_chat_or_unknown_providers(client, provider_id):
    res = client.post("/api/ai/command", json={"prompt": "x", "provider": provider_id})
    assert res.status_code == 400


def test_command_bad_model_reply_is_502(client, recorder):
    recorder.on("POST", ZAI_URL, anthropic_reply("Sorry, I can only chat."))
    res = client.post("/api/ai/command", json={"prompt": "make it pop"})
    assert res.status_code == 502
    assert "expected JSON" in res.json()["detail"]


def test_command_vendor_error_is_passed_through_without_key(client, recorder):
    recorder.on("POST", ZAI_URL, httpx.Response(429, json={"error": {"message": "quota exhausted"}}))
    res = client.post("/api/ai/command", json={"prompt": "make it pop"})
    assert res.status_code == 429
    assert "quota exhausted" in res.json()["detail"]
    assert ZAI_KEY not in res.text


def test_command_validation(client):
    assert client.post("/api/ai/command", json={"prompt": ""}).status_code == 422
    assert client.post("/api/ai/command", json={"prompt": "x" * 5000}).status_code == 422


def test_command_without_any_chat_provider(settings, web_dir, recorder):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings.providers = [p for p in settings.providers if not p.is_chat]
    app = create_app(settings, web_dir=web_dir, http_transport=httpx.MockTransport(recorder))
    with TestClient(app, base_url="http://127.0.0.1:7020") as c:
        assert c.get("/api/ai/providers").json() == {"default": None, "providers": []}
        res = c.post("/api/ai/command", json={"prompt": "warmer"})
    assert res.status_code == 400
    assert "No language model is configured" in res.json()["detail"]
    assert recorder.requests == []
