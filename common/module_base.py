"""The module runtime shared by all four services.

A module declares *what* it is via :class:`ModuleDefinition` -- its agents, its
optional upstream dependency, its capabilities -- and this runtime supplies the
*how*: trace context, spans, token and latency roll-up, failure injection and
error handling.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Sequence

from common.agents import AgentPipeline, AgentSpec, PipelineContext, PipelineError
from common.config import Settings
from common.failures import (
    apply_entry_failure,
    check_dependency_failure,
    check_tool_failure,
    resolve_mode,
)
from common.http_client import DependencyClient, DependencyError
from common.telemetry import (
    AgentExecution,
    DependencyCall,
    DependencyDescriptor,
    MetadataResponse,
    Recorder,
    RunResult,
    SpanKind,
    SpanStatus,
    TokenUsage,
    TraceStore,
    get_logger,
    get_trace_context,
    summarize_latency,
    utc_now,
)
from common.tools import make_web_search

logger = get_logger(__name__)

ContextBuilder = Callable[[str, dict], Awaitable[str | None]]


@dataclass
class ModuleDefinition:
    """Everything that makes one module distinct from the others."""

    module_id: str
    service_id: str
    description: str
    input_name: str
    input_description: str
    capabilities: list[str]
    agents: Sequence[AgentSpec]
    depends_on: list[DependencyDescriptor] = field(default_factory=list)
    dependency_prompt: str = "Context from the upstream module"

    @property
    def independent(self) -> bool:
        return not self.depends_on


class ModuleRuntime:
    """Executes a module's pipeline with full telemetry."""

    def __init__(self, definition: ModuleDefinition, settings: Settings) -> None:
        self.definition = definition
        self.settings = settings
        self.started_at = time.monotonic()

        self.store = TraceStore(max_traces=settings.trace_buffer_size)
        self.recorder = Recorder(definition.module_id, definition.service_id, self.store)
        self.dependencies = DependencyClient(settings, self.recorder)

        self.pipeline = AgentPipeline(
            specs=definition.agents,
            settings=settings,
            recorder=self.recorder,
            tools={"web_search": make_web_search(settings)},
        )

    # ------------------------------------------------------------------ run
    async def run(
        self,
        user_input: str,
        *,
        external_context: str | None = None,
        failure_mode: str | None = None,
        use_dependencies: bool = True,
    ) -> RunResult:
        """Execute the module end to end and always return a telemetry-complete result."""
        ctx = get_trace_context()
        mode = resolve_mode(failure_mode, self.settings)
        started_at = utc_now()
        wall_start = time.perf_counter()

        agents: list[AgentExecution] = []
        dependency_calls: list[DependencyCall] = []
        status = SpanStatus.OK
        result_text: str | None = None
        error_text: str | None = None

        logger.info(
            "request.start",
            extra={
                "module_id": self.definition.module_id,
                "failure_mode": mode,
                "input_chars": len(user_input),
                "use_dependencies": use_dependencies,
            },
        )

        with self.recorder.span(
            f"{self.definition.module_id}.request",
            SpanKind.REQUEST,
            attributes={
                "module_id": self.definition.module_id,
                "failure_mode": mode,
                "input_chars": len(user_input),
            },
        ) as request_span:
            try:
                await apply_entry_failure(mode, self.settings)
                check_tool_failure(mode)

                upstream, dependency_calls = await self._gather_dependencies(
                    user_input, mode, use_dependencies
                )
                merged_context = _merge_context(external_context, upstream)

                pipeline_context = PipelineContext(
                    user_input=user_input, external_context=merged_context
                )
                agents, result_text = await self.pipeline.run(pipeline_context)

            except PipelineError as exc:
                agents = exc.executions
                status, error_text = SpanStatus.ERROR, str(exc)
                _record_error(request_span, exc, SpanStatus.ERROR)
            except DependencyError as exc:
                dependency_calls.append(exc.call)
                status = exc.call.status
                error_text = str(exc)
                _record_error(request_span, exc, status)
            except TimeoutError as exc:
                status, error_text = SpanStatus.TIMEOUT, str(exc)
                _record_error(request_span, exc, SpanStatus.TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - always answer with telemetry
                status, error_text = SpanStatus.ERROR, f"{type(exc).__name__}: {exc}"
                _record_error(request_span, exc, SpanStatus.ERROR)
                logger.exception("request.unhandled_error")

            tokens = self._aggregate_tokens(agents, dependency_calls)
            request_span.tokens = tokens
            request_span.attributes["agents_run"] = len(agents)
            request_span.attributes["dependencies_called"] = len(dependency_calls)

        total_ms = (time.perf_counter() - wall_start) * 1000
        trace = self.store.get(ctx.trace_id)
        latency = summarize_latency(trace.spans if trace else [], total_ms)

        logger.info(
            "request.end",
            extra={
                "module_id": self.definition.module_id,
                "status": status.value,
                "duration_ms": round(total_ms, 3),
                "total_tokens": tokens.total_tokens,
                "token_source": tokens.source.value,
            },
        )

        return RunResult(
            application_id=self.settings.application_id,
            module_id=self.definition.module_id,
            service_id=self.definition.service_id,
            version=self.settings.application_version,
            deployment_id=self.settings.deployment_id,
            trace_id=ctx.trace_id,
            request_id=ctx.request_id,
            status=status,
            result=result_text,
            error=error_text,
            agents=agents,
            dependencies=dependency_calls,
            tokens=tokens,
            latency=latency,
            failure_mode=mode,
            started_at=started_at,
            ended_at=utc_now(),
        )

    # ---------------------------------------------------------- dependencies
    async def _gather_dependencies(
        self, user_input: str, mode: str, enabled: bool
    ) -> tuple[str | None, list[DependencyCall]]:
        """Call every declared upstream module and collect its context."""
        calls: list[DependencyCall] = []
        if not self.definition.depends_on or not enabled:
            return None, calls

        check_dependency_failure(mode)

        fragments: list[str] = []
        for dependency in self.definition.depends_on:
            try:
                body, call = await self.dependencies.run_module(
                    dependency.module_id,
                    {"input": user_input, "failure_mode": "normal"},
                )
            except DependencyError as exc:
                calls.append(exc.call)
                if dependency.required:
                    raise
                logger.warning(
                    "dependency.optional_failed",
                    extra={"module_id": dependency.module_id, "error": str(exc)[:200]},
                )
                continue

            calls.append(call)
            text = body.get("result")
            if text:
                fragments.append(f"[{dependency.module_id}]\n{text}")

        return ("\n\n".join(fragments) or None), calls

    # --------------------------------------------------------------- tokens
    def _aggregate_tokens(
        self, agents: list[AgentExecution], dependencies: list[DependencyCall]
    ) -> TokenUsage:
        """Roll agent usage up, then add any usage reported by downstream modules."""
        total = TokenUsage()
        for agent in agents:
            total = total.merge(agent.tokens)
        for call in dependencies:
            if call.tokens is not None:
                total = total.merge(call.tokens)
        return total

    # ------------------------------------------------------------- metadata
    def metadata(self) -> MetadataResponse:
        from common.failures import SUPPORTED_MODES

        return MetadataResponse(
            application_id=self.settings.application_id,
            application_version=self.settings.application_version,
            module_id=self.definition.module_id,
            service_id=self.definition.service_id,
            version=self.settings.application_version,
            deployment_id=self.settings.deployment_id,
            environment=self.settings.environment,
            description=self.definition.description,
            input_name=self.definition.input_name,
            input_description=self.definition.input_description,
            capabilities=list(self.definition.capabilities),
            agents=self.pipeline.descriptors(),
            agent_pipeline=self.pipeline.agent_ids,
            dependencies=[
                DependencyDescriptor(
                    module_id=d.module_id,
                    transport=d.transport,
                    url=self.settings.service_url(d.module_id),
                    required=d.required,
                    description=d.description,
                )
                for d in self.definition.depends_on
            ],
            independent=self.definition.independent,
            endpoints=["/health", "/metadata", "/run", "/telemetry/traces", "/telemetry/traces/{trace_id}"],
            supported_failure_modes=list(SUPPORTED_MODES),
            model=self.settings.model,
            agent_engine="llm",
        )

    @property
    def uptime_seconds(self) -> float:
        return round(time.monotonic() - self.started_at, 3)


def _merge_context(caller_context: str | None, upstream: str | None) -> str | None:
    parts = [p for p in (caller_context, upstream) if p]
    return "\n\n".join(parts) or None


def _record_error(span, exc: BaseException, status: SpanStatus) -> None:
    """Stamp the failure onto the request span.

    The runtime handles these exceptions so it can always return a complete
    telemetry document, which means the span never sees them raised -- so the
    error identity has to be attached explicitly or it is lost. ``PipelineError``
    and ``DependencyError`` are wrappers, so report the original cause.
    """
    cause = exc.__cause__ if isinstance(exc, (PipelineError, DependencyError)) else None
    reported = cause or exc

    span.error_type = type(reported).__name__
    span.error_message = str(reported)[:500]
    span.status = status
