"""The API-configuration view: masking, validation and the ``.env`` write."""

from __future__ import annotations

import os

import pytest

from gateway_helpers import settings_for
from myagent.config.settings import (
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_MAX_TOKENS,
    ENV_LLM_MODEL,
    ENV_LLM_TEMPERATURE,
    LLMSettings,
)
from myagent.gateway.config import (
    apply_config,
    capability_payload,
    config_payload,
    mask_api_key,
    merge_capabilities,
    merge_config,
    split_config,
)
from myagent.gateway.errors import GatewayError


def test_a_long_key_is_masked_to_its_ends():
    assert mask_api_key("sk-abcdefghijklmnop") == "sk-…mnop"


def test_a_short_key_is_masked_completely():
    assert mask_api_key("sk-123") == "…"


def test_no_key_masks_to_nothing():
    assert mask_api_key(None) == ""
    assert mask_api_key("") == ""


def test_the_payload_describes_the_configuration_without_leaking_the_key():
    payload = config_payload(LLMSettings(model="qwen3-max", api_key="sk-abcdefghijklmnop"))

    assert payload["api_key_set"] is True
    assert payload["api_key_hint"] == "sk-…mnop"
    assert payload["missing"] == []
    assert payload["resolved_base_url"] == "https://api.openai.com/v1"
    assert payload["providers"] == ["openai_compat"]
    assert payload["max_tokens"] == 4096
    assert payload["context_window"] == 128_000
    assert payload["temperature"] == 0.1
    assert "sk-abcdefghijklmnop" not in repr(payload)


def test_missing_values_are_named_for_the_page():
    payload = config_payload(LLMSettings())

    assert payload["missing"] == [ENV_LLM_MODEL, ENV_LLM_API_KEY]
    assert payload["model"] is None
    assert payload["api_key_set"] is False
    assert payload["api_key_hint"] == ""


def test_an_omitted_field_keeps_its_value():
    current = LLMSettings(
        model="keep", api_key="k", base_url="http://localhost:11434/v1", temperature=0.2
    )

    candidate, writes = merge_config(current, {"max_tokens": 512})

    assert candidate.model == "keep"
    assert candidate.api_key == "k"
    assert candidate.base_url == "http://localhost:11434/v1"
    assert candidate.temperature == 0.2
    assert candidate.max_tokens == 512
    assert writes[ENV_LLM_MODEL] == "keep"
    assert set(writes) == {
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MAX_TOKENS",
        "LLM_CONTEXT_WINDOW",
        "LLM_TEMPERATURE",
    }


def test_an_empty_string_clears_a_field():
    current = LLMSettings(model="m", api_key="k", base_url="http://localhost:11434/v1")

    candidate, writes = merge_config(current, {"model": "", "api_key": "   ", "base_url": None})

    assert candidate.model is None
    assert candidate.api_key is None
    assert candidate.base_url is None
    assert writes[ENV_LLM_MODEL] == ""
    assert writes[ENV_LLM_API_KEY] == ""
    assert writes[ENV_LLM_BASE_URL] == ""


def test_whitespace_around_a_value_is_trimmed():
    candidate, _ = merge_config(LLMSettings(), {"model": "  qwen3-max  "})

    assert candidate.model == "qwen3-max"


def test_a_whole_number_temperature_becomes_a_float():
    candidate, writes = merge_config(LLMSettings(), {"temperature": 1})

    assert candidate.temperature == 1.0
    assert writes[ENV_LLM_TEMPERATURE] == "1.0"


def test_every_field_can_be_replaced_at_once():
    candidate, writes = merge_config(
        LLMSettings(),
        {
            "provider": "openai_compat",
            "model": "qwen3-max",
            "api_key": "sk-abcdefghijklmnop",
            "base_url": "https://example.test/v1",
            "max_tokens": 1024,
            "context_window": 32_000,
            "temperature": 0.7,
        },
    )

    assert candidate == LLMSettings(
        provider="openai_compat",
        model="qwen3-max",
        api_key="sk-abcdefghijklmnop",
        base_url="https://example.test/v1",
        max_tokens=1024,
        context_window=32_000,
        temperature=0.7,
    )
    assert writes[ENV_LLM_MAX_TOKENS] == "1024"


def test_an_unknown_field_is_refused():
    with pytest.raises(GatewayError) as error:
        merge_config(LLMSettings(), {"modle": "typo"})

    assert error.value.status == 400
    assert "modle" in error.value.message


@pytest.mark.parametrize(
    "payload",
    [
        {"model": 3},
        {"base_url": []},
        {"api_key": {"nested": True}},
        {"provider": ""},
        {"provider": 7},
        {"max_tokens": "10"},
        {"max_tokens": True},
        {"max_tokens": 1.5},
        {"max_tokens": 0},
        {"context_window": -1},
        {"temperature": "hot"},
        {"temperature": False},
        {"temperature": -0.1},
        {"provider": "anthropic"},
        {"base_url": "ftp://example.test/v1"},
    ],
)
def test_a_bad_value_is_a_400(payload):
    with pytest.raises(GatewayError) as error:
        merge_config(LLMSettings(), payload)

    assert error.value.status == 400
    assert error.value.message


def test_applying_a_configuration_writes_the_env_file(isolated_env_file):
    result = apply_config(
        LLMSettings(model="before", api_key=""), {"model": "after", "api_key": "sk-new"}
    )

    assert result.model == "after"
    text = isolated_env_file.read_text(encoding="utf-8")
    assert "LLM_MODEL=after" in text
    assert "LLM_API_KEY=sk-new" in text
    assert os.environ[ENV_LLM_MODEL] == "after"
    assert os.environ[ENV_LLM_API_KEY] == "sk-new"


def test_applying_a_configuration_rewrites_the_existing_line(isolated_env_file):
    isolated_env_file.write_text("# keys\nLLM_MODEL=old\nOTHER=1\n", encoding="utf-8")

    apply_config(LLMSettings(model="old"), {"model": "new"})

    lines = isolated_env_file.read_text(encoding="utf-8").splitlines()
    assert "# keys" in lines
    assert "OTHER=1" in lines
    assert lines.count("LLM_MODEL=new") == 1
    assert "LLM_MODEL=old" not in lines


def test_a_rejected_configuration_is_not_written(isolated_env_file):
    with pytest.raises(GatewayError):
        apply_config(LLMSettings(model="keep"), {"model": "new", "max_tokens": 0})

    assert "LLM_MODEL" not in isolated_env_file.read_text(encoding="utf-8")
    assert os.environ.get(ENV_LLM_MODEL) is None


def test_optional_settings_are_masked_and_off_by_default(tmp_path):
    payload = capability_payload(settings_for(tmp_path))

    assert payload["memory_enabled"] is False
    assert payload["rag_enabled"] is False
    assert payload["qdrant_url"] == "http://localhost:6333"
    assert payload["embedding_model_types"] == ["dashscope", "openai"]
    assert payload["embedding_api_key_set"] is False
    assert payload["qdrant_api_key_set"] is False


def test_optional_settings_validate_types_and_urls_before_writing(tmp_path):
    current = settings_for(tmp_path)
    for payload in (
        {"memory_enabled": "true"},
        {"rag_enabled": 1},
        {"embedding_model_type": "unknown"},
        {"embedding_model_name": ""},
        {"embedding_base_url": "ftp://example.test"},
        {"qdrant_url": "localhost:6333"},
        {"qdrant_api_key": []},
    ):
        with pytest.raises(GatewayError) as error:
            merge_capabilities(current, payload)
        assert error.value.status == 400


def test_optional_settings_accept_local_qdrant_without_a_key(tmp_path):
    llm, optional = split_config({"model": "m", "memory_enabled": True, "rag_enabled": True})
    writes = merge_capabilities(settings_for(tmp_path), optional)

    assert llm == {"model": "m"}
    assert writes == {"MYAGENT_MEMORY_ENABLED": "true", "MYAGENT_RAG_ENABLED": "true"}


def test_optional_keys_can_be_cleared_for_local_use(tmp_path):
    writes = merge_capabilities(
        settings_for(tmp_path), {"embedding_api_key": "", "qdrant_api_key": ""}
    )

    assert writes == {"EMBED_API_KEY": "", "MYAGENT_QDRANT_API_KEY": ""}


def test_unknown_optional_field_is_rejected():
    with pytest.raises(GatewayError, match="unknown config field"):
        split_config({"qdrant_password": "secret"})
