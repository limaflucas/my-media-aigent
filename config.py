"""Centralized configuration engine using Pydantic Settings.

Validates application environment variables, loads local .env files,
resolves Docker secrets, and formats human-readable error messages on failure.
"""

import os
import sys
import logging
from typing import List, Optional, Any
from pydantic import HttpUrl, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

VAR_EXAMPLES = {
    "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ (or TELEGRAM_BOT_TOKEN_FILE / Docker secret)",
    "OVERSEERR_API_KEY": "your_overseerr_api_key (or OVERSEERR_API_KEY_FILE / Docker secret)",
    "OVERSEERR_URL": "http://seerr:5055",
    "LITELLM_BASE_URL": "http://litellm:4000/v1",
    "LITELLM_API_KEY": "sk-litellm-dummy (or LITELLM_API_KEY_FILE / Docker secret)",
    "DEFAULT_LLM_MODEL": "gemma4-fit",
}


def _read_secret(key: str) -> Optional[str]:
    """Reads a secret from FILE env var, docker secret path, or env var."""
    file_path = os.getenv(f"{key}_FILE")
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            logger.error(f"Failed to read secret from file path {file_path}: {e}")

    secret_name = key.lower()
    docker_secret_path = f"/run/secrets/{secret_name}"
    if os.path.exists(docker_secret_path):
        try:
            with open(docker_secret_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            logger.error(f"Failed to read Docker secret from {docker_secret_path}: {e}")

    return os.getenv(key)


class Settings(BaseSettings):
    """Pydantic Settings definition for application configuration parameters."""

    # Telegram Configuration
    TELEGRAM_BOT_TOKEN: SecretStr
    TELEGRAM_ALLOWED_USERS: List[int] = []

    # Overseerr Service Configuration
    OVERSEERR_URL: HttpUrl = HttpUrl("http://seerr:5055")
    OVERSEERR_API_KEY: SecretStr
    OVERSEERR_TIMEOUT: float = 10.0
    OVERSEERR_SSL_VERIFY: bool = True

    # LiteLLM / Local AI Lab Configuration
    LITELLM_BASE_URL: HttpUrl = HttpUrl("http://litellm:4000/v1")
    LITELLM_API_KEY: SecretStr
    DEFAULT_LLM_MODEL: SecretStr
    MAX_TRANSCRIPT_TOKENS: int = 3000

    # Application Behavior
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("TELEGRAM_BOT_TOKEN", "OVERSEERR_API_KEY", "LITELLM_API_KEY", "DEFAULT_LLM_MODEL", mode="after")
    @classmethod
    def validate_non_empty_secret(cls, v: SecretStr) -> SecretStr:
        """Ensures secret values are not empty strings."""
        if not v.get_secret_value().strip():
            raise ValueError("Secret value cannot be empty")
        return v

    @field_validator("LITELLM_BASE_URL", mode="after")
    @classmethod
    def validate_non_empty_url(cls, v: Any) -> Any:
        """Ensures URL configuration values are not empty."""
        if not str(v).strip():
            raise ValueError("URL value cannot be empty")
        return v

    def __init__(self, **kwargs):
        """Initializes settings, resolving Docker/file secrets if not explicitly provided."""
        for secret_key in ["TELEGRAM_BOT_TOKEN", "OVERSEERR_API_KEY", "LITELLM_API_KEY", "DEFAULT_LLM_MODEL"]:
            if secret_key not in kwargs or not kwargs.get(secret_key):
                val = _read_secret(secret_key)
                if val:
                    kwargs[secret_key] = val

        super().__init__(**kwargs)


def load_settings() -> Settings:
    """Loads application settings with clear, pinpointed error messages for missing variables."""
    try:
        return Settings()
    except ValidationError as e:
        missing_lines = []
        for error in e.errors():
            loc_list = error.get("loc", [])
            var_name = str(loc_list[0]) if loc_list else "UNKNOWN"
            msg = error.get("msg", "Field required or invalid")
            example = VAR_EXAMPLES.get(var_name, "your_value_here")

            missing_lines.append(
                f"  ❌ {var_name}\n"
                f"     Reason: {msg}\n"
                f"     Example: {var_name}={example}\n"
            )

        env_file_path = os.path.abspath(".env")
        if os.path.exists(".env"):
            env_file_status = f"Found .env file at: {env_file_path}"
        else:
            env_file_status = "No .env file found in current working directory."

        error_message = (
            "\n" + "=" * 67 + "\n"
            "❌ CONFIGURATION ERROR: Missing or invalid environment variables!\n"
            "The application cannot start because the following required setting(s) are missing or invalid:\n\n"
            + "\n".join(missing_lines) + "\n"
            "💡 How to fix:\n"
            "   Add the missing variable(s) to your OS environment, Docker secrets, or a .env file.\n"
            f"   {env_file_status}\n"
            + "=" * 67 + "\n"
        )
        sys.stderr.write(error_message)
        sys.exit(1)


settings = load_settings()
