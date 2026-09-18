"""Reusable visual components for the dashboard."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

# One palette, used consistently across every chart.
PALETTE = {
    "request": "#6366f1",
    "module": "#8b5cf6",
    "agent": "#0ea5e9",
    "llm": "#10b981",
    "tool": "#f59e0b",
    "service_call": "#ec4899",
}
STATUS_COLOR = {"ok": "#10b981", "error": "#ef4444", "timeout": "#f59e0b"}
MODULE_COLOR = {
    "research": "#6366f1",
    "fact_checker": "#10b981",
    "marketing": "#f59e0b",
    "travel": "#ec4899",
}

_GRID = "rgba(148,163,184,0.18)"


def _base_layout(fig: go.Figure, height: int, title: str | None = None) -> go.Figure:
    """Apply the shared chart styling: transparent, theme-neutral, low chrome."""
    fig.update_layout(
        height=height,
        title=title,
        margin=dict(l=8, r=8, t=40 if title else 12, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=12),
        hoverlabel=dict(font_size=12),
        showlegend=False,
    )
    fig.update_xaxes(gridcolor=_GRID, zeroline=False)
    fig.update_yaxes(gridcolor=_GRID, zeroline=False)
    return fig


# --------------------------------------------------------------- topology
def topology_graph(topology: dict[str, Any], health: dict[str, bool]) -> go.Figure:
    """Application map: modules, their agents, and cross-module HTTP edges."""
    modules = topology.get("modules", {})
    order = ["research", "fact_checker", "marketing", "travel"]
    present = [m for m in order if m in modules] + [m for m in modules if m not in order]

    # Research sits on its own row because the other two depend on it.
    positions = {
        "research": (0.5, 1.0),
        "fact_checker": (0.0, 0.0),
        "marketing": (1.0, 0.0),
        "travel": (2.0, 0.55),
    }

    fig = go.Figure()

    # Dependency edges, drawn first so nodes sit on top.
    for edge in topology.get("module_dependencies", []):
        src, dst = edge.get("from"), edge.get("to")
        if src not in positions or dst not in positions:
            continue
        x0, y0 = positions[src]
        x1, y1 = positions[dst]
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[y0 + 0.14, y1 - 0.14], mode="lines",
            line=dict(color="#94a3b8", width=2, dash="dot"),
            hovertemplate=f"{src} → {dst} (HTTP)<extra></extra>",
        ))
        fig.add_annotation(
            x=x1, y=y1 - 0.14, ax=x0, ay=y0 + 0.14,
            xref="x", yref="y", axref="x", ayref="y",
            showarrow=True, arrowhead=3, arrowsize=1.1, arrowwidth=2, arrowcolor="#94a3b8",
        )

    for module_id in present:
        if module_id not in positions:
            continue
        info = modules[module_id]
        x, y = positions[module_id]
        up = health.get(module_id, False)
        agents = info.get("agents", [])

        fig.add_trace(go.Scatter(
            x=[x], y=[y], mode="markers+text",
            marker=dict(
                size=104,
                color=MODULE_COLOR.get(module_id, "#6366f1"),
                opacity=0.95 if up else 0.32,
                line=dict(color=STATUS_COLOR["ok"] if up else "#ef4444", width=4),
                symbol="circle",
            ),
            text=[f"<b>{module_id}</b>"], textposition="middle center",
            textfont=dict(color="white", size=13),
            hovertemplate=(
                f"<b>{module_id}</b><br>"
                f"{info.get('service_id','')}<br>"
                f"port {info.get('default_port','?')}<br>"
                f"agents: {' → '.join(agents)}<br>"
                f"{'independent' if info.get('independent') else 'depends on ' + ', '.join(info.get('depends_on', []))}<br>"
                f"status: {'UP' if up else 'DOWN'}<extra></extra>"
            ),
        ))
        # Agent pipeline caption under each module.
        fig.add_annotation(
            x=x, y=y - 0.30, text=" → ".join(agents), showarrow=False,
            font=dict(size=10, color="#64748b"), xanchor="center",
        )
        fig.add_annotation(
            x=x, y=y + 0.27,
            text="independent" if info.get("independent") else "dependent",
            showarrow=False, font=dict(size=9, color="#94a3b8"), xanchor="center",
        )

    fig.update_xaxes(visible=False, range=[-0.6, 2.6])
    fig.update_yaxes(visible=False, range=[-0.6, 1.45])
    return _base_layout(fig, 420)


# ---------------------------------------------------------------- traces
def trace_waterfall(spans: list[dict[str, Any]], title: str | None = None) -> go.Figure:
    """Gantt-style span timeline for one trace.

    Bars are positioned by real start time, so concurrent work is visibly
    concurrent rather than stacked end to end.
    """
    if not spans:
        return _base_layout(go.Figure(), 200, "No spans")

    rows = []
    for span in spans:
        started = pd.to_datetime(span.get("started_at"), utc=True, errors="coerce")
        if pd.isna(started):
            continue
        rows.append({
            "name": span.get("name", "?"),
            "kind": span.get("kind", "?"),
            "status": span.get("status", "ok"),
            "started": started,
            "duration_ms": float(span.get("duration_ms") or 0.0),
            "service": span.get("service_id", ""),
            "agent": span.get("agent_id") or "",
            "error": span.get("error_type") or "",
            "tokens": ((span.get("tokens") or {}).get("total_tokens")),
        })
    if not rows:
        return _base_layout(go.Figure(), 200, "No timed spans")

    df = pd.DataFrame(rows).sort_values("started").reset_index(drop=True)
    origin = df["started"].min()
    df["offset_ms"] = (df["started"] - origin).dt.total_seconds() * 1000
    df["label"] = df.apply(lambda r: f"{r['name']}", axis=1)

    fig = go.Figure()
    for i, row in df.iterrows():
        colour = PALETTE.get(row["kind"], "#64748b")
        failed = row["status"] != "ok"
        fig.add_trace(go.Bar(
            x=[max(row["duration_ms"], 0.6)], y=[i], base=[row["offset_ms"]],
            orientation="h", width=0.68,
            marker=dict(
                color=colour, opacity=0.45 if failed else 0.92,
                line=dict(color=STATUS_COLOR.get(row["status"], colour), width=3 if failed else 0),
            ),
            hovertemplate=(
                f"<b>{row['name']}</b><br>kind: {row['kind']}<br>"
                f"service: {row['service']}<br>"
                f"start: +{row['offset_ms']:.0f}ms<br>"
                f"duration: {row['duration_ms']:.1f}ms<br>"
                f"status: {row['status']}"
                + (f"<br>tokens: {row['tokens']}" if row["tokens"] else "")
                + (f"<br><b>error: {row['error']}</b>" if row["error"] else "")
                + "<extra></extra>"
            ),
        ))

    fig.update_yaxes(
        tickmode="array", tickvals=list(range(len(df))), ticktext=df["label"].tolist(),
        autorange="reversed", tickfont=dict(size=11),
    )
    fig.update_xaxes(title_text="milliseconds since trace start")
    return _base_layout(fig, max(220, 34 * len(df) + 90), title)


def span_kind_legend() -> str:
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:14px;font-size:12px">'
        f'<span style="width:11px;height:11px;border-radius:3px;background:{colour};display:inline-block"></span>'
        f"{kind}</span>"
        for kind, colour in PALETTE.items()
    )
    return f'<div style="margin:2px 0 10px">{chips}</div>'


# ---------------------------------------------------------------- tokens
def agent_token_chart(agents: list[dict[str, Any]]) -> go.Figure:
    """Stacked input/output tokens per agent, in execution order."""
    if not agents:
        return _base_layout(go.Figure(), 200, "No agents")

    names = [a.get("agent_id", "?") for a in agents]
    inputs = [(a.get("tokens") or {}).get("input_tokens", 0) for a in agents]
    outputs = [(a.get("tokens") or {}).get("output_tokens", 0) for a in agents]
    reasoning = [((a.get("tokens") or {}).get("reasoning_tokens") or 0) for a in agents]

    fig = go.Figure()
    fig.add_trace(go.Bar(name="input", x=names, y=inputs, marker_color="#0ea5e9",
                         hovertemplate="%{x}<br>input: %{y}<extra></extra>"))
    fig.add_trace(go.Bar(name="output", x=names, y=outputs, marker_color="#10b981",
                         hovertemplate="%{x}<br>output: %{y}<extra></extra>"))
    if any(reasoning):
        fig.add_trace(go.Bar(name="reasoning", x=names, y=reasoning, marker_color="#8b5cf6",
                             hovertemplate="%{x}<br>reasoning: %{y}<extra></extra>"))

    fig.update_layout(barmode="stack", showlegend=True,
                      legend=dict(orientation="h", y=1.14, x=0))
    fig.update_yaxes(title_text="tokens")
    return _base_layout(fig, 300)


def latency_breakdown_chart(latency: dict[str, Any]) -> go.Figure:
    """Where the wall clock went, as a share of measured total."""
    categories = [
        ("LLM", latency.get("llm_ms", 0.0), PALETTE["llm"]),
        ("Tool", latency.get("tool_ms", 0.0), PALETTE["tool"]),
        ("Dependency", latency.get("dependency_ms", 0.0), PALETTE["service_call"]),
        ("Overhead", latency.get("overhead_ms", 0.0), "#94a3b8"),
    ]
    categories = [c for c in categories if c[1] and c[1] > 0]
    if not categories:
        return _base_layout(go.Figure(), 220, "No latency recorded")

    fig = go.Figure(go.Bar(
        x=[c[1] for c in categories], y=[c[0] for c in categories], orientation="h",
        marker_color=[c[2] for c in categories],
        text=[f"{c[1]:.0f}ms" for c in categories], textposition="auto",
        hovertemplate="%{y}: %{x:.1f}ms<extra></extra>",
    ))
    fig.update_xaxes(title_text="milliseconds")
    return _base_layout(fig, 240)


def module_token_chart(rows: list[dict[str, Any]]) -> go.Figure:
    """Total tokens observed per module."""
    if not rows:
        return _base_layout(go.Figure(), 200, "No data")

    df = pd.DataFrame(rows)
    fig = go.Figure(go.Bar(
        x=df["module"], y=df["tokens"],
        marker_color=[MODULE_COLOR.get(m, "#6366f1") for m in df["module"]],
        text=df["tokens"], textposition="auto",
        hovertemplate="%{x}<br>%{y} tokens<extra></extra>",
    ))
    fig.update_yaxes(title_text="tokens observed")
    return _base_layout(fig, 280)
