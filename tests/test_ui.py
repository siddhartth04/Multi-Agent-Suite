"""Tests for the dashboard.

The UI must never raise at the user: unreachable services and empty data are
normal states it has to render. These tests run without any service running.
"""

from __future__ import annotations

import contextlib
import pathlib

import httpx
import pytest

from ui.api import MODULE_PORTS, ApiResult, SutClient, default_module_urls
from ui.components import (
    agent_token_chart,
    latency_breakdown_chart,
    module_token_chart,
    topology_graph,
    trace_waterfall,
)


class TestModuleUrls:
    def test_defaults_are_local_ports(self) -> None:
        urls = default_module_urls()
        assert set(urls) == set(MODULE_PORTS)
        for module_id, port in MODULE_PORTS.items():
            assert urls[module_id] == f"http://127.0.0.1:{port}"

    def test_environment_overrides_the_default(self, monkeypatch) -> None:
        """The same image must run against Docker service names or remote hosts."""
        monkeypatch.setenv("RESEARCH_URL", "http://research:8000/")
        assert default_module_urls()["research"] == "http://research:8000"


class TestClientNeverRaises:
    def test_unreachable_service_returns_a_failed_result(self) -> None:
        client = SutClient("http://127.0.0.1:1", {"research": "http://127.0.0.1:1"})
        result = client.topology()

        assert isinstance(result, ApiResult)
        assert result.failed
        assert result.error

    def test_health_of_a_down_module_is_reported_not_raised(self) -> None:
        client = SutClient("http://127.0.0.1:1", {"research": "http://127.0.0.1:1"})
        assert client.module_health("research").failed
        assert client.any_reachable() is False

    def test_a_handled_failure_keeps_its_body(self, monkeypatch) -> None:
        """A module answers 500 with full telemetry; the UI must still get it."""
        body = {"status": "error", "error": "Injected failure", "agents": [], "trace_id": "a" * 32}

        def fake_post(url, json=None, timeout=None):
            return httpx.Response(500, json=body, request=httpx.Request("POST", url))

        monkeypatch.setattr("ui.api.httpx.post", fake_post)
        result = SutClient().run_module("research", "x")

        assert result.status_code == 500
        assert result.failed
        assert result.data == body, "the telemetry document must survive an error status"


class TestChartsHandleEmptyData:
    """Every chart is reachable before any run has happened."""

    def test_waterfall_with_no_spans(self) -> None:
        assert trace_waterfall([]) is not None

    def test_waterfall_ignores_spans_without_a_start_time(self) -> None:
        assert trace_waterfall([{"name": "x", "kind": "llm"}]) is not None

    def test_agent_chart_with_no_agents(self) -> None:
        assert agent_token_chart([]) is not None

    def test_latency_chart_with_no_latency(self) -> None:
        assert latency_breakdown_chart({}) is not None

    def test_module_chart_with_no_rows(self) -> None:
        assert module_token_chart([]) is not None

    def test_topology_with_no_modules(self) -> None:
        assert topology_graph({"modules": {}}, {}) is not None


class TestChartsRenderRealShapes:
    def test_waterfall_draws_one_bar_per_timed_span(self) -> None:
        spans = [
            {"name": "research.request", "kind": "request", "status": "ok",
             "started_at": "2026-01-01T00:00:00Z", "duration_ms": 100.0, "service_id": "s"},
            {"name": "llm.researcher", "kind": "llm", "status": "ok",
             "started_at": "2026-01-01T00:00:00.010Z", "duration_ms": 80.0, "service_id": "s"},
        ]
        assert len(trace_waterfall(spans).data) == 2

    def test_waterfall_marks_a_failed_span(self) -> None:
        spans = [
            {"name": "llm.x", "kind": "llm", "status": "error", "error_type": "LLMError",
             "started_at": "2026-01-01T00:00:00Z", "duration_ms": 5.0, "service_id": "s"},
        ]
        figure = trace_waterfall(spans)
        assert "error" in figure.data[0].hovertemplate

    def test_agent_chart_includes_reasoning_when_reported(self) -> None:
        agents = [
            {"agent_id": "researcher",
             "tokens": {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 30}},
        ]
        names = {trace.name for trace in agent_token_chart(agents).data}
        assert names == {"input", "output", "reasoning"}

    def test_agent_chart_omits_reasoning_when_absent(self) -> None:
        agents = [{"agent_id": "a", "tokens": {"input_tokens": 10, "output_tokens": 5}}]
        names = {trace.name for trace in agent_token_chart(agents).data}
        assert names == {"input", "output"}

    def test_latency_chart_skips_empty_categories(self) -> None:
        figure = latency_breakdown_chart(
            {"total_ms": 100, "llm_ms": 80, "tool_ms": 0, "dependency_ms": 0, "overhead_ms": 20}
        )
        assert list(figure.data[0].y) == ["LLM", "Overhead"]

    def test_topology_draws_dependency_edges(self) -> None:
        topology = {
            "modules": {
                "research": {"agents": ["researcher"], "independent": True, "depends_on": []},
                "fact_checker": {"agents": ["verification"], "independent": False,
                                 "depends_on": ["research"]},
            },
            "module_dependencies": [{"from": "fact_checker", "to": "research"}],
        }
        figure = topology_graph(topology, {"research": True, "fact_checker": False})
        # one edge line + two module nodes
        assert len(figure.data) == 3


class TestDashboardRuns:
    """The app script itself must execute without raising, services or not."""

    @staticmethod
    def _app():
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file("ui/app.py", default_timeout=60)
        app.run()
        return app

    def test_app_renders_with_no_services_running(self) -> None:
        app = self._app()

        assert not app.exception, [e.value for e in app.exception]
        # The application overview: topology, traces, tokens.
        assert len(app.tabs) == 3

    def test_scope_bar_offers_the_overview_and_every_module(self) -> None:
        """Each module is its own page, selected from the bar above the tabs."""
        app = self._app()

        assert app.radio, "the scope selector must be present"
        options = app.radio[0].options
        assert len(options) == 5, "one overview plus four modules"
        for label in ("Research", "Fact Checker", "Marketing", "Travel"):
            assert any(label in option for option in options), f"{label} missing from the bar"

    @pytest.mark.parametrize(
        ("module_id", "expected_agents"),
        [
            ("research", ["researcher", "analyst", "reviewer"]),
            ("fact_checker", ["fact_researcher", "verification"]),
            ("marketing", ["researcher", "strategist", "writer"]),
            ("travel", ["planner", "search", "booking"]),
        ],
    )
    def test_each_module_page_renders(self, module_id: str, expected_agents: list[str]) -> None:
        app = self._app()
        app.radio[0].set_value(module_id).run()

        assert not app.exception, [e.value for e in app.exception]
        # Run, Traces, Tokens, Failure modes.
        assert len(app.tabs) == 4


class TestChartTitles:
    def test_untitled_chart_has_no_undefined_title(self) -> None:
        """Plotly renders a None title as the literal string 'undefined'."""
        figure = topology_graph({"modules": {}}, {})
        assert figure.layout.title.text in (None, "")

    def test_titled_chart_keeps_its_title(self) -> None:
        spans = [{"name": "a", "kind": "llm", "status": "ok",
                  "started_at": "2026-01-01T00:00:00Z", "duration_ms": 1.0, "service_id": "s"}]
        assert trace_waterfall(spans, "Trace abc").layout.title.text == "Trace abc"


class TestAgentOutputRendering:
    """An agent's deliverable is markdown and must be shown in full.

    It used to go into a fixed-height HTML div, which both left the markdown
    unrendered and cut long output off part way through.
    """

    def test_output_is_rendered_as_markdown_not_raw_html(self) -> None:
        source = pathlib.Path("ui/app.py").read_text(encoding="utf-8")

        assert "agent-out" not in source, "the clipping fixed-height box must be gone"
        assert "max-height:320px" not in source

    def test_renderer_writes_the_whole_output(self, monkeypatch) -> None:
        import ui.app as app

        written: list[str] = []
        monkeypatch.setattr(app.st, "markdown", lambda text, **kw: written.append(text))
        monkeypatch.setattr(app.st, "caption", lambda *a, **kw: None)
        monkeypatch.setattr(app.st, "code", lambda *a, **kw: None)
        monkeypatch.setattr(app.st, "expander", lambda *a, **kw: contextlib.nullcontext())

        long_output = "# Heading\n\n" + ("a paragraph of text. " * 400)
        app.render_agent_output(long_output)

        assert written, "the output must be rendered"
        assert written[0] == long_output, "the full text, untruncated"

    def test_missing_output_does_not_raise(self, monkeypatch) -> None:
        import ui.app as app

        monkeypatch.setattr(app.st, "caption", lambda *a, **kw: None)
        app.render_agent_output(None)
        app.render_agent_output("")


class TestChartKeys:
    """Streamlit derives a chart's id from its type and parameters.

    The same chart drawn in two tabs collides and raises
    StreamlitDuplicateElementId, so every call site passes an explicit key.
    """

    def test_every_chart_call_passes_a_key(self) -> None:
        source = pathlib.Path("ui/app.py").read_text(encoding="utf-8")

        calls = source.count("st.plotly_chart(")
        keyed = source.count("key=")
        assert calls > 0
        assert keyed >= calls, f"{calls} charts but only {keyed} keys"
