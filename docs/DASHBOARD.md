# The Control Room — what it shows and why

A walkthrough of the dashboard, the agents behind it, and what this project delivers.

Every screenshot below is a real capture of the running system, with real LLM calls against a live provider. Nothing is mocked or drawn by hand.

Run it with `streamlit run ui/app.py` → **http://localhost:8501**

---

## What you are looking at

The application is a **System Under Test (SUT)**: a realistic multi-agent system built so an *external testing platform* can onboard it, drive it, and check that what it observes matches what the application reports.

That makes observability the product, not a side feature. The dashboard exists to make that observable surface visible to a human — and it is deliberately built as a **pure consumer of the public HTTP API**. It imports no module or agent code. Everything on screen is something the external platform can read for itself.

```
4 modules   ·   11 agents   ·   2 cross-module HTTP dependencies   ·   2 fully independent modules
```

---

## 1. Topology — the map

![Topology tab](screenshots/1-topology.png)

The application graph, read live from the gateway's `/topology` and each module's `/health`.

**The four modules and their eleven agents:**

| Module | Port | Agent pipeline | Depends on |
|---|---|---|---|
| **research** | 8001 | researcher → analyst → reviewer | — (independent) |
| **fact_checker** | 8002 | fact_researcher → verification | research, over HTTP |
| **marketing** | 8003 | researcher → strategist → writer | research, over HTTP |
| **travel** | 8004 | planner → search → booking | — (independent) |

Each agent is a distinct role with its own goal, prompt and deliverable, running in sequence with the previous agent's output as input. The Research pipeline, for example: the **researcher** gathers evidence (and may call the `web_search` tool), the **analyst** finds themes and weighs evidence quality, the **reviewer** challenges unsupported claims and states the surviving conclusions.

**What the picture encodes:**

- **Green ring** — the service is reachable right now. Red would mean that module is down; the dashboard keeps working and shows the rest.
- **Arrows** — cross-module dependencies, and they are real network calls. Fact Checker and Marketing each POST to `http://research:8001/run`.
- **Travel sits alone** on the right. That is the point being demonstrated: it is deployed exactly like the others but depends on nothing.

The central design claim this tab makes concrete:

> **Independent deployment does not mean independent dependency.**

All four are separate processes, separate images, separately deployable to separate hosts. Two of them still depend on Research. Those are orthogonal properties, and the graph shows both at once.

---

## 2. Run — drive an agent pipeline

![Run tab](screenshots/2-run.png)

Pick a module, type an input, press **Run**. The screenshot is a real Research run:

```
HTTP 200   ·   3 agents   ·   3,557 tokens   ·   6,536 ms
trace_id: bc9b189e042f4c7f7dd93ab2fd4c35e4
```

Each agent is listed with its own token count — researcher 1,043, analyst 1,116, reviewer 1,398 — and expands to show the **actual text it produced**. The stacked bar chart underneath splits every agent's usage into input, output and reasoning tokens.

Two controls matter for testing:

- **Failure mode** — inject a fault into this one request without touching the deployed service.
- **Dependencies** toggle — turn the upstream HTTP call off, so you can compare a module's behaviour with and without its dependency. Useful for isolating whether a problem is in the module or in what it depends on.

---

## 3. Traces — the distributed trace

![Traces tab](screenshots/3-traces.png)

This is the strongest evidence of the whole system, so it is worth reading closely.

```
2 services   ·   15 spans   ·   5,245 tokens   ·   status ok
"One trace spans 2 services (fact_checker, research) — the trace id survived the HTTP boundary."
```

One request to Fact Checker produced this waterfall. Reading down the bars:

```
fact_checker.request          ← the whole request, on :8002
  service_call.research       ← the outbound HTTP call
    research.request          ← recorded by a DIFFERENT PROCESS on :8001
      agent.researcher          → tool.web_search, llm.researcher
      agent.analyst             → llm.analyst
      agent.reviewer            → llm.reviewer
  agent.fact_researcher       ← back on :8002
  agent.verification
```

Two separate OS processes recorded spans under **one trace id**, and the gateway reassembled them. That works because every request carries a W3C `traceparent` header, which the dependency client forwards — so any OpenTelemetry-aware collector stitches these together with no custom glue.

Bars are positioned by real start time, not stacked end to end, so concurrent work looks concurrent. Colours are span kinds: request, agent, llm, tool, service_call. A failed span gets a coloured outline, and hovering shows its error type.

---

## 4. Tokens & Cost — honest accounting

![Tokens tab](screenshots/4-tokens-cost.png)

```
27,565 tokens observed   ·   14,783 input   ·   12,782 output   ·   13 traces
```

Usage rolls up at every level: **LLM call → agent → module → request**, including tokens reported by a downstream module. When Fact Checker calls Research, Research's usage is folded into Fact Checker's total, so one number covers the whole distributed request.

The `source` column is the part that matters for a testing platform:

| Source | Meaning |
|---|---|
| `provider` | The provider reported these counts |
| `estimated` | Approximated locally — **not exact, and labelled as such** |
| `unavailable` | The provider reported nothing |

**Counts are never fabricated.** Categories a provider does not report stay `null`, not `0` — because zero is a measurement and null is an absence. In the screenshot the Cost shows `—` for the same reason: litellm has no pricing table for this model, so the system reports nothing rather than inventing a figure.

The **Last run breakdown** underneath splits wall-clock latency into LLM, tool, dependency and overhead time. `total_ms` is always measured, never summed from child spans — otherwise concurrent work would be double counted — and overhead clamps at zero.

---

## 5. Failure modes — deterministic faults

![Failure modes tab](screenshots/5-failure-modes.png)

Six injectable scenarios, each with a button. The screenshot shows `error` fired at Research:

```
HTTP 500   ·   0 agents completed   ·   4 ms
InjectedFailure: Injected failure: FAILURE_MODE=error
✅ Telemetry survived the failure: trace cf9316416a58a010… is still correlated and queryable.
```

| Mode | Behaviour |
|---|---|
| `normal` | No injection — the control case |
| `slow` | Delays, then succeeds |
| `error` | Labelled application error (HTTP 500) |
| `timeout` | Sleeps past the caller's timeout (HTTP 504) |
| `tool_failure` | Fails when a tool is invoked |
| `dependency_failure` | Fails the cross-service call |

Failures are **configuration, not chance** — the same input fails the same way every time, which is what makes them testable. And critically, a failed run still returns a *complete telemetry document*: the trace stays correlated, agents that already ran are still listed, and their tokens are still counted. A partial failure is fully observable, not a black hole.

---

## What this project delivers

**The application**
- 4 independently deployable FastAPI services, 11 agents, one Dockerfile each
- Provider-neutral LLM access via litellm — Gemini, OpenAI, Anthropic, Groq or a local Ollama, switched by editing `.env`
- A gateway for discovery and routing that never imports agent code
- Cross-module dependencies over HTTP with explicit timeouts, retries, and graceful degradation when an optional dependency is down

**The observable surface** — the same endpoints on every module
- `/health` — liveness and identity
- `/metadata` — full discovery: ids, version, agents, capabilities, dependencies, supported failure modes
- `/run` — execute, returning result *and* complete telemetry
- `/telemetry/traces`, `/telemetry/traces/{id}`, `/telemetry/tokens`
- Gateway: `/topology`, `/discovery`, `/health`, `/telemetry/traces/{id}`

**Observability**
- W3C distributed tracing that survives service boundaries
- Token accounting at call, agent, module and request level, with an explicit source
- Latency measured as wall clock and decomposed by category
- Structured JSON logs stamped with trace and request ids
- Optional OTLP or Langfuse export — opt-in, and nothing breaks when it is off

**Quality**
- 204 tests, fully offline: no API key, no network, no running services
- Verified end to end against a live provider: 11/11 agents producing real output

---

## Where the tabs get their data

Nothing here is computed in the UI. Each panel maps to an endpoint the testing platform can call itself:

| Tab | Endpoints |
|---|---|
| Topology | `GET /topology`, `GET /health`, `GET /metadata` |
| Run | `POST /run` |
| Traces | `GET /telemetry/traces`, `GET /telemetry/traces/{id}` (gateway assembles across services) |
| Tokens & Cost | `GET /telemetry/tokens` |
| Failure modes | `POST /run` with `failure_mode` |

That is the real point of the dashboard: if it can show it, the external platform can observe it.
