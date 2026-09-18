"""Cross-module integration: the Research dependency over a real HTTP boundary.

The downstream Research service is a genuine ASGI app reached through an httpx
transport, so the request is serialised, headers are propagated and the trace is
stitched exactly as it would be between two hosts -- without opening a socket.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from common.config import Settings
from common.service import create_service
from common.telemetry import TraceContext
from modules.fact_checker.module import DEFINITION as FACT_CHECKER
from modules.marketing.module import DEFINITION as MARKETING
from modules.research.module import DEFINITION as RESEARCH
from modules.travel.module import DEFINITION as TRAVEL

DEPENDENT_MODULES = [FACT_CHECKER, MARKETING]
DEPENDENT_IDS = [m.module_id for m in DEPENDENT_MODULES]


@pytest.fixture
def research_app(settings, fake_llm):
    return create_service(RESEARCH, settings)


@pytest.fixture
def wired(monkeypatch, settings, fake_llm, research_app, request):
    """Point the dependency client at the in-process Research app.

    Only the network hop is replaced; the module still builds a real request,
    sends real headers and parses a real response.
    """
    definition = getattr(request, "param", FACT_CHECKER)
    downstream = httpx.ASGITransport(app=research_app)
    original = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs.setdefault("transport", downstream)
        return original(*args, **kwargs)

    monkeypatch.setattr("common.http_client.httpx.AsyncClient", _client)

    app = create_service(definition, settings)
    with TestClient(app) as client:
        yield client, definition, research_app


@pytest.mark.parametrize("wired", DEPENDENT_MODULES, ids=DEPENDENT_IDS, indirect=True)
class TestDependencyCall:
    def test_dependent_module_calls_research(self, wired) -> None:
        client, definition, _ = wired
        body = client.post("/run", json={"input": "a claim to check"}).json()

        assert body["status"] == "ok"
        assert len(body["dependencies"]) == 1

        call = body["dependencies"][0]
        assert call["module_id"] == "research"
        assert call["status"] == "ok"
        assert call["http_status"] == 200
        assert call["url"].endswith("/research/run") or call["url"].endswith("/run")

    def test_trace_survives_the_service_boundary(self, wired) -> None:
        client, _, research_app = wired
        upstream = TraceContext()

        body = client.post(
            "/run", json={"input": "test claim"}, headers=upstream.to_headers()
        ).json()

        assert body["trace_id"] == upstream.trace_id

        # The downstream service recorded the same trace id under its own spans.
        with TestClient(research_app) as research_client:
            downstream_trace = research_client.get(
                f"/telemetry/traces/{upstream.trace_id}"
            ).json()

        assert downstream_trace["trace_id"] == upstream.trace_id
        assert downstream_trace["module_id"] == "research"
        assert {s["agent_id"] for s in downstream_trace["spans"] if s["agent_id"]} == {
            "researcher", "analyst", "reviewer",
        }

    def test_service_call_span_is_recorded(self, wired) -> None:
        client, _, _ = wired
        trace_id = client.post("/run", json={"input": "test"}).json()["trace_id"]

        spans = client.get(f"/telemetry/traces/{trace_id}").json()["spans"]
        service_calls = [s for s in spans if s["kind"] == "service_call"]

        assert len(service_calls) == 1
        assert service_calls[0]["attributes"]["http.status_code"] == 200
        assert service_calls[0]["duration_ms"] > 0

    def test_downstream_tokens_roll_up_into_the_caller(self, wired) -> None:
        client, definition, _ = wired
        body = client.post("/run", json={"input": "test"}).json()

        own = sum(a["tokens"]["total_tokens"] for a in body["agents"])
        downstream = body["dependencies"][0]["tokens"]["total_tokens"]

        assert downstream > 0, "the dependency must report its own usage"
        assert body["tokens"]["total_tokens"] == own + downstream

    def test_research_context_reaches_the_first_agent(self, wired) -> None:
        client, _, _ = wired
        body = client.post("/run", json={"input": "test"}).json()

        assert body["dependencies"][0]["status"] == "ok"
        assert body["agents"][0]["output"]

    def test_dependencies_can_be_disabled_per_request(self, wired) -> None:
        client, _, _ = wired
        body = client.post(
            "/run", json={"input": "test", "use_dependencies": False}
        ).json()

        assert body["status"] == "ok"
        assert body["dependencies"] == []
        assert body["result"], "the module must still work alone"

    def test_dependency_latency_is_attributed_separately(self, wired) -> None:
        client, _, _ = wired
        body = client.post("/run", json={"input": "test"}).json()
        latency = body["latency"]

        assert latency["dependency_ms"] > 0, "cross-service time is its own category"
        assert body["dependencies"][0]["duration_ms"] > 0

        # With a mocked LLM the whole request finishes in around a millisecond,
        # which is the resolution of the clock on some platforms, so compare with
        # a tolerance rather than asserting an exact ordering.
        assert latency["total_ms"] >= latency["dependency_ms"] - 1.0
        assert latency["overhead_ms"] >= 0


class TestIndependentModules:
    @pytest.mark.parametrize("definition", [RESEARCH, TRAVEL], ids=["research", "travel"])
    def test_independent_modules_make_no_service_calls(
        self, definition, settings, fake_llm
    ) -> None:
        app = create_service(definition, settings)
        with TestClient(app) as client:
            body = client.post("/run", json={"input": "test"}).json()

            assert body["status"] == "ok"
            assert body["dependencies"] == []

            spans = client.get(f"/telemetry/traces/{body['trace_id']}").json()["spans"]
            assert not [s for s in spans if s["kind"] == "service_call"]

    def test_travel_runs_without_any_other_module_configured(self, fake_llm) -> None:
        """Travel must work even when every other service URL is unreachable."""
        settings = Settings(
            otel_enabled=False,
            search_enabled=False,
            llm_api_key="test-key",
            research_url="http://127.0.0.1:1",
            fact_checker_url="http://127.0.0.1:1",
            marketing_url="http://127.0.0.1:1",
            log_level="WARNING",
        )
        app = create_service(TRAVEL, settings)
        with TestClient(app) as client:
            body = client.post("/run", json={"input": "5 days in Lisbon"}).json()

        assert body["status"] == "ok"
        assert [a["agent_id"] for a in body["agents"]] == ["planner", "search", "booking"]


class TestDependencyFailure:
    def test_unreachable_dependency_degrades_but_records_the_failure(
        self, settings, fake_llm
    ) -> None:
        """Research is optional: the module continues and reports the failed call."""
        broken = settings.model_copy(
            update={
                "research_url": "http://127.0.0.1:1",
                "dependency_connect_timeout_seconds": 0.3,
            }
        )
        app = create_service(FACT_CHECKER, broken)

        with TestClient(app) as client:
            response = client.post("/run", json={"input": "a claim"})
            body = response.json()

        assert response.status_code == 200, "an optional dependency failing is not fatal"
        assert body["status"] == "ok"
        assert body["result"], "the module still produced its own answer"

        assert len(body["dependencies"]) == 1
        call = body["dependencies"][0]
        # A refused connection surfaces as a transport error on some platforms
        # and as a connect timeout on others; both are recorded failures.
        assert call["status"] in {"error", "timeout"}
        assert call["module_id"] == "research"
        assert call["error"]

    def test_failed_dependency_still_appears_in_the_trace(self, settings, fake_llm) -> None:
        broken = settings.model_copy(
            update={
                "research_url": "http://127.0.0.1:1",
                "dependency_connect_timeout_seconds": 0.3,
            }
        )
        app = create_service(FACT_CHECKER, broken)

        with TestClient(app) as client:
            trace_id = client.post("/run", json={"input": "a claim"}).json()["trace_id"]
            spans = client.get(f"/telemetry/traces/{trace_id}").json()["spans"]

        service_calls = [s for s in spans if s["kind"] == "service_call"]
        assert len(service_calls) == 1
        assert service_calls[0]["status"] in {"error", "timeout"}
        assert service_calls[0]["retry_count"] >= 1, "retries must be recorded"
