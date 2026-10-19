"""Quick Edit planner API.

GET  /api/ai/providers  language models the browser may pick, and the default
POST /api/ai/command    plain-language request -> validated editor actions

The model is reached through app/llm.py (Anthropic Messages or OpenAI Chat
Completions), so any configured vendor works: Z.ai GLM, Anthropic, OpenAI,
OpenRouter, Ollama, ...
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import llm, planner
from app.config import Provider, Settings
from app.deps import get_settings, get_transport
from app.services.image_vendors import make_client
from app.services.local_tools import availability

logger = logging.getLogger(__name__)
router = APIRouter()

LLM_TIMEOUT_SECONDS = 90.0
# A 768px JPEG preview is ~100-300 KB; leave room but reject absurd bodies.
MAX_IMAGE_CHARS = 8_000_000


class CommandRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=planner.MAX_PROMPT_CHARS)
    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)
    image: str | None = Field(default=None, max_length=MAX_IMAGE_CHARS)


def _is_ready(provider: Provider) -> bool:
    """Enabled, and has its key when it names one (local servers need none)."""
    return provider.enabled and (provider.has_key or not provider.api_key_env)


def _not_ready_reason(provider: Provider) -> str | None:
    if not provider.enabled:
        return "disabled in config"
    if provider.api_key_env and not provider.has_key:
        return f"{provider.api_key_env} is not set"
    if not provider.models:
        return "no models listed in config"
    return None


def _default_choice(settings: Settings) -> tuple[Provider, str] | None:
    """The configured default (defaults.llm / llm_model), else the first ready model."""
    chat = [p for p in settings.chat_providers() if _is_ready(p) and p.models]
    preferred = settings.get(str(settings.defaults.get("llm") or ""))
    if preferred in chat:
        model = str(settings.defaults.get("llm_model") or "")
        return preferred, model if model in preferred.models else preferred.models[0]
    if chat:
        return chat[0], chat[0].models[0]
    return None


def _resolve(settings: Settings, provider_id: str | None, model: str | None) -> tuple[Provider, str]:
    if not provider_id:
        choice = _default_choice(settings)
        if choice is None:
            raise HTTPException(400, "No language model is configured. Enable a chat provider in "
                                     "config/providers.toml and set its API key in .env.")
        provider_id = choice[0].id
    provider = settings.get(provider_id)
    if provider is None or not provider.is_chat or not provider.enabled:
        raise HTTPException(400, f"Unknown or disabled language model provider: {provider_id}")
    if provider.api_key_env and not provider.has_key:
        raise HTTPException(400, f"{provider.name} needs an API key: set {provider.api_key_env} in .env.")
    if model:
        if provider.models and model not in provider.models:
            raise HTTPException(400, f"Model {model!r} is not listed for {provider.name}.")
        return provider, model
    if not provider.models:
        raise HTTPException(400, f"{provider.name} has no models listed in config/providers.toml.")
    default_model = str(settings.defaults.get("llm_model") or "")
    return provider, default_model if default_model in provider.models else provider.models[0]


@router.get("/api/ai/providers")
async def ai_providers(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    choice = _default_choice(settings)
    return {
        "default": {"provider": choice[0].id, "model": choice[1]} if choice else None,
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "models": list(p.models),
                "vision": p.supports("vision"),
                "ready": _is_ready(p) and bool(p.models),
                "reason": _not_ready_reason(p),
            }
            for p in settings.chat_providers()
        ],
    }


@router.post("/api/ai/command")
async def ai_command(
    body: CommandRequest,
    settings: Settings = Depends(get_settings),
    transport: httpx.AsyncBaseTransport | None = Depends(get_transport),
) -> dict[str, Any]:
    provider, model = _resolve(settings, body.provider, body.model)
    image = body.image if provider.supports("vision") else None

    ai_actions = planner.available_ai_actions({
        "local_tools": availability(),
        "image_providers": [{"capabilities": sorted(p.capabilities)} for p in settings.image_providers()],
    })
    system = planner.build_system_prompt(ai_actions)
    user = planner.build_user_message(body.prompt, body.context, has_image=image is not None)

    try:
        async with make_client(LLM_TIMEOUT_SECONDS, transport) as client:
            result = await llm.chat(provider, model, system, user, image=image, client=client)
        raw = llm.extract_json_object(result.text)
    except llm.LLMError as exc:
        logger.warning("Quick Edit planner failed (%s/%s): %s", provider.id, model, exc)
        raise HTTPException(exc.status_code, str(exc)) from exc

    message, actions, dropped = planner.validate_plan(raw, ai_actions)
    if dropped:
        logger.info("Quick Edit planner dropped actions from %s/%s: %s", provider.id, model, dropped)
    return {
        "message": message,
        "actions": actions,
        "dropped": dropped,
        "provider": provider.id,
        "model": result.model or model,
    }
