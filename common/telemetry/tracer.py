"""Span recording: an in-process execution tree mirrored into OpenTelemetry.

The recorder is the single place business code touches. OpenTelemetry export is
optional and best-effort, so a missing collector never breaks a request.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from common.telemetry.context import TraceContext, get_trace_context, new_span_id
from common.telemetry.logging import get_logger
from common.telemetry.models import (
    LatencyBreakdown,
    SpanKind,
    SpanRecord,
    SpanStatus,
    TokenUsage,
    TraceTree,
    utc_now,
)

logger = get_logger(__name__)

_current_span: ContextVar[SpanRecord | None] = ContextVar("current_span", default=None)


class TraceStore:
    """Bounded, thread-safe ring buffer of completed traces.

    The testing platform reads this over HTTP to compare its own observations
    against what the application recorded.
    """

    def __init__(self, max_traces: int = 64) -> None:
        self._max = max_traces
        self._traces: OrderedDict[str, TraceTree] = OrderedDict()
        self._order: deque[str] = deque()
        self._lock = threading.Lock()

    def record(self, span: SpanRecord) -> None:
        with self._lock:
            tree = self._traces.get(span.trace_id)
            if tree is None:
                tree = TraceTree(
                    trace_id=span.trace_id,
                    request_id=span.request_id,
                    module_id=span.module_id,
                    service_id=span.service_id,
                    status=span.status,
                    started_at=span.started_at,
                )
                self._traces[span.trace_id] = tree
                self._order.append(span.trace_id)
                while len(self._order) > self._max:
                    evicted = self._order.popleft()
                    self._traces.pop(evicted, None)

            tree.spans.append(span)

            if span.tokens is not None and span.kind is SpanKind.LLM:
                tree.tokens = tree.tokens.merge(span.tokens)

            if span.kind is SpanKind.REQUEST:
                tree.status = span.status
                tree.ended_at = span.ended_at
                tree.duration_ms = span.duration_ms
            elif span.status is not SpanStatus.OK and tree.status is SpanStatus.OK:
                tree.status = span.status

    def get(self, trace_id: str) -> TraceTree | None:
        with self._lock:
            return self._traces.get(trace_id)

    def list(self, limit: int = 20) -> list[TraceTree]:
        with self._lock:
            ids = list(self._order)[-limit:][::-1]
            return [self._traces[i] for i in ids if i in self._traces]

    def clear(self) -> None:
        with self._lock:
            self._traces.clear()
            self._order.clear()

    def aggregate_tokens(self) -> TokenUsage:
        with self._lock:
            total = TokenUsage()
            for tree in self._traces.values():
                total = total.merge(tree.tokens)
            return total


class Recorder:
    """Creates spans for one service and fans them out to the sinks."""

    def __init__(self, module_id: str, service_id: str, store: TraceStore) -> None:
        self.module_id = module_id
        self.service_id = service_id
        self.store = store
        self._otel = None  # set by common.telemetry.otel.install_otel

    def bind_otel(self, tracer: Any) -> None:
        self._otel = tracer

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind,
        *,
        agent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        trace_context: TraceContext | None = None,
    ) -> Iterator[SpanRecord]:
        ctx = trace_context or get_trace_context()
        parent = _current_span.get()

        span = SpanRecord(
            span_id=new_span_id(),
            parent_span_id=parent.span_id if parent else ctx.parent_span_id,
            trace_id=ctx.trace_id,
            request_id=ctx.request_id,
            name=name,
            kind=kind,
            module_id=self.module_id,
            service_id=self.service_id,
            agent_id=agent_id,
            attributes=dict(attributes or {}),
        )

        token = _current_span.set(span)
        otel_cm = self._otel_span(span) if self._otel else _null_cm()

        try:
            with otel_cm as otel_span:
                try:
                    yield span
                except TimeoutError as exc:
                    _fail(span, exc, SpanStatus.TIMEOUT)
                    _mark_otel(otel_span, span)
                    raise
                except Exception as exc:  # noqa: BLE001 - recorded then re-raised
                    _fail(span, exc, SpanStatus.ERROR)
                    _mark_otel(otel_span, span)
                    raise
                else:
                    if span.ended_at is None:
                        span.close(span.status)
                    _mark_otel(otel_span, span)
        finally:
            _current_span.reset(token)
            if span.ended_at is None:
                span.close(span.status)
            self.store.record(span)
            logger.info(
                "span.end",
                extra={
                    "span_id": span.span_id,
                    "parent_span_id": span.parent_span_id,
                    "span_name": span.name,
                    "span_kind": span.kind.value,
                    "span_status": span.status.value,
                    "duration_ms": span.duration_ms,
                    "agent_id": span.agent_id,
                    "tokens": span.tokens.model_dump() if span.tokens else None,
                    "error_type": span.error_type,
                },
            )

    @contextmanager
    def _otel_span(self, span: SpanRecord) -> Iterator[Any]:
        try:
            with self._otel.start_as_current_span(span.name) as otel_span:  # type: ignore[union-attr]
                otel_span.set_attribute("sut.module_id", span.module_id)
                otel_span.set_attribute("sut.service_id", span.service_id)
                otel_span.set_attribute("sut.span_kind", span.kind.value)
                otel_span.set_attribute("sut.request_id", span.request_id)
                if span.agent_id:
                    otel_span.set_attribute("sut.agent_id", span.agent_id)
                yield otel_span
        except Exception:  # pragma: no cover - telemetry must never break a request
            logger.debug("otel span failed", exc_info=True)
            yield None


@contextmanager
def _null_cm() -> Iterator[None]:
    yield None


def _fail(span: SpanRecord, exc: BaseException, status: SpanStatus) -> None:
    span.error_type = type(exc).__name__
    span.error_message = str(exc)[:500]
    span.close(status)


def _mark_otel(otel_span: Any, span: SpanRecord) -> None:
    if otel_span is None:
        return
    try:
        if span.duration_ms is not None:
            otel_span.set_attribute("sut.duration_ms", span.duration_ms)
        if span.tokens:
            otel_span.set_attribute("gen_ai.usage.input_tokens", span.tokens.input_tokens)
            otel_span.set_attribute("gen_ai.usage.output_tokens", span.tokens.output_tokens)
            otel_span.set_attribute("sut.tokens.total", span.tokens.total_tokens)
            otel_span.set_attribute("sut.tokens.source", span.tokens.source.value)
            if span.tokens.cost_usd is not None:
                otel_span.set_attribute("sut.cost_usd", span.tokens.cost_usd)
        if span.model:
            otel_span.set_attribute("gen_ai.request.model", span.model)
        if span.status is not SpanStatus.OK:
            from opentelemetry.trace import Status, StatusCode

            otel_span.set_status(Status(StatusCode.ERROR, span.error_message or span.status.value))
            otel_span.set_attribute("sut.error_type", span.error_type or "")
    except Exception:  # pragma: no cover
        logger.debug("otel annotation failed", exc_info=True)


def current_span() -> SpanRecord | None:
    return _current_span.get()


def summarize_latency(spans: list[SpanRecord], total_ms: float) -> LatencyBreakdown:
    """Bucket self-time by span kind.

    ``total_ms`` stays the measured wall clock; category times are summed only
    over leaf-ish work, and ``overhead_ms`` is clamped at zero so overlapping
    concurrent spans can never produce a negative remainder.
    """
    llm_ms = sum(s.duration_ms or 0.0 for s in spans if s.kind is SpanKind.LLM)
    tool_ms = sum(s.duration_ms or 0.0 for s in spans if s.kind is SpanKind.TOOL)
    dep_ms = sum(s.duration_ms or 0.0 for s in spans if s.kind is SpanKind.SERVICE_CALL)
    accounted = llm_ms + tool_ms + dep_ms
    return LatencyBreakdown(
        total_ms=round(total_ms, 3),
        llm_ms=round(llm_ms, 3),
        tool_ms=round(tool_ms, 3),
        dependency_ms=round(dep_ms, 3),
        overhead_ms=round(max(total_ms - accounted, 0.0), 3),
    )
