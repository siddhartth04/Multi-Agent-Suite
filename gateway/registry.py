"""Service registry and the declared application topology.

This is the map the external testing platform onboards: which modules exist,
which agents each runs, and which modules depend on which.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common.config import Settings


@dataclass(frozen=True)
class ModuleRegistration:
    module_id: str
    service_id: str
    default_port: int
    agents: tuple[str, ...]
    capabilities: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    description: str = ""
    workflow_path: str = ""

    @property
    def independent(self) -> bool:
        return not self.depends_on


REGISTRY: tuple[ModuleRegistration, ...] = (
    ModuleRegistration(
        module_id="research",
        service_id="research-service",
        default_port=8001,
        agents=("researcher", "analyst", "reviewer"),
        capabilities=("research", "analysis", "review"),
        depends_on=(),
        description="Independent research, analysis and review.",
        workflow_path="/workflow/research",
    ),
    ModuleRegistration(
        module_id="fact_checker",
        service_id="fact-checker-service",
        default_port=8002,
        agents=("fact_researcher", "verification"),
        capabilities=("fact_checking", "evidence_gathering", "verification"),
        depends_on=("research",),
        description="Verifies a claim; calls Research over HTTP for background.",
        workflow_path="/workflow/fact-check",
    ),
    ModuleRegistration(
        module_id="marketing",
        service_id="marketing-service",
        default_port=8003,
        agents=("researcher", "strategist", "writer"),
        capabilities=("market_research", "strategy", "copywriting"),
        depends_on=("research",),
        description="Campaign strategy and copy; calls Research over HTTP for market context.",
        workflow_path="/workflow/marketing",
    ),
    ModuleRegistration(
        module_id="travel",
        service_id="travel-service",
        default_port=8004,
        agents=("planner", "search", "booking"),
        capabilities=("trip_planning", "travel_search", "booking_preparation"),
        depends_on=(),
        description="Fully independent trip planning, search and booking preparation.",
        workflow_path="/workflow/travel",
    ),
)

BY_ID: dict[str, ModuleRegistration] = {m.module_id: m for m in REGISTRY}


def service_urls(settings: Settings) -> dict[str, str]:
    return {m.module_id: settings.service_url(m.module_id) for m in REGISTRY}


def dependency_edges() -> list[dict[str, str]]:
    """The module-to-module edges, as the platform should observe them."""
    return [
        {"from": m.module_id, "to": dep, "transport": "http"}
        for m in REGISTRY
        for dep in m.depends_on
    ]


def agent_edges() -> list[dict[str, str]]:
    """Agent-to-agent edges within each module's sequential pipeline."""
    edges: list[dict[str, str]] = []
    for module in REGISTRY:
        for source, target in zip(module.agents, module.agents[1:]):
            edges.append(
                {
                    "module_id": module.module_id,
                    "from": source,
                    "to": target,
                    "relationship": "sequential",
                }
            )
    return edges
