"""Gateway tests: discovery, topology and routing.

The topology the gateway declares is what the external platform onboards, so it
is checked against the modules' own metadata rather than asserted in isolation.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from common.service import create_service
from gateway.registry import BY_ID, REGISTRY, agent_edges, dependency_edges
from modules.fact_checker.module import DEFINITION as FACT_CHECKER
from modules.marketing.module import DEFINITION as MARKETING
from modules.research.module import DEFINITION as RESEARCH
from modules.travel.module import DEFINITION as TRAVEL

MODULE_DEFINITIONS = {
    "research": RESEARCH,
    "fact_checker": FACT_CHECKER,
    "marketing": MARKETING,
    "travel": TRAVEL,
}


@pytest.fixture
def gateway_client():
    from gateway.app import app

    with TestClient(app) as client:
        yield client


class TestTopology:
    def test_topology_lists_all_four_modules(self, gateway_client) -> None:
        modules = gateway_client.get("/topology").json()["modules"]
        assert set(modules) == {"research", "fact_checker", "marketing", "travel"}

    def test_declared_topology_matches_each_module_definition(self, gateway_client) -> None:
        """The gateway's claims must match what the modules actually implement."""
        modules = gateway_client.get("/topology").json()["modules"]

        for module_id, declared in modules.items():
            definition = MODULE_DEFINITIONS[module_id]
            assert declared["agents"] == [a.agent_id for a in definition.agents]
            assert declared["capabilities"] == definition.capabilities
            assert declared["service_id"] == definition.service_id
            assert declared["independent"] is definition.independent
            assert declared["depends_on"] == [d.module_id for d in definition.depends_on]

    def test_dependency_edges_match_the_specification(self, gateway_client) -> None:
        edges = gateway_client.get("/topology").json()["module_dependencies"]
        pairs = {(e["from"], e["to"]) for e in edges}

        assert pairs == {("fact_checker", "research"), ("marketing", "research")}
        assert all(e["transport"] == "http" for e in edges)

    def test_travel_and_research_are_the_independent_modules(self, gateway_client) -> None:
        body = gateway_client.get("/topology").json()
        assert set(body["independent_modules"]) == {"research", "travel"}

    def test_agent_edges_describe_each_sequential_pipeline(self, gateway_client) -> None:
        edges = gateway_client.get("/topology").json()["agent_dependencies"]
        research_edges = [(e["from"], e["to"]) for e in edges if e["module_id"] == "research"]

        assert research_edges == [("researcher", "analyst"), ("analyst", "reviewer")]

    def test_no_module_depends_on_itself(self, gateway_client) -> None:
        for edge in gateway_client.get("/topology").json()["module_dependencies"]:
            assert edge["from"] != edge["to"]

    def test_every_module_exposes_a_workflow_route(self, gateway_client) -> None:
        from gateway.app import app

        paths = {route.path for route in app.routes}
        for module in REGISTRY:
            assert module.workflow_path in paths


class TestRegistry:
    def test_registry_and_definitions_agree_on_agents(self) -> None:
        for module in REGISTRY:
            definition = MODULE_DEFINITIONS[module.module_id]
            assert list(module.agents) == [a.agent_id for a in definition.agents]

    def test_dependency_edges_are_derived_not_hardcoded(self) -> None:
        assert dependency_edges() == [
            {"from": "fact_checker", "to": "research", "transport": "http"},
            {"from": "marketing", "to": "research", "transport": "http"},
        ]

    def test_agent_edges_cover_every_module(self) -> None:
        modules_with_edges = {e["module_id"] for e in agent_edges()}
        assert modules_with_edges == set(BY_ID)

    def test_ports_are_unique(self) -> None:
        ports = [m.default_port for m in REGISTRY]
        assert len(set(ports)) == len(ports)


class TestGatewayRouting:
    def test_workflow_forwards_to_its_module(self, monkeypatch, settings, fake_llm) -> None:
        research_app = create_service(RESEARCH, settings)
        transport = httpx.ASGITransport(app=research_app)
        original = httpx.AsyncClient

        def _client(*args, **kwargs):
            kwargs.setdefault("transport", transport)
            return original(*args, **kwargs)

        monkeypatch.setattr("gateway.app.httpx.AsyncClient", _client)

        from gateway.app import app

        with TestClient(app) as client:
            response = client.post("/workflow/research", json={"input": "a topic"})

        assert response.status_code == 200
        body = response.json()
        assert body["module_id"] == "research"
        assert body["status"] == "ok"
        assert "traceparent" in response.headers

    def test_unreachable_module_returns_502(self, monkeypatch) -> None:
        def _fail(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def post(self, *args, **kwargs):
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr("gateway.app.httpx.AsyncClient", _Client)

        from gateway.app import app

        with TestClient(app) as client:
            response = client.post("/workflow/travel", json={"input": "test"})

        assert response.status_code == 502
        assert "unavailable" in response.json()["detail"]

    def test_empty_input_is_rejected_before_forwarding(self, gateway_client) -> None:
        assert gateway_client.post("/workflow/research", json={"input": ""}).status_code == 422


class TestGatewayHealth:
    def test_health_reports_every_module_even_when_down(self, gateway_client) -> None:
        body = gateway_client.get("/health").json()

        assert body["gateway"] == "ok"
        assert set(body["services"]) == {"research", "fact_checker", "marketing", "travel"}
        assert body["modules_total"] == 4

    def test_root_advertises_the_endpoints(self, gateway_client) -> None:
        body = gateway_client.get("/").json()

        assert body["application_id"] == "multi-agent-sut"
        assert "/topology" in body["endpoints"]
