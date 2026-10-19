"""Multi-vendor chat clients for the Quick Edit planner.

Two wire formats cover most vendors:

* ``anthropic``: Anthropic Messages API (``POST {base}/v1/messages``). Used by
  Anthropic itself and by Anthropic-compatible endpoints such as Z.ai GLM
  (``https://api.z.ai/api/anthropic``).
* ``openai``: OpenAI-compatible Chat Completions (``POST {base}/chat/completions``).
  Used by OpenAI, OpenRouter, Ollama, LM Studio, vLLM and Z.ai's PaaS API.

Only text in, text out, plus one optional image for vision models. API keys
come from the provider config and never from the request.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
MAX_ERROR_DETAIL = 300


class LLMError(Exception):
    """A provider call failed. ``status_code`` is what the API should return."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ChatResult:
    text: str
    model: str
    usage: dict[str, Any] | None = None


def _join(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _anthropic_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return _join(base, "messages") if base.endswith("/v1") else _join(base, "v1/messages")


def _split_data_url(data_url: str) -> tuple[str, str]:
    """Return (media_type, base64_data) for a ``data:image/...;base64,`` URL."""
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", data_url or "", re.S)
    if not match:
        raise LLMError("The canvas preview is not a base64 image data URL.", 400)
    media_type, data = match.group(1), match.group(2)
    try:
        base64.b64decode(data[:64] + "=" * (-len(data[:64]) % 4), validate=True)
    except ValueError as exc:
        raise LLMError("The canvas preview is not valid base64.", 400) from exc
    return media_type, data


def _error_detail(response: httpx.Response) -> str:
    """Best-effort human message from a provider error body, without secrets."""
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:MAX_ERROR_DETAIL] if text else response.reason_phrase
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:MAX_ERROR_DETAIL]
        if isinstance(err, str):
            return err[:MAX_ERROR_DETAIL]
        for key in ("message", "detail", "msg"):
            if body.get(key):
                return str(body[key])[:MAX_ERROR_DETAIL]
    return json.dumps(body)[:MAX_ERROR_DETAIL]


def _raise_for_status(provider_name: str, response: httpx.Response) -> None:
    if response.is_success:
        return
    detail = _error_detail(response)
    status = response.status_code
    if status in (401, 403):
        raise LLMError(f"{provider_name} rejected the API key ({status}): {detail}", 502)
    if status == 429:
        raise LLMError(f"{provider_name} is rate limiting requests (still busy after retrying). "
                       f"Wait a minute and try again. Vendor says: {detail}", 429)
    raise LLMError(f"{provider_name} returned {status}: {detail}", 502)


# Rate limits (429) and overload (503, 529) are often momentary: Z.ai answers
# a burst of requests with 429 "[1302] Rate limit reached". Wait and retry a
# couple of times before giving up. Retry-After wins when the vendor sends it.
RETRY_STATUSES = frozenset({429, 503, 529})
RETRY_DELAYS = (2.0, 5.0)
MAX_RETRY_AFTER = 10.0
_sleep = asyncio.sleep


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after", "")
    try:
        return min(MAX_RETRY_AFTER, max(0.0, float(header)))
    except ValueError:
        return RETRY_DELAYS[attempt]


async def _post(client: httpx.AsyncClient, provider_name: str, url: str, **kwargs) -> httpx.Response:
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = await client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise LLMError(f"{provider_name} did not answer in time.", 504) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach {provider_name}: {exc.__class__.__name__}", 502) from exc
        if response.status_code not in RETRY_STATUSES or attempt == len(RETRY_DELAYS):
            return response
        delay = _retry_delay(response, attempt)
        logger.info("%s returned %s; retrying in %.1fs", provider_name, response.status_code, delay)
        await _sleep(delay)
    return response


async def _chat_anthropic(client, provider, model, system, user_text, image, max_tokens, temperature) -> ChatResult:
    headers = {"anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
    if provider.api_key:
        if (provider.auth or "x-api-key") == "bearer":
            headers["authorization"] = f"Bearer {provider.api_key}"
        else:
            headers["x-api-key"] = provider.api_key
    content: list[dict[str, Any]] = []
    if image:
        media_type, data = _split_data_url(image)
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    content.append({"type": "text", "text": user_text})
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
    }
    response = await _post(client, provider.name, _anthropic_url(provider.base_url), headers=headers, json=body)
    _raise_for_status(provider.name, response)
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"{provider.name} returned a non-JSON response.") from exc
    # Thinking models (GLM included) may add non-text blocks; keep the text.
    text = "".join(
        block.get("text", "")
        for block in data.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
    return ChatResult(text=text, model=data.get("model") or model, usage=data.get("usage"))


async def _chat_openai(client, provider, model, system, user_text, image, max_tokens, temperature) -> ChatResult:
    headers = {"content-type": "application/json"}
    if provider.api_key:
        headers["authorization"] = f"Bearer {provider.api_key}"
    user_content: Any = user_text
    if image:
        _split_data_url(image)  # validate before sending
        user_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image}},
        ]
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    url = _join(provider.base_url, "chat/completions")
    response = await _post(client, provider.name, url, headers=headers, json=body)
    if response.status_code == 400:
        # Newer OpenAI reasoning models reject max_tokens/temperature.
        detail = _error_detail(response).lower()
        retry = dict(body)
        if "max_tokens" in detail:
            retry.pop("max_tokens", None)
            retry["max_completion_tokens"] = max_tokens
        if "temperature" in detail:
            retry.pop("temperature", None)
        if retry != body:
            response = await _post(client, provider.name, url, headers=headers, json=retry)
    _raise_for_status(provider.name, response)
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"{provider.name} returned a non-JSON response.") from exc
    choices = data.get("choices") or []
    message = (choices[0] or {}).get("message", {}) if choices else {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ChatResult(text=content or "", model=data.get("model") or model, usage=data.get("usage"))


async def chat(
    provider,
    model: str,
    system: str,
    user_text: str,
    *,
    image: str | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.2,
    client: httpx.AsyncClient | None = None,
) -> ChatResult:
    """Send one system + user turn to ``provider`` and return the reply text."""
    if provider.kind not in ("anthropic", "openai"):
        raise LLMError(f"Provider {provider.id} ({provider.kind}) is not a chat provider.", 400)
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        if provider.kind == "anthropic":
            return await _chat_anthropic(client, provider, model, system, user_text, image, max_tokens, temperature)
        return await _chat_openai(client, provider, model, system, user_text, image, max_tokens, temperature)
    finally:
        if own_client:
            await client.aclose()


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object in a model reply (tolerates code fences and prose)."""
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S)
    candidates = [fence.group(1)] if fence else []
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(cleaned)):
            ch = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(cleaned[start:index + 1])
                    break
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    snippet = cleaned[:160].replace("\n", " ")
    raise LLMError(f"The model did not return the expected JSON. It said: {snippet!r}")
