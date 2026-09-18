# Modular Multi-Agent Application Architecture

One logical application containing multiple **independently deployable modules**. Each module may contain one or more related agents, while the modules themselves do not have to depend on one another.

```text
                         OUR APPLICATION
                              |
        +---------------------+----------------------+
        |                     |                      |
        v                     v                      v
  Research Module       Fact Checker Module    Marketing Module
  :8001                 :8002                  :8003
  Researcher            Researcher             Researcher
  Analyst                -> Verification       -> Strategist
  Reviewer                                      -> Writer
        |
        | independent module
        |
        +------------------------------------------+
                                                   v
                                             Travel Module
                                             :8004
                                             Planner
                                             -> Search
                                             -> Booking
```

## Module relationships

- **Research is an independent module.** Its Researcher/Analyst/Reviewer agents work internally, but it does not depend on another module.
- **Fact Checker is an internal workflow:** Researcher -> Verification Agent.
- **Marketing is an internal workflow:** Researcher -> Strategist -> Writer.
- **Travel is an internal workflow:** Planner -> Search -> Booking.
- There is intentionally **no required cross-module dependency**. This lets an external testing platform onboard and exercise each module independently.

## Separate deployment

Every module has its own FastAPI server and Dockerfile. Start them in separate terminals:

```powershell
uvicorn modules.research.server:app --host 0.0.0.0 --port 8001
uvicorn modules.fact_checker.server:app --host 0.0.0.0 --port 8002
uvicorn modules.marketing.server:app --host 0.0.0.0 --port 8003
uvicorn modules.travel.server:app --host 0.0.0.0 --port 8004
```

The gateway can discover/register the four service URLs, but the modules do not need to run in the same process or on the same machine. Change the `*_URL` environment variables to point at independently deployed services.

This is deliberately lightweight: local Python processes are enough for development, while the same service boundaries can later map to Docker containers, VMs, or Kubernetes Deployments.
