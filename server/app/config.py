"""Provider registry and server settings.

Loads `.env` from the repository root, then the provider TOML from
`$ODYSSEUS_EDIT_PROVIDERS`, `config/providers.toml`, or
`config/providers.example.toml` (first that exists; relative paths resolve
against the repository root). API keys are never stored in the TOML: each
provider names an environment variable and `Provider.api_key` reads it.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]

KINDS = frozenset({"anthropic", "openai", "openai-images", "diffusion"})
CAPABILITIES = frozenset({"chat", "vision", "image-gen", "image-edit"})
AUTH_MODES = frozenset({"x-api-key", "bearer"})
IMAGE_CAPABILITIES = frozenset({"image-gen", "image-edit"})
IMAGE_KINDS = frozenset({"openai-images", "diffusion"})
LLM_KINDS = frozenset({"anthropic", "openai"})

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7020

_PROVIDER_KEYS = frozenset({
    "id", "name", "kind", "base_url", "api_key_env", "auth",
    "models", "capabilities", "enabled",
})
# defaults.<key> values that must name a configured provider id.
_DEFAULT_PROVIDER_REFS = ("llm", "image")


class ConfigError(ValueError):
    """The provider configuration is missing or invalid."""


def normalize_base_url(url: str) -> str:
    """Canonical form used to compare base URLs.

    Lowercases scheme and host, drops trailing slashes and a trailing `/v1`
    (upstream callers append `/v1` themselves), so `http://Host:7860/v1/`
    and `http://host:7860` compare equal.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3].rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str
    base_url: str
    api_key_env: str | None = None
    auth: str = "bearer"
    models: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    enabled: bool = True

    @property
    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env, "").strip()
        return value or None

    @property
    def has_key(self) -> bool:
        return self.api_key is not None

    @property
    def is_image(self) -> bool:
        return self.kind in IMAGE_KINDS and bool(self.capabilities & IMAGE_CAPABILITIES)

    @property
    def is_chat(self) -> bool:
        return self.kind in LLM_KINDS and "chat" in self.capabilities

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass
class Settings:
    defaults: dict[str, Any] = field(default_factory=dict)
    providers: list[Provider] = field(default_factory=list)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    source: Path | None = None

    def get(self, provider_id: str) -> Provider | None:
        for p in self.providers:
            if p.id == provider_id:
                return p
        return None

    def enabled(self) -> list[Provider]:
        return [p for p in self.providers if p.enabled]

    def image_providers(self, *, enabled_only: bool = True) -> list[Provider]:
        return [p for p in self.providers if p.is_image and (p.enabled or not enabled_only)]

    def chat_providers(self, *, enabled_only: bool = True) -> list[Provider]:
        return [p for p in self.providers if p.is_chat and (p.enabled or not enabled_only)]

    def find_by_base_url(
        self,
        url: str,
        *,
        kinds: Iterable[str] | None = None,
        capability: str | None = None,
    ) -> Provider | None:
        """Return the enabled provider whose base URL matches `url`.

        Only configured providers can ever be returned, so the caller's
        outbound URL always comes from config, never from the request.
        Several providers may share a base URL (e.g. OpenAI chat and OpenAI
        images); `kinds` / `capability` narrow the match.
        """
        target = normalize_base_url(url)
        if not target:
            return None
        allowed = frozenset(kinds) if kinds is not None else None
        for p in self.providers:
            if not p.enabled:
                continue
            if allowed is not None and p.kind not in allowed:
                continue
            if capability and not p.supports(capability):
                continue
            if normalize_base_url(p.base_url) == target:
                return p
        return None

    def find_image_provider(self, url: str) -> Provider | None:
        return self.find_by_base_url(url, kinds=IMAGE_KINDS) if url else None

    def default_image_provider(
        self,
        capability: str = "image-edit",
        *,
        prefer_kinds: Iterable[str] = (),
    ) -> Provider | None:
        """Provider used when the browser did not pick one.

        `defaults.image` wins when it names an enabled provider with the
        capability; otherwise the first match, preferring `prefer_kinds`.
        """
        candidates = [p for p in self.image_providers() if p.supports(capability)]
        if not candidates:
            return None
        preferred_id = self.defaults.get("image")
        if preferred_id:
            for p in candidates:
                if p.id == preferred_id:
                    return p
        for kind in prefer_kinds:
            for p in candidates:
                if p.kind == kind:
                    return p
        return candidates[0]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if not 0 < value < 65536:
        raise ConfigError(f"{name} must be a TCP port (1-65535), got {value}")
    return value


def resolve_config_path(explicit: str | os.PathLike | None = None) -> Path:
    """Pick the provider TOML: explicit > $ODYSSEUS_EDIT_PROVIDERS > providers.toml > example."""
    if explicit is None:
        explicit = os.environ.get("ODYSSEUS_EDIT_PROVIDERS") or None
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.is_file():
            raise ConfigError(f"Provider config not found: {path}")
        return path
    for candidate in (REPO_ROOT / "config" / "providers.toml", REPO_ROOT / "config" / "providers.example.toml"):
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"No provider config found. Copy {REPO_ROOT / 'config' / 'providers.example.toml'} "
        f"to config/providers.toml."
    )


def _str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return [v.strip() for v in value if v.strip()]


def _parse_provider(raw: Any, index: int) -> Provider:
    where = f"providers[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")
    pid = raw.get("id")
    if not isinstance(pid, str) or not pid.strip():
        raise ConfigError(f"{where}.id is required")
    pid = pid.strip()
    where = f"provider '{pid}'"

    unknown = sorted(set(raw) - _PROVIDER_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")

    kind = raw.get("kind")
    if kind not in KINDS:
        raise ConfigError(f"{where}: kind must be one of {', '.join(sorted(KINDS))}, got {kind!r}")

    base_url = raw.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigError(f"{where}: base_url is required")
    base_url = base_url.strip()
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{where}: base_url must be an http(s) URL, got {base_url!r}")

    name = raw.get("name", pid)
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where}: name must be a non-empty string")

    api_key_env = raw.get("api_key_env")
    if api_key_env is not None and (not isinstance(api_key_env, str) or not api_key_env.strip()):
        raise ConfigError(f"{where}: api_key_env must be an environment variable name")

    auth = raw.get("auth", "x-api-key" if kind == "anthropic" else "bearer")
    if auth not in AUTH_MODES:
        raise ConfigError(f"{where}: auth must be one of {', '.join(sorted(AUTH_MODES))}, got {auth!r}")

    capabilities = _str_list(raw.get("capabilities"), f"{where}: capabilities")
    bad_caps = sorted(set(capabilities) - CAPABILITIES)
    if bad_caps:
        raise ConfigError(
            f"{where}: unknown capabilit{'y' if len(bad_caps) == 1 else 'ies'} {', '.join(bad_caps)} "
            f"(allowed: {', '.join(sorted(CAPABILITIES))})"
        )
    if not capabilities:
        raise ConfigError(f"{where}: capabilities must list at least one of {', '.join(sorted(CAPABILITIES))}")
    caps = frozenset(capabilities)
    if kind in IMAGE_KINDS and not caps & IMAGE_CAPABILITIES:
        raise ConfigError(f"{where}: kind {kind!r} needs image-gen and/or image-edit capability")
    if kind in IMAGE_KINDS and caps - IMAGE_CAPABILITIES:
        raise ConfigError(
            f"{where}: kind {kind!r} is an image API; chat/vision need kind 'anthropic' or 'openai'"
        )
    if kind in LLM_KINDS and caps & IMAGE_CAPABILITIES:
        raise ConfigError(
            f"{where}: kind {kind!r} is a chat API; use kind 'openai-images' or 'diffusion' for image capabilities"
        )

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}: enabled must be true or false")

    return Provider(
        id=pid,
        name=name.strip(),
        kind=kind,
        base_url=base_url.rstrip("/"),
        api_key_env=api_key_env.strip() if api_key_env else None,
        auth=auth,
        models=tuple(_str_list(raw.get("models"), f"{where}: models")),
        capabilities=caps,
        enabled=enabled,
    )


def parse_settings(data: dict[str, Any], *, source: Path | None = None) -> Settings:
    """Validate parsed TOML data and build Settings (host/port from env)."""
    label = str(source) if source else "provider config"
    try:
        defaults = data.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ConfigError("[defaults] must be a table")
        raw_providers = data.get("providers", [])
        if not isinstance(raw_providers, list):
            raise ConfigError("providers must be an array of tables ([[providers]])")
        providers = [_parse_provider(raw, i) for i, raw in enumerate(raw_providers)]
        seen: set[str] = set()
        for p in providers:
            if p.id in seen:
                raise ConfigError(f"duplicate provider id {p.id!r}")
            seen.add(p.id)
        for key in _DEFAULT_PROVIDER_REFS:
            ref = defaults.get(key)
            if ref is not None and ref not in seen:
                raise ConfigError(f"defaults.{key} = {ref!r} does not match any provider id")
    except ConfigError as exc:
        raise ConfigError(f"{label}: {exc}") from None

    host = os.environ.get("ODYSSEUS_EDIT_HOST", "").strip() or DEFAULT_HOST
    port = _env_int("ODYSSEUS_EDIT_PORT", DEFAULT_PORT)
    return Settings(defaults=dict(defaults), providers=providers, host=host, port=port, source=source)


def load_env_file(path: Path | None = None) -> None:
    """Load `.env` from the repo root without overriding the real environment."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is a base dependency; stay usable without it
        return
    load_dotenv(path or REPO_ROOT / ".env", override=False)


def load_settings(path: str | os.PathLike | None = None, *, load_env: bool = True) -> Settings:
    if load_env:
        load_env_file()
    config_path = resolve_config_path(path)
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{config_path}: invalid TOML: {exc}") from None
    return parse_settings(data, source=config_path)
