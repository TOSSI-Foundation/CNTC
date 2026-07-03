"""Compare — overlay runs across UPFs and dataplane modes. Grouped run-chip picker
(verified shown, experimental collapsed) + metric tabs (throughput / latency / Mpps-per-
core). Chip toggles and metric switch are Dash callbacks; the chart re-renders from the
selection."""
from __future__ import annotations

import dash
from dash import ALL, Input, Output, State, callback, ctx, dcc, html
import plotly.graph_objects as go

from dashboard import theme as T
from dashboard.components import card
from dashboard.charts import _num, _rows
from dashboard.data import get_campaign, load_campaigns

dash.register_page(__name__, path="/compare", name="Compare")

_METRICS = [("throughput", "Throughput"), ("latency", "Latency"), ("percore", "Mpps / core")]


def _perf_runs():
    return [c for c in load_campaigns() if c.suite("performance")]


def _chip(c, selected):
    w = f" · {c.workers}w" if c.workers else ""
    cls = "run-chip selected" if selected else "run-chip"
    return html.Button(className=cls, id={"type": "rchip", "k": c.key}, n_clicks=0, children=[
        html.Span(c.campaign_id, className="run-chip-name",
                  style={"borderColor": T.mode_color(c.mode)}),
        html.Span(f"{c.upf.replace('sd-core ', 'SD-Core ')} · {(c.mode or '?').upper()}{w} · "
                  f"{c.date[:10]}", className="run-chip-meta")])


def _picker(runs, sel):
    verified = [c for c in runs if c.group == "verified"]
    experimental = [c for c in runs if c.group == "experimental"]
    block = [html.Div("Verified runs", className="run-group-label"),
             html.Div([_chip(c, c.key in sel) for c in verified], className="run-chips")]
    if experimental:
        block.append(html.Details(className="run-experimental", children=[
            html.Summary(f"Experimental / debug ({len(experimental)})"),
            html.Div([_chip(c, c.key in sel) for c in experimental], className="run-chips")]))
    return card(html.Div(block), title="Runs",
                sub=f"{len(sel)} of {len(verified)} verified selected")


def _tabs(metric):
    return html.Div(className="metric-tabs", children=[
        html.Button(label, id={"type": "mtab", "m": m}, n_clicks=0,
                    className="metric-tab" + (" active" if m == metric else ""))
        for m, label in _METRICS])


def layout(**_):
    runs = _perf_runs()
    default = [c.key for c in runs if c.group == "verified"][:3]
    return html.Div(className="page", children=[
        dcc.Store(id="cmp-sel", data=default),
        dcc.Store(id="cmp-metric", data="throughput"),
        html.Div(className="page-head", children=[
            html.H1("Compare"),
            html.Div("Overlay runs across UPFs and dataplane modes. Mpps-per-core is the "
                     "fair cross-UPF metric.", className="muted")]),
        html.Div(id="cmp-picker"),
        html.Div(id="cmp-tabs"),
        html.Div(id="cmp-out"),
    ])


# --- chip toggle -> selection store ---------------------------------------------
@callback(Output("cmp-sel", "data"), Input({"type": "rchip", "k": ALL}, "n_clicks"),
          State("cmp-sel", "data"), prevent_initial_call=True)
def _toggle(_clicks, sel):
    tid = ctx.triggered_id
    if not tid or not isinstance(tid, dict):
        return sel
    k = tid["k"]
    sel = list(sel or [])
    if k in sel:
        sel.remove(k)
    else:
        sel.append(k)
    return sel


@callback(Output("cmp-metric", "data"), Input({"type": "mtab", "m": ALL}, "n_clicks"),
          prevent_initial_call=True)
def _metric(_clicks):
    tid = ctx.triggered_id
    return tid["m"] if isinstance(tid, dict) else "throughput"


# --- render picker + tabs + chart from the stores -------------------------------
@callback(Output("cmp-picker", "children"), Output("cmp-tabs", "children"),
          Output("cmp-out", "children"),
          Input("cmp-sel", "data"), Input("cmp-metric", "data"))
def _render(sel, metric):
    runs = _perf_runs()
    sel = sel or []
    camps = [get_campaign(k) for k in sel]
    camps = [c for c in camps if c]
    fig = _figure(camps, metric)
    out = card(dcc.Graph(figure=fig, config={"displaylogo": False}) if fig
               else html.Div("Pick one or more runs, or no data for this metric.",
                             className="muted", style={"padding": "24px"}),
               title=dict(_METRICS).get(metric, "Comparison"),
               sub="converging lines = both runs at the test-rig ceiling, not the UPF limit")
    return _picker(runs, sel), _tabs(metric), out


def _figure(camps, metric):
    fig = go.Figure()
    any_data = False
    for i, c in enumerate(camps):
        s = c.suite("performance")
        color = T.mode_color(c.mode, i)
        name = f"{(c.mode or '?').upper()} · {c.campaign_id}"
        if metric == "throughput":
            test = next((t for t in s.tests for tn in t.tables if "frame size" in tn.lower()), None)
            rows = _rows(test, "frame size") if test else []
            if rows:
                any_data = True
                fig.add_trace(go.Scatter(x=[r.get("frame_B") for r in rows],
                              y=[_num(r.get("PDR_Mpps")) for r in rows], name=name,
                              mode="lines+markers", line=dict(color=color, width=3)))
        elif metric == "latency":
            lat = (c.kpis or {}).get("lat")
            if lat:
                p99 = lat.split("/")[-1].strip()
                any_data = True
                fig.add_trace(go.Bar(x=[name], y=[_num(p99)], marker_color=color, name=name))
        elif metric == "percore":
            pk = (c.kpis or {}).get("pdr")
            if pk and c.workers:
                any_data = True
                fig.add_trace(go.Bar(x=[name], y=[round(_num(pk) / c.workers, 3)],
                              marker_color=color, name=name))
    if not any_data:
        return None
    titles = {"throughput": ("Frame size (B)", "Mpps"), "latency": ("run", "p99 latency (µs)"),
              "percore": ("run", "Mpps per worker core")}
    xt, yt = titles[metric]
    if metric == "throughput":
        fig.add_hline(y=14.88, line=dict(color=T.MUTE, width=1, dash="dash"),
                      annotation_text="generator / NIC ceiling", annotation_position="top left")
    fig.update_layout(xaxis_title=xt, yaxis_title=yt)
    return T.apply(fig, height=420)
