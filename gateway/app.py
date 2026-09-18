"""Gateway: one onboarding surface in front of the four module services.

The gateway is a thin router. It never imports a module's agents -- every call
crosses the network, so the modules stay independently deployable and the
gateway only needs their URLs.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from common.config import APPLICATION_ID, APPLICATION_VERSION, get_settings
from common.http_client import check_health
from common.telemetry import (
    REQUEST_ID_HEADER,
    TraceContext,
    configure_logging,
    get_logger,
    install_otel,
    instrument_app,
    use_trace_context,
)
from gateway.registry import BY_ID, REGISTRY, agent_edges, dependency_edges, service_urls

settings = get_settings()
configure_logging("gateway", "gateway", settings.log_level)
logger = get_logger(__name__)

app = FastAPI(
    title=f"Modular Multi-Agent Gateway ({APPLICATION_ID})",
    description="Discovery and routing for the independently deployed agent modules.",
    version=APPLICATION_VERSION,
)

_tracer = install_otel(settings, "gateway", "gateway")
if _tracer is not None:
    instrument_app(app)


class WorkflowRequest(BaseModel):
    input: str = Field(..., min_length=1)
    context: str | None = None
    failure_mode: str | None = None
    use_dependencies: bool = True


async def _forward(module_id: str, req: WorkflowRequest, response: Response) -> dict[str, Any]:
    """Proxy a workflow call to its module, preserving the trace context."""
    ctx = TraceContext.from_headers({})
    url = f"{settings.service_url(module_id)}/run"
    payload = req.model_dump()

    with use_trace_context(ctx):
        logger.info("gateway.forward", extra={"module_id": module_id, "url": url})
        try:
            timeout = httpx.Timeout(
                settings.dependency_timeout_seconds,
                connect=settings.dependency_connect_timeout_seconds,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                upstream = await client.post(url, json=payload, headers=ctx.to_headers())
        except httpx.TimeoutException as exc:
            raise HTTPException(504, f"{module_id} timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"{module_id} unavailable: {exc}") from exc

    response.headers["traceparent"] = ctx.to_traceparent()
    response.headers[REQUEST_ID_HEADER] = ctx.request_id
    # Pass the module's own status through so a handled failure stays visible.
    if upstream.status_code >= 400:
        response.status_code = upstream.status_code

    try:
        return upstream.json()
    except ValueError as exc:
        raise HTTPException(502, f"{module_id} returned a non-JSON body") from exc


@app.get("/health", tags=["operations"])
async def health() -> dict[str, Any]:
    """Aggregate health. Probes all modules concurrently."""
    urls = service_urls(settings)
    results = await asyncio.gather(*(check_health(url) for url in urls.values()))
    services = dict(zip(urls.keys(), results))
    reachable = sum(1 for s in services.values() if s.get("status") == "ok")

    return {
        "gateway": "ok",
        "application_id": APPLICATION_ID,
        "version": APPLICATION_VERSION,
        "modules_total": len(urls),
        "modules_reachable": reachable,
        "status": "ok" if reachable == len(urls) else "degraded",
        "services": services,
    }


@app.get("/topology", tags=["discovery"])
def topology() -> dict[str, Any]:
    """The declared application graph: modules, agents and dependency edges."""
    return {
        "application_id": APPLICATION_ID,
        "version": APPLICATION_VERSION,
        "deployment_id": settings.deployment_id,
        "environment": settings.environment,
        "modules": {
            m.module_id: {
                "service_id": m.service_id,
                "url": settings.service_url(m.module_id),
                "default_port": m.default_port,
                "description": m.description,
                "agents": list(m.agents),
                "agent_pipeline": " -> ".join(m.agents),
                "capabilities": list(m.capabilities),
                "depends_on": list(m.depends_on),
                "independent": m.independent,
                "workflow_path": m.workflow_path,
            }
            for m in REGISTRY
        },
        "module_dependencies": dependency_edges(),
        "agent_dependencies": agent_edges(),
        "independent_modules": [m.module_id for m in REGISTRY if m.independent],
        "services": service_urls(settings),
    }


@app.get("/discovery", tags=["discovery"])
async def discovery() -> dict[str, Any]:
    """Live discovery: each module's own `/metadata`, fetched concurrently.

    Where `/topology` is what the gateway declares, this is what the services
    report about themselves -- the two should agree.
    """

    async def fetch(module_id: str) -> dict[str, Any]:
        url = f"{settings.service_url(module_id)}/metadata"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except Exception as exc:  # noqa: BLE001
            return {"module_id": module_id, "error": f"{type(exc).__name__}: {exc}"[:200], "url": url}

    module_ids = [m.module_id for m in REGISTRY]
    results = await asyncio.gather(*(fetch(mid) for mid in module_ids))
    return {
        "application_id": APPLICATION_ID,
        "version": APPLICATION_VERSION,
        "modules": dict(zip(module_ids, results)),
    }


@app.get("/telemetry/traces/{trace_id}", tags=["observability"])
async def distributed_trace(trace_id: str) -> dict[str, Any]:
    """Assemble one distributed trace from every service that saw it."""

    async def fetch(module_id: str) -> tuple[str, dict[str, Any] | None]:
        url = f"{settings.service_url(module_id)}/telemetry/traces/{trace_id}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    return module_id, None
                response.raise_for_status()
                return module_id, response.json()
        except Exception:  # noqa: BLE001
            return module_id, None

    results = await asyncio.gather(*(fetch(m.module_id) for m in REGISTRY))
    segments = {mid: data for mid, data in results if data is not None}
    if not segments:
        raise HTTPException(404, f"No service reported trace {trace_id}")

    spans = [span for seg in segments.values() for span in seg.get("spans", [])]
    total_tokens = sum(
        (s.get("tokens") or {}).get("total_tokens", 0)
        for s in spans
        if s.get("kind") == "llm"
    )

    return {
        "trace_id": trace_id,
        "services_involved": sorted(segments.keys()),
        "span_count": len(spans),
        "total_tokens": total_tokens,
        "segments": segments,
    }


def _make_workflow(module_id: str):
    async def workflow(req: WorkflowRequest, request: Request, response: Response) -> dict[str, Any]:
        return await _forward(module_id, req, response)

    workflow.__name__ = f"workflow_{module_id}"
    return workflow


for _registration in REGISTRY:
    app.add_api_route(
        _registration.workflow_path,
        _make_workflow(_registration.module_id),
        methods=["POST"],
        tags=["workflows"],
        summary=f"Run the {_registration.module_id} module",
        description=_registration.description,
    )


@app.get("/", tags=["discovery"])
def root() -> dict[str, Any]:
    return {
        "application_id": APPLICATION_ID,
        "version": APPLICATION_VERSION,
        "endpoints": ["/health", "/topology", "/discovery", "/telemetry/traces/{trace_id}", "/docs"]
        + [m.workflow_path for m in REGISTRY],
    }
