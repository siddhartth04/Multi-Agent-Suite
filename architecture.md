# Architecture

This document explains *why* the system is shaped the way it is. For how to run it, see [README.md](README.md).

## The central idea

> Independent deployment does not mean independent dependency.

All four modules are separately deployable — separate processes, images, hosts. That is orthogonal to whether one calls another. Fact Checker and Marketing both depend on Research *and* deploy independently of it. Travel depends on nothing. That mix is deliberate: it gives the testing platform independent modules, cross-module network dependencies, and multi-agent pipelines in one application.

## Layers

```text
┌──────────────────────────────────────────────┐
│ modules/<name>/module.py                     │  what this module IS (data)
│   agent specs, capabilities, dependencies    │
├──────────────────────────────────────────────┤
│ common/service.py      HTTP contract         │  how every module RUNS
│ common/module_base.py  orchestration         │
│ common/agents/         pipeline + LLM        │
│ common/telemetry/      spans, tokens, logs   │
│ common/http_client.py  cross-service calls   │
└──────────────────────────────────────────────┘
```

A module declares agents as data, so `/metadata` can describe exactly what will run without executing anything — the testing platform can verify the declared topology against observed execution. Adding a module is one definition file plus a three-line entry point.

## Request flow

```text
POST /run
  │
  ├─ TraceContext.from_headers()      continue the caller's trace, or start one
  ├─ resolve failure mode             request override > service default
  ├─ span: <module>.request
  │    ├─ failure injection
  │    ├─ span: service_call.<dep>    → HTTP, traceparent forwarded, retries
  │    ├─ span: agent.<id>            per agent, in declared order
  │    │    ├─ span: tool.<name>
  │    │    └─ span: llm.<id>         tokens + cost captured here
  │    └─ aggregate tokens
  └─ RunResult                        result + agents + dependencies + tokens + latency
```

Every exit path produces a `RunResult`. A failure is not an error shape — it is the same document with `status` set, the agents that already ran still listed, and their tokens still counted. The testing platform can therefore compare expected against observed behaviour even for failures.

## Design decisions

**Network boundaries, never imports.** A dependent module calls `http://research:8000/run`. Nothing imports another module's agents, so services can move hosts without code changes. URLs come only from environment variables.

**Provider-neutral LLM access.** `litellm` means one code path serves Gemini, OpenAI, Anthropic, Groq or a local Ollama server. Swapping providers is a `.env` edit.

**Telemetry is a library, not a coupling.** Business code calls `recorder.span(...)`. Where spans go is a separate concern: always in-process for `/telemetry/*`, and optionally to OTLP or Langfuse. With no exporter configured the application is fully observable through its own endpoints.

**Honest measurement.** Three rules the code enforces:

1. Token counts carry a `source` (`provider` / `estimated` / `unavailable`). Estimates are never presented as exact, and unreported categories stay `null`, not `0`.
2. Wall-clock latency is measured, never summed from children — parallel work would otherwise be double counted. `overhead_ms` clamps at zero.
3. Failures record their real exception type. Because the runtime catches exceptions to guarantee a well-formed response, the error identity is attached to the span explicitly, and wrapper exceptions are unwrapped to their cause.

**Deterministic failures.** Injected failures are configuration, not chance, so a test that fails once fails the same way every time.

**Optional dependencies degrade.** Research being unreachable does not fail a Fact Checker request: the module continues with its own evidence gathering and records the failed call with its retry count. `required=True` on a dependency would make it fatal instead.

## Distributed tracing

Trace context is W3C `traceparent`, so any OTel-aware collector stitches services together with no custom glue.

```text
Client ──traceparent: 00-6f9073…-a1b2…-01──▶ Fact Checker :8002
                                                  │  same trace id
                                                  ▼
                                            Research :8001
```

Both services record spans under one trace id. Each keeps its own segment; the gateway's `/telemetry/traces/{trace_id}` fans out and assembles them. Verified end to end: a single request produced 17 spans across two separate OS processes, including three visible retry attempts.

## Scaling the deployment

The service boundaries already match deployment boundaries, so the same code runs unchanged as:

| Target | What changes |
|---|---|
| Separate terminals | Nothing — different ports |
| Docker Compose | `*_URL` point at service names |
| Separate VMs | `*_URL` point at hostnames |
| Kubernetes | `*_URL` point at Services; `/health` drives probes |

No Kafka, Redis, Postgres or vector DB is required. The demo is meant to stay lightweight.
