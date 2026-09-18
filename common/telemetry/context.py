"""Distributed trace context that survives the HTTP boundary.

Uses W3C Trace Context (``traceparent``) so the same trace id spans
Research -> Fact Checker / Marketing, and so any OpenTelemetry-aware collector
stitches the services together without custom glue.
"""

from __future__ import annotations

import os
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"
REQUEST_ID_HEADER = "x-request-id"

_TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def new_trace_id() -> str:
    return os.urandom(16).hex()


def new_span_id() -> str:
    return os.urandom(8).hex()


def new_request_id() -> str:
    return f"req_{os.urandom(8).hex()}"


@dataclass
class TraceContext:
    """Identity of the in-flight request, propagated across services."""

    trace_id: str = field(default_factory=new_trace_id)
    span_id: str = field(default_factory=new_span_id)
    request_id: str = field(default_factory=new_request_id)
    parent_span_id: str | None = None
    sampled: bool = True
    tracestate: str | None = None

    def to_traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"

    def to_headers(self) -> dict[str, str]:
        headers = {
            TRACEPARENT_HEADER: self.to_traceparent(),
            REQUEST_ID_HEADER: self.request_id,
        }
        if self.tracestate:
            headers[TRACESTATE_HEADER] = self.tracestate
        return headers

    def child(self) -> "TraceContext":
        """A context for an outbound call: same trace, this span as parent."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=new_span_id(),
            request_id=self.request_id,
            parent_span_id=self.span_id,
            sampled=self.sampled,
            tracestate=self.tracestate,
        )

    @classmethod
    def from_headers(cls, headers: dict[str, str] | None) -> "TraceContext":
        """Continue an upstream trace when the headers carry one, else start fresh."""
        if not headers:
            return cls()

        lowered = {k.lower(): v for k, v in headers.items()}
        request_id = lowered.get(REQUEST_ID_HEADER) or new_request_id()
        traceparent = lowered.get(TRACEPARENT_HEADER)

        if traceparent:
            match = _TRACEPARENT_RE.match(traceparent.strip().lower())
            if match:
                trace_id, parent_span_id, flags = match.groups()
                # all-zero ids are invalid per spec; fall through to a new trace
                if trace_id != "0" * 32 and parent_span_id != "0" * 16:
                    return cls(
                        trace_id=trace_id,
                        span_id=new_span_id(),
                        request_id=request_id,
                        parent_span_id=parent_span_id,
                        sampled=bool(int(flags, 16) & 0x01),
                        tracestate=lowered.get(TRACESTATE_HEADER),
                    )

        return cls(request_id=request_id)


_current: ContextVar[TraceContext | None] = ContextVar("trace_context", default=None)


def get_trace_context() -> TraceContext:
    ctx = _current.get()
    if ctx is None:
        ctx = TraceContext()
        _current.set(ctx)
    return ctx


def set_trace_context(ctx: TraceContext) -> Token:
    return _current.set(ctx)


def reset_trace_context(token: Token) -> None:
    _current.reset(token)


class use_trace_context:
    """Context manager binding a :class:`TraceContext` to the current task."""

    def __init__(self, ctx: TraceContext) -> None:
        self._ctx = ctx
        self._token: Token | None = None

    def __enter__(self) -> TraceContext:
        self._token = set_trace_context(self._ctx)
        return self._ctx

    def __exit__(self, *exc_info: object) -> None:
        if self._token is not None:
            reset_trace_context(self._token)
            self._token = None
