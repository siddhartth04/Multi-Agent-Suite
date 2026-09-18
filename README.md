# Lightweight Modular Multi-Agent Application

A small CrewAI application that demonstrates **one logical application containing multiple agent modules**, with a deliberate mix of related and independent groups.

## Structure

```text
ONE APPLICATION
│
├── Research
│   ├── Researcher
│   └── Reviewer
├── Fact Checker
│   ├── Researcher
│   └── Verification Agent
├── Marketing
│   ├── Researcher
│   ├── Strategist
│   └── Writer
├── Travel
│   ├── Planner
│   ├── Search Agent
│   └── Booking Advisor```

Relationships:

```text
Research ─────→ Fact Checker
    └──────────→ Marketing

Travel  = independent
```

## Separate deployment is a first-class feature

Each module is an independent FastAPI service. Therefore:

- each module can run in a different terminal;
- each module can listen on its own port;
- each module can be built into its own Docker image;
- each module can later be deployed to a different VM/container/host;
- the gateway only needs the URL of each module.

### Local service mode

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Start four terminals:

```powershell
uvicorn modules.research.server:app --host 0.0.0.0 --port 8001
uvicorn modules.fact_checker.server:app --host 0.0.0.0 --port 8002
uvicorn modules.marketing.server:app --host 0.0.0.0 --port 8003
uvicorn modules.travel.server:app --host 0.0.0.0 --port 8004
```

Each has `/health`, `/metadata`, `/run`, and `/docs`.

### Docker mode

```powershell
docker compose up --build
```

Five separate containers are created. The container-internal port is 8000, while the host ports are 8001-8005.

### Gateway mode

```powershell
uvicorn gateway.app:app --host 0.0.0.0 --port 8000
```

Endpoints:

- `GET /topology`
- `GET /health`
- `POST /workflow/research` — Research module only
- `POST /workflow/fact-check` — Fact Checker module only
- `POST /workflow/marketing` — Marketing module only
- `POST /workflow/travel` — Travel module only

The gateway exposes each independently deployable module without forcing cross-module dependencies.

## Keep it lightweight

Required: Python, CrewAI, FastAPI/Uvicorn, HTTPX, and an LLM API key.

Not required: Kubernetes, Kafka, Redis, PostgreSQL, NATS, vector DB, or cloud deployment.

## References

The internal crews follow CrewAI's agent/task pattern. The service boundary follows lightweight FastAPI deployment patterns, where services/processes can be independently run and containerized.

- CrewAI: https://github.com/crewAIInc/crewAI
- CrewAI examples: https://github.com/crewAIInc/crewAI-examples
- Travel Planner A2A: https://github.com/plaban1981/Travel-Planner-Multi-Agent-A2A
- FastAPI deployment concepts: https://fastapi.tiangolo.com/deployment/concepts/
- FastAPI containers: https://fastapi.tiangolo.com/deployment/docker/


## IMPORTANT: Claude Code project context

Read `PROJECT_CONTEXT_FOR_CLAUDE_CODE.md` before making architectural changes. It is the source of truth for the SUT purpose, hybrid dependency model, separate deployment, observability, token/latency tracking, failure scenarios, and engineering constraints.
