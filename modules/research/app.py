from crewai import Agent, Crew, Process, Task
from common.llm import get_llm
from common.tools import web_search


def run(topic: str):
    llm = get_llm()
    researcher = Agent(role="Researcher", goal="Find useful, verifiable information", backstory="You research a topic and distinguish evidence from speculation.", tools=[web_search], llm=llm, verbose=True)
    reviewer = Agent(role="Research Reviewer", goal="Check the research for gaps and unsupported claims", backstory="You challenge weak evidence and produce a concise quality review.", llm=llm, verbose=True)
    crew = Crew(agents=[researcher, reviewer], tasks=[
        Task(description=f"Research: {topic}. Use web_search. Return key findings and sources.", expected_output="Evidence-based research notes with source URLs.", agent=researcher),
        Task(description="Review the research notes. Identify unsupported claims, missing evidence and the three most important conclusions.", expected_output="Concise review and corrected conclusions.", agent=reviewer)
    ], process=Process.sequential, verbose=True)
    return crew.kickoff()

if __name__ == "__main__":
    run(input("Research topic: "))
