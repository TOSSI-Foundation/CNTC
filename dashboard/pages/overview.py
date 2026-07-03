"""Overview — dashboard landing. Compact title, a latest-run summary card (verdict + mini
throughput chart + stat cells), a KPI quad, and the suite coverage cards. Data-first, not a
marketing hero (redesign v2)."""
from __future__ import annotations

import dash
from dash import dcc, html
import plotly.graph_objects as go

from dashboard import theme as T
from dashboard.charts import _num, _rows
from dashboard.data import SUITE_LABEL, load_campaigns

dash.register_page(__name__, path="/", name="Overview")

# monochrome glyphs for the suite icon box (CSS tints them neutral)
_SUITES = [
    ("01", "performance", "▥", "Throughput (NDR/PDR), latency, jitter, burst, multi-flow — RFC 2544 / 8219 / 9004, ETSI TST009."),
    ("02", "load", "☰", "Multi-UE: max concurrent sessions, aggregate + per-UE throughput, latency under load."),
    ("03", "pfcp", "⇄", "TS 29.244 N4 conformance: association, establish, modify, delete, error handling."),
    ("04", "n3neg", "⚠", "N3 data-plane negative/robustness: malformed GTP-U, unknown TEID, PSC (0x85) ext-header."),
]


def _n_findings(camps):
    n = 0
    for c in camps:
        s = c.suite("n3neg")
        if s and any((t.metrics or {}).get("crash_inducing_variants") for t in s.tests):
            n += 1
    return n


def _perf_rows(c):
    s = c.suite("performance")
    if not s:
        return []
    t = next((t for t in s.tests for tn in t.tables if "frame size" in tn.lower()), None)
    return _rows(t, "frame size") if t else []


def _mini_chart(rows):
    fig = go.Figure(go.Scatter(
        x=[r.get("frame_B") for r in rows], y=[_num(r.get("PDR_Mpps")) for r in rows],
        mode="lines+markers", line=dict(color=T.ACCENT, width=2.5),
        marker=dict(size=5), fill="tozeroy", fillcolor="rgba(13,148,136,.09)"))
    fig.update_layout(height=152, margin=dict(l=34, r=8, t=6, b=24), showlegend=False,
                      xaxis=dict(title=None, showgrid=False), yaxis=dict(title=None))
    return T.apply(fig, height=152)


def _latest_card(c):
    rows = _perf_rows(c)
    tot = c.totals
    ok = tot["bad"] == 0
    verdict = html.Div("✓" if ok else "✗",
                       className="lr-verdict" + ("" if ok else " fail"))
    meta = [html.Span(f"{tot['good']}/{tot['tests']} ok",
                      className="pill " + ("pill--pass" if ok else "pill--warn")),
            html.Span((c.mode or "?").upper(), className="pill pill--mode"),
            html.Span(c.date[5:16], className="muted small mono")]
    head = html.Div(className="lr-head", children=[
        verdict,
        html.Div(style={"minWidth": "0"}, children=[
            html.Div("Latest run", className="lr-eyebrow"),
            html.Div(c.campaign_id, className="lr-name")]),
        html.Div(meta, className="lr-meta")])
    chart = html.Div(className="lr-chart-wrap", children=[
        html.Div(className="chart-mini-head", children=[
            html.Span("Throughput vs frame size"), html.Span("Mpps · PDR", className="u")]),
        dcc.Graph(figure=_mini_chart(rows), config={"displayModeBar": False},
                  style={"height": "152px"})]) if rows else None
    k = c.kpis or {}
    lat = (k.get("lat") or "").split("/")
    # af_packet / kernel-path generators cap ~0.01 Mpps: the number is generator-limited,
    # not the UPF's ceiling. Label it so a low value isn't misread as a slow UPF.
    rig_limited = c.mode in ("af_packet", "afpacket", "simpleswitch", "linux")
    peak_sub = "af_packet rig — generator-limited" if rig_limited else None
    stats = html.Div(className="lr-stats", children=[
        _stat(f"{k.get('pdr', '—')}", " Mpps", "peak throughput", "var(--accent-fg)", sub=peak_sub),
        _stat((lat[-1].strip() if len(lat) > 1 else "—"), " µs", "p99 latency", "var(--violet-fg)"),
        _stat((lat[0].strip() if lat and lat[0] else "—"), " µs", "avg latency"),
        dcc.Link("Open run →", href=f"/campaign/{c.key}", className="btn btn-ghost",
                 style={"marginLeft": "auto", "alignSelf": "center"})])
    return html.Div(className="card latest-run", style={"margin": "0"},
                    children=[head, chart, stats] if chart else [head, stats])


def _stat(val, unit, label, color=None, sub=None):
    kids = [
        html.Div(className="stat-val", style={"color": color} if color else {}, children=[
            html.Span(val, className="tnum"),
            html.Span(unit, style={"fontSize": "12px", "color": "var(--mute)"})]),
        html.Div(label, className="stat-label")]
    if sub:
        kids.append(html.Div(sub, className="stat-caveat"))
    return html.Div(className="stat", children=kids)


def _kq(label, value, sub, color=None):
    return html.Div(className="kq-cell", children=[
        html.Div(label, className="l"),
        html.Div(value, className="v tnum", style={"color": color} if color else {}),
        html.Div(sub, className="s")])


def _suite_cards():
    cards = []
    for num, key, glyph, desc in _SUITES:
        cards.append(html.Div(className="suite-card", children=[
            html.Div(num, className="suite-card-num"),
            html.Div(glyph, className="suite-card-ico"),
            html.Div(SUITE_LABEL.get(key, key), className="suite-card-title"),
            html.Div(desc, className="suite-card-desc")]))
    return html.Div(cards, className="suite-cards")


def layout(**_):
    camps = [c for c in load_campaigns() if c.totals["tests"]]
    latest = next((c for c in camps if _perf_rows(c)), camps[0] if camps else None)
    n_runs = len(camps)
    n_find = _n_findings(camps)
    head = html.Div(className="page-head", style={"alignItems": "center", "marginBottom": "4px"},
        children=[
            html.H1("Overview", style={"fontSize": "26px", "margin": "0"}),
            html.Div(className="head-badges", children=[
                html.Span(className="status live", children=[
                    html.Span(className="dotc"), "Framework online"]),
                html.Span(f"updated {latest.date[:16]}" if latest else "no runs",
                          className="muted small mono")])])
    sub = html.P("Black-box benchmarking for 5G UPFs over real N3 / N4 interfaces — "
                 "performance, load, conformance and robustness across datapaths.",
                 className="muted small", style={"margin": "2px 0 18px", "maxWidth": "700px"})
    quad = html.Div(className="kpi-quad", children=[
        _kq("UPFs", "5", "3 live · 2 planned"),
        _kq("Dataplane modes", "4", "DPDK measured"),
        _kq("Runs", str(n_runs), "on record"),
        _kq("Suites", "4", "16 test cases"),
        _kq("Findings", str(n_find), "remote DoS · high" if n_find else "none",
            "var(--bad-fg)" if n_find else None),
        _kq("Last activity", latest.date[5:10] if latest else "—",
            (latest.date[11:16] + " · " + latest.campaign_id) if latest else ""),
    ])
    dash_top = html.Div(className="dash-top", children=[
        _latest_card(latest) if latest else html.Div("No runs yet.", className="muted"),
        quad])
    coverage = html.Div(className="section-label", children=[
        html.H2("Coverage"), html.Div(className="rule"),
        dcc.Link("UPF registry →", href="/upfs", className="finding-link",
                 style={"margin": "0", "fontSize": "12.5px"})])
    return html.Div(className="page", children=[head, sub, dash_top, coverage, _suite_cards()])
