"""Fact Checker module: Fact Researcher -> Verification Agent.

Independently deployed, but it depends on the Research module across an HTTP
boundary. The dependency is optional: if Research is unreachable, this module
degrades to its own evidence gathering and records the failed call.
"""

from __future__ import annotations

from common.agents import AgentSpec
from common.module_base import ModuleDefinition
from common.telemetry import DependencyDescriptor

FACT_RESEARCHER = AgentSpec(
    agent_id="fact_researcher",
    role="Fact Researcher",
    goal="Find evidence for and against a specific claim",
    backstory="You locate independent evidence and quote only what the sources support.",
    instruction=(
        "Investigate the claim. Gather evidence that supports it and evidence that contradicts "
        "it, using the upstream research context and search results. Do not take a side yet."
    ),
    expected_output="Evidence table: each item with its source and what it actually shows.",
    tools=("web_search",),
)

VERIFICATION = AgentSpec(
    agent_id="verification",
    role="Verification Agent",
    goal="Judge whether a claim is supported, contradicted or unresolved",
    backstory="You compare evidence dispassionately and report uncertainty explicitly.",
    instruction=(
        "Using the evidence gathered, classify the claim as SUPPORTED, CONTRADICTED or "
        "UNRESOLVED. Justify the verdict from the evidence and state your confidence."
    ),
    expected_output="Neutral verification report with an explicit verdict and confidence.",
)

DEFINITION = ModuleDefinition(
    module_id="fact_checker",
    service_id="fact-checker-service",
    description="Verifies a claim against evidence, using the Research module for background context.",
    input_name="claim",
    input_description="The claim to verify, e.g. 'solid-state batteries are in mass production'.",
    capabilities=["fact_checking", "evidence_gathering", "verification"],
    agents=(FACT_RESEARCHER, VERIFICATION),
    depends_on=[
        DependencyDescriptor(
            module_id="research",
            transport="http",
            url="",  # resolved from settings at runtime
            required=False,
            description="Background research used as context before verification.",
        )
    ],
)
