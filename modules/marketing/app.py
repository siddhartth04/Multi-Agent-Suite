from crewai import Agent, Crew, Process, Task
from common.llm import get_llm
from common.tools import web_search


def run(product: str):
    llm = get_llm()
    researcher = Agent(role="Marketing Researcher", goal="Research audience, competitors and positioning", backstory="You gather concise market context before strategy is written.", tools=[web_search], llm=llm, verbose=True)
    strategist = Agent(role="Marketing Strategist", goal="Turn research into a practical campaign strategy", backstory="You create positioning, channels and measurable campaign ideas.", llm=llm, verbose=True)
    writer = Agent(role="Marketing Writer", goal="Produce clear campaign copy", backstory="You write concise audience-focused copy based only on the strategy.", llm=llm, verbose=True)
    crew = Crew(agents=[researcher, strategist, writer], tasks=[
        Task(description=f"Research the market for: {product}. Find audience, competitors and relevant trends.", expected_output="Research brief.", agent=researcher),
        Task(description="Create a campaign strategy from the research: audience, positioning, channels, message pillars and KPIs.", expected_output="One-page marketing strategy.", agent=strategist),
        Task(description="Write a landing-page headline, short value proposition and three social posts from the strategy.", expected_output="Campaign copy pack.", agent=writer)
    ], process=Process.sequential, verbose=True)
    return crew.kickoff()

if __name__ == "__main__":
    run(input("Product/service: "))
