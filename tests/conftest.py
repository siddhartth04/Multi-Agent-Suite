"""Shared test fixtures.

The whole suite runs offline and deterministically: no API key, no network, no
running services. LLM calls are replaced with a fake that returns fixed text and
fixed token usage, so token and latency assertions are exact.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# Keep telemetry export and outbound search off for the whole suite. Anything
# else is passed explicitly to Settings(), so that env vars never shadow the
# values a test is trying to assert.
os.environ["OTEL_ENABLED"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
for _leaked in ("SEARCH_ENABLED", "LLM_API_KEY", "LLM_API_BASE", "MODEL", "FAILURE_MODE",
                "RESEARCH_URL", "FACT_CHECKER_URL", "MARKETING_URL", "TRAVEL_URL",
                "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_leaked, None)

from common.agents import llm as llm_module  # noqa: E402
from common.config import Settings, reset_settings_cache  # noqa: E402
from common.telemetry.models import TokenSource, TokenUsage  # noqa: E402

# A developer's local .env holds real credentials and a real model name. Tests
# assert on documented defaults, so the suite must not read it -- otherwise
# results depend on whose machine is running them.
def make_settings(**overrides) -> Settings:
    """Build Settings for a test, ignoring any local .env file."""
    return Settings(_env_file=None, **overrides)

FAKE_INPUT_TOKENS = 100
FAKE_OUTPUT_TOKENS = 40


class FakeLLM:
    """Stand-in for :class:`common.agents.llm.LLMClient`.

    Records every call so tests can assert which agents ran, in what order.
    """

    def __init__(self, settings: Settings, *, fail_on: str | None = None, delay: float = 0.0) -> None:
        self.settings = settings
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on
        self.delay = delay

    @property
    def model(self) -> str:
        return self.settings.model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> llm_module.LLMResponse:
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)

        self.calls.append({"system": system_prompt, "user": user_prompt})

        if self.fail_on and self.fail_on in system_prompt:
            raise llm_module.LLMError(f"fake failure for {self.fail_on}")

        role = _role_from_prompt(system_prompt)
        return llm_module.LLMResponse(
            text=f"[{role}] deterministic output",
            model=self.settings.model,
            tokens=TokenUsage(
                input_tokens=FAKE_INPUT_TOKENS,
                output_tokens=FAKE_OUTPUT_TOKENS,
                total_tokens=FAKE_INPUT_TOKENS + FAKE_OUTPUT_TOKENS,
                cost_usd=0.0001,
                source=TokenSource.PROVIDER,
            ),
            retry_count=0,
            finish_reason="stop",
        )


def _role_from_prompt(system_prompt: str) -> str:
    first = system_prompt.splitlines()[0] if system_prompt else ""
    return first.replace("You are the ", "").rstrip(".") or "agent"


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return make_settings(
        otel_enabled=False,
        search_enabled=False,
        llm_api_key="test-key",
        model="gpt-4o-mini",
        log_level="WARNING",
        dependency_max_retries=1,
        dependency_retry_backoff_seconds=0.0,
        llm_max_retries=0,
        slow_mode_delay_seconds=0.05,
        timeout_mode_delay_seconds=0.05,
    )


@pytest.fixture
def fake_llm(monkeypatch) -> type[FakeLLM]:
    """Patch the pipeline's LLM client with the deterministic fake."""
    monkeypatch.setattr("common.agents.base.LLMClient", FakeLLM)
    return FakeLLM


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
