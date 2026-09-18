"""FastAPI service factory.

Every module gets the identical contract -- ``/health``, ``/metadata``, ``/run``
and the ``/telemetry`` endpoints -- so the external platform onboards all four
services the same way.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from common.config import Settings, get_settings
from common.module_base import ModuleDefinition, ModuleRuntime
from common.telemetry import (
    HealthResponse,
    MetadataResponse,
    REQUEST_ID_HEADER,
    RunResult,
    SpanStatus,
    TraceContext,
    TraceTree,
    configure_logging,
    get_logger,
    install_otel,
    instrument_app,
    use_trace_context,
)
from common.telemetry.models import TokenUsage

logger = get_logger(__name__)


class RunRequest(BaseModel):
    """The `/run` body. Only ``input`` is required."""

    input: str = Field(..., min_length=1, description="The module's primary input")
    context: str | None = Field(default=None, description="Optional caller-supplied context")
    failure_mode: str | None = Field(
        default=None, description="Override the service failure mode for this request"
    )
    use_dependencies: bool = Field(
        default=True, description="Set false to run this module without calling upstream modules"
    )


def create_service(definition: ModuleDefinition, settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app for one module."""
    settings = settings or get_settings()
    configure_logging(definition.service_id, definition.module_id, settings.log_level)

    runtime = ModuleRuntime(definition, settings)

    tracer = install_otel(settings, definition.service_id, definition.module_id)
    if tracer is not None:
        runtime.recorder.bind_otel(tracer)

    app = FastAPI(
        title=f"{definition.service_id} ({settings.application_id})",
        description=definition.description,
        version=settings.application_version,
    )
    app.state.runtime = runtime
    app.state.settings = settings

    if tracer is not None:
        instrument_app(app)

    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        dependency_status = {
            d.module_id: settings.service_url(d.module_id) for d in definition.depends_on
        }
        return HealthResponse(
            status="ok" if settings.llm_configured else "degraded",
            application_id=settings.application_id,
            module_id=definition.module_id,
            service_id=definition.service_id,
            version=settings.application_version,
            deployment_id=settings.deployment_id,
            uptime_seconds=runtime.uptime_seconds,
            llm_configured=settings.llm_configured,
            agent_engine="llm",
            failure_mode=settings.failure_mode,
            dependencies=dependency_status,
        )

    @app.get("/metadata", response_model=MetadataResponse, tags=["discovery"])
    async def metadata() -> MetadataResponse:
        return runtime.metadata()

    @app.post("/run", response_model=RunResult, tags=["execution"])
    async def run(req: RunRequest, request: Request, response: Response) -> RunResult:
        ctx = TraceContext.from_headers(dict(request.headers))
        with use_trace_context(ctx):
            result = await runtime.run(
                req.input,
                external_context=req.context,
                failure_mode=req.failure_mode,
                use_dependencies=req.use_dependencies,
            )

        response.headers["traceparent"] = ctx.to_traceparent()
        response.headers[REQUEST_ID_HEADER] = ctx.request_id
        # A handled failure is still a complete, well-formed telemetry document,
        # so it is returned with a status code rather than an error shape.
        if result.status is SpanStatus.TIMEOUT:
            response.status_code = 504
        elif result.status is SpanStatus.ERROR:
            response.status_code = 500
        return result

    @app.get("/telemetry/traces", tags=["observability"])
    async def list_traces(limit: int = 20) -> dict[str, Any]:
        traces = runtime.store.list(limit=limit)
        return {
            "service_id": definition.service_id,
            "module_id": definition.module_id,
            "count": len(traces),
            "aggregate_tokens": runtime.store.aggregate_tokens().model_dump(),
            "traces": [t.model_dump(mode="json") for t in traces],
        }

    @app.get("/telemetry/traces/{trace_id}", response_model=TraceTree, tags=["observability"])
    async def get_trace(trace_id: str) -> TraceTree:
        trace = runtime.store.get(trace_id)
        if trace is None:
            raise HTTPException(404, f"No trace {trace_id} recorded by {definition.service_id}")
        return trace

    @app.get("/telemetry/tokens", tags=["observability"])
    async def token_totals() -> dict[str, Any]:
        total: TokenUsage = runtime.store.aggregate_tokens()
        return {
            "module_id": definition.module_id,
            "service_id": definition.service_id,
            "traces_observed": len(runtime.store.list(limit=settings.trace_buffer_size)),
            "tokens": total.model_dump(),
        }

    @app.delete("/telemetry/traces", tags=["observability"])
    async def clear_traces() -> dict[str, str]:
        runtime.store.clear()
        return {"status": "cleared", "service_id": definition.service_id}

    return app
