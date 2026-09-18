"""Typed telemetry records.

These models are the contract the external testing platform reads. They are
intentionally provider-neutral: token fields carry an explicit ``source`` so a
consumer can tell a reported count from an estimated one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SpanKind(str, Enum):
    REQUEST = "request"
    MODULE = "module"
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    SERVICE_CALL = "service_call"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


class TokenSource(str, Enum):
    """Where a token count came from. Never fabricate counts; say so instead."""

    PROVIDER = "provider"       # reported by the LLM provider
    ESTIMATED = "estimated"     # locally tokenized approximation
    UNAVAILABLE = "unavailable" # not obtainable


class TokenUsage(BaseModel):
    """Token accounting for one LLM call, or an aggregate of several."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    source: TokenSource = TokenSource.UNAVAILABLE

    def merge(self, other: "TokenUsage") -> "TokenUsage":
        """Combine two usage records, degrading ``source`` to the weakest input."""
        if other.source is TokenSource.UNAVAILABLE and self.source is TokenSource.UNAVAILABLE:
            source = TokenSource.UNAVAILABLE
        elif TokenSource.ESTIMATED in (self.source, other.source):
            source = TokenSource.ESTIMATED
        else:
            source = TokenSource.PROVIDER

        def _add(a: int | None, b: int | None) -> int | None:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_input_tokens=_add(self.cached_input_tokens, other.cached_input_tokens),
            reasoning_tokens=_add(self.reasoning_tokens, other.reasoning_tokens),
            cost_usd=_add_float(self.cost_usd, other.cost_usd),
            source=source,
        )


def _add_float(a: float | None, b: float | None) -> float | None:
    if a is None and b is None:
        return None
    return round((a or 0.0) + (b or 0.0), 8)


class SpanRecord(BaseModel):
    """One node of the execution tree."""

    span_id: str
    parent_span_id: str | None = None
    trace_id: str
    request_id: str
    name: str
    kind: SpanKind
    status: SpanStatus = SpanStatus.OK

    module_id: str
    service_id: str
    agent_id: str | None = None

    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: float | None = None

    tokens: TokenUsage | None = None
    model: str | None = None

    attributes: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    retry_count: int = 0

    def close(self, status: SpanStatus = SpanStatus.OK) -> None:
        self.ended_at = utc_now()
        self.duration_ms = round((self.ended_at - self.started_at).total_seconds() * 1000, 3)
        self.status = status


class LatencyBreakdown(BaseModel):
    """Wall-clock latency plus per-category self time.

    ``total_ms`` is measured wall clock, never a sum of children, so parallel
    work is not double counted.
    """

    total_ms: float
    llm_ms: float = 0.0
    tool_ms: float = 0.0
    dependency_ms: float = 0.0
    overhead_ms: float = 0.0


class AgentExecution(BaseModel):
    agent_id: str
    role: str
    status: SpanStatus
    duration_ms: float
    tokens: TokenUsage
    model: str | None = None
    output: str | None = None
    error: str | None = None


class DependencyCall(BaseModel):
    module_id: str
    url: str
    status: SpanStatus
    duration_ms: float
    retry_count: int = 0
    http_status: int | None = None
    error: str | None = None
    tokens: TokenUsage | None = None


class RunResult(BaseModel):
    """The `/run` response body: result plus everything needed to verify it."""

    application_id: str
    module_id: str
    service_id: str
    version: str
    deployment_id: str

    trace_id: str
    request_id: str

    status: SpanStatus
    result: str | None = None
    error: str | None = None

    agents: list[AgentExecution] = Field(default_factory=list)
    dependencies: list[DependencyCall] = Field(default_factory=list)

    tokens: TokenUsage = Field(default_factory=TokenUsage)
    latency: LatencyBreakdown
    failure_mode: str = "normal"

    started_at: datetime
    ended_at: datetime


class TraceTree(BaseModel):
    """A whole trace as captured by one service, for `/telemetry/traces`."""

    trace_id: str
    request_id: str
    module_id: str
    service_id: str
    status: SpanStatus
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    spans: list[SpanRecord] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    application_id: str
    module_id: str
    service_id: str
    version: str
    deployment_id: str
    uptime_seconds: float
    llm_configured: bool
    agent_engine: str
    failure_mode: str
    dependencies: dict[str, str] = Field(default_factory=dict)


class AgentDescriptor(BaseModel):
    agent_id: str
    role: str
    goal: str
    depends_on: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class DependencyDescriptor(BaseModel):
    module_id: str
    transport: Literal["http"] = "http"
    url: str
    required: bool = False
    description: str = ""


class MetadataResponse(BaseModel):
    """Onboarding/discovery contract (PROJECT_CONTEXT section 7)."""

    application_id: str
    application_version: str
    module_id: str
    service_id: str
    version: str
    deployment_id: str
    environment: str

    description: str
    input_name: str
    input_description: str

    capabilities: list[str]
    agents: list[AgentDescriptor]
    agent_pipeline: list[str]
    dependencies: list[DependencyDescriptor]
    independent: bool

    endpoints: list[str]
    supported_failure_modes: list[str]
    model: str
    agent_engine: str
