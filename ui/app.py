"""Control room for the multi-agent SUT.

A pure API consumer: everything shown here comes from the same public endpoints
the external testing platform uses, so the dashboard is also a demonstration of
what is observable from outside.

Layout: a top bar selects the scope -- the whole application, or one module.
Each module gets its own page (its agents, runs, traces, tokens and failures);
the application page keeps the cross-cutting views.

    streamlit run ui/app.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.api import DEFAULT_GATEWAY, MODULE_PORTS, SutClient  # noqa: E402
from ui.components import (  # noqa: E402
    MODULE_COLOR,
    STATUS_COLOR,
    agent_token_chart,
    latency_breakdown_chart,
    module_token_chart,
    span_kind_legend,
    topology_graph,
    trace_waterfall,
)

st.set_page_config(
    page_title="Multi-Agent SUT Control Room",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

FAILURE_MODES = ["normal", "slow", "error", "timeout", "tool_failure", "dependency_failure"]

SAMPLE_INPUTS = {
    "research": "the current state of solid-state battery technology",
    "fact_checker": "Solid-state batteries are in mass production today.",
    "marketing": "a budget e-bike for city commuters",
    "travel": "5 days in Lisbon in May, two adults, mid-range budget",
}

MODULE_LABELS = {
    "research": "Research",
    "fact_checker": "Fact Checker",
    "marketing": "Marketing",
    "travel": "Travel",
}

OVERVIEW = "__overview__"

st.markdown(
    """
<style>
  .block-container {padding-top: 2.2rem; padding-bottom: 2rem; max-width: 1500px;}
  [data-testid="stMetricValue"] {font-size: 1.5rem;}
  .pill {display:inline-block;padding:2px 10px;border-radius:999px;
         font-size:11px;font-weight:600;letter-spacing:.02em;}
  .mono {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;}
  .card {border:1px solid rgba(148,163,184,.25); border-radius:12px; padding:14px 16px; margin-bottom:10px;}
  .agent-out {white-space:pre-wrap; font-size:13px; line-height:1.5; max-height:320px; overflow-y:auto;}
  h1 {letter-spacing:-.02em;}
  /* The module bar: bigger, more prominent than the tabs underneath it. */
  div[data-testid="stHorizontalBlock"] .module-chip {padding:2px 0 6px;}
</style>
""",
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ helpers
def pill(text: str, colour: str) -> str:
    return f'<span class="pill" style="background:{colour}22;color:{colour};border:1px solid {colour}55">{text}</span>'


def status_pill(status: str) -> str:
    return pill(status.upper(), STATUS_COLOR.get(status, "#64748b"))


@st.cache_resource
def get_client(gateway: str) -> SutClient:
    return SutClient(gateway)


def health_map(client: SutClient) -> dict[str, bool]:
    return {mid: result.ok for mid, result in client.all_health().items()}


def render_tokens(tokens: dict[str, Any]) -> None:
    cols = st.columns(5)
    cols[0].metric("Total tokens", f"{tokens.get('total_tokens', 0):,}")
    cols[1].metric("Input", f"{tokens.get('input_tokens', 0):,}")
    cols[2].metric("Output", f"{tokens.get('output_tokens', 0):,}")
    reasoning = tokens.get("reasoning_tokens")
    cols[3].metric("Reasoning", f"{reasoning:,}" if reasoning else "—")
    cost = tokens.get("cost_usd")
    cols[4].metric("Cost", f"${cost:.6f}" if cost else "—",
                   help="Null when the provider's pricing is unknown; never estimated.")
    source = tokens.get("source", "unavailable")
    note = {
        "provider": "Counts reported by the provider.",
        "estimated": "Counts are a local approximation, not exact.",
        "unavailable": "The provider reported no usage.",
    }.get(source, "")
    st.caption(f"Token source: **{source}** — {note}")


def render_run_result(body: dict[str, Any], status_code: int | None, elapsed: float) -> None:
    """The shared result panel: status, dependencies, agents, output."""
    status = body.get("status", "unknown")
    st.markdown(
        f"### {status_pill(status)} &nbsp; `{body.get('module_id','')}` "
        f"<span style='color:#94a3b8;font-size:13px'>in {elapsed:.1f}s</span>",
        unsafe_allow_html=True,
    )
    if body.get("error"):
        st.error(body["error"])

    m = st.columns(4)
    m[0].metric("HTTP", status_code or "—")
    m[1].metric("Agents run", len(body.get("agents", [])))
    m[2].metric("Tokens", f"{(body.get('tokens') or {}).get('total_tokens', 0):,}")
    m[3].metric("Latency", f"{(body.get('latency') or {}).get('total_ms', 0):.0f} ms")

    st.code(f"trace_id: {body.get('trace_id','')}\nrequest_id: {body.get('request_id','')}",
            language="text")

    for dep in body.get("dependencies") or []:
        tokens = (dep.get("tokens") or {}).get("total_tokens")
        st.markdown(
            f'<div class="card">{status_pill(dep.get("status","?"))} '
            f'<b>calls {dep.get("module_id")}</b> '
            f'<span class="mono" style="color:#94a3b8">HTTP {dep.get("http_status","—")} · '
            f'{dep.get("duration_ms",0):.0f}ms · retries {dep.get("retry_count",0)}'
            f'{" · " + str(tokens) + " tokens" if tokens else ""}</span>'
            + (f'<br><span class="mono" style="color:#ef4444">{dep.get("error")}</span>'
               if dep.get("error") else "")
            + "</div>",
            unsafe_allow_html=True,
        )

    agents = body.get("agents") or []
    if agents:
        st.markdown("#### Agents")
        for agent in agents:
            tok = agent.get("tokens") or {}
            with st.expander(
                f"{'✅' if agent.get('status') == 'ok' else '❌'}  "
                f"{agent.get('agent_id')} — {agent.get('role','')}  ·  "
                f"{tok.get('total_tokens',0)} tokens",
                expanded=False,
            ):
                cols = st.columns(4)
                cols[0].metric("Input", tok.get("input_tokens", 0))
                cols[1].metric("Output", tok.get("output_tokens", 0))
                cols[2].metric("Reasoning", tok.get("reasoning_tokens") or "—")
                cols[3].metric("Duration", f"{agent.get('duration_ms',0):.0f} ms")
                if agent.get("error"):
                    st.error(agent["error"])
                st.markdown(
                    f'<div class="agent-out">{(agent.get("output") or "(no output)")}</div>',
                    unsafe_allow_html=True,
                )
        st.plotly_chart(agent_token_chart(agents), width="stretch",
                        config={"displayModeBar": False})

    if body.get("result"):
        st.markdown("#### Final result")
        st.markdown(body["result"])

    with st.expander("Raw /run response"):
        st.json(body)


def render_trace_view(client: SutClient, module_id: str, trace_id: str | None = None) -> None:
    """Trace picker plus waterfall, scoped to one module's recordings."""
    listing = client.traces(module_id, limit=25)
    if listing.failed:
        st.warning(f"`{module_id}` unreachable — {listing.error}")
        return

    traces = listing.data.get("traces", [])
    if not traces:
        st.info("No traces recorded yet. Run this module first.")
        return

    options = {
        f"{t['trace_id'][:16]}…  ·  {t.get('status','?')}  ·  "
        f"{(t.get('duration_ms') or 0):.0f}ms  ·  {t.get('tokens',{}).get('total_tokens',0)} tok": t
        for t in traces
    }
    chosen = st.selectbox("Trace", list(options), key=f"trace_pick_{module_id}")
    trace = options[chosen]
    tid = trace["trace_id"]

    distributed = client.distributed_trace(tid)
    if distributed.ok:
        segments = distributed.data.get("segments", {})
        services = distributed.data.get("services_involved", [])
        c = st.columns(4)
        c[0].metric("Services", len(services))
        c[1].metric("Spans", distributed.data.get("span_count", 0))
        c[2].metric("Tokens", f"{distributed.data.get('total_tokens', 0):,}")
        c[3].metric("Status", trace.get("status", "?"))

        if len(services) > 1:
            st.success(
                f"One trace spans **{len(services)} services** ({', '.join(services)}) — "
                "the trace id survived the HTTP boundary.",
                icon="🔗",
            )

        st.markdown(span_kind_legend(), unsafe_allow_html=True)
        all_spans = [s for seg in segments.values() for s in seg.get("spans", [])]
        st.plotly_chart(trace_waterfall(all_spans, f"Trace {tid[:16]}…"),
                        width="stretch", config={"displayModeBar": False})

        for service, segment in segments.items():
            with st.expander(f"Spans recorded by `{service}` ({len(segment.get('spans', []))})"):
                st.dataframe(
                    [
                        {
                            "span": s.get("name"),
                            "kind": s.get("kind"),
                            "status": s.get("status"),
                            "ms": round(s.get("duration_ms") or 0, 1),
                            "agent": s.get("agent_id") or "",
                            "tokens": (s.get("tokens") or {}).get("total_tokens"),
                            "error": s.get("error_type") or "",
                        }
                        for s in segment.get("spans", [])
                    ],
                    width="stretch", hide_index=True,
                )
    else:
        st.markdown(span_kind_legend(), unsafe_allow_html=True)
        st.plotly_chart(trace_waterfall(trace.get("spans", [])),
                        width="stretch", config={"displayModeBar": False})
        st.caption("Gateway unavailable — showing only this service's segment.")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### ◆ Multi-Agent SUT")
    st.caption("System Under Test — control room")

    gateway_url = st.text_input("Gateway URL", value=os.getenv("GATEWAY_URL", DEFAULT_GATEWAY))
    client = get_client(gateway_url)

    if st.button("Refresh", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("**Service health**")
    health = health_map(client)
    for module_id, port in MODULE_PORTS.items():
        up = health.get(module_id, False)
        st.markdown(
            f"{'🟢' if up else '🔴'} `{module_id}` "
            f"<span style='color:#94a3b8;font-size:11px'>:{port}</span>",
            unsafe_allow_html=True,
        )

    if not any(health.values()):
        st.warning("No modules reachable. Start them first:", icon="⚠️")
        st.code(
            "uvicorn modules.research.server:app --port 8001\n"
            "uvicorn modules.fact_checker.server:app --port 8002\n"
            "uvicorn modules.marketing.server:app --port 8003\n"
            "uvicorn modules.travel.server:app --port 8004\n"
            "uvicorn gateway.app:app --port 8000",
            language="bash",
        )

    st.divider()
    st.caption(
        "This dashboard only consumes the public HTTP API — "
        "the same surface the external testing platform sees."
    )

st.title("Multi-Agent SUT Control Room")

# ------------------------------------------------------- module selector bar
# Each module is its own page; the overview keeps the cross-cutting views.
scope = st.radio(
    "Scope",
    [OVERVIEW, *MODULE_PORTS],
    format_func=lambda s: "◆ Application" if s == OVERVIEW else (
        f"{'🟢' if health.get(s) else '🔴'} {MODULE_LABELS.get(s, s)}"
    ),
    horizontal=True,
    label_visibility="collapsed",
    key="scope",
)
st.divider()


# =========================================================== APPLICATION PAGE
if scope == OVERVIEW:
    tab_topology, tab_traces, tab_tokens = st.tabs(["Topology", "Traces", "Tokens & Cost"])

    with tab_topology:
        topo = client.topology()
        if topo.failed:
            st.error(f"Gateway unreachable at `{gateway_url}` — {topo.error}")
            st.info("Start the gateway with `uvicorn gateway.app:app --port 8000`.")
        else:
            data = topo.data
            modules = data.get("modules", {})

            c = st.columns(4)
            c[0].metric("Application", data.get("application_id", "?"))
            c[1].metric("Version", data.get("version", "?"))
            c[2].metric("Modules", len(modules))
            c[3].metric("Agents", sum(len(m.get("agents", [])) for m in modules.values()))

            st.plotly_chart(topology_graph(data, health), width="stretch",
                            config={"displayModeBar": False})
            st.caption(
                "Solid ring = reachable. Dotted arrows are cross-module HTTP dependencies. "
                "Research is independent, so Fact Checker and Marketing can depend on it; "
                "Travel depends on nothing. Open a module above for its own page."
            )

            with st.expander("Raw /topology response"):
                st.json(data)

    with tab_traces:
        st.subheader("Distributed traces")
        recorder = st.selectbox("Recorded by", list(MODULE_PORTS), key="overview_trace_module")
        render_trace_view(client, recorder)

    with tab_tokens:
        st.subheader("Token usage across the application")
        rows = []
        for module_id in MODULE_PORTS:
            result = client.tokens(module_id)
            if result.ok:
                tokens = result.data.get("tokens", {})
                rows.append({
                    "module": module_id,
                    "tokens": tokens.get("total_tokens", 0),
                    "input": tokens.get("input_tokens", 0),
                    "output": tokens.get("output_tokens", 0),
                    "source": tokens.get("source", "—"),
                    "traces": result.data.get("traces_observed", 0),
                })

        if not rows:
            st.info("No modules reachable.")
        else:
            c = st.columns(4)
            c[0].metric("Total tokens observed", f"{sum(r['tokens'] for r in rows):,}")
            c[1].metric("Input", f"{sum(r['input'] for r in rows):,}")
            c[2].metric("Output", f"{sum(r['output'] for r in rows):,}")
            c[3].metric("Traces", sum(r["traces"] for r in rows))

            st.plotly_chart(module_token_chart(rows), width="stretch",
                            config={"displayModeBar": False})
            st.dataframe(rows, width="stretch", hide_index=True)
            st.caption(
                "`source` records where the counts came from: **provider** (reported), "
                "**estimated** (approximated locally), **unavailable** (not reported). "
                "Counts are never fabricated."
            )


# =============================================================== MODULE PAGES
else:
    module_id = scope
    up = health.get(module_id, False)
    meta = client.module_metadata(module_id)
    colour = MODULE_COLOR.get(module_id, "#6366f1")

    # ---- module header -----------------------------------------------
    st.markdown(
        f"## <span style='color:{colour}'>●</span> {MODULE_LABELS[module_id]} "
        f"<span class='mono' style='color:#94a3b8'>:{MODULE_PORTS[module_id]}</span>",
        unsafe_allow_html=True,
    )

    if not up:
        st.error(
            f"`{module_id}` is not reachable. Start it with "
            f"`uvicorn modules.{module_id}.server:app --port {MODULE_PORTS[module_id]}`."
        )

    if meta.ok:
        info = meta.data
        st.caption(info.get("description", ""))

        c = st.columns(4)
        c[0].metric("Agents", len(info.get("agents", [])))
        c[1].metric("Version", info.get("version", "?"))
        c[2].metric(
            "Dependencies",
            len(info.get("dependencies", [])) or "none",
            help="Cross-module calls this module makes over HTTP.",
        )
        c[3].metric("Model", info.get("model", "?"))

        st.markdown(
            (pill("INDEPENDENT", "#10b981") if info.get("independent")
             else pill("DEPENDS ON " + ", ".join(d["module_id"] for d in info["dependencies"]).upper(),
                       "#f59e0b"))
            + " " + "".join(pill(cap, colour) + " " for cap in info.get("capabilities", [])),
            unsafe_allow_html=True,
        )

        st.markdown("#### Agent pipeline")
        for i, agent in enumerate(info.get("agents", [])):
            arrow = "" if i == 0 else "↓"
            if arrow:
                st.markdown(
                    f"<div style='color:#94a3b8;margin:-6px 0 -6px 22px'>{arrow}</div>",
                    unsafe_allow_html=True,
                )
            tools = "".join(pill("tool: " + t, "#f59e0b") for t in agent.get("tools", []))
            st.markdown(
                f'<div class="card"><b>{agent["agent_id"]}</b> '
                f'<span style="color:#94a3b8">— {agent["role"]}</span> {tools}'
                f'<br><span style="font-size:12px;color:#64748b">{agent["goal"]}</span></div>',
                unsafe_allow_html=True,
            )

        for dep in info.get("dependencies", []):
            st.markdown(
                f'<div class="card">🔗 Calls <b>{dep["module_id"]}</b> over '
                f'{dep["transport"].upper()} · <span class="mono">{dep["url"]}</span> · '
                f'{"required" if dep["required"] else "optional"}'
                f'<br><span style="font-size:12px;color:#64748b">{dep.get("description","")}</span></div>',
                unsafe_allow_html=True,
            )

    st.divider()
    tab_run, tab_traces, tab_tokens, tab_failures = st.tabs(
        ["Run", "Traces", "Tokens", "Failure modes"]
    )

    # ---- run ----------------------------------------------------------
    with tab_run:
        col_a, col_b = st.columns([3, 1])
        with col_a:
            user_input = st.text_area(
                "Input", value=SAMPLE_INPUTS.get(module_id, ""), height=90,
                key=f"input_{module_id}",
            )
        with col_b:
            use_deps = st.toggle(
                "Call dependencies", value=True, key=f"deps_{module_id}",
                help="Off runs this module alone, without calling upstream modules.",
            )

        if st.button("Run", type="primary", disabled=not up, key=f"run_{module_id}"):
            with st.spinner(f"Running {module_id}…"):
                started = time.time()
                result = client.run_module(module_id, user_input, use_dependencies=use_deps)
                elapsed = time.time() - started
            st.session_state[f"last_run_{module_id}"] = {"result": result, "elapsed": elapsed}

        run = st.session_state.get(f"last_run_{module_id}")
        if run:
            result, body = run["result"], (run["result"].data or {})
            if result.failed and not body:
                st.error(f"Request failed: {result.error}")
            else:
                render_run_result(body, result.status_code, run["elapsed"])

    # ---- traces -------------------------------------------------------
    with tab_traces:
        st.markdown(f"Traces recorded by `{module_id}`.")
        render_trace_view(client, module_id)

    # ---- tokens -------------------------------------------------------
    with tab_tokens:
        totals = client.tokens(module_id)
        if totals.failed:
            st.warning(f"`{module_id}` unreachable — {totals.error}")
        else:
            st.markdown("#### Observed by this module")
            render_tokens(totals.data.get("tokens", {}))
            st.caption(f"Across {totals.data.get('traces_observed', 0)} recorded traces.")

        run = st.session_state.get(f"last_run_{module_id}")
        if run and (run["result"].data or {}).get("latency"):
            body = run["result"].data
            st.divider()
            st.markdown("#### Last run")
            render_tokens(body.get("tokens") or {})
            st.plotly_chart(latency_breakdown_chart(body.get("latency") or {}),
                            width="stretch", config={"displayModeBar": False})
            st.caption(
                "`total_ms` is measured wall clock, never a sum of spans — so concurrent "
                "work is not double counted, and overhead never goes negative."
            )
            if body.get("agents"):
                st.plotly_chart(agent_token_chart(body["agents"]), width="stretch",
                                config={"displayModeBar": False})

    # ---- failure modes ------------------------------------------------
    with tab_failures:
        st.caption(
            "Failures are deterministic and configurable, never random. A failed run still "
            "returns a complete telemetry document: the trace stays correlated, the agents "
            "that already ran are reported, and their tokens are still counted."
        )

        fail_input = st.text_input(
            "Input", value=SAMPLE_INPUTS.get(module_id, "test"), key=f"fail_input_{module_id}"
        )

        descriptions = {
            "normal": "No injection — the control case.",
            "slow": "Delays the request, then succeeds.",
            "error": "Fails with a labelled application error (HTTP 500).",
            "timeout": "Sleeps past the caller's timeout, reports `timeout` (HTTP 504).",
            "tool_failure": "Fails when a tool is invoked.",
            "dependency_failure": "Fails the cross-service call.",
        }

        cols = st.columns(3)
        for i, mode in enumerate(FAILURE_MODES):
            with cols[i % 3]:
                st.markdown(
                    f'<div class="card"><b>{mode}</b><br>'
                    f'<span style="font-size:12px;color:#94a3b8">{descriptions[mode]}</span></div>',
                    unsafe_allow_html=True,
                )
                if st.button(f"Run `{mode}`", key=f"fire_{module_id}_{mode}",
                             disabled=not up, width="stretch"):
                    timeout = 20.0 if mode == "timeout" else 300.0
                    with st.spinner(f"{module_id} · {mode}…"):
                        started = time.time()
                        res = client.run_module(module_id, fail_input,
                                                failure_mode=mode, timeout=timeout)
                        elapsed = time.time() - started
                    st.session_state[f"last_failure_{module_id}"] = {
                        "mode": mode, "result": res, "elapsed": elapsed
                    }

        outcome = st.session_state.get(f"last_failure_{module_id}")
        if outcome:
            st.divider()
            res, body = outcome["result"], (outcome["result"].data or {})
            st.markdown(
                f"### `{outcome['mode']}` → {status_pill(body.get('status', 'unknown'))} "
                f"<span style='color:#94a3b8;font-size:13px'>in {outcome['elapsed']:.1f}s</span>",
                unsafe_allow_html=True,
            )

            if not body:
                st.error(f"No response body — {res.error}")
            else:
                c = st.columns(4)
                c[0].metric("HTTP", res.status_code or "—")
                c[1].metric("Agents completed", len(body.get("agents", [])))
                c[2].metric("Tokens still counted",
                            f"{(body.get('tokens') or {}).get('total_tokens', 0):,}")
                c[3].metric("Latency", f"{(body.get('latency') or {}).get('total_ms', 0):.0f} ms")

                if body.get("error"):
                    st.error(body["error"])

                st.success(
                    f"Telemetry survived the failure: trace `{body.get('trace_id','')[:16]}…` "
                    f"is still correlated and queryable.",
                    icon="✅",
                )

                trace = client.trace(module_id, body.get("trace_id", ""))
                if trace.ok:
                    st.markdown(span_kind_legend(), unsafe_allow_html=True)
                    st.plotly_chart(trace_waterfall(trace.data.get("spans", [])),
                                    width="stretch", config={"displayModeBar": False})

                with st.expander("Raw response"):
                    st.json(body)
