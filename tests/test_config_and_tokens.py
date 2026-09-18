"""Unit tests for configuration, token extraction and tools."""

from __future__ import annotations

import pytest

from common.agents.llm import estimate_usage, extract_usage
from common.config import Settings
from common.telemetry import TokenSource
from common.tools import web_search


class TestSettings:
    def test_service_urls_come_from_configuration(self) -> None:
        settings = Settings(research_url="http://research.internal:9000/")
        assert settings.service_url("research") == "http://research.internal:9000"

    def test_unknown_module_is_rejected(self) -> None:
        with pytest.raises(KeyError):
            Settings().service_url("nonexistent")

    def test_no_service_url_is_hardcoded_to_a_remote_host(self) -> None:
        settings = Settings()
        for module_id in ("research", "fact_checker", "marketing", "travel"):
            assert "127.0.0.1" in settings.service_url(module_id)

    def test_llm_is_configured_by_a_key_or_a_base_url(self) -> None:
        assert Settings(llm_api_key="sk-test").llm_configured is True
        # A local OpenAI-compatible server often needs no key.
        assert Settings(llm_api_key=None, llm_api_base="http://localhost:11434").llm_configured
        assert Settings(llm_api_key=None, llm_api_base=None).llm_configured is False

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
         ("false", False), ("0", False), ("no", False), ("", False)],
    )
    def test_boolean_env_values_are_coerced(
        self, raw: str, expected: bool, monkeypatch
    ) -> None:
        """Env vars arrive as strings, so the usual spellings must all work."""
        monkeypatch.setenv("SEARCH_ENABLED", raw)
        assert Settings().search_enabled is expected

    def test_langfuse_needs_both_keys(self) -> None:
        assert Settings(langfuse_public_key="pk", langfuse_secret_key="sk").langfuse_configured
        assert not Settings(langfuse_public_key="pk").langfuse_configured
        assert not Settings().langfuse_configured

    def test_no_secrets_are_baked_into_defaults(self) -> None:
        settings = Settings()
        assert settings.llm_api_key is None
        assert settings.langfuse_public_key is None
        assert settings.langfuse_secret_key is None


class TestTokenExtraction:
    def test_provider_usage_is_used_verbatim(self) -> None:
        response = {
            "usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165},
        }
        usage = extract_usage(response, [{"role": "user", "content": "hi"}], "out", "gpt-4o-mini")

        assert usage.input_tokens == 120
        assert usage.output_tokens == 45
        assert usage.total_tokens == 165
        assert usage.source is TokenSource.PROVIDER

    def test_missing_usage_falls_back_to_a_labelled_estimate(self) -> None:
        usage = extract_usage({}, [{"role": "user", "content": "hello there"}], "reply", "gpt-4o-mini")

        assert usage.source is TokenSource.ESTIMATED, "an estimate must never claim to be exact"
        assert usage.total_tokens > 0

    def test_total_is_derived_when_the_provider_omits_it(self) -> None:
        response = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        usage = extract_usage(response, [], "", "gpt-4o-mini")

        assert usage.total_tokens == 15

    def test_cached_and_reasoning_tokens_are_captured_when_present(self) -> None:
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 80},
                "completion_tokens_details": {"reasoning_tokens": 20},
            }
        }
        usage = extract_usage(response, [], "", "gpt-4o-mini")

        assert usage.cached_input_tokens == 80
        assert usage.reasoning_tokens == 20

    def test_absent_categories_stay_none_rather_than_zero(self) -> None:
        """None means 'not reported'; zero would be a fabricated measurement."""
        response = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        usage = extract_usage(response, [], "", "gpt-4o-mini")

        assert usage.cached_input_tokens is None
        assert usage.reasoning_tokens is None

    def test_estimates_are_always_labelled(self) -> None:
        usage = estimate_usage([{"role": "user", "content": "x" * 400}], "y" * 200, "gpt-4o-mini")

        assert usage.source is TokenSource.ESTIMATED
        assert usage.input_tokens > 0


class TestWebSearchTool:
    async def test_disabled_search_returns_a_message_not_an_error(self) -> None:
        settings = Settings(search_enabled=False)
        result = await web_search("anything", settings)

        assert "disabled" in result.lower()

    async def test_network_failure_degrades_gracefully(self, monkeypatch) -> None:
        """A flaky tool must not abort the agent run."""
        import httpx

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, *args, **kwargs):
                raise httpx.ConnectError("no network")

        monkeypatch.setattr("common.tools.httpx.AsyncClient", _Client)
        result = await web_search("query", Settings(search_enabled=True))

        assert "unavailable" in result.lower()

    async def test_results_are_formatted_with_sources(self, monkeypatch) -> None:
        payload = {
            "AbstractText": "A concise answer.",
            "AbstractURL": "https://example.com/a",
            "RelatedTopics": [{"Text": "Related fact", "FirstURL": "https://example.com/b"}],
        }

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            def json(self):
                return payload

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, *args, **kwargs):
                return _Response()

        monkeypatch.setattr("common.tools.httpx.AsyncClient", _Client)
        result = await web_search("query", Settings(search_enabled=True))

        assert "A concise answer." in result
        assert "https://example.com/a" in result
        assert "Related fact" in result
