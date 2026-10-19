from __future__ import annotations

import json

import pytest
from conftest import PROVIDERS

from app import config
from app.config import (
    ConfigError,
    load_settings,
    normalize_base_url,
    parse_settings,
    resolve_config_path,
)

EXAMPLE = config.REPO_ROOT / "config" / "providers.example.toml"


def _data(**provider_overrides):
    data = json.loads(json.dumps(PROVIDERS))
    data["providers"] = [dict(data["providers"][2], **provider_overrides)]
    data["defaults"] = {}
    return data


def test_example_config_loads():
    settings = load_settings(EXAMPLE, load_env=False)
    assert settings.source == EXAMPLE
    ids = [p.id for p in settings.providers]
    assert {"zai", "openai-images", "local-diffusion"} <= set(ids)
    assert all(p.kind in config.KINDS for p in settings.providers)
    assert settings.defaults["llm"] == "zai"
    assert settings.host == "127.0.0.1" and settings.port == 7020


def test_env_var_selects_config(tmp_path, monkeypatch):
    cfg = tmp_path / "custom.toml"
    cfg.write_text(
        '[[providers]]\nid = "d"\nkind = "diffusion"\nbase_url = "http://127.0.0.1:1"\n'
        'capabilities = ["image-edit"]\n'
    )
    monkeypatch.setenv("ODYSSEUS_EDIT_PROVIDERS", str(cfg))
    settings = load_settings(load_env=False)
    assert [p.id for p in settings.providers] == ["d"]
    assert settings.providers[0].name == "d"  # name defaults to id


def test_relative_env_path_resolves_against_repo_root(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "mine.toml").write_text("")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("ODYSSEUS_EDIT_PROVIDERS", "config/mine.toml")
    assert resolve_config_path() == tmp_path / "config" / "mine.toml"


def test_falls_back_to_example_then_prefers_local(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    example = tmp_path / "config" / "providers.example.toml"
    example.write_text("")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    assert resolve_config_path() == example
    local = tmp_path / "config" / "providers.toml"
    local.write_text("")
    assert resolve_config_path() == local


def test_missing_config_is_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    with pytest.raises(ConfigError, match="No provider config found"):
        resolve_config_path()
    with pytest.raises(ConfigError, match="not found"):
        resolve_config_path(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[[providers]\nid = ")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings(bad, load_env=False)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "gemini"}, "kind must be one of"),
        ({"capabilities": ["image-edit", "video"]}, "unknown capability video"),
        ({"capabilities": []}, "capabilities must list"),
        ({"capabilities": "image-edit"}, "must be a list of strings"),
        ({"base_url": "ftp://example.com"}, "base_url must be an http"),
        ({"base_url": ""}, "base_url is required"),
        ({"auth": "basic"}, "auth must be one of"),
        ({"enabled": "yes"}, "enabled must be true or false"),
        ({"models": [1, 2]}, "must be a list of strings"),
        ({"capabilites": ["chat"]}, "unknown key"),
        ({"capabilities": ["image-edit", "chat"]}, "is an image API"),
        ({"capabilities": ["chat"]}, "needs image-gen and/or image-edit"),
        ({"kind": "openai", "capabilities": ["chat", "image-gen"]}, "is a chat API"),
        ({"id": ""}, "id is required"),
    ],
)
def test_bad_provider_rejected(overrides, message):
    with pytest.raises(ConfigError, match=message):
        parse_settings(_data(**overrides))


def test_duplicate_ids_and_bad_default_ref():
    data = _data()
    data["providers"].append(dict(data["providers"][0]))
    with pytest.raises(ConfigError, match="duplicate provider id"):
        parse_settings(data)
    data = _data()
    data["defaults"] = {"llm": "ghost"}
    with pytest.raises(ConfigError, match="defaults.llm"):
        parse_settings(data)


def test_api_key_resolved_from_env(settings, monkeypatch):
    oa = settings.get("oa")
    assert oa.api_key == "sk-test-openai-SECRET-1234"
    monkeypatch.setenv("TEST_OPENAI_KEY", "   ")
    assert oa.api_key is None
    monkeypatch.delenv("TEST_OPENAI_KEY")
    assert oa.api_key is None and not oa.has_key
    assert settings.get("diff").api_key is None  # no api_key_env


def test_auth_default_by_kind(settings):
    assert settings.get("claude").auth == "x-api-key"
    assert settings.get("zai").auth == "bearer"
    assert settings.get("oa").auth == "bearer"


def test_provider_filters(settings):
    assert [p.id for p in settings.image_providers()] == ["oa", "diff", "or", "genonly"]
    assert "off" in [p.id for p in settings.image_providers(enabled_only=False)]
    assert [p.id for p in settings.chat_providers()] == ["zai", "claude"]
    assert settings.get("nope") is None


def test_default_image_provider(settings):
    assert settings.default_image_provider("image-edit").id == "oa"
    assert settings.default_image_provider("image-edit", prefer_kinds=("diffusion",)).id == "diff"
    settings.defaults["image"] = "or"
    assert settings.default_image_provider("image-edit", prefer_kinds=("diffusion",)).id == "or"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.openai.com/v1", "oa"),
        ("https://API.openai.com/v1/", "oa"),
        ("https://api.openai.com", "oa"),
        ("http://127.0.0.1:7860", "diff"),
        ("http://127.0.0.1:7860/v1", "diff"),
        ("http://127.0.0.1:7861", None),
        ("http://10.0.0.5:7860", None),  # disabled
        ("https://api.z.ai/api/anthropic", None),  # chat provider, not an image provider
        ("http://169.254.169.254/latest", None),
        ("not a url", None),
        ("", None),
    ],
)
def test_find_image_provider(settings, url, expected):
    found = settings.find_image_provider(url)
    assert (found.id if found else None) == expected


def test_find_by_base_url_any_kind(settings):
    assert settings.find_by_base_url("https://api.z.ai/api/anthropic").id == "zai"
    assert settings.find_by_base_url("https://api.z.ai/api/anthropic", capability="vision") is None


def test_normalize_base_url():
    assert normalize_base_url(" HTTP://Host:1/v1/ ") == "http://host:1"
    assert normalize_base_url("https://x/api/v1") == "https://x/api"
    assert normalize_base_url("nope") == ""


def test_host_port_env(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_EDIT_HOST", "0.0.0.0")
    monkeypatch.setenv("ODYSSEUS_EDIT_PORT", "8123")
    s = parse_settings({})
    assert (s.host, s.port) == ("0.0.0.0", 8123)
    monkeypatch.setenv("ODYSSEUS_EDIT_PORT", "http")
    with pytest.raises(ConfigError, match="ODYSSEUS_EDIT_PORT"):
        parse_settings({})


def test_dotenv_does_not_override_real_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ODY_TEST_FROM_DOTENV=from-file\nODY_TEST_PRESET=from-file\n")
    monkeypatch.delenv("ODY_TEST_FROM_DOTENV", raising=False)
    monkeypatch.setenv("ODY_TEST_PRESET", "from-shell")
    config.load_env_file(env)
    import os

    assert os.environ["ODY_TEST_FROM_DOTENV"] == "from-file"
    assert os.environ["ODY_TEST_PRESET"] == "from-shell"
    monkeypatch.delenv("ODY_TEST_FROM_DOTENV")
