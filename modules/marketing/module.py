"""Marketing module: Researcher -> Strategist -> Writer.

Independently deployed, depends on the Research module over HTTP for market
background. The dependency is optional so the module stays useful alone.
"""

from __future__ import annotations

from common.agents import AgentSpec
from common.module_base import ModuleDefinition
from common.telemetry import DependencyDescriptor

MARKET_RESEARCHER = AgentSpec(
    agent_id="researcher",
    role="Marketing Researcher",
    goal="Research the audience, competitors and positioning for a product",
    backstory="You gather concise market context before any strategy is written.",
    instruction=(
        "Research the market for this product. Identify the target audience, the main "
        "competitors and the trends that matter, drawing on the upstream research context."
    ),
    expected_output="Short market research brief: audience, competitors, trends.",
    tools=("web_search",),
)

STRATEGIST = AgentSpec(
    agent_id="strategist",
    role="Marketing Strategist",
    goal="Turn research into a practical campaign strategy",
    backstory="You define positioning, channels and measurable campaign ideas.",
    instruction=(
        "Build a campaign strategy from the research: target audience, positioning, "
        "channels, message pillars and the KPIs you would measure."
    ),
    expected_output="One-page marketing strategy.",
)

WRITER = AgentSpec(
    agent_id="writer",
    role="Marketing Writer",
    goal="Produce clear campaign copy from the strategy",
    backstory="You write concise, audience-focused copy grounded strictly in the strategy.",
    instruction=(
        "Write a landing-page headline, a short value proposition and three social posts. "
        "Use only claims the strategy and research support."
    ),
    expected_output="Campaign copy pack: headline, value proposition, three social posts.",
)

DEFINITION = ModuleDefinition(
    module_id="marketing",
    service_id="marketing-service",
    description="Builds a campaign strategy and copy, using the Research module for market context.",
    input_name="product",
    input_description="The product or service to market, e.g. 'a budget e-bike for commuters'.",
    capabilities=["market_research", "strategy", "copywriting"],
    agents=(MARKET_RESEARCHER, STRATEGIST, WRITER),
    depends_on=[
        DependencyDescriptor(
            module_id="research",
            transport="http",
            url="",  # resolved from settings at runtime
            required=False,
            description="Market background research used as input to the strategy.",
        )
    ],
)
