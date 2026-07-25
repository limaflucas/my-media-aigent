import os
import sys
import logging
from typing import List, Optional
from pydantic import HttpUrl, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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
    # Telegram Configuration
    TELEGRAM_BOT_TOKEN: SecretStr
    TELEGRAM_ALLOWED_USERS: List[int] = []

    # Overseerr Service Configuration
    OVERSEERR_URL: HttpUrl
    OVERSEERR_API_KEY: SecretStr
    OVERSEERR_TIMEOUT: float = 10.0
    OVERSEERR_SSL_VERIFY: bool = True

    # LiteLLM / Local AI Lab Configuration
    LITELLM_BASE_URL: str
    LITELLM_API_KEY: SecretStr
    DEFAULT_LLM_MODEL: str
    MAX_TRANSCRIPT_TOKENS: int = 3000

    # Application Behavior
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("TELEGRAM_BOT_TOKEN", "OVERSEERR_API_KEY", "LITELLM_API_KEY", mode="after")
    @classmethod
    def validate_non_empty_secret(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("Secret value cannot be empty")
        return v

    @field_validator("LITELLM_BASE_URL", "DEFAULT_LLM_MODEL", mode="after")
    @classmethod
    def validate_non_empty_str(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Configuration string cannot be empty")
        return v.strip()

    def __init__(self, **kwargs):
        # Resolve secrets from docker / file if not explicitly passed
        for secret_key in ["TELEGRAM_BOT_TOKEN", "OVERSEERR_API_KEY", "LITELLM_API_KEY"]:
            if secret_key not in kwargs or not kwargs.get(secret_key):
                val = _read_secret(secret_key)
                if val:
                    kwargs[secret_key] = val

        super().__init__(**kwargs)


def load_settings() -> Settings:
    """Loads settings with clear, user-friendly error formatting on failure."""
    try:
        return Settings()
    except ValidationError as e:
        missing_vars = []
        for error in e.errors():
            loc = " -> ".join(str(loc_item) for loc_item in error.get("loc", []))
            msg = error.get("msg", "")
            missing_vars.append(f"  • {loc}: {msg}")

        error_message = (
            "\n" + "=" * 65 + "\n"
            "❌ CONFIGURATION ERROR: Missing or invalid environment variables!\n"
            "The application cannot start because required settings are missing:\n\n"
            + "\n".join(missing_vars) + "\n\n"
            "Please provide the required values in your environment, .env file, or Docker secrets:\n"
            "  - TELEGRAM_BOT_TOKEN  (or TELEGRAM_BOT_TOKEN_FILE / Docker secret)\n"
            "  - OVERSEERR_URL       (e.g., http://seerr:5055)\n"
            "  - OVERSEERR_API_KEY   (or OVERSEERR_API_KEY_FILE / Docker secret)\n"
            "  - LITELLM_BASE_URL    (e.g., http://litellm:4000/v1)\n"
            "  - LITELLM_API_KEY     (or LITELLM_API_KEY_FILE / Docker secret)\n"
            "  - DEFAULT_LLM_MODEL   (e.g., gemma4-fit)\n"
            + "=" * 65 + "\n"
        )
        sys.stderr.write(error_message)
        sys.exit(1)


settings = load_settings()
