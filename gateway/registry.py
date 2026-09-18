import os
SERVICES = {
    "research": os.getenv("RESEARCH_URL", "http://127.0.0.1:8001"),
    "fact_checker": os.getenv("FACT_CHECKER_URL", "http://127.0.0.1:8002"),
    "marketing": os.getenv("MARKETING_URL", "http://127.0.0.1:8003"),
    "travel": os.getenv("TRAVEL_URL", "http://127.0.0.1:8004"),
}
