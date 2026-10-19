"""Tests for the multi-vendor chat clients and the Quick Edit planner."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app import llm, planner

PNG_DATA_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBD"


def provider(**overrides):
    base = {
        "id": "zai", "name": "Z.ai GLM", "kind": "anthropic", "base_url": "https://api.z.ai/api/anthropic",
        "api_key": "secret-key", "auth": "bearer", "models": ["glm-5.3"], "capabilities": ["chat"],
        "enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── Anthropic Messages (Anthropic, Z.ai GLM) ────────────────────────────────

def test_anthropic_bearer_request_shape():
    async def scenario():
        seen = {}

        def handler(request: httpx.Request):
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "model": "glm-5.3",
                "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": '{"actions": []}'}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })

        async with client_for(handler) as client:
            result = await llm.chat(provider(), "glm-5.3", "SYS", "hello", client=client)

        assert seen["url"] == "https://api.z.ai/api/anthropic/v1/messages"
        assert seen["headers"]["authorization"] == "Bearer secret-key"
        assert "x-api-key" not in seen["headers"]
        assert seen["headers"]["anthropic-version"] == llm.ANTHROPIC_VERSION
        assert seen["body"]["system"] == "SYS"
        assert seen["body"]["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
        assert result.text == '{"actions": []}'  # thinking block dropped
        assert result.usage["output_tokens"] == 5

    asyncio.run(scenario())


def test_anthropic_x_api_key_and_image_block():
    async def scenario():
        seen = {}

        def handler(request):
            seen["headers"] = dict(request.headers)
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

        p = provider(id="anthropic", name="Anthropic", base_url="https://api.anthropic.com/v1", auth="x-api-key")
        async with client_for(handler) as client:
            await llm.chat(p, "claude-sonnet-5-5", "SYS", "look", image=PNG_DATA_URL, client=client)

        assert seen["url"] == "https://api.anthropic.com/v1/messages"
        assert seen["headers"]["x-api-key"] == "secret-key"
        assert "authorization" not in seen["headers"]
        image_block = seen["body"]["messages"][0]["content"][0]
        assert image_block["type"] == "image"
        assert image_block["source"]["media_type"] == "image/jpeg"

    asyncio.run(scenario())


def test_anthropic_error_message_is_surfaced_without_key():
    async def scenario():
        def handler(request):
            error = {"type": "authentication_error", "message": "invalid x-api-key"}
            return httpx.Response(401, json={"type": "error", "error": error})

        async with client_for(handler) as client:
            with pytest.raises(llm.LLMError) as info:
                await llm.chat(provider(), "glm-5.3", "S", "u", client=client)
        assert "invalid x-api-key" in str(info.value)
        assert "secret-key" not in str(info.value)
        assert info.value.status_code == 502

    asyncio.run(scenario())


def test_rate_limit_maps_to_429():
    async def scenario():
        async with client_for(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}})) as client:
            with pytest.raises(llm.LLMError) as info:
                await llm.chat(provider(), "glm-5.3", "S", "u", client=client)
        assert info.value.status_code == 429

    asyncio.run(scenario())


def test_timeout_maps_to_504():
    async def scenario():
        def handler(request):
            raise httpx.ReadTimeout("timed out", request=request)

        async with client_for(handler) as client:
            with pytest.raises(llm.LLMError) as info:
                await llm.chat(provider(), "glm-5.3", "S", "u", client=client)
        assert info.value.status_code == 504

    asyncio.run(scenario())


# ── OpenAI-compatible Chat Completions ────────────────────────────────────

def test_openai_request_shape_and_reply():
    async def scenario():
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"model": "gpt-x", "choices": [{"message": {"content": "hi"}}]})

        p = provider(id="openai", name="OpenAI", kind="openai", base_url="https://api.openai.com/v1", auth=None)
        async with client_for(handler) as client:
            result = await llm.chat(p, "gpt-x", "SYS", "look", image=PNG_DATA_URL, client=client)

        assert seen["url"] == "https://api.openai.com/v1/chat/completions"
        assert seen["headers"]["authorization"] == "Bearer secret-key"
        assert seen["body"]["messages"][0] == {"role": "system", "content": "SYS"}
        parts = seen["body"]["messages"][1]["content"]
        assert parts[1] == {"type": "image_url", "image_url": {"url": PNG_DATA_URL}}
        assert result.text == "hi"

    asyncio.run(scenario())


def test_openai_retries_without_max_tokens_and_temperature():
    async def scenario():
        bodies = []

        def handler(request):
            body = json.loads(request.content)
            bodies.append(body)
            if "max_tokens" in body:
                message = ("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'. "
                           "'temperature' is not supported.")
                return httpx.Response(400, json={"error": {"message": message}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        p = provider(kind="openai", base_url="https://api.openai.com/v1")
        async with client_for(handler) as client:
            result = await llm.chat(p, "gpt-x", "S", "u", client=client)
        assert result.text == "ok"
        assert len(bodies) == 2
        assert "max_completion_tokens" in bodies[1] and "temperature" not in bodies[1]

    asyncio.run(scenario())


def test_local_openai_provider_without_key_sends_no_auth():
    async def scenario():
        seen = {}

        def handler(request):
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        p = provider(kind="openai", base_url="http://127.0.0.1:11434/v1", api_key=None)
        async with client_for(handler) as client:
            await llm.chat(p, "qwen", "S", "u", client=client)
        assert "authorization" not in seen["headers"]

    asyncio.run(scenario())


def test_bad_image_data_url_is_rejected():
    async def scenario():
        async with client_for(lambda r: httpx.Response(200)) as client:
            with pytest.raises(llm.LLMError) as info:
                await llm.chat(provider(), "glm-5.3", "S", "u", image="https://example.com/x.png", client=client)
        assert info.value.status_code == 400

    asyncio.run(scenario())


# ── JSON extraction ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    '{"message": "ok", "actions": []}',
    'Sure!\n```json\n{"message": "ok", "actions": []}\n```',
    '<think>{"nope": 1}</think>{"message": "ok", "actions": []} trailing',
    'Here: {"message": "a } brace in text", "actions": []}',
])
def test_extract_json_object(text):
    value = llm.extract_json_object(text)
    assert value["actions"] == []


def test_extract_json_object_failure():
    with pytest.raises(llm.LLMError):
        llm.extract_json_object("I cannot help with that.")


# ── Planner ───────────────────────────────────────────────────────────────

def test_available_ai_actions_follow_capabilities():
    caps = {
        "local_tools": {"sharpen": True, "denoise": True, "upscale_local": False, "remove_bg": True,
                        "mask": False, "enhance_face": False},
        "image_providers": [{"id": "oi", "capabilities": ["image-gen", "image-edit"]}],
    }
    assert set(planner.available_ai_actions(caps)) == {"remove_background", "denoise", "sharpen", "style_edit"}
    assert planner.available_ai_actions({}) == []


def test_system_prompt_lists_only_available_ai_actions():
    prompt = planner.build_system_prompt(["remove_background"])
    assert '"remove_background"' in prompt
    assert '"style_edit"' not in prompt
    assert "{ai_actions}" not in prompt
    assert "No AI actions" in planner.build_system_prompt([])


def test_user_message_keeps_only_known_context():
    msg = planner.build_user_message("make it warmer", {
        "width": 800, "height": 600, "secret": "x",
        "layers": [{"name": "Photo", "kind": "raster", "visible": True, "extra": 1}],
        "has_selection": True,
    }, has_image=False)
    assert "make it warmer" in msg
    assert '"secret"' not in msg and '"extra"' not in msg
    assert '"layers_bottom_to_top"' in msg


def test_validate_plan_clamps_drops_and_limits():
    raw = {
        "message": "Warmer and brighter",
        "actions": [
            {"type": "white_balance", "temperature": 400, "label": "Warm " * 20},
            {"type": "brightness_contrast", "brightness": "15"},
            {"type": "rotate", "degrees": -90},
            {"type": "flip", "axis": "V"},
            {"type": "remove_background"},          # not allowed here
            {"type": "launch_rockets"},             # unknown
            {"type": "curves", "points": [[0, 0]]},  # too few points
            "not an object",
        ],
    }
    message, actions, dropped = planner.validate_plan(raw, allowed_ai=[])
    assert message == "Warmer and brighter"
    assert actions[0]["temperature"] == 100 and len(actions[0]["label"]) == 40
    assert actions[1] == {"type": "brightness_contrast", "brightness": 15.0, "contrast": 0}
    assert actions[2] == {"type": "rotate", "degrees": 270}
    assert actions[3] == {"type": "flip", "axis": "vertical"}
    assert dropped == ["remove_background", "launch_rockets", "curves", "str"]


def test_validate_plan_ai_actions_when_allowed():
    raw = {"actions": [
        {"type": "upscale", "scale": 3},
        {"type": "style_edit", "prompt": "  watercolor  ", "strength": 5},
        {"type": "style_edit", "prompt": ""},
    ]}
    _, actions, dropped = planner.validate_plan(raw, allowed_ai=["upscale", "style_edit"])
    assert actions == [
        {"type": "upscale", "scale": 2},
        {"type": "style_edit", "prompt": "watercolor", "strength": 0.9},
    ]
    assert dropped == ["style_edit"]


def test_validate_plan_caps_action_count():
    raw = {"actions": [{"type": "vibrance", "amount": i} for i in range(20)]}
    _, actions, _ = planner.validate_plan(raw, allowed_ai=[])
    assert len(actions) == planner.MAX_ACTIONS
