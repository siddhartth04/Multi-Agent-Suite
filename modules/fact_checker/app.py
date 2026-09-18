from crewai import Agent, Crew, Process, Task
from common.llm import get_llm
from common.tools import web_search


def run(claim: str):
    llm = get_llm()
    researcher = Agent(role="Fact Researcher", goal="Find evidence relevant to a claim", backstory="You locate independent evidence and quote only what the sources support.", tools=[web_search], llm=llm, verbose=True)
    verifier = Agent(role="Verification Agent", goal="Assess whether a claim is supported, contradicted or unresolved", backstory="You compare evidence and explicitly report uncertainty.", llm=llm, verbose=True)
    crew = Crew(agents=[researcher, verifier], tasks=[
        Task(description=f"Investigate this claim: {claim}. Search for supporting and contradicting evidence.", expected_output="Evidence table with source URLs and short findings.", agent=researcher),
        Task(description="Using the evidence, classify the claim as supported, contradicted, or unresolved. Explain why.", expected_output="Neutral verification report.", agent=verifier)
    ], process=Process.sequential, verbose=True)
    return crew.kickoff()

if __name__ == "__main__":
    run(input("Claim to verify: "))
