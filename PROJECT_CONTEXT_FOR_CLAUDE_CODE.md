# PROJECT CONTEXT FOR CLAUDE CODE — Multi-Agent Application / System Under Test (SUT)

## Read this first

We are building the **actual multi-agent application that another team's testing platform will onboard and test**.

This repository is the **System Under Test (SUT)**. It is NOT the testing platform.

The external platform will test our application, so this application must be realistic, modular, independently deployable, observable, traceable, and easy to exercise.

---

## 1. Why we are building this

We need a lightweight but credible multi-agent application that demonstrates:

- multiple logical modules inside one application;
- multiple agents inside some modules;
- agent-to-agent dependencies;
- module-to-module dependencies;
- completely independent modules;
- independently deployable services;
- agents running in separate terminals/processes/containers;
- distributed tracing;
- token tracking;
- individual and overall latency;
- LLM/tool/service call visibility;
- errors, retries and timeouts;
- enough telemetry for an external testing platform to compare its observations with our application's behavior.

The goal is **not** to build a huge infrastructure platform. The goal is to provide a good, realistic SUT for their testing system.

---

## 2. Manager requirement — interpreted correctly

The manager described an application made of multiple logical "pages"/units/modules. Treat "page" as a **logical module/component**, not necessarily a UI page.

The important requirements from that discussion are:

- one application can contain multiple modules;
- one module can have one or several agents;
- agents can be related or interdependent;
- some modules can be independent;
- some agents/modules can depend on another module;
- components can be independently deployed;
- the overall application should be onboarded and tested as a system;
- the testing platform should be able to observe what the application and its agents are doing.

Therefore the desired topology is deliberately **hybrid**, not a single chain.

---

## 3. Required application structure

```text
OUR MULTI-AGENT APPLICATION
│
├── Research Module
│   ├── Researcher Agent
│   ├── Analyst Agent
│   └── Reviewer Agent
│
├── Fact Checker Module
│   ├── Fact Researcher Agent
│   └── Verification Agent
│
├── Marketing Module
│   ├── Researcher Agent
│   ├── Strategist Agent
│   └── Writer Agent
│
└── Travel Module
    ├── Planner Agent
    ├── Search Agent
    └── Booking Agent
```

**There is no GitHub Issue module. Do not add it back unless explicitly requested.**

---

## 4. Dependency topology — critical

This is neither a fully independent set of modules nor one giant connected workflow.

### Research: independent module

```text
Research
Researcher → Analyst → Reviewer
```

Research can run without any other module.

### Fact Checker: depends on Research

```text
Research :8001
     │
     │ research result/context
     ▼
Fact Checker :8002
     ├── Fact Researcher
     └── Verification
```

Fact Checker is separately deployed, but it may call Research through a network boundary.

### Marketing: depends on Research

```text
Research :8001
     │
     │ research result/context
     ▼
Marketing :8003
     ├── Researcher
     ├── Strategist
     └── Writer
```

Marketing is separately deployed, but it may call Research through a network boundary.

### Travel: completely independent

```text
Travel :8004
Planner → Search → Booking
```

Travel does not require Research, Fact Checker, or Marketing.

### Key principle

**Independent deployment does NOT mean independent dependency.**

Example:

```text
Research service :8001
        │
        │ HTTP/A2A
        ▼
Fact Checker service :8002
```

These are separate services, but Fact Checker can depend on Research.

---

## 5. Target graph

```text
                         OUR APPLICATION
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
        RESEARCH         FACT CHECKER       MARKETING
        :8001              :8002              :8003
             │                ▲                ▲
             │                │                │
             ├────────────────┘                │
             │                                 │
             └─────────────────────────────────┘
                    cross-module dependencies


                    TRAVEL :8004
                    │
                    ├── Planner
                    ├── Search
                    └── Booking

                    completely independent
```

The exact internal orchestration can evolve, but this hybrid relationship model must remain.

---

## 6. Independent deployment

Every module must be independently deployable.

Local development should allow:

```text
Terminal 1 → Research service :8001
Terminal 2 → Fact Checker :8002
Terminal 3 → Marketing :8003
Terminal 4 → Travel :8004
```

The same services should be capable of becoming:

```text
separate Docker containers
separate VMs/hosts
separate Kubernetes deployments later
```

Do NOT implement cross-module dependencies by importing another service's Python agent object directly.

Use a network boundary such as HTTP initially; A2A can be introduced later if justified.

Service URLs must come from configuration/environment variables.

---

## 7. Service contract

Every module should expose:

```text
GET  /health
GET  /metadata
POST /run
```

Metadata should identify at least:

```text
application_id
module_id
service_id
version
capabilities
agents
deployment identity
```

Example:

```json
{
  "application_id": "multi-agent-sut",
  "module_id": "research",
  "service_id": "research-service",
  "version": "0.1.0",
  "agents": ["researcher", "analyst", "reviewer"],
  "capabilities": ["research", "analysis", "review"]
}
```

This makes onboarding/discovery easier for the external testing platform.

---

## 8. Observability — first-class requirement

Do not build a large custom observability database.

Preferred architecture:

```text
Agent services
     │
     ▼
OpenTelemetry
     │
     ▼
Langfuse (preferred)
```

LangWatch is an acceptable alternative if it is a better fit for a particular integration.

The application should not tightly couple business logic to the observability backend.

---

## 9. Distributed tracing

Every execution needs a trace/request identity.

Example:

```text
Trace ID: tr_abc123
Request ID: req_456
```

Cross-service calls must preserve trace context:

```text
Research :8001
     │
     │ HTTP
     ▼
Fact Checker :8002
     │
     ▼
Verification Agent
```

Conceptually:

```text
tr_abc123
│
├── research.request
│   ├── researcher
│   │   └── llm.call
│   ├── tool.call
│   └── analyst
│       └── llm.call
│
├── fact_checker.request
│   ├── fact_researcher
│   │   └── llm.call
│   └── verification
│       └── llm.call
│
└── final.response
```

The same distributed trace must survive the HTTP boundary.

---

## 10. Token tracking

Capture token usage at all meaningful levels.

### LLM call

```text
input tokens
output tokens
total tokens
model
cost when available
```

### Agent

Aggregate the LLM calls belonging to that agent.

### Module

Aggregate the agents in that module.

### Application

Aggregate all module usage.

Example:

```text
Application total
├── Research: ...
├── Fact Checker: ...
├── Marketing: ...
└── Travel: ...
```

If the provider exposes cached/reasoning token categories, capture them where supported.

Do not fabricate token counts. If a provider does not expose a value, mark it as unavailable/estimated rather than pretending it is exact.

---

## 11. Latency tracking

Capture:

- overall application/request latency;
- module latency;
- individual agent latency;
- individual LLM call latency;
- tool call latency;
- cross-service HTTP latency.

Example:

```text
Application: 8.42s

Research: 3.21s
  Researcher: 2.61s
    LLM: 1.82s
    Tool: 0.73s
  Analyst: 0.60s

Fact Checker: 1.94s
Marketing: 3.27s
```

Do not incorrectly calculate overall wall-clock latency by simply summing spans if operations can run in parallel.

---

## 12. Structured telemetry

Make it possible to observe:

- request start/end;
- agent start/end;
- LLM start/end;
- tool start/end;
- service-to-service calls;
- errors;
- retries;
- timeouts;
- status;
- service/module/agent identity;
- application version;
- deployment ID.

Structured JSON logging is preferred.

---

## 13. Controlled failure scenarios

The SUT is intended to be tested, so deterministic failure scenarios are useful.

At minimum support configurable modes such as:

```text
normal
slow
error
timeout
```

Potential future modes:

```text
tool_failure
dependency_failure
rate_limit
invalid_output
retry
partial_failure
```

Failures should be deterministic/configurable, not random by default.

Example:

```text
Research → Fact Checker

Fact Checker timeout

Expected telemetry:
- Research span succeeds
- cross-service call records timeout
- Fact Checker records failure
- trace remains correlated
- latency is measurable
- error is visible
```

---

## 14. Why this architecture is useful for their testing platform

They should be able to verify:

### Application
- reachability;
- application identity;
- modules;
- versions;
- deployment status.

### Topology
- agents;
- agent-to-agent relationships;
- module-to-module relationships;
- independent modules.

### Execution
- which agent ran;
- execution order;
- service calls;
- tool calls;
- LLM calls.

### Observability
- complete distributed trace;
- overall latency;
- individual latency;
- cross-service latency;
- errors.

### LLM
- model;
- input/output/total tokens;
- cost where available.

### Reliability
- timeout;
- retry;
- dependency failure;
- failure propagation;
- recovery.

---

## 15. Lightweight stack

Preferred:

```text
Python
FastAPI
Uvicorn
HTTPX
CrewAI (for internal agent orchestration where useful)
OpenTelemetry
Langfuse
```

Do not require for the basic local demo:

```text
Kubernetes
Kafka
NATS
Redis
PostgreSQL
Qdrant
large local models
cloud deployment
```

Docker Compose is useful for demonstrating independent deployment.

The application must also work as separate Python processes.

---

## 16. Code quality

Treat this as an expert engineering project, not a throwaway demo.

Requirements:

- clean package boundaries;
- typed Python where practical;
- Pydantic models;
- environment-based configuration;
- no committed secrets;
- no hard-coded service URLs;
- reusable telemetry utilities;
- reusable HTTP client;
- explicit timeouts;
- robust error handling;
- health checks;
- deterministic tests;
- unit tests;
- cross-module integration tests;
- no dead code;
- no unnecessary dependencies;
- clear README;
- clear architecture documentation.

After changes, run:

```bash
python -m compileall .
pytest
```

Also manually exercise the relevant services.

---

## 17. Important boundary

This repository is the **SUT**.

Do NOT turn it into the external testing platform.

The external platform will:

```text
onboard our application
discover/register our services
send test requests
observe execution
evaluate behavior
compare expected vs observed behavior
```

Our responsibility is to provide a realistic, observable, independently deployable application.

---

## 18. Example scenarios

### Scenario A — Fact checking

```text
External Testing Platform
        │
        ▼
Fact Checker :8002
        │
        │ dependency
        ▼
Research :8001
        │
        ├── Researcher
        ├── Analyst
        └── Reviewer
        │
        ▼
Fact Checker
        │
        ├── Fact Researcher
        └── Verification
        │
        ▼
Response
```

One distributed trace should cover the entire execution.

### Scenario B — Independent travel

```text
External Testing Platform
        │
        ▼
Travel :8004
        │
        ├── Planner
        ├── Search
        └── Booking
        │
        ▼
Response
```

No Research dependency.

### Scenario C — Marketing

```text
External Testing Platform
        │
        ▼
Marketing :8003
        │
        │ dependency
        ▼
Research :8001
        │
        ▼
Marketing Researcher
        │
        ▼
Strategist
        │
        ▼
Writer
```

This demonstrates independent deployment plus cross-module dependency.

---

## 19. Source-of-truth rules for Claude Code

Before modifying the repository:

1. Read this file completely.
2. Preserve the four modules: Research, Fact Checker, Marketing, Travel.
3. Never add GitHub back unless explicitly requested.
4. Research must be independently runnable.
5. Fact Checker must be able to depend on Research through a service boundary.
6. Marketing must be able to depend on Research through a service boundary.
7. Travel must remain independently runnable.
8. Every module must remain independently deployable.
9. Preserve distributed trace context across service calls.
10. Capture tokens and latency at call, agent, module, and application levels where possible.
11. Use OpenTelemetry/Langfuse rather than inventing a large custom telemetry stack.
12. Keep failure modes deterministic and testable.
13. Keep the project lightweight.
14. Do not introduce heavyweight infrastructure without a concrete requirement.
15. Run tests and compile checks after modifications.
16. Never claim a feature works without testing it.
17. If a proposed change conflicts with this document, explain the conflict before changing the architecture.

This document is the **source of truth for the intended project purpose and architecture**.
