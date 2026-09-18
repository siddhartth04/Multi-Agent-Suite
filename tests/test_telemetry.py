"""Unit tests for trace context, spans, tokens and latency."""

from __future__ import annotations

import pytest

from common.telemetry import (
    Recorder,
    SpanKind,
    SpanStatus,
    TokenSource,
    TokenUsage,
    TraceContext,
    TraceStore,
    summarize_latency,
    use_trace_context,
)


class TestTraceContext:
    def test_traceparent_round_trip_preserves_trace_id(self) -> None:
        parent = TraceContext()
        child = TraceContext.from_headers(parent.to_headers())

        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id
        assert child.span_id != parent.span_id
        assert child.request_id == parent.request_id

    def test_no_headers_starts_a_new_trace(self) -> None:
        ctx = TraceContext.from_headers(None)
        assert len(ctx.trace_id) == 32
        assert ctx.parent_span_id is None

    @pytest.mark.parametrize(
        "bad",
        [
            "garbage",
            "00-tooshort-0000000000000001-01",
            f"00-{'0' * 32}-{'0' * 16}-01",  # all-zero ids are invalid per spec
            "",
        ],
    )
    def test_malformed_traceparent_starts_a_new_trace(self, bad: str) -> None:
        ctx = TraceContext.from_headers({"traceparent": bad})
        assert len(ctx.trace_id) == 32
        assert ctx.trace_id != "0" * 32

    def test_child_keeps_trace_and_links_parent(self) -> None:
        parent = TraceContext()
        child = parent.child()

        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id
        assert child.request_id == parent.request_id

    def test_headers_are_case_insensitive(self) -> None:
        parent = TraceContext()
        upper = {k.upper(): v for k, v in parent.to_headers().items()}
        assert TraceContext.from_headers(upper).trace_id == parent.trace_id

    def test_sampled_flag_round_trips(self) -> None:
        ctx = TraceContext(sampled=False)
        assert ctx.to_traceparent().endswith("-00")
        assert TraceContext.from_headers(ctx.to_headers()).sampled is False


class TestTokenUsage:
    def test_merge_adds_counts(self) -> None:
        a = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15, source=TokenSource.PROVIDER)
        b = TokenUsage(input_tokens=7, output_tokens=3, total_tokens=10, source=TokenSource.PROVIDER)
        merged = a.merge(b)

        assert (merged.input_tokens, merged.output_tokens, merged.total_tokens) == (17, 8, 25)
        assert merged.source is TokenSource.PROVIDER

    def test_merging_an_estimate_degrades_the_source(self) -> None:
        provider = TokenUsage(input_tokens=10, total_tokens=10, source=TokenSource.PROVIDER)
        estimated = TokenUsage(input_tokens=5, total_tokens=5, source=TokenSource.ESTIMATED)

        assert provider.merge(estimated).source is TokenSource.ESTIMATED

    def test_two_unavailable_stay_unavailable(self) -> None:
        empty = TokenUsage()
        assert empty.merge(TokenUsage()).source is TokenSource.UNAVAILABLE

    def test_costs_add_and_stay_none_when_both_unknown(self) -> None:
        a = TokenUsage(cost_usd=0.001, source=TokenSource.PROVIDER)
        b = TokenUsage(cost_usd=0.002, source=TokenSource.PROVIDER)
        assert a.merge(b).cost_usd == pytest.approx(0.003)
        assert TokenUsage().merge(TokenUsage()).cost_usd is None


class TestRecorder:
    def test_span_is_recorded_with_timing(self) -> None:
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        with use_trace_context(TraceContext()) as ctx:
            with recorder.span("test", SpanKind.AGENT, agent_id="researcher"):
                pass

        trace = store.get(ctx.trace_id)
        assert trace is not None and len(trace.spans) == 1

        span = trace.spans[0]
        assert span.status is SpanStatus.OK
        assert span.duration_ms is not None and span.duration_ms >= 0
        assert span.agent_id == "researcher"

    def test_nested_spans_link_parent_to_child(self) -> None:
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        with use_trace_context(TraceContext()) as ctx:
            with recorder.span("parent", SpanKind.REQUEST) as parent:
                with recorder.span("child", SpanKind.AGENT) as child:
                    assert child.parent_span_id == parent.span_id

        spans = {s.name: s for s in store.get(ctx.trace_id).spans}
        assert spans["child"].parent_span_id == spans["parent"].span_id

    def test_exception_is_recorded_then_re_raised(self) -> None:
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        with use_trace_context(TraceContext()) as ctx:
            with pytest.raises(ValueError):
                with recorder.span("boom", SpanKind.AGENT):
                    raise ValueError("kaboom")

        span = store.get(ctx.trace_id).spans[0]
        assert span.status is SpanStatus.ERROR
        assert span.error_type == "ValueError"
        assert "kaboom" in (span.error_message or "")

    def test_timeout_is_recorded_as_timeout_not_error(self) -> None:
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        with use_trace_context(TraceContext()) as ctx:
            with pytest.raises(TimeoutError):
                with recorder.span("slow", SpanKind.SERVICE_CALL):
                    raise TimeoutError("too slow")

        assert store.get(ctx.trace_id).spans[0].status is SpanStatus.TIMEOUT


class TestTraceStore:
    def test_evicts_oldest_beyond_capacity(self) -> None:
        store = TraceStore(max_traces=2)
        recorder = Recorder("research", "research-service", store)

        ids = []
        for _ in range(3):
            ctx = TraceContext()
            ids.append(ctx.trace_id)
            with use_trace_context(ctx):
                with recorder.span("r", SpanKind.REQUEST):
                    pass

        assert store.get(ids[0]) is None
        assert store.get(ids[1]) is not None
        assert store.get(ids[2]) is not None

    def test_llm_tokens_roll_up_into_the_trace(self) -> None:
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        with use_trace_context(TraceContext()) as ctx:
            for _ in range(2):
                with recorder.span("llm", SpanKind.LLM) as span:
                    span.tokens = TokenUsage(
                        input_tokens=10, output_tokens=5, total_tokens=15,
                        source=TokenSource.PROVIDER,
                    )

        assert store.get(ctx.trace_id).tokens.total_tokens == 30

    def test_list_returns_newest_first(self) -> None:
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        ids = []
        for _ in range(3):
            ctx = TraceContext()
            ids.append(ctx.trace_id)
            with use_trace_context(ctx):
                with recorder.span("r", SpanKind.REQUEST):
                    pass

        assert [t.trace_id for t in store.list()] == ids[::-1]


class TestLatency:
    def test_categories_are_bucketed_by_span_kind(self) -> None:
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        with use_trace_context(TraceContext()) as ctx:
            with recorder.span("llm", SpanKind.LLM):
                pass
            with recorder.span("tool", SpanKind.TOOL):
                pass
            with recorder.span("dep", SpanKind.SERVICE_CALL):
                pass

        spans = store.get(ctx.trace_id).spans
        breakdown = summarize_latency(spans, total_ms=1000.0)

        assert breakdown.total_ms == 1000.0
        assert breakdown.llm_ms >= 0
        assert breakdown.overhead_ms >= 0

    def test_overhead_never_goes_negative_for_parallel_work(self) -> None:
        """Concurrent spans can sum past wall clock; overhead must clamp at zero."""
        store = TraceStore()
        recorder = Recorder("research", "research-service", store)

        with use_trace_context(TraceContext()) as ctx:
            for _ in range(5):
                with recorder.span("llm", SpanKind.LLM):
                    pass

        spans = store.get(ctx.trace_id).spans
        # Simulate five 100ms calls that ran concurrently inside a 100ms request.
        for span in spans:
            span.duration_ms = 100.0

        breakdown = summarize_latency(spans, total_ms=100.0)

        assert breakdown.llm_ms == 500.0, "self-time may exceed wall clock"
        assert breakdown.total_ms == 100.0, "wall clock is measured, never summed"
        assert breakdown.overhead_ms == 0.0, "overhead must clamp at zero"
