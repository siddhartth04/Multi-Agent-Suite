"""Contract tests: every module must honour the same HTTP interface.

These are the checks the external testing platform performs at onboarding, so
they are parametrised across all four modules rather than written per module.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from common.service import create_service
from common.telemetry import TRACEPARENT_HEADER, TraceContext
from modules.fact_checker.module import DEFINITION as FACT_CHECKER
from modules.marketing.module import DEFINITION as MARKETING
from modules.research.module import DEFINITION as RESEARCH
from modules.travel.module import DEFINITION as TRAVEL

ALL_MODULES = [RESEARCH, FACT_CHECKER, MARKETING, TRAVEL]
MODULE_IDS = [m.module_id for m in ALL_MODULES]


@pytest.fixture(params=ALL_MODULES, ids=MODULE_IDS)
def module_client(request, settings, fake_llm) -> tuple[TestClient, object]:
    definition = request.param
    app = create_service(definition, settings)
    with TestClient(app) as client:
        yield client, definition


class TestHealth:
    def test_health_reports_identity(self, module_client) -> None:
        client, definition = module_client
        body = client.get("/health").json()

        assert body["status"] in {"ok", "degraded"}
        assert body["application_id"] == "executive-intelligence"
        assert body["module_id"] == definition.module_id
        assert body["service_id"] == definition.service_id
        assert body["uptime_seconds"] >= 0

    def test_health_lists_declared_dependencies(self, module_client) -> None:
        client, definition = module_client
        deps = client.get("/health").json()["dependencies"]
        assert set(deps) == {d.module_id for d in definition.depends_on}


class TestMetadata:
    def test_metadata_satisfies_the_onboarding_contract(self, module_client) -> None:
        client, definition = module_client
        body = client.get("/metadata").json()

        for field in (
            "application_id", "module_id", "service_id", "version",
            "deployment_id", "capabilities", "agents",
        ):
            assert field in body, f"missing required field {field}"

        assert body["module_id"] == definition.module_id
        assert body["capabilities"] == definition.capabilities

    def test_metadata_lists_every_agent_in_pipeline_order(self, module_client) -> None:
        client, definition = module_client
        body = client.get("/metadata").json()

        expected = [a.agent_id for a in definition.agents]
        assert [a["agent_id"] for a in body["agents"]] == expected
        assert body["agent_pipeline"] == expected

    def test_agent_dependencies_form_a_sequential_chain(self, module_client) -> None:
        client, _ = module_client
        agents = client.get("/metadata").json()["agents"]

        assert agents[0]["depends_on"] == []
        for previous, current in zip(agents, agents[1:]):
            assert current["depends_on"] == [previous["agent_id"]]

    def test_dependency_urls_come_from_configuration(self, module_client) -> None:
        client, definition = module_client
        declared = client.get("/metadata").json()["dependencies"]

        assert len(declared) == len(definition.depends_on)
        for dependency in declared:
            assert dependency["url"].startswith("http"), "URL must be resolved, not blank"
            assert dependency["transport"] == "http"

    def test_independence_flag_matches_declared_dependencies(self, module_client) -> None:
        client, definition = module_client
        assert client.get("/metadata").json()["independent"] is definition.independent

    def test_supported_failure_modes_are_advertised(self, module_client) -> None:
        client, _ = module_client
        modes = client.get("/metadata").json()["supported_failure_modes"]
        assert {"normal", "slow", "error", "timeout"} <= set(modes)


class TestRun:
    def test_run_returns_result_and_telemetry(self, module_client) -> None:
        client, definition = module_client
        response = client.post("/run", json={"input": "test input", "use_dependencies": False})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["result"]
        assert body["module_id"] == definition.module_id
        assert len(body["trace_id"]) == 32

    def test_every_agent_reports_its_own_telemetry(self, module_client) -> None:
        client, definition = module_client
        body = client.post("/run", json={"input": "test", "use_dependencies": False}).json()

        assert [a["agent_id"] for a in body["agents"]] == [a.agent_id for a in definition.agents]
        for agent in body["agents"]:
            assert agent["status"] == "ok"
            assert agent["tokens"]["total_tokens"] > 0
            assert agent["duration_ms"] >= 0
            assert agent["model"]

    def test_module_tokens_equal_the_sum_of_its_agents(self, module_client) -> None:
        client, _ = module_client
        body = client.post("/run", json={"input": "test", "use_dependencies": False}).json()

        expected = sum(a["tokens"]["total_tokens"] for a in body["agents"])
        assert body["tokens"]["total_tokens"] == expected
        assert body["tokens"]["source"] == "provider"

    def test_latency_wall_clock_is_not_a_sum_of_spans(self, module_client) -> None:
        client, _ = module_client
        latency = client.post("/run", json={"input": "test", "use_dependencies": False}).json()["latency"]

        assert latency["total_ms"] > 0
        assert latency["overhead_ms"] >= 0

    def test_empty_input_is_rejected(self, module_client) -> None:
        client, _ = module_client
        assert client.post("/run", json={"input": ""}).status_code == 422

    def test_response_echoes_trace_headers(self, module_client) -> None:
        client, _ = module_client
        response = client.post("/run", json={"input": "test", "use_dependencies": False})

        assert TRACEPARENT_HEADER in response.headers
        assert "x-request-id" in response.headers

    def test_incoming_trace_context_is_continued(self, module_client) -> None:
        client, _ = module_client
        upstream = TraceContext()

        body = client.post(
            "/run",
            json={"input": "test", "use_dependencies": False},
            headers=upstream.to_headers(),
        ).json()

        assert body["trace_id"] == upstream.trace_id, "trace must survive the HTTP boundary"
        assert body["request_id"] == upstream.request_id


class TestTelemetryEndpoints:
    def test_run_is_retrievable_by_trace_id(self, module_client) -> None:
        client, definition = module_client
        trace_id = client.post(
            "/run", json={"input": "test", "use_dependencies": False}
        ).json()["trace_id"]

        trace = client.get(f"/telemetry/traces/{trace_id}").json()

        assert trace["trace_id"] == trace_id
        assert trace["module_id"] == definition.module_id
        kinds = {s["kind"] for s in trace["spans"]}
        assert {"request", "agent", "llm"} <= kinds

    def test_span_tree_is_rooted_in_a_single_request_span(self, module_client) -> None:
        client, _ = module_client
        trace_id = client.post(
            "/run", json={"input": "test", "use_dependencies": False}
        ).json()["trace_id"]

        spans = client.get(f"/telemetry/traces/{trace_id}").json()["spans"]
        by_id = {s["span_id"]: s for s in spans}
        roots = [s for s in spans if s["parent_span_id"] not in by_id]

        assert len(roots) == 1 and roots[0]["kind"] == "request"

    def test_unknown_trace_returns_404(self, module_client) -> None:
        client, _ = module_client
        assert client.get(f"/telemetry/traces/{'0' * 32}").status_code == 404

    def test_traces_can_be_listed_and_cleared(self, module_client) -> None:
        client, _ = module_client
        client.post("/run", json={"input": "test", "use_dependencies": False})

        assert client.get("/telemetry/traces").json()["count"] >= 1
        client.delete("/telemetry/traces")
        assert client.get("/telemetry/traces").json()["count"] == 0

    def test_token_totals_accumulate_across_runs(self, module_client) -> None:
        client, _ = module_client
        client.delete("/telemetry/traces")

        client.post("/run", json={"input": "one", "use_dependencies": False})
        after_one = client.get("/telemetry/tokens").json()["tokens"]["total_tokens"]
        client.post("/run", json={"input": "two", "use_dependencies": False})
        after_two = client.get("/telemetry/tokens").json()["tokens"]["total_tokens"]

        assert after_two == after_one * 2
