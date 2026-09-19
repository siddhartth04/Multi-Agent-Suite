"""Tests for the agent workspace.

The workspace is a demo surface, so two things matter most: it must never show
a technical failure to the audience, and it must never leak telemetry (tokens,
traces, spans) into the interface.

Every test runs without any service running.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from ui.api import MODULE_PORTS, ApiResult, SutClient, default_module_urls

APP_SOURCE = pathlib.Path("ui/app.py").read_text(encoding="utf-8")


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
    def test_unreachable_module_returns_a_failed_result(self) -> None:
        client = SutClient("http://127.0.0.1:1", {"research": "http://127.0.0.1:1"})
        result = client.module_health("research")

        assert isinstance(result, ApiResult)
        assert result.failed
        assert result.error

    def test_a_handled_failure_keeps_its_body(self, monkeypatch) -> None:
        """A module answers 500 with a body; the caller must still receive it."""
        body = {"status": "error", "error": "Injected failure", "result": None}

        def fake_post(url, json=None, timeout=None):
            return httpx.Response(500, json=body, request=httpx.Request("POST", url))

        monkeypatch.setattr("ui.api.httpx.post", fake_post)
        result = SutClient().run_module("research", "x")

        assert result.status_code == 500
        assert result.failed
        assert result.data == body


class TestAssistantCatalogue:
    """The catalogue is what the audience sees, so it must cover every module."""

    def test_every_module_has_an_assistant(self) -> None:
        import ui.app as app

        assert set(app.ASSISTANTS) == set(MODULE_PORTS)

    def test_each_assistant_is_fully_described(self) -> None:
        import ui.app as app

        for module_id, spec in app.ASSISTANTS.items():
            for field in ("name", "icon", "tagline", "placeholder", "team", "examples"):
                assert spec.get(field), f"{module_id} is missing {field}"
            assert len(spec["examples"]) >= 2, f"{module_id} needs example prompts"

    def test_team_sizes_match_the_real_agent_pipelines(self) -> None:
        """The named specialists must match how many agents actually run."""
        import ui.app as app

        expected = {"research": 3, "fact_checker": 2, "marketing": 3, "travel": 3}
        for module_id, count in expected.items():
            assert len(app.ASSISTANTS[module_id]["team"]) == count

    def test_no_internal_agent_ids_are_shown(self) -> None:
        """The audience sees role names, never snake_case internals."""
        import ui.app as app

        for spec in app.ASSISTANTS.values():
            for member in spec["team"]:
                assert "_" not in member, f"{member} looks like an internal id"


class TestNoTelemetryInTheInterface:
    """This screen is the product, not the instrumentation."""

    @pytest.mark.parametrize(
        # Telemetry field names, not HTML: "<span>" is a layout tag, not a trace span.
        "term",
        ["trace_id", "request_id", "span_id", "tokens", "duration_ms",
         "failure_mode", "http_status", "latency"],
    )
    def test_telemetry_fields_are_not_rendered(self, term: str) -> None:
        # Strip comments and docstrings; only what reaches the screen counts.
        rendered = "\n".join(
            line for line in APP_SOURCE.splitlines()
            if not line.strip().startswith("#")
        )
        _, _, after_docstring = rendered.partition('"""')
        _, _, body = after_docstring.partition('"""')

        assert term not in body, f"{term} must not appear in the interface"

    def test_no_charts_are_imported(self) -> None:
        """The old telemetry dashboard's charting must not come back.

        `streamlit.components.v1` is unrelated -- it carries the script that
        keeps the sidebar reopenable.
        """
        assert "plotly" not in APP_SOURCE
        assert "ui.components" not in APP_SOURCE
        assert "agent_token_chart" not in APP_SOURCE
        assert "trace_waterfall" not in APP_SOURCE


class TestDashboardRuns:
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

    def test_sidebar_lists_every_assistant(self) -> None:
        import ui.app as app_module

        app = self._app()
        labels = " ".join(b.label for b in app.button)
        for spec in app_module.ASSISTANTS.values():
            assert spec["name"] in labels, f"{spec['name']} missing from the sidebar"

    def test_switching_assistant_changes_the_view(self) -> None:
        app = self._app()
        app.session_state["assistant"] = "travel"
        app.run()

        assert not app.exception
        assert app.session_state["assistant"] == "travel"

    def test_each_assistant_has_its_own_conversation(self) -> None:
        """Switching assistants mid-demo must not discard the other thread."""
        app = self._app()

        threads = app.session_state["threads"]
        assert set(threads) == set(MODULE_PORTS)
        assert all(messages == [] for messages in threads.values())

    def test_a_failed_run_shows_a_plain_message(self, monkeypatch) -> None:
        """No status codes or stack traces reach the audience."""
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        import ui.api

        # Force the failure path regardless of whether services happen to be
        # running on this machine.
        def refuse(url, json=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(ui.api.httpx, "post", refuse)

        app = AppTest.from_file("ui/app.py", default_timeout=60)
        app.run()
        app.session_state["pending"] = "a question"
        app.run()

        assert not app.exception
        warnings = [w.value for w in app.warning]
        assert warnings, "a failure must be reported to the user"
        for text in warnings:
            assert "HTTP" not in text, "no status codes in front of an audience"
            assert "Traceback" not in text
            assert "ConnectError" not in text
            assert "could not complete" in text


class TestOutputCleaning:
    """Model output is markdown, and it does not always arrive well formed."""

    def test_br_tags_are_stripped(self) -> None:
        import ui.app as app

        assert app.clean("a<br>b<br />c<BR/>d") == "a b c d"

    def test_a_run_on_table_header_is_repaired(self) -> None:
        """A header and its separator on one line stops the table parsing."""
        import ui.app as app

        broken = "| Day | Morning | |-----|---------|\n| 1 | Kinkaku-ji |"
        fixed = app.clean(broken)

        lines = fixed.splitlines()
        assert len(lines) == 3, "header, separator and row must each be on a line"
        assert lines[1].strip().startswith("|-")

    def test_a_valid_table_is_left_alone(self) -> None:
        import ui.app as app

        table = "| a | b |\n|---|---|\n| 1 | 2 |"
        assert app.clean(table) == table

    def test_ordinary_prose_is_unchanged(self) -> None:
        import ui.app as app

        prose = "**Trip Brief**\n\n- Destination: Lisbon\n- Budget: mid-range"
        assert app.clean(prose) == prose


class TestThemeIsPinned:
    """Streamlit follows the OS theme, which rendered white text on white."""

    def test_a_light_theme_is_configured(self) -> None:
        config = pathlib.Path(".streamlit/config.toml")
        assert config.exists(), "the theme must be pinned, not left to the OS"

        text = config.read_text(encoding="utf-8")
        assert 'base = "light"' in text
        assert 'textColor = "#0f172a"' in text
        assert 'backgroundColor = "#ffffff"' in text

    def test_the_stylesheet_sets_its_own_text_colour(self) -> None:
        """Belt and braces: the CSS must not rely on inherited colours."""
        assert "--ink:" in APP_SOURCE
        assert ".stApp p, .stApp li" in APP_SOURCE


class TestSidebarIsAlwaysReachable:
    """A collapsed sidebar with no way back is a dead end."""

    def test_the_header_is_not_hidden_outright(self) -> None:
        """Streamlit's reopen control lives in the header."""
        assert "#MainMenu, footer, header," not in APP_SOURCE, (
            "hiding the header strands the user with a collapsed sidebar"
        )

    def test_a_reopen_guarantee_is_installed(self) -> None:
        assert "aw-reopen" in APP_SOURCE
        assert "stExpandSidebarButton" in APP_SOURCE

    def test_the_fallback_shows_only_when_collapsed(self) -> None:
        assert "collapsed ? 'block' : 'none'" in APP_SOURCE
