from crewai import Agent, Crew, Process, Task
from common.llm import get_llm
from common.tools import web_search


def run(request: str):
    llm = get_llm()
    planner = Agent(role="Travel Planner", goal="Turn travel requirements into a practical trip plan", backstory="You structure destinations, dates, budget and priorities.", llm=llm, verbose=True)
    searcher = Agent(role="Travel Search Agent", goal="Find useful current travel information", backstory="You search the public web for attractions, transport and lodging context.", tools=[web_search], llm=llm, verbose=True)
    booking = Agent(role="Booking Advisor", goal="Turn the plan into booking-ready options without pretending to book", backstory="You summarize options, constraints and what the user should verify before booking.", llm=llm, verbose=True)
    crew = Crew(agents=[planner, searcher, booking], tasks=[
        Task(description=f"Parse this request and produce a trip brief: {request}", expected_output="Destination, dates, budget, preferences and constraints.", agent=planner),
        Task(description="Use web_search to find relevant attractions, transport and accommodation areas. Do not claim a reservation was made.", expected_output="Short list of travel options with URLs where available.", agent=searcher),
        Task(description="Create a booking-ready itinerary and checklist using the trip brief and search results. Clearly mark anything that must be verified.", expected_output="Practical itinerary and booking checklist.", agent=booking)
    ], process=Process.sequential, verbose=True)
    return crew.kickoff()

if __name__ == "__main__":
    run(input("Travel request: "))
