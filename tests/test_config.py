import os
import pytest
from pydantic import ValidationError, SecretStr
from config import Settings, _read_secret


def test_settings_validation_success():
    settings = Settings(
        TELEGRAM_BOT_TOKEN="test_bot_token",
        OVERSEERR_URL="http://localhost:5055",
        OVERSEERR_API_KEY="test_api_key",
        LITELLM_BASE_URL="http://localhost:4000/v1",
        LITELLM_API_KEY="test_litellm_key",
        DEFAULT_LLM_MODEL="gemma4-fit"
    )
    assert settings.TELEGRAM_BOT_TOKEN.get_secret_value() == "test_bot_token"
    assert str(settings.OVERSEERR_URL).rstrip("/") == "http://localhost:5055"
    assert settings.OVERSEERR_API_KEY.get_secret_value() == "test_api_key"
    assert str(settings.LITELLM_BASE_URL).rstrip("/") == "http://localhost:4000/v1"
    assert settings.DEFAULT_LLM_MODEL.get_secret_value() == "gemma4-fit"


def test_settings_validation_missing_required(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OVERSEERR_URL", raising=False)
    monkeypatch.delenv("OVERSEERR_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("DEFAULT_LLM_MODEL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_validator_empty_string(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OVERSEERR_URL", raising=False)
    monkeypatch.delenv("OVERSEERR_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("DEFAULT_LLM_MODEL", raising=False)

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN="",
            OVERSEERR_URL="http://localhost:5055",
            OVERSEERR_API_KEY="test_api_key",
            LITELLM_BASE_URL="http://localhost:4000/v1",
            LITELLM_API_KEY="test_litellm_key",
            DEFAULT_LLM_MODEL="gemma4-fit"
        )
