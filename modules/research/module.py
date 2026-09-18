"""Research module: Researcher -> Analyst -> Reviewer.

Independent. Nothing upstream is required, which makes it the module the other
two can safely depend on.
"""

from __future__ import annotations

from common.agents import AgentSpec
from common.module_base import ModuleDefinition

RESEARCHER = AgentSpec(
    agent_id="researcher",
    role="Researcher",
    goal="Find useful, verifiable information about a topic",
    backstory="You research a topic and carefully distinguish evidence from speculation.",
    instruction=(
        "Research the topic. Use the supplied search results where they are relevant. "
        "Report key findings with sources, and say plainly when evidence is thin."
    ),
    expected_output="Evidence-based research notes with source URLs where available.",
    tools=("web_search",),
)

ANALYST = AgentSpec(
    agent_id="analyst",
    role="Research Analyst",
    goal="Turn raw findings into structured analysis",
    backstory="You find patterns, weigh evidence quality and surface the implications.",
    instruction=(
        "Analyse the researcher's notes. Identify themes, contradictions and the strength of "
        "the evidence behind each claim. Separate what is well supported from what is not."
    ),
    expected_output="Structured analysis with themes, evidence strength and open questions.",
)

REVIEWER = AgentSpec(
    agent_id="reviewer",
    role="Research Reviewer",
    goal="Check the research and analysis for gaps and unsupported claims",
    backstory="You challenge weak evidence and produce a concise quality review.",
    instruction=(
        "Review the research and the analysis. Flag unsupported claims and missing evidence, "
        "then state the three most important conclusions that survive scrutiny."
    ),
    expected_output="Concise quality review followed by the corrected conclusions.",
)

DEFINITION = ModuleDefinition(
    module_id="research",
    service_id="research-service",
    description="Independent research module: researches a topic, analyses it, and reviews the result.",
    input_name="topic",
    input_description="The topic to research, e.g. 'the state of solid-state batteries'.",
    capabilities=["research", "analysis", "review"],
    agents=(RESEARCHER, ANALYST, REVIEWER),
    depends_on=[],
)
