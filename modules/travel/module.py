"""Travel module: Planner -> Search -> Booking Advisor.

Completely independent -- no upstream module, by design. This is the control
case that proves independent deployment does not imply a dependency.
"""

from __future__ import annotations

from common.agents import AgentSpec
from common.module_base import ModuleDefinition

PLANNER = AgentSpec(
    agent_id="planner",
    role="Travel Planner",
    goal="Turn travel requirements into a practical trip brief",
    backstory="You structure destinations, dates, budget and priorities.",
    instruction=(
        "Parse the request into a trip brief: destination, dates, budget, travellers, "
        "preferences and constraints. Mark anything the request left unspecified."
    ),
    expected_output="Trip brief covering destination, dates, budget, preferences, constraints.",
)

SEARCH = AgentSpec(
    agent_id="search",
    role="Travel Search Agent",
    goal="Find useful, current travel information",
    backstory="You search public sources for attractions, transport and lodging context.",
    instruction=(
        "Using the trip brief and the search results, list relevant attractions, transport "
        "options and accommodation areas. Never claim a reservation has been made."
    ),
    expected_output="Shortlist of travel options with sources where available.",
    tools=("web_search",),
)

BOOKING = AgentSpec(
    agent_id="booking",
    role="Booking Advisor",
    goal="Turn the plan into booking-ready options without pretending to book",
    backstory="You summarise options and constraints, and flag what must be verified.",
    instruction=(
        "Produce a practical itinerary and a booking checklist from the brief and the "
        "options. Clearly mark every item the traveller must verify or book themselves."
    ),
    expected_output="Day-by-day itinerary plus a booking checklist with verification flags.",
)

DEFINITION = ModuleDefinition(
    module_id="travel",
    service_id="travel-service",
    description="Independent travel module: plans a trip, finds options, and prepares a booking checklist.",
    input_name="request",
    input_description="The travel request, e.g. '5 days in Lisbon in May, mid-range budget, two adults'.",
    capabilities=["trip_planning", "travel_search", "booking_preparation"],
    agents=(PLANNER, SEARCH, BOOKING),
    depends_on=[],
)
