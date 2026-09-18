import os
from crewai import LLM


def get_llm():
    """Use an OpenAI-compatible provider without bundling a local model."""
    model = os.getenv("MODEL", "gpt-4o-mini")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY before running an agent module.")
    return LLM(model=f"openai/{model}", api_key=api_key, temperature=0.2)
