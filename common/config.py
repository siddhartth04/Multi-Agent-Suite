"""Environment-based configuration shared by every module service.

Nothing in this package reads ``os.environ`` directly; everything goes through
:func:`get_settings` so that tests can override configuration deterministically.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FailureMode = Literal["normal", "slow", "error", "timeout", "tool_failure", "dependency_failure"]

APPLICATION_ID = "executive-intelligence"
APPLICATION_VERSION = "1.0.0"


class Settings(BaseSettings):
    """Runtime configuration for a single service process."""

    # ``populate_by_name`` matters: each field below declares an env alias, and
    # without it the alias becomes the only accepted input name, so constructing
    # Settings(research_url=...) in code would be silently ignored.
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ----- identity -------------------------------------------------------
    application_id: str = APPLICATION_ID
    application_version: str = APPLICATION_VERSION
    deployment_id: str = Field(default="local", validation_alias="DEPLOYMENT_ID")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")

    # ----- llm ------------------------------------------------------------
    model: str = Field(default="gpt-4o-mini", validation_alias="MODEL")
    llm_api_key: str | None = Field(default=None, validation_alias="LLM_API_KEY")
    llm_api_base: str | None = Field(default=None, validation_alias="LLM_API_BASE")
    llm_temperature: float = Field(default=0.2, validation_alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=800, validation_alias="LLM_MAX_TOKENS")
    llm_timeout_seconds: float = Field(default=60.0, validation_alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, validation_alias="LLM_MAX_RETRIES")

    # ----- service discovery ---------------------------------------------
    research_url: str = Field(default="http://127.0.0.1:8001", validation_alias="RESEARCH_URL")
    fact_checker_url: str = Field(default="http://127.0.0.1:8002", validation_alias="FACT_CHECKER_URL")
    marketing_url: str = Field(default="http://127.0.0.1:8003", validation_alias="MARKETING_URL")
    travel_url: str = Field(default="http://127.0.0.1:8004", validation_alias="TRAVEL_URL")

    # ----- cross-service http --------------------------------------------
    dependency_timeout_seconds: float = Field(default=120.0, validation_alias="DEPENDENCY_TIMEOUT_SECONDS")
    dependency_connect_timeout_seconds: float = Field(
        default=5.0, validation_alias="DEPENDENCY_CONNECT_TIMEOUT_SECONDS"
    )
    dependency_max_retries: int = Field(default=2, validation_alias="DEPENDENCY_MAX_RETRIES")
    dependency_retry_backoff_seconds: float = Field(
        default=0.25, validation_alias="DEPENDENCY_RETRY_BACKOFF_SECONDS"
    )

    # ----- observability --------------------------------------------------
    otel_enabled: bool = Field(default=True, validation_alias="OTEL_ENABLED")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_exporter_otlp_headers: str | None = Field(
        default=None, validation_alias="OTEL_EXPORTER_OTLP_HEADERS"
    )
    otel_console_export: bool = Field(default=False, validation_alias="OTEL_CONSOLE_EXPORT")
    langfuse_public_key: str | None = Field(default=None, validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, validation_alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", validation_alias="LANGFUSE_HOST")
    trace_buffer_size: int = Field(default=64, validation_alias="TRACE_BUFFER_SIZE")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # ----- agent execution ------------------------------------------------
    agent_engine: Literal["llm", "stub", "auto"] = Field(default="auto", validation_alias="AGENT_ENGINE")

    # ----- tools ----------------------------------------------------------
    search_enabled: bool = Field(default=True, validation_alias="SEARCH_ENABLED")
    search_timeout_seconds: float = Field(default=10.0, validation_alias="SEARCH_TIMEOUT_SECONDS")

    # ----- failure injection ---------------------------------------------
    failure_mode: FailureMode = Field(default="normal", validation_alias="FAILURE_MODE")
    slow_mode_delay_seconds: float = Field(default=5.0, validation_alias="SLOW_MODE_DELAY_SECONDS")
    timeout_mode_delay_seconds: float = Field(
        default=125.0, validation_alias="TIMEOUT_MODE_DELAY_SECONDS"
    )

    @field_validator("otel_enabled", "otel_console_export", "search_enabled", mode="before")
    @classmethod
    def _coerce_bool(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value

    @property
    def langfuse_configured(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def llm_configured(self) -> bool:
        """True when a real LLM call can be attempted.

        A local OpenAI-compatible base URL (Ollama, vLLM, LM Studio) often needs
        no key, so an explicit base is enough on its own.
        """
        return bool(self.llm_api_key or self.llm_api_base)

    def service_url(self, module_id: str) -> str:
        urls = {
            "research": self.research_url,
            "fact_checker": self.fact_checker_url,
            "marketing": self.marketing_url,
            "travel": self.travel_url,
        }
        if module_id not in urls:
            raise KeyError(f"Unknown module_id: {module_id}")
        return urls[module_id].rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings. Used by tests after mutating the environment."""
    get_settings.cache_clear()


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
