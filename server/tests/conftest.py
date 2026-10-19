from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable
from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import parse_settings
from app.main import create_app
from app.services import local_tools

OPENAI_KEY = "sk-test-openai-SECRET-1234"
OPENROUTER_KEY = "sk-or-test-SECRET-5678"
ZAI_KEY = "zai-test-SECRET-9999"

PROVIDERS = {
    "defaults": {"llm": "zai", "llm_model": "glm-5.3"},
    "providers": [
        {"id": "zai", "name": "Z.ai GLM", "kind": "anthropic", "base_url": "https://api.z.ai/api/anthropic",
         "api_key_env": "TEST_ZAI_KEY", "auth": "bearer", "models": ["glm-5.3"], "capabilities": ["chat"]},
        {"id": "claude", "name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com",
         "api_key_env": "TEST_MISSING_KEY", "models": ["claude-x"], "capabilities": ["chat", "vision"]},
        {"id": "oa", "name": "OpenAI Images", "kind": "openai-images", "base_url": "https://api.openai.com/v1",
         "api_key_env": "TEST_OPENAI_KEY", "models": ["gpt-image-1", "dall-e-2", "dall-e-3"],
         "capabilities": ["image-gen", "image-edit"]},
        {"id": "diff", "name": "Local diffusion", "kind": "diffusion", "base_url": "http://127.0.0.1:7860",
         "capabilities": ["image-gen", "image-edit"]},
        {"id": "or", "name": "OpenRouter Images", "kind": "openai-images", "base_url": "https://openrouter.ai/api/v1",
         "api_key_env": "TEST_OR_KEY", "models": ["openai/gpt-5-image"], "capabilities": ["image-edit"]},
        {"id": "genonly", "name": "Gen only", "kind": "diffusion", "base_url": "http://127.0.0.1:9999",
         "capabilities": ["image-gen"]},
        {"id": "off", "name": "Disabled diffusion", "kind": "diffusion", "base_url": "http://10.0.0.5:7860",
         "capabilities": ["image-gen", "image-edit"], "enabled": False},
    ],
}


def png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def decode_png(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def parse_multipart(request: httpx.Request) -> dict[str, bytes]:
    ctype = request.headers["content-type"].encode()
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype + b"\r\n\r\n" + request.content)
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in msg.iter_parts()
    }


def request_json(request: httpx.Request) -> dict:
    return json.loads(request.content)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("TEST_OPENAI_KEY", OPENAI_KEY)
    monkeypatch.setenv("TEST_OR_KEY", OPENROUTER_KEY)
    monkeypatch.setenv("TEST_ZAI_KEY", ZAI_KEY)
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    for name in ("ODYSSEUS_EDIT_HOST", "ODYSSEUS_EDIT_PORT", "ODYSSEUS_EDIT_PROVIDERS", "IMAGE_BLOCK_PRIVATE_IPS"):
        monkeypatch.delenv(name, raising=False)
    local_tools._cache.clear()
    yield
    local_tools._cache.clear()


@pytest.fixture
def settings():
    return parse_settings(json.loads(json.dumps(PROVIDERS)))


@pytest.fixture
def web_dir(tmp_path):
    root = tmp_path / "web"
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Odysseus-Edit test</title>")
    (root / "js" / "app.js").write_text("export const ok = true;\n")
    return root


class Recorder:
    """MockTransport handler: routes by (method, url) and records requests."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def on(self, method: str, url: str, responder):
        self.routes[(method.upper(), url)] = responder if callable(responder) else (lambda _r: responder)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url))
        if key not in self.routes:
            raise AssertionError(f"unexpected outbound request {key}")
        return self.routes[key](request)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def client(settings, web_dir, recorder):
    app = create_app(settings, web_dir=web_dir, http_transport=httpx.MockTransport(recorder))
    with TestClient(app, base_url="http://127.0.0.1:7020") as c:
        yield c
