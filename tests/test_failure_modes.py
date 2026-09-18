"""Failure injection must be deterministic, observable and non-destructive.

Every injected failure still returns a complete telemetry document: the trace
stays correlated, the partial agent list is preserved, and the error is visible.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from common.agents import llm as llm_module
from common.config import Settings
from common.failures import SUPPORTED_MODES, resolve_mode
from common.service import create_service
from modules.research.module import DEFINITION as RESEARCH


@pytest.fixture
def client(settings, fake_llm):
    app = create_service(RESEARCH, settings)
    with TestClient(app) as test_client:
        yield test_client


class TestModeResolution:
    def test_request_mode_overrides_the_service_default(self, settings) -> None:
        service_default = settings.model_copy(update={"failure_mode": "slow"})
        assert resolve_mode("error", service_default) == "error"

    def test_service_default_applies_when_the_request_is_silent(self, settings) -> None:
        service_default = settings.model_copy(update={"failure_mode": "slow"})
        assert resolve_mode(None, service_default) == "slow"

    def test_unknown_modes_fall_back_to_normal(self, settings) -> None:
        assert resolve_mode("chaos", settings) == "normal"

    def test_modes_are_case_insensitive(self, settings) -> None:
        assert resolve_mode("ERROR", settings) == "error"


class TestNormalMode:
    def test_normal_mode_succeeds(self, client) -> None:
        response = client.post("/run", json={"input": "test", "failure_mode": "normal"})

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestErrorMode:
    def test_error_mode_fails_deterministically(self, client) -> None:
        response = client.post("/run", json={"input": "test", "failure_mode": "error"})
        body = response.json()

        assert response.status_code == 500
        assert body["status"] == "error"
        assert "Injected failure" in body["error"]
        assert body["failure_mode"] == "error"

    def test_error_mode_is_repeatable(self, client) -> None:
        errors = {
            client.post("/run", json={"input": "test", "failure_mode": "error"}).json()["error"]
            for _ in range(3)
        }
        assert len(errors) == 1, "the same input must fail the same way every time"

    def test_failed_request_still_carries_a_correlated_trace(self, client) -> None:
        body = client.post("/run", json={"input": "test", "failure_mode": "error"}).json()

        trace = client.get(f"/telemetry/traces/{body['trace_id']}").json()

        assert trace["status"] == "error"
        request_spans = [s for s in trace["spans"] if s["kind"] == "request"]
        assert len(request_spans) == 1
        assert request_spans[0]["status"] == "error"
        assert request_spans[0]["error_type"] == "InjectedFailure"

    def test_failure_does_not_leak_into_the_next_request(self, client) -> None:
        client.post("/run", json={"input": "test", "failure_mode": "error"})
        assert client.post("/run", json={"input": "test"}).json()["status"] == "ok"


class TestSlowMode:
    def test_slow_mode_delays_but_still_succeeds(self, settings, fake_llm) -> None:
        slow = settings.model_copy(update={"slow_mode_delay_seconds": 0.2})
        app = create_service(RESEARCH, slow)

        with TestClient(app) as client:
            body = client.post("/run", json={"input": "test", "failure_mode": "slow"}).json()

        assert body["status"] == "ok"
        assert body["latency"]["total_ms"] >= 200, "the injected delay must be measurable"


class TestTimeoutMode:
    def test_timeout_mode_reports_a_timeout_status(self, settings, fake_llm) -> None:
        fast = settings.model_copy(update={"timeout_mode_delay_seconds": 0.05})
        app = create_service(RESEARCH, fast)

        with TestClient(app) as client:
            response = client.post("/run", json={"input": "test", "failure_mode": "timeout"})
            body = response.json()

        assert response.status_code == 504
        assert body["status"] == "timeout"
        assert body["failure_mode"] == "timeout"

    def test_timeout_is_recorded_as_a_timeout_span(self, settings, fake_llm) -> None:
        fast = settings.model_copy(update={"timeout_mode_delay_seconds": 0.05})
        app = create_service(RESEARCH, fast)

        with TestClient(app) as client:
            trace_id = client.post(
                "/run", json={"input": "test", "failure_mode": "timeout"}
            ).json()["trace_id"]
            trace = client.get(f"/telemetry/traces/{trace_id}").json()

        assert trace["status"] == "timeout"


class TestToolFailureMode:
    def test_tool_failure_is_reported(self, client) -> None:
        response = client.post("/run", json={"input": "test", "failure_mode": "tool_failure"})
        body = response.json()

        assert response.status_code == 500
        assert body["status"] == "error"
        assert "tool failure" in body["error"].lower()


class TestAgentFailure:
    def test_a_failing_agent_preserves_the_agents_that_already_ran(
        self, settings, monkeypatch
    ) -> None:
        """The reviewer fails; the researcher and analyst must still be reported."""
        from tests.conftest import FakeLLM

        def failing(settings_arg):
            return FakeLLM(settings_arg, fail_on="Research Reviewer")

        monkeypatch.setattr("common.agents.base.LLMClient", failing)
        app = create_service(RESEARCH, settings)

        with TestClient(app) as client:
            response = client.post("/run", json={"input": "test"})
            body = response.json()

        assert response.status_code == 500
        assert body["status"] == "error"
        assert [a["agent_id"] for a in body["agents"]] == ["researcher", "analyst"]
        assert body["tokens"]["total_tokens"] > 0, "partial usage is still billed and reported"


class TestSupportedModes:
    @pytest.mark.parametrize("mode", SUPPORTED_MODES)
    def test_every_advertised_mode_is_accepted(self, mode, settings, fake_llm) -> None:
        fast = settings.model_copy(
            update={"slow_mode_delay_seconds": 0.01, "timeout_mode_delay_seconds": 0.05}
        )
        app = create_service(RESEARCH, fast)

        with TestClient(app) as client:
            response = client.post("/run", json={"input": "test", "failure_mode": mode})

        assert response.status_code in {200, 500, 504}
        body = response.json()
        assert body["failure_mode"] == mode
        assert body["trace_id"], "a failed request still reports its trace"


class TestLLMRetries:
    def test_llm_errors_are_retried_then_surfaced(self, settings, monkeypatch) -> None:
        attempts = {"count": 0}

        class AlwaysFails:
            def __init__(self, settings_arg) -> None:
                self.settings = settings_arg

            @property
            def model(self) -> str:
                return self.settings.model

            async def complete(self, *args, **kwargs):
                attempts["count"] += 1
                raise llm_module.LLMError("provider is down")

        monkeypatch.setattr("common.agents.base.LLMClient", AlwaysFails)
        app = create_service(RESEARCH, settings)

        with TestClient(app) as client:
            body = client.post("/run", json={"input": "test"}).json()

        assert body["status"] == "error"
        assert attempts["count"] >= 1
        assert "provider is down" in body["error"]
